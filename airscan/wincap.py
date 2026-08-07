"""窗口捕获 (Windows, 基于 PrintWindow flag=2 = PW_RENDERFULLCONTENT)。

用途: 接收端锁定发送端所在窗口, 即使该窗口被遮挡或在后台也能持续抓取其内容,
从而支持"后台运行"传输 (对齐旧 HTML 版 getDisplayMedia 抓窗口的体验)。

仅 Windows 可用; 非 Windows 环境导入不报错, list_windows 返回空。
"""
import os
import sys


_TARGET_FIELDS = ("process_path", "class_name", "title")


def window_target(window: dict) -> dict:
    """提取可跨窗口重启保存的稳定身份，不保存 hwnd/PID。"""
    return {field: str(window.get(field) or "").strip()
            for field in _TARGET_FIELDS}


def _normalized(value) -> str:
    return os.path.normcase(str(value or "").strip()).casefold()


def _unique_match(windows, fields, target):
    matches = [window for window in windows if all(
        _normalized(window.get(field)) == _normalized(target.get(field))
        for field in fields
    )]
    return matches[0] if len(matches) == 1 else None


def resolve_window_target(target: dict, windows: list[dict]):
    """按稳定身份解析新 hwnd；候选不唯一时拒绝自动误连。"""
    target = window_target(target or {})
    exact_fields = [field for field in _TARGET_FIELDS if target[field]]
    if not exact_fields:
        return None
    exact = _unique_match(windows, exact_fields, target)
    if exact:
        return exact
    for fields in (("process_path", "class_name"),
                   ("process_path", "title"),
                   ("class_name", "title"), ("title",)):
        if all(target.get(field) for field in fields):
            match = _unique_match(windows, fields, target)
            if match:
                return match
    return None

_WIN = sys.platform == "win32"

if _WIN:
    import win32gui
    import win32ui
    from ctypes import byref, create_unicode_buffer, windll
    from ctypes import wintypes
    from PIL import Image

    # 系统级/无内容窗口标题, 枚举时跳过。
    _SKIP_TITLES = {"Program Manager", "Windows 输入体验", "Windows Input Experience"}
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def _process_path(hwnd):
        pid = wintypes.DWORD()
        windll.user32.GetWindowThreadProcessId(hwnd, byref(pid))
        handle = windll.kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = create_unicode_buffer(size.value)
            if windll.kernel32.QueryFullProcessImageNameW(
                    handle, 0, buffer, byref(size)):
                return buffer.value
            return ""
        finally:
            windll.kernel32.CloseHandle(handle)

    def list_windows():
        """返回可选窗口列表 [{hwnd, title, w, h}], 只含可见、有标题、尺寸够大的顶层窗口。"""
        out = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).strip()
            if not title or title in _SKIP_TITLES:
                return
            try:
                l, t, r, b = win32gui.GetWindowRect(hwnd)
            except Exception:
                return
            w, h = r - l, b - t
            if w > 120 and h > 120:
                out.append({"hwnd": hwnd, "title": title,
                            "class_name": win32gui.GetClassName(hwnd),
                            "process_path": _process_path(hwnd),
                            "x": l, "y": t, "w": w, "h": h})

        win32gui.EnumWindows(cb, None)
        return out

    def window_exists(hwnd) -> bool:
        try:
            return bool(win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd))
        except Exception:
            return False

    def grab_window(hwnd):
        """抓取指定窗口当前内容, 返回 PIL.Image (RGB); 失败返回 None。"""
        try:
            l, t, r, b = win32gui.GetWindowRect(hwnd)
        except Exception:
            return None
        w, h = r - l, b - t
        if w <= 0 or h <= 0:
            return None

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        try:
            bmp.CreateCompatibleBitmap(mfc_dc, w, h)
            save_dc.SelectObject(bmp)
            # flag=2 (PW_RENDERFULLCONTENT): 对 GPU 渲染窗口 (浏览器/webview) 才不黑屏。
            windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
            info = bmp.GetInfo()
            bits = bmp.GetBitmapBits(True)
            img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                                   bits, "raw", "BGRX", 0, 1)
        except Exception:
            img = None
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)
        return img

    def focus_window(hwnd) -> bool:
        """把指定窗口恢复并尝试置前; 失败返回 False。"""
        try:
            win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False

    _HWND_TOPMOST = -1
    _SWP_NOSIZE = 0x0001
    _SWP_NOMOVE = 0x0002
    _SWP_NOACTIVATE = 0x0010

    def set_topmost(hwnd) -> bool:
        """用 Win32 SetWindowPos 置顶窗口。

        不能用 WinForms 的 TopMost 属性: 那是跨线程属性赋值, .NET 会隐式 marshal
        到 GUI 线程并同步等待, 与 GUI 线程正在处理的窗口消息/WebView2 渲染形成死锁,
        表现为整个程序无响应。SetWindowPos 是纯 Win32 调用, 跨线程安全。
        """
        if not hwnd:
            return False
        try:
            return bool(windll.user32.SetWindowPos(
                int(hwnd), _HWND_TOPMOST, 0, 0, 0, 0,
                _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE))
        except Exception:
            return False

    def find_window_by_title(title) -> int:
        """按精确标题找顶层窗口句柄; 找不到返回 0。"""
        target = str(title or "").strip()
        if not target:
            return 0
        found = []

        def cb(hwnd, _):
            try:
                if win32gui.GetWindowText(hwnd).strip() == target:
                    found.append(hwnd)
            except Exception:
                pass

        try:
            win32gui.EnumWindows(cb, None)
        except Exception:
            return 0
        return int(found[0]) if found else 0

else:  # 非 Windows: 占位实现
    def list_windows():
        return []

    def window_exists(hwnd) -> bool:
        return False

    def grab_window(hwnd):
        return None

    def focus_window(hwnd) -> bool:
        return False

    def set_topmost(hwnd) -> bool:
        return False

    def find_window_by_title(title) -> int:
        return 0
