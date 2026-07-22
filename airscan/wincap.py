"""窗口捕获 (Windows, 基于 PrintWindow flag=2 = PW_RENDERFULLCONTENT)。

用途: 接收端锁定发送端所在窗口, 即使该窗口被遮挡或在后台也能持续抓取其内容,
从而支持"后台运行"传输 (对齐旧 HTML 版 getDisplayMedia 抓窗口的体验)。

仅 Windows 可用; 非 Windows 环境导入不报错, list_windows 返回空。
"""
import sys

_WIN = sys.platform == "win32"

if _WIN:
    import win32gui
    import win32ui
    from ctypes import windll
    from PIL import Image

    # 系统级/无内容窗口标题, 枚举时跳过。
    _SKIP_TITLES = {"Program Manager", "Windows 输入体验", "Windows Input Experience"}

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

else:  # 非 Windows: 占位实现
    def list_windows():
        return []

    def window_exists(hwnd) -> bool:
        return False

    def grab_window(hwnd):
        return None
