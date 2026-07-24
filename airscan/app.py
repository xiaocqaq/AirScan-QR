"""AirScan-QR 桌面主窗口，使用 pywebview 连接 UI 与后台传输线程。"""
import base64
import io
import json
import os
import sys
import threading
import time

import webview
from PIL import Image

from . import protocol as P
from . import wincap
from .clipboard import ClipboardWatcher, normalize_clipboard_text, read_clipboard_text
from .overlay import OVERLAY_HTML
from .sender import Sender, load_file, load_text, parse_frame_selection
from .receiver import Receiver
from .storage import default_download_dir, save_received_file, set_clipboard


def _resource_path(name: str) -> str:
    """兼容 PyInstaller onefile: 资源在 sys._MEIPASS 下。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _img_to_dataurl(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _js_str(s: str) -> str:
    """安全地把 Python 字符串嵌入 evaluate_js 调用 (JSON 转义)。"""
    return json.dumps(s, ensure_ascii=False)


_window = None  # 保持模块级，避免 pywebview introspect 内部 .NET 对象。
_overlay_window = None
_tray = None    # 托盘图标 (pystray.Icon)
_really_quit = False  # True 时 closing 事件放行真正退出


def _js(code: str):
    if _window is not None:
        _window.evaluate_js(code)


def _overlay_js(code: str):
    if _overlay_window is None:
        return
    try:
        _overlay_window.evaluate_js(code)
    except Exception:
        pass


class Api:
    def __init__(self):
        self.sender = None
        self._send_stop = threading.Event()
        self._send_thread = None
        self._send_lock = threading.RLock()
        self._clipboard_watcher = ClipboardWatcher(self._on_clipboard_text)
        self._clipboard_monitor_enabled = False
        self._send_error_level = "m"
        self._active_text = None
        self.fps = 8
        self._picked_file = None
        self._send_start_index = 1
        self.receiver = None
        self.hwnd = None          # 锁定的目标窗口句柄
        self._recv_stop = threading.Event()
        self._recv_thread = None
        self._messages = []       # 已接收的文本消息 (供逐条复制)

    def pick_file(self):
        res = _window.create_file_dialog(webview.OPEN_DIALOG)
        if not res:
            return None
        self._picked_file = res[0]
        return os.path.basename(self._picked_file)

    def clear_file(self):
        self._picked_file = None
        return {"ok": True}

    def start_send(self, text, grid, err, fps, start_index=1):
        if self._picked_file:
            src = ("file", self._picked_file)
        elif text and text.strip():
            src = ("text", text)
        else:
            clipboard_text = normalize_clipboard_text(read_clipboard_text())
            if not clipboard_text:
                return {"error": "请先输入文本、选择文件或复制文本"}
            src = ("text", clipboard_text)
        return self._replace_send_source(src, err, fps, start_index)

    def _replace_send_source(self, src, err, fps, start_index=1):
        with self._send_lock:
            self._send_stop.set()
            old = self._send_thread
            if old and old.is_alive() and old is not threading.current_thread():
                old.join()
            self.sender = None
            self._clipboard_monitor_enabled = True
            self.fps = max(1, int(fps))
            self._send_error_level = err
            self._active_text = src[1] if src[0] == "text" else None
            self._send_start_index = max(1, int(start_index))
            self._send_stop.clear()
            self.open_overlay()
            self._send_thread = threading.Thread(
                target=self._build_and_send,
                args=(src, err, 1, self._send_start_index),
                daemon=True,
            )
            self._send_thread.start()
        return {"ok": True, "grid": 1}

    def _build_and_send(self, src, err, grid, start_index):
        try:
            if src[0] == "file":
                data, name, is_text = load_file(src[1])
            else:
                data, name, is_text = load_text(src[1])
            self.sender = Sender(
                data, name, is_text, error=err, grid=grid,
                start_index=start_index,
            )
            self._send_start_index = self.sender.start_index
        except Exception as e:
            _js(f"onSendError({_js_str(f'发送失败: {e}')})")
            self._clipboard_monitor_enabled = False
            self._stop_clipboard_watch()
            self.close_overlay()
            return
        _js(f"onSendReady({self.sender.total}, {self.sender.start_index})")
        self._start_clipboard_watch()
        self._send_loop()

    def set_fps(self, fps):
        self.fps = max(1, int(fps))

    def pause_send(self):
        self._send_stop.set()
        self._clipboard_monitor_enabled = False
        self._stop_clipboard_watch()
        self.close_overlay()
        old = self._send_thread
        if old and old.is_alive() and old is not threading.current_thread():
            old.join()
        return {"ok": True}

    def resume_send(self, start_index, resend_spec=None):
        if not self.sender:
            return {"error": "当前没有可继续的广播任务"}
        selection = None
        if resend_spec is not None:
            try:
                selection = (parse_frame_selection(resend_spec, self.sender.total)
                             if str(resend_spec).strip() else [])
            except ValueError as error:
                return {"error": str(error)}
        requested = max(1, int(start_index))
        if selection is not None:
            self.pause_send()
            count = (self.sender.set_resend_indices(selection) if selection
                     else (self.sender.clear_resend_indices() or 0))
        elif requested != self._send_start_index:
            self._send_start_index = self.sender.seek(requested)
            count = 0
        else:
            count = len(getattr(self.sender, "_resend_indices", ()) or ())
        if self._send_thread and self._send_thread.is_alive():
            return {"ok": True, "start_index": self._send_start_index,
                    "selection_count": count}
        self._send_stop.clear()
        self._clipboard_monitor_enabled = True
        self.open_overlay()
        self._start_clipboard_watch()
        self._send_thread = threading.Thread(target=self._send_loop, daemon=True)
        self._send_thread.start()
        return {"ok": True, "start_index": self._send_start_index,
                "selection_count": count}

    def stop_send(self):
        """兼容旧前端：停止语义降级为暂停，不销毁 Sender。"""
        return self.pause_send()

    # 默认展示阈值: 循环到 max(5 遍, 30 秒) 取较长者后自动暂停 (非停止, 保留任务)。
    AUTO_STOP_CYCLES = 5
    AUTO_STOP_SECONDS = 30.0

    def _send_loop(self):
        start = time.monotonic()
        base_frames = self.sender.sent_frames if self.sender else 0
        while not self._send_stop.is_set() and self.sender:
            s = self.sender
            img = s.next_image()
            dataurl = _img_to_dataurl(img)
            status = f"[{s.name}] {s.status()} · 已广播 {s.sent_frames} 帧"
            _js(f"pushQR({_js_str(dataurl)}, {_js_str(status)})")
            _overlay_js(f"pushOverlayQR({_js_str(dataurl)}, {_js_str(status)})")
            # 补发模式持续循环不自动停; 默认广播达到 max(5遍, 30s) 后自动暂停,
            # 仅结束循环线程, 保留 Sender 与 _pos, 点“继续广播”从暂停处续播。
            if not s._resend_indices and s.total:
                cycles = (s.sent_frames - base_frames) / s.total
                elapsed = time.monotonic() - start
                if cycles >= self.AUTO_STOP_CYCLES and elapsed >= self.AUTO_STOP_SECONDS:
                    self._send_stop.set()
                    _overlay_js("onOverlayPaused('已自动暂停广播')")
                    _js(f"onSendAutoStopped({int(cycles)})")
                    break
            time.sleep(1.0 / max(1, self.fps))

    def _start_clipboard_watch(self):
        self._clipboard_watcher.start()

    def _stop_clipboard_watch(self):
        self._clipboard_watcher.stop()

    def _on_clipboard_text(self, text):
        if not self._clipboard_monitor_enabled or self.sender is None:
            return
        if text == self._active_text:
            return
        self._replace_send_source(("text", text), self._send_error_level, self.fps)
        _js("onClipboardSendStarted()")

    def _set_clipboard(self, text):
        self._clipboard_watcher.ignore_text(text)
        set_clipboard(text)

    def open_overlay(self):
        global _overlay_window
        if _overlay_window is not None:
            try:
                _overlay_window.show()
                return {"ok": True}
            except Exception:
                _overlay_window = None
        _overlay_window = webview.create_window(
            "AirScan-QR 悬浮广播",
            html=OVERLAY_HTML,
            js_api=self,
            width=360,
            height=420,
            min_size=(180, 220),
            resizable=True,
            on_top=True,
        )
        try:
            def on_closing():
                global _overlay_window
                _overlay_window = None
                return True
            _overlay_window.events.closing += on_closing
        except Exception:
            pass
        return {"ok": True}

    def close_overlay(self):
        global _overlay_window
        overlay = _overlay_window
        _overlay_window = None
        if overlay is not None:
            try:
                overlay.destroy()
            except Exception:
                pass
        return {"ok": True}

    def list_windows(self):
        return wincap.list_windows()

    def set_window(self, hwnd):
        self.hwnd = int(hwnd)
        return {"ok": True}

    def start_recv(self):
        if not self.hwnd:
            return {"error": "请先选择窗口"}
        resumed = self.receiver is not None
        if self.receiver is None:
            self.receiver = Receiver(on_meta=self._on_meta,
                                     on_progress=self._on_progress,
                                     on_complete=self._on_complete)
        if self._recv_thread and self._recv_thread.is_alive():
            return {"ok": True, "resumed": resumed}
        self._recv_stop.clear()
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        return {"ok": True, "resumed": resumed}

    def pause_recv(self):
        self._recv_stop.set()
        old = self._recv_thread
        if old and old.is_alive() and old is not threading.current_thread():
            old.join()
        return {"ok": True}

    def reset_recv(self):
        self.pause_recv()
        if self.receiver and self.receiver.task:
            self.receiver.task.cleanup()
        self.receiver = None
        return {"ok": True}

    def stop_recv(self):
        """兼容旧前端：停止语义降级为暂停，不清理接收任务。"""
        return self.pause_recv()

    def get_missing(self):
        """返回当前接收任务的缺失帧摘要，序号按 1-based 展示。"""
        task = self.receiver.task if self.receiver else None
        if task:
            return task.missing_summary()
        return {
            "name": "",
            "received": 0,
            "total": 0,
            "missing_count": 0,
            "ranges": "暂无接收任务",
            "done": False,
        }

    def get_download_dir(self):
        return str(default_download_dir())

    def open_download_dir(self):
        directory = default_download_dir()
        os.startfile(directory)
        return {"ok": True, "path": str(directory)}

    def open_file(self, path):
        """用系统默认应用打开已下载的文件。"""
        try:
            if not path or not os.path.exists(path):
                return {"ok": False, "error": "文件不存在"}
            os.startfile(path)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _recv_loop(self):
        while not self._recv_stop.is_set():
            try:
                img = wincap.grab_window(self.hwnd)
                self.receiver.feed(img)
            except Exception as e:
                _js(f"document.getElementById('recvStatus').innerText={_js_str('捕获错误: ' + str(e))}")
            time.sleep(0.06)

    def _on_meta(self, task):
        _js(f"onMeta({_js_str(task.name)}, {task.total}, {str(task.is_text).lower()})")

    def _on_progress(self, got, total):
        _js(f"onProgress({got}, {total})")

    def _on_complete(self, ok, task, text):
        if not ok:
            _js("onComplete(false, false, '')")
            return
        if task.is_text and text is not None:
            content = text.decode("utf-8", "replace")
            # 静默写系统剪贴板 (不弹提示), 并把消息追加到前端消息列表。
            try:
                self._set_clipboard(content)
            except Exception:
                pass
            self._messages.append(content)
            _js(f"addMessage({_js_str(content)})")
            task.cleanup()
            _js("onComplete(true, true, '')")
        else:
            try:
                path = save_received_file(task.path, task.file_size, task.name)
                task.cleanup()
                info = f"已保存: {path} · 等待下一次发送"
                _js(f"onComplete(true, false, {_js_str(info)}, "
                    f"{_js_str(str(path))}, {_js_str(os.path.basename(str(path)))})")
            except Exception as e:
                info = f"保存失败: {e} · 临时文件已保留"
                _js(f"onComplete(false, false, {_js_str(info)})")

    def copy_text(self, text):
        """逐条复制: 前端点消息旁的复制图标, 把该条文本重新写入剪贴板。"""
        try:
            self._set_clipboard(text)
            return {"ok": True}
        except Exception:
            return {"ok": False}


def _tray_image():
    """托盘图标: 优先用打包的 icon.ico, 失败则画一个蓝底 QR 占位。"""
    from PIL import Image as _Image
    try:
        return _Image.open(_resource_path("icon.ico"))
    except Exception:
        from PIL import ImageDraw
        img = _Image.new("RGB", (64, 64), "#2563eb")
        d = ImageDraw.Draw(img)
        d.rectangle([14, 14, 26, 26], fill="#ffffff")
        d.rectangle([38, 14, 50, 26], fill="#ffffff")
        d.rectangle([14, 38, 26, 50], fill="#ffffff")
        return img


def _show_window(*_):
    if _window is not None:
        _window.show()


def _quit_app(*_):
    """从托盘退出: 置标志, 停托盘, 销毁窗口 (closing 会放行)。"""
    global _really_quit
    _really_quit = True
    if _tray is not None:
        _tray.stop()
    if _window is not None:
        _window.destroy()


def _on_closing():
    """拦截关闭: 非真正退出时隐藏到托盘, 返回 False 取消关闭。"""
    if _really_quit:
        return True
    if _window is not None:
        _window.hide()
    return False


def _start_tray():
    """在独立线程运行系统托盘 (icon.run 会阻塞)。"""
    import pystray
    from pystray import MenuItem as Item

    global _tray
    _tray = pystray.Icon(
        "AirScan-QR",
        _tray_image(),
        "AirScan-QR",
        menu=pystray.Menu(
            Item("显示主窗口", _show_window, default=True),
            Item("退出", _quit_app),
        ),
    )
    threading.Thread(target=_tray.run, daemon=True).start()


def main():
    if sys.platform == "win32":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    api = Api()
    window = webview.create_window(
        "AirScan-QR · PC → PC",
        url=_resource_path("ui.html"),
        js_api=api,
        width=820,
        height=900,
        min_size=(640, 720),
    )
    global _window
    _window = window
    window.events.closing += _on_closing
    _start_tray()
    webview.start()


if __name__ == "__main__":
    main()
