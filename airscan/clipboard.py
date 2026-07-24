"""发送端剪贴板文本监听。"""
import os
import threading
import time


REMOTE_CLIPBOARD_PROCESSES = frozenset({
    "hsrclient.exe",
    "hsrclientlauncher.exe",
    "rdpclip.exe",
    "runhsr.exe",
    "vdagent.exe",
    "vmtoolsd.exe",
    "wfica32.exe",
    "中移在线云桌面.exe",
})

CF_UNICODETEXT = 13
NON_TEXT_CLIPBOARD_FORMATS = frozenset({2, 8, 15, 17})


def normalize_clipboard_text(value: str):
    """只接受非空文本，保留用户原始换行与空格。"""
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def is_text_only_clipboard(available_formats) -> bool:
    """仅接受 Unicode 文本，显式拒绝文件拖放和位图格式。"""
    formats = set(available_formats)
    return (CF_UNICODETEXT in formats
            and not formats.intersection(NON_TEXT_CLIPBOARD_FORMATS))


def _read_open_clipboard_formats(win32clipboard):
    formats = set()
    current = 0
    while True:
        current = win32clipboard.EnumClipboardFormats(current)
        if not current:
            return formats
        formats.add(current)


def read_clipboard_text():
    """读取 Windows Unicode 剪贴板文本；无文本或占用时返回 None。"""
    import win32clipboard

    try:
        win32clipboard.OpenClipboard()
        try:
            if not is_text_only_clipboard(
                    _read_open_clipboard_formats(win32clipboard)):
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


def _read_window_process(window):
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = wintypes.HANDLE
        if not window:
            return None
        process_id = wintypes.DWORD()
        if not ctypes.windll.user32.GetWindowThreadProcessId(
                window, ctypes.byref(process_id)):
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


def read_clipboard_owner_process():
    """读取当前剪贴板所有者进程路径；无所有者或查询失败时返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes

        ctypes.windll.user32.GetClipboardOwner.restype = wintypes.HWND
        return _read_window_process(ctypes.windll.user32.GetClipboardOwner())
    except Exception:
        return None


def read_foreground_process():
    """读取当前前台窗口所属进程路径。"""
    try:
        import ctypes
        from ctypes import wintypes

        ctypes.windll.user32.GetForegroundWindow.restype = wintypes.HWND
        return _read_window_process(ctypes.windll.user32.GetForegroundWindow())
    except Exception:
        return None


def read_last_input_age():
    """返回距离本机最近一次键盘或鼠标输入的秒数。"""
    try:
        import ctypes
        from ctypes import wintypes

        class LastInputInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT),
                        ("dwTime", wintypes.DWORD)]

        info = LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        current = ctypes.windll.kernel32.GetTickCount()
        elapsed_ms = (current - info.dwTime) & 0xFFFFFFFF
        return elapsed_ms / 1000.0
    except Exception:
        return None


def is_remote_clipboard_owner(process_path, ignored_processes=None):
    """判断剪贴板所有者是否为远程桌面剪贴板同步代理。"""
    if not process_path:
        return False
    ignored = ignored_processes or REMOTE_CLIPBOARD_PROCESSES
    return os.path.basename(process_path).casefold() in ignored


def is_foreground_clipboard_owner(owner_path, foreground_path):
    """无法查询时放行；可查询时只接受前台应用产生的复制事件。"""
    if not owner_path or not foreground_path:
        return True
    return os.path.normcase(owner_path) == os.path.normcase(foreground_path)


class ClipboardWatcher:
    """轮询剪贴板，发现新文本后调用回调。"""

    def __init__(self, on_text, interval=0.2, reader=None,
                 sequence_reader=None, owner_reader=None,
                 foreground_reader=None, input_age_reader=None, clock=None,
                 duplicate_window=0.5, ignored_owner_names=None,
                 local_input_window=1.5):
        self.on_text = on_text
        self.interval = max(0.05, float(interval))
        self.reader = reader or read_clipboard_text
        self.sequence_reader = sequence_reader or read_clipboard_sequence
        uses_system_clipboard = reader is None and sequence_reader is None
        self.owner_reader = owner_reader or (
            read_clipboard_owner_process if uses_system_clipboard else lambda: None
        )
        self.foreground_reader = foreground_reader or (
            read_foreground_process if uses_system_clipboard else lambda: None
        )
        self.input_age_reader = input_age_reader or (
            read_last_input_age if uses_system_clipboard else lambda: 0.0
        )
        self.local_input_window = max(0.1, float(local_input_window))
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
        if text == self._ignored_text:
            self._ignored_text = None
            return
        self._ignored_text = None
        owner = self.owner_reader()
        if is_remote_clipboard_owner(owner, self.ignored_owner_names):
            return
        if not is_foreground_clipboard_owner(
                owner, self.foreground_reader()):
            return
        input_age = self.input_age_reader()
        if input_age is not None and input_age > self.local_input_window:
            return
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
