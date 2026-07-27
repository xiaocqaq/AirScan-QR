"""AirScan-QR 接收端逻辑 (纯逻辑, 不含 GUI 主窗口).

职责:
- 定区屏幕捕获 (ImageGrab.grab(bbox)) + pyzbar 解出画面中所有 QR。
- 收到 meta 后按 fileSize 预分配临时文件, data 帧按 index*chunk 偏移 seek+write,
  用 bytearray 位图记录已收帧 -> 边收边落盘, 避免大文件全内存爆炸。
- 完成后校验 sha1; 文本模式返回文本(交给 GUI 写剪贴板), 否则保留临时文件供另存。
- 完成后不停止: 检测到新 tid (新 meta) 自动重置, 开始下一轮接收 (连传)。

区域框选 overlay 在 app.py (需 tkinter), 这里只接收 bbox。
"""
import hashlib
import math
import os
import tempfile

from PIL import ImageGrab

from . import protocol as P

_HASH_BUFFER_SIZE = 1024 * 1024  # 流式校验 SHA-1 的读块大小


def format_missing_ranges(indices: list[int]) -> str:
    """把 0-based 缺失索引压缩为 1-based 连续区间。"""
    if not indices:
        return "无缺失帧"
    ranges = []
    start = previous = indices[0] + 1
    for index in indices[1:]:
        current = index + 1
        if current == previous + 1:
            previous = current
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = current
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


class Task:
    """单次接收任务的状态 + 落盘。"""

    def __init__(self, meta: dict):
        self.tid = meta["tid"]
        self.name = meta["name"]
        self.total = meta["total"]
        self.file_size = meta["file_size"]
        self.sha1 = meta["sha1"]
        self.is_text = bool(meta["flags"] & P.FLAG_TEXT)
        self.is_sync = bool(meta["flags"] & P.FLAG_SYNC)
        self.chunk_size = meta["chunk_size"]  # 由 meta 明确给出, 不再反推

        self.received = bytearray(self.total)  # 位图: 1=已收
        self.got_count = 0
        self.done = False

        fd, self.path = tempfile.mkstemp(prefix="airscan_", suffix=".part")
        os.close(fd)
        # 预分配文件大小, 便于任意偏移写入。
        with open(self.path, "wb") as f:
            if self.file_size:
                f.seek(self.file_size - 1)
                f.write(b"\x00")
        self._fh = open(self.path, "r+b")

    def add(self, index: int, payload: bytes) -> bool:
        if self.done or index >= self.total or self.received[index]:
            return False
        self._fh.seek(index * self.chunk_size)
        self._fh.write(payload)
        self.received[index] = 1
        self.got_count += 1
        return True

    def is_complete(self) -> bool:
        return self.got_count == self.total

    def missing(self) -> list:
        return [i for i in range(self.total) if not self.received[i]]

    def missing_summary(self) -> dict:
        missing = self.missing()
        return {
            "name": self.name,
            "received": self.got_count,
            "total": self.total,
            "missing_count": len(missing),
            "ranges": format_missing_ranges(missing),
            "done": self.done,
        }

    def finalize(self):
        """flush 并流式校验 sha1; 返回 (ok, data_or_None)。文本/同步模式才读回内容。

        文件模式下按块读取算哈希, 不把整文件读进内存 (对齐"边收边落盘, 大文件不吃
        内存"的设计); 文本/同步清单通常很小, 校验通过后一并读回交给 GUI。
        """
        self._fh.flush()
        ok = self._verify_sha1()
        if ok:
            self._fh.close()
            self.done = True
        if self.is_text or self.is_sync:
            # 文本模式回传解码文本; 同步模式回传清单字节 (gzip), 均由 GUI 后续处理。
            with open(self.path, "rb") as f:
                return ok, f.read(self.file_size)
        return ok, None  # 文件模式内容留在 self.path, 供 GUI 另存

    def _verify_sha1(self) -> bool:
        """按块读取临时文件流式计算 SHA-1, 与 meta 里的期望值比对。"""
        h = hashlib.sha1()
        remaining = self.file_size
        with open(self.path, "rb") as f:
            while remaining > 0:
                chunk = f.read(min(_HASH_BUFFER_SIZE, remaining))
                if not chunk:
                    break  # 文件短于预期 -> 哈希必然不匹配
                h.update(chunk)
                remaining -= len(chunk)
        return h.digest() == self.sha1

    def reset_for_retry(self):
        """校验失败后清空接收位图，允许同一任务完整重传。"""
        self.received = bytearray(self.total)
        self.got_count = 0
        self.done = False
        self._fh.seek(0)

    def cleanup(self):
        try:
            if not self._fh.closed:
                self._fh.close()
        except Exception:
            pass
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except Exception:
            pass


