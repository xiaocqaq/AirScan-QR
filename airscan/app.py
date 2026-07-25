"""AirScan-QR 桌面主窗口，使用 pywebview 连接 UI 与后台传输线程。"""
import base64
import io
import json
import os
import sys
import threading
import time
from collections import OrderedDict

import webview
from webview.dom import DOMEventHandler
from PIL import Image

from . import protocol as P
from . import wincap
from . import foldersync
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


class ImageDataUrlCache:
    """按图像对象身份缓存 PNG data URL，并限制缓存数量。"""

    def __init__(self, maxsize=32, encoder=None):
        self.maxsize = max(1, int(maxsize))
        self.encoder = encoder or _img_to_dataurl
        self._items = OrderedDict()

    def get(self, image):
        key = id(image)
        cached = self._items.get(key)
        if cached is not None and cached[0] is image:
            self._items.move_to_end(key)
            return cached[1]
        dataurl = self.encoder(image)
        self._items[key] = (image, dataurl)
        self._items.move_to_end(key)
        while len(self._items) > self.maxsize:
            self._items.popitem(last=False)
        return dataurl

    def clear(self):
        self._items.clear()


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


def _on_sync_drop(event):
    """云端第 ④ 步拖放区: 从拖入项取真实磁盘路径, 落成 applied 来源。

    WebView2 后端下, pywebview 会把真实路径塞进 files[i]['pywebviewFullPath'];
    标准浏览器 drop 只给文件名, 所以这一步必须走 pywebview 的 DOM drop 事件。
    """
    try:
        files = (event or {}).get("dataTransfer", {}).get("files", []) or []
    except Exception:
        files = []
    raw = None
    for f in files:
        raw = f.get("pywebviewFullPath") or raw
        if raw:
            break
    folder = None
    if raw and os.path.exists(raw):
        folder = raw if os.path.isdir(raw) else os.path.dirname(raw)
    if folder:
        _js(f"onSyncFolderDropped({_js_str(folder)})")
    else:
        _js("onSyncFolderDropped(null)")


def _register_sync_dnd():
    """window loaded 后注册拖放区; get_element 失败时静默跳过 (面板结构变更兜底)。"""
    if _window is None:
        return
    try:
        el = _window.dom.get_element("#syncDropZone")
        if el is not None:
            el.events.drop += DOMEventHandler(_on_sync_drop, prevent_default=True)
    except Exception:
        pass


def _apply_window_icon():
    """运行时把主窗口的标题栏/任务栏图标设为 icon.ico。

    源码运行时 pywebview 借用 python.exe 的图标; 用 WM_SETICON 强制覆盖,
    这样源码和打包 exe 都显示 QR 图标。找不到窗口/图标时静默跳过。
    """
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll, wintypes

        ico = _resource_path("icon.ico")
        if not os.path.exists(ico):
            return
        # 找到本进程标题为 "AirScan-QR..." 的顶层窗口句柄。
        user32 = windll.user32
        hwnd = user32.FindWindowW(None, "AirScan-QR · PC → PC")
        if not hwnd:
            return
        # LR_LOADFROMFILE=0x10, IMAGE_ICON=1
        big = user32.LoadImageW(None, ico, 1, 0, 0, 0x10 | 0x40)
        small = user32.LoadImageW(None, ico, 1, 16, 16, 0x10)
        WM_SETICON = 0x0080
        if big:
            user32.SendMessageW(hwnd, WM_SETICON, 1, big)   # ICON_BIG
        if small:
            user32.SendMessageW(hwnd, WM_SETICON, 0, small)  # ICON_SMALL
    except Exception:
        pass


def _on_window_loaded():
    _register_sync_dnd()
    _apply_window_icon()


