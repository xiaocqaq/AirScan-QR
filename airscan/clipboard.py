"""发送端剪贴板文本监听。"""
import threading
import time


def normalize_clipboard_text(value: str):
    """只接受非空文本，保留用户原始换行与空格。"""
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def read_clipboard_text():
    """读取 Windows Unicode 剪贴板文本；无文本或占用时返回 None。"""
    import win32clipboard

    try:
        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return None
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return None


def read_clipboard_sequence():
    """读取 Windows 剪贴板变更序号；不可用时返回 None。"""
    try:
        from ctypes import windll
        return int(windll.user32.GetClipboardSequenceNumber())
    except Exception:
        return None


class ClipboardWatcher:
    """轮询剪贴板，发现新文本后调用回调。"""

    def __init__(self, on_text, interval=0.2, reader=None,
                 sequence_reader=None, clock=None, duplicate_window=0.5):
        self.on_text = on_text
        self.interval = max(0.05, float(interval))
        self.reader = reader or read_clipboard_text
        self.sequence_reader = sequence_reader or read_clipboard_sequence
        self.clock = clock or time.monotonic
        self.duplicate_window = max(0.0, float(duplicate_window))
        self._stop = threading.Event()
        self._thread = None
        self._last_text = None
        self._last_sequence = None
        self._ignored_text = None
        self._last_emitted_text = None
        self._last_emitted_at = float("-inf")

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.prime()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.interval * 2)
        self._thread = None

    def ignore_text(self, text):
        self._ignored_text = normalize_clipboard_text(text)

    def prime(self):
        """记录当前剪贴板状态，避免监听启动时广播旧内容。"""
        self._last_sequence = self.sequence_reader()
        self._last_text = normalize_clipboard_text(self.reader())

    def poll_once(self):
        sequence = self.sequence_reader()
        if sequence is not None and sequence == self._last_sequence:
            return
        text = normalize_clipboard_text(self.reader())
        if text is None:
            return
        if sequence is None and text == self._last_text:
            return
        self._last_text = text
        self._last_sequence = sequence
        if text == self._ignored_text:
            self._ignored_text = None
            return
        self._ignored_text = None
        now = self.clock()
        if (text == self._last_emitted_text
                and now - self._last_emitted_at < self.duplicate_window):
            return
        self.on_text(text)
        self._last_emitted_text = text
        self._last_emitted_at = now

    def _run(self):
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)