class Receiver:
    """管理捕获与当前任务; GUI 定时调用 poll(bbox) 驱动。"""

    # 解码前把画面等比缩小: ZBar 耗时随像素数急剧上升 (同一 2×2 宫格画面 2100px 约
    # 500ms, 800px 约 20ms), 低配机上高分辨率解码就是卡顿主因。QR 每模块 2-3 像素即
    # 可识别, 捕获图往往远超所需。
    #
    # 关键约束: 宫格越密每格模块越小, 缩太狠会丢帧 (实测 3×3 缩到 900px 只解出 3/9)。
    # 且"部分解出"不能当失败信号, 否则会永久卡在丢帧档位 —— 故不用"从最省档起、失败
    # 再升"的阶梯, 而是从对 1×1~3×3 都安全的 SAFE_START 起步, 再按实际解出的 QR 数
    # 反推宫格数、收敛到该宫格的安全下限。
    DECODE_SAFE_START = 1100
    # 每格至少需要的像素 (实测下限约 370, 取 380 留余量)。
    DECODE_PX_PER_CELL = 380
    # 任何宫格都不缩到这个长边以下: 再小收益已微 (20ms 内), 风险却上升。
    DECODE_MIN_SIDE = 800
    # 连续解空这么多轮才放宽一档。容忍发送端刷新间隙、窗口重绘等偶发空帧, 避免画面
    # 正常却因一两次空转就退化。
    DECODE_MISS_LIMIT = 5
    # 空转时逐档放宽 (而非直接跳原分辨率): 发送端暂停/无画面时本就解不出, 若立刻升到
    # 原分辨率, 空闲状态反而占用最多 CPU。0 = 原分辨率, 兜底保证不会因缩放收不到。
    DECODE_RELAX_LADDER = (1400, 1800, 0)

    def __init__(self, on_meta=None, on_progress=None, on_complete=None):
        self.task = None
        self.on_meta = on_meta            # (Task) -> None
        self.on_progress = on_progress    # (got, total) -> None
        self.on_complete = on_complete    # (ok, task, text_or_None) -> None
        self._last_done_tid = None        # 防止已完成任务重复触发
        self.decode_max_side = self.DECODE_SAFE_START  # 0 = 原分辨率
        self._miss_streak = 0             # 连续解空的轮数
        self._max_found = 0               # 本任务内单帧解出过的最多 QR 数

    def reset_decode_scale(self):
        """回到安全起点。发送端换任务时宫格可能变密, 必须重新探测。"""
        self.decode_max_side = self.DECODE_SAFE_START
        self._miss_streak = 0
        self._max_found = 0

    def _on_decode_result(self, found: int):
        """按解码结果调整降采样上限。

        用"本任务见过的最多 QR 数"反推宫格边长 (ceil(sqrt(N))), 据此收敛到该宫格的
        安全长边。只随 _max_found 单调放宽, 不按单帧结果回缩 —— 否则一旦缩到某档后
        只解出部分 QR (如 3×3 在 800px 仅出 3/9), 会据此误判成更小的宫格而永久卡在
        丢帧档位。宫格变化必然伴随新任务 (新 tid), 由 reset_decode_scale 重新探测。
        """
        if not found:
            self._miss_streak += 1
            if self._miss_streak < self.DECODE_MISS_LIMIT:
                return
            self._miss_streak = 0
            for step in self.DECODE_RELAX_LADDER:
                if self.decode_max_side and (step == 0 or step > self.decode_max_side):
                    self.decode_max_side = step
                    break
            return
        self._miss_streak = 0
        if found <= self._max_found:
            return
        self._max_found = found
        if self.decode_max_side == 0:
            return  # 已在原分辨率, 不回缩 (兜底档位保持稳定)
        cells_per_side = math.isqrt(found - 1) + 1  # ceil(sqrt(found))
        self.decode_max_side = max(self.DECODE_MIN_SIDE,
                                   cells_per_side * self.DECODE_PX_PER_CELL)

    def grab(self, bbox):
        return ImageGrab.grab(bbox=bbox)

    def feed(self, img):
        """解码已捕获的图像并处理所有 QR。返回本次新收帧数。"""
        if img is None:
            return 0
        new = 0
        raws = P.decode_qr_all(img, max_side=self.decode_max_side)
        self._on_decode_result(len(raws))
        for raw in raws:
            f = P.parse_frame(raw)
            if not f:
                continue
            if f["type"] == P.TYPE_META:
                self._handle_meta(f)
            elif f["type"] == P.TYPE_DATA:
                new += self._handle_data(f)
        return new

    def poll(self, bbox):
        """区域截屏一帧并处理。返回本次新收帧数。"""
        return self.feed(self.grab(bbox))

    def _handle_meta(self, f):
        tid = f["tid"]
        # 新任务: 与当前不同, 且不是刚完成的那个 -> 重置开始新一轮 (连传)。
        if self.task is None or (tid != self.task.tid and tid != self._last_done_tid):
            if self.task and not self.task.done:
                self.task.cleanup()
            self.task = Task(f)
            self.reset_decode_scale()  # 新任务宫格可能变密, 重新探测安全档位
            if self.on_meta:
                self.on_meta(self.task)
        elif tid == self._last_done_tid:
            return  # 同一已完成任务的 meta, 忽略

    def _handle_data(self, f):
        t = self.task
        if t is None or t.done or f["tid"] != t.tid:
            return 0
        if t.add(f["index"], f["payload"]):
            if self.on_progress:
                self.on_progress(t.got_count, t.total)
            if t.is_complete():
                ok, text = t.finalize()
                if ok:
                    self._last_done_tid = t.tid
                else:
                    t.reset_for_retry()
                if self.on_complete:
                    self.on_complete(ok, t, text)
            return 1
        return 0
