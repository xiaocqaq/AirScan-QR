"""AirScan-QR 接收端逻辑 (纯逻辑, 不含 GUI 主窗口).

职责:
- 定区屏幕捕获 (ImageGrab.grab(bbox)) + pyzbar 解出画面中所有 QR。
- 收到 meta 后按 fileSize 预分配临时文件, data 帧按 index*chunk 偏移 seek+write,
  用 bytearray 位图记录已收帧 -> 边收边落盘, 避免大文件全内存爆炸。
- 完成后校验 sha1; 文本模式返回文本(交给 GUI 写剪贴板), 否则保留临时文件供另存。
- 完成后不停止: 检测到新 tid (新 meta) 自动重置, 开始下一轮接收 (连传)。

区域框选 overlay 在 app.py (需 tkinter), 这里只接收 bbox。
"""
import os
import tempfile

from PIL import ImageGrab

from . import protocol as P


class Task:
    """单次接收任务的状态 + 落盘。"""

    def __init__(self, meta: dict):
        self.tid = meta["tid"]
        self.name = meta["name"]
        self.total = meta["total"]
        self.file_size = meta["file_size"]
        self.sha1 = meta["sha1"]
        self.is_text = bool(meta["flags"] & P.FLAG_TEXT)
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

    def finalize(self):
        """flush 并校验 sha1; 返回 (ok, data_or_None)。文本模式一并读回内容。"""
        self._fh.flush()
        self._fh.close()
        self.done = True
        with open(self.path, "rb") as f:
            data = f.read(self.file_size)
        ok = P.sha1_bytes(data) == self.sha1
        if self.is_text:
            return ok, data
        return ok, None  # 文件模式内容留在 self.path, 供 GUI 另存

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

    def __init__(self, on_meta=None, on_progress=None, on_complete=None):
        self.task = None
        self.on_meta = on_meta            # (Task) -> None
        self.on_progress = on_progress    # (got, total) -> None
        self.on_complete = on_complete    # (ok, task, text_or_None) -> None
        self._last_done_tid = None        # 防止已完成任务重复触发

    def grab(self, bbox):
        return ImageGrab.grab(bbox=bbox)

    def feed(self, img):
        """解码已捕获的图像并处理所有 QR。返回本次新收帧数。"""
        if img is None:
            return 0
        new = 0
        for raw in P.decode_qr_all(img):
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
                self._last_done_tid = t.tid
                if self.on_complete:
                    self.on_complete(ok, t, text)
            return 1
        return 0