def publish_send_frame(dataurl, status):
    """主窗口仅更新状态，二维码只推送到悬浮窗。"""
    _js(f"updateSendStatus({_js_str(status)})")
    _overlay_js(
        f"pushOverlayQR({_js_str(dataurl)}, {_js_str(status)})"
    )


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
        self._image_dataurls = ImageDataUrlCache()
        self.fps = 8
        self._picked_file = None
        self._send_start_index = 1
        self.receiver = None
        self.hwnd = None          # 锁定的目标窗口句柄
        self._recv_stop = threading.Event()
        self._recv_thread = None

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

    def _replace_send_source(self, src, err, fps, start_index=1, monitor_clipboard=True):
        with self._send_lock:
            self._send_stop.set()
            old = self._send_thread
            if old and old.is_alive() and old is not threading.current_thread():
                old.join()
            self.sender = None
            self._image_dataurls.clear()
            # 同步广播 (清单) 不监听剪贴板, 否则复制文本会顶掉正在广播的清单。
            self._clipboard_monitor_enabled = monitor_clipboard
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
            dataurl = self._image_dataurls.get(img)
            status = f"[{s.name}] {s.status()} · 已广播 {s.sent_frames} 帧"
            publish_send_frame(dataurl, status)
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
            # 静默写系统剪贴板，并把消息交给前端的有界历史列表展示。
            try:
                self._set_clipboard(content)
            except Exception:
                pass
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

    # --- 文件夹同步 ---
    def sync_pick_folder(self):
        """弹出文件夹选择框, 返回所选路径 (取消返回 None)。"""
        res = _window.create_file_dialog(webview.FOLDER_DIALOG)
        if not res:
            return None
        return res[0]

    def sync_resolve_dropped(self, path):
        """把拖入的路径规整成文件夹: 若拖入的是文件, 取其所在目录。"""
        if not path or not os.path.exists(path):
            return None
        return path if os.path.isdir(path) else os.path.dirname(path)

    def sync_git_build(self, repo_folder):
        """宿主机: 用 git 变动 (工作区 vs HEAD, 含未跟踪) 生成待粘贴输出文件夹。"""
        if not repo_folder or not os.path.isdir(repo_folder):
            return {"error": "请选择有效的文件夹"}
        if not foldersync.is_git_repo(repo_folder):
            return {"error": "所选文件夹不是 git 仓库 (未找到工作区)"}
        out_root = os.path.join(default_download_dir(),
                                f"airscan-sync-out-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            summary = foldersync.git_build_output(repo_folder, out_root)
        except Exception as e:
            return {"error": f"生成变动文件夹失败: {e}"}
        if not summary["copied"] and not summary["delete_count"]:
            return {"error": "工作区没有未提交的变动, 无需同步"}
        try:
            os.startfile(out_root)
        except Exception:
            pass
        summary["ok"] = True
        return summary

    def sync_apply(self, applied_folder, target_folder):
        """云端: 把粘贴进来的输出文件夹应用到目标文件夹 (删除带备份 + mtime 校正 + 校验)。"""
        if not applied_folder or not os.path.isdir(applied_folder):
            return {"error": "请选择粘贴进来的同步文件夹"}
        if not target_folder or not os.path.isdir(target_folder):
            return {"error": "请选择要同步的目标文件夹"}
        try:
            report = foldersync.apply_sync(applied_folder, target_folder)
        except FileNotFoundError:
            return {"error": "所选文件夹缺少 .airscan-sync/plan.json, 不是有效的同步文件夹"}
        except Exception as e:
            return {"error": f"应用同步失败: {e}"}
        report["ok_flag"] = report["ok"]
        return report

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
        # 绑定 AppUserModelID: 让任务栏用 exe 自带的 QR 图标, 而非默认 python 图标。
        try:
            windll.shell32.SetCurrentProcessExplicitAppUserModelID("AirScan-QR")
        except Exception:
            pass

    api = Api()
    window = webview.create_window(
        "AirScan-QR · PC → PC",
        url=_resource_path("ui.html"),
        js_api=api,
        width=820,
        height=900,
        min_size=(380, 480),
    )
    global _window
    _window = window
    window.events.closing += _on_closing
    window.events.loaded += _on_window_loaded
    _start_tray()
    webview.start()


if __name__ == "__main__":
    main()
