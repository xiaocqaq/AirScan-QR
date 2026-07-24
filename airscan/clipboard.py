"""发送端剪贴板文本监听。"""
import os
import threading
import time


REMOTE_CLIPBOARD_PROCESSES = frozenset({
    "rdpclip.exe",
    "vdagent.exe",
    "vmtoolsd.exe",
    "wfica32.exe",
})


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


def read_clipboard_owner_process():
    """读取当前剪贴板所有者进程路径；无所有者或查询失败时返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetClipboardOwner.restype = wintypes.HWND
        kernel32.OpenProcess.restype = wintypes.HANDLE
        owner = user32.GetClipboardOwner()
        if not owner:
            return None
        process_id = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(owner, ctypes.byref(process_id)):
            return None
        handle = kernel32.OpenProcess(0x1000, False, process_id.value)
        if not handle:
            return None
        try:
            path = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(path))
            if not kernel32.QueryFullProcessImageNameW(
                    handle, 0, path, ctypes.byref(size)):
                return None
            return path.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def is_remote_clipboard_owner(process_path, ignored_processes=None):
    """判断剪贴板所有者是否为远程桌面剪贴板同步代理。"""
    if not process_path:
        return False
    ignored = ignored_processes or REMOTE_CLIPBOARD_PROCESSES
    return os.path.basename(process_path).casefold() in ignored


class ClipboardWatcher:
    """轮询剪贴板，发现新文本后调用回调。"""

    def __init__(self, on_text, interval=0.2, reader=None,
                 sequence_reader=None, owner_reader=None, clock=None,
                 duplicate_window=0.5, ignored_owner_names=None):
        self.on_text = on_text
        self.interval = max(0.05, float(interval))
        self.reader = reader or read_clipboard_text
        self.sequence_reader = sequence_reader or read_clipboard_sequence
        uses_system_clipboard = reader is None and sequence_reader is None
        self.owner_reader = owner_reader or (
            read_clipboard_owner_process if uses_system_clipboard else lambda: None
        )
        self.ignored_owner_names = frozenset(
            name.casefold() for name in (
                ignored_owner_names or REMOTE_CLIPBOARD_PROCESSES
            )
        )
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
        if is_remote_clipboard_owner(
                self.owner_reader(), self.ignored_owner_names):
            return
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
