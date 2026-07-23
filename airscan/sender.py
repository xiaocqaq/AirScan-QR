"""AirScan-QR 发送端逻辑 (纯逻辑, 不含 GUI).

职责:
- 把文件/文本切片成 data 帧, 生成 meta 帧。
- 循环广播 (cyclic): 每次 next_image() 取 N×N 个帧合成一张宫格图。
- 周期性插入 meta 帧, 保证接收端任何时刻加入都能拿到文件元信息。

app.py 用定时器反复调用 next_image() 并显示返回的合成图。
"""
import os
import random
import string

from PIL import Image

from . import protocol as P


def _gen_tid() -> bytes:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=4)).encode("ascii")


class Sender:
    def __init__(self, data: bytes, name: str, is_text: bool,
                 error: str = "m", chunk_size: int = None,
                 grid: int = 2, scale: int = 6, start_index: int = 1):
        self.data = data
        self.name = name
        self.is_text = is_text
        self.error = error
        self.grid = max(1, grid)
        self.scale = scale
        self.tid = _gen_tid()

        cap = P.max_payload(error)
        self.chunk_size = min(chunk_size or cap, cap)

        self.chunks = P.slice_data(data, self.chunk_size)
        self.total = len(self.chunks)
        self.start_index = max(1, min(int(start_index), self.total))
        flags = P.FLAG_TEXT if is_text else 0
        self.meta = P.build_meta(self.tid, flags, self.total, self.chunk_size,
                                 len(data), name, P.sha1_bytes(data))

        # 懒渲染: 构建时不编码任何 QR (大文件几百帧会阻塞几秒)。
        # 每帧 QR 图在首次 next_image 需要时才编码, 之后缓存复用。
        # 编码开销分摊到后台广播线程, 主线程 (UI) 永不阻塞。
        self._meta_img = None
        self._data_cache = {}    # index -> 已渲染的 QR 图

        self._pos = self.start_index - 1  # 当前在 data 帧序列中的位置
        self._since_meta = self.total  # 初始设满 -> 第一格先发 meta
        self.sent_frames = 0
        # 每发约一屏的 data 帧后注入一次 meta, 保证接收端随时加入都能拿到元信息。
        # 用 data 帧计数(而非总格子数)驱动, 避免 total=1 时永远发不出 data 的死锁。
        self._meta_gap = max(self.total, self.grid * self.grid)

    def _meta_image(self) -> Image.Image:
        if self._meta_img is None:
            self._meta_img = P.encode_qr_img(self.meta, self.error, self.scale)
        return self._meta_img

    def _data_image(self, idx: int) -> Image.Image:
        img = self._data_cache.get(idx)
        if img is None:
            frame = P.build_data(self.tid, idx, self.chunks[idx])
            img = P.encode_qr_img(frame, self.error, self.scale)
            self._data_cache[idx] = img
        return img

    def seek(self, frame_number: int) -> int:
        """把下一帧定位到指定的 1-based 序号。"""
        self.start_index = max(1, min(int(frame_number), self.total))
        self._pos = self.start_index - 1
        return self.start_index

    def _next_cell(self) -> Image.Image:
        """广播流: 每发满 _meta_gap 个 data 帧后注入一次 meta, 其余循环发 data。"""
        if self._since_meta >= self._meta_gap:
            self._since_meta = 0
            return self._meta_image()
        img = self._data_image(self._pos)
        self._pos = (self._pos + 1) % self.total
        self._since_meta += 1
        self.sent_frames += 1
        return img

    def next_image(self) -> Image.Image:
        cells = self.grid * self.grid
        picks = [self._next_cell() for _ in range(cells)]
        return self._compose(picks)

    def _compose(self, imgs) -> Image.Image:
        n = self.grid
        cw = max(im.width for im in imgs)
        ch = max(im.height for im in imgs)
        canvas = Image.new("L", (cw * n, ch * n), 255)
        for idx, im in enumerate(imgs):
            r, c = divmod(idx, n)
            # 居中放入格子
            x = c * cw + (cw - im.width) // 2
            y = r * ch + (ch - im.height) // 2
            canvas.paste(im, (x, y))
        return canvas

    def status(self) -> str:
        return f"{self.total} 帧 · {self.grid}×{self.grid} 宫格 · 单帧{self.chunk_size}B · 纠错{self.error.upper()}"


def load_file(path: str):
    """读文件返回 (bytes, name, is_text=False)."""
    with open(path, "rb") as f:
        return f.read(), os.path.basename(path), False


def load_text(text: str):
    """文本消息返回 (utf-8 bytes, 显示名, is_text=True)."""
    return text.encode("utf-8"), "message.txt", True
