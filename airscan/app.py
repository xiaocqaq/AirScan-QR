"""AirScan-QR 桌面主窗口，使用 pywebview 连接 UI 与后台传输线程。"""
import base64
import io
import json
import os
import sys
import tempfile
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
from .git_tunnel import CloudTunnel, HostTunnel
from .git_tunnel_protocol import is_clipboard_message
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


def _png_to_dataurl(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


class GitQrPageCache:
    """将 Git QR PNG 缓存在临时文件，避免重复编码和大内存驻留。"""

    def __init__(self, encoder):
        self.encoder = encoder
        self._file = tempfile.TemporaryFile(prefix="airscan-git-qr-")
        self._entries = {}

    def get(self, index, page):
        entry = self._entries.get(index)
        if entry is None:
            png = self.encoder(page)
            self._file.seek(0, io.SEEK_END)
            entry = (self._file.tell(), len(png))
            self._file.write(png)
            self._entries[index] = entry
        else:
            self._file.seek(entry[0])
            png = self._file.read(entry[1])
        return _png_to_dataurl(png)

    def close(self):
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


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


def _preview_text(text: str, limit: int = 60) -> str:
    """把剪贴板文本压成单行短预览，供确认框展示。"""
    one_line = " ".join(str(text).split())
    if len(one_line) <= limit:
        return one_line
    return one_line[:limit] + "…"


_window = None  # 保持模块级，避免 pywebview introspect 内部 .NET 对象。
_overlay_window = None
_overlay_minimized = False
_tray = None    # 托盘图标 (pystray.Icon)
_really_quit = False  # True 时 closing 事件放行真正退出
APP_TITLE = "AirScan-QR"
GIT_QR_FRAME_SECONDS = 0.4
GIT_QR_SINGLE_SECONDS = 0.25


def _js(code: str):
    if _window is not None:
        _window.evaluate_js(code)


def _overlay_js(code: str):
    if _overlay_window is None:
        return
    if _overlay_minimized:
        return
    try:
        _overlay_window.evaluate_js(code)
    except Exception:
        pass


def _set_overlay_on_top(on_top: bool):
    if _overlay_window is None:
        return
    try:
        _overlay_window.on_top = bool(on_top)
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
        hwnd = user32.FindWindowW(None, APP_TITLE)
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
        self._qr_output_lock = threading.Lock()
        self._clipboard_watcher = ClipboardWatcher(self._on_clipboard_text)
        self._clipboard_monitor_enabled = False
        self._send_error_level = "m"
        self._active_text = None
        self._image_dataurls = ImageDataUrlCache()
        self.fps = 8
        self._picked_file = None
        self._send_start_index = 1
        self._send_grid = 1  # 当前宫格设置; 剪贴板热切换沿用上次选择
        self._send_is_file = False
        self._pending_clipboard_text = None
        self._pending_clipboard_seq = 0
        self._clipboard_switch_lock = threading.Lock()
        self._queued_clipboard_text = None
        self._clipboard_switch_thread = None
        self.receiver = None
        self.hwnd = None          # 锁定的目标窗口句柄
        self._git_host_hwnd = None
        self._window_targets = {"recv": None, "git_host": None}
        self._window_reconnect_after = {"recv": 0.0, "git_host": 0.0}
        self._recv_stop = threading.Event()
        self._recv_thread = None
        self.git_host = None
        self.git_cloud = None

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
        return self._replace_send_source(src, err, fps, start_index, grid=grid)

    def _replace_send_source(self, src, err, fps, start_index=1,
                             monitor_clipboard=True, from_clipboard=False,
                             grid=None):
        with self._send_lock:
            # 剪贴板触发的热切换: 拿到锁后复查开关 —— 等锁期间用户可能已手动
            # 暂停 (pause_send 会关闭监听), 此时放弃切换, 避免暂停后又自动开播。
            if from_clipboard and not self._clipboard_monitor_enabled:
                return {"ok": False, "skipped": True}
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
            self._send_is_file = src[0] == "file"
            self._pending_clipboard_text = None
            self._send_start_index = max(1, int(start_index))
            # grid 为 None 表示沿用当前设置 (剪贴板热切换): 复用上次的宫格数。
            if grid is not None:
                self._send_grid = max(1, int(grid))
            self._send_stop.clear()
            self.open_overlay()
            self._send_thread = threading.Thread(
                target=self._build_and_send,
                args=(src, err, self._send_grid, self._send_start_index),
                daemon=True,
            )
            self._send_thread.start()
        return {"ok": True, "grid": self._send_grid}

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
        with self._send_lock:
            self._send_stop.set()
            self._clipboard_monitor_enabled = False
            self._stop_clipboard_watch()
            _set_overlay_on_top(False)
            _overlay_js("onOverlayPaused('已暂停广播')")
            old = self._send_thread
            if old and old.is_alive() and old is not threading.current_thread():
                old.join()
            return {"ok": True}

    def resume_send(self, start_index, resend_spec=None):
        with self._send_lock:
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
                count = getattr(self.sender, "resend_count", 0)
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
            if not self._publish_regular_frame(dataurl, status):
                break
            # 补发模式持续循环不自动停; 默认广播达到 max(5遍, 30s) 后自动暂停,
            # 仅结束循环线程, 保留 Sender 与 _pos, 点“继续广播”从暂停处续播。
            if not s.is_resending and s.total:
                cycles = (s.sent_frames - base_frames) / s.total
                elapsed = time.monotonic() - start
                if cycles >= self.AUTO_STOP_CYCLES and elapsed >= self.AUTO_STOP_SECONDS:
                    self._send_stop.set()
                    _set_overlay_on_top(False)
                    _overlay_js("onOverlayPaused('已自动暂停广播')")
                    _js(f"onSendAutoStopped({int(cycles)})")
                    break
            time.sleep(1.0 / max(1, self.fps))

    def _publish_regular_frame(self, dataurl, status):
        while not self._send_stop.is_set():
            if not self._qr_output_lock.acquire(timeout=0.1):
                continue
            try:
                publish_send_frame(dataurl, status)
                return True
            finally:
                self._qr_output_lock.release()
        return False

    def _start_clipboard_watch(self):
        self._clipboard_watcher.start()

    def _stop_clipboard_watch(self):
        self._clipboard_watcher.stop()

    def _on_clipboard_text(self, text):
        if is_clipboard_message(text):
            return
        if not self._clipboard_monitor_enabled or self.sender is None:
            return
        if text == self._active_text:
            return
        if self._send_is_file:
            self._pending_clipboard_seq += 1
            self._pending_clipboard_text = text
            _js(f"onClipboardNeedsConfirm({_js_str(_preview_text(text))}, "
                f"{self._pending_clipboard_seq})")
            return
        self._start_clipboard_text_send(text)

    def _start_clipboard_text_send(self, text):
        with self._clipboard_switch_lock:
            self._queued_clipboard_text = text
            worker = self._clipboard_switch_thread
            if worker and worker.is_alive():
                return {"ok": True, "queued": True}
            worker = threading.Thread(
                target=self._run_clipboard_text_send, daemon=True)
            self._clipboard_switch_thread = worker
            worker.start()
        return {"ok": True, "queued": True}

    def _run_clipboard_text_send(self):
        while True:
            with self._clipboard_switch_lock:
                text = self._queued_clipboard_text
                self._queued_clipboard_text = None
            try:
                result = self._replace_send_source(
                    ("text", text), self._send_error_level, self.fps,
                    from_clipboard=True)
                if result and result.get("ok"):
                    _js("onClipboardSendStarted()")
            except Exception as exc:
                _js(f"onSendError({_js_str(f'剪贴板切换失败: {exc}')})")
            with self._clipboard_switch_lock:
                if self._queued_clipboard_text is None:
                    self._clipboard_switch_thread = None
                    return

    def confirm_clipboard_send(self, seq=None):
        if seq is not None and int(seq) != self._pending_clipboard_seq:
            return {"ok": True, "stale": True}
        text = self._pending_clipboard_text
        self._pending_clipboard_text = None
        if not text:
            return {"ok": False, "error": "没有待确认的剪贴板文本"}
        return self._start_clipboard_text_send(text)

    def discard_clipboard_send(self, seq=None):
        if seq is not None and int(seq) != self._pending_clipboard_seq:
            return {"ok": True, "stale": True}
        self._pending_clipboard_text = None
        return {"ok": True}

    def _write_clipboard(self, text):
        self._clipboard_watcher.ignore_text(text)
        set_clipboard(text)

    def _tunnel_uses_clipboard(self):
        for tunnel in (self.git_host, self.git_cloud):
            try:
                if tunnel and tunnel.status().get("running"):
                    return True
            except Exception:
                continue
        return False

    def _set_clipboard(self, text):
        if self._tunnel_uses_clipboard():
            return False
        self._write_clipboard(text)
        return True

    def _set_tunnel_clipboard(self, text):
        self._write_clipboard(text)

    def open_overlay(self):
        global _overlay_window, _overlay_minimized
        if _overlay_window is not None:
            try:
                _overlay_window.show()
                _overlay_minimized = False
                _set_overlay_on_top(True)
                return {"ok": True}
            except Exception:
                _overlay_window = None
                _overlay_minimized = False
        _overlay_minimized = False
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
            def on_minimized():
                global _overlay_minimized
                _overlay_minimized = True

            def on_restored():
                global _overlay_minimized
                _overlay_minimized = False

            def on_closing():
                global _overlay_window, _overlay_minimized
                _overlay_window = None
                _overlay_minimized = False
                return True
            _overlay_window.events.minimized += on_minimized
            _overlay_window.events.restored += on_restored
            _overlay_window.events.closing += on_closing
        except Exception:
            pass
        return {"ok": True}

    def close_overlay(self):
        global _overlay_window, _overlay_minimized
        overlay = _overlay_window
        _overlay_window = None
        _overlay_minimized = False
        if overlay is not None:
            try:
                overlay.destroy()
            except Exception:
                pass
        return {"ok": True}

    def list_windows(self):
        return wincap.list_windows()

    def _set_role_hwnd(self, role, hwnd):
        if role == "recv":
            self.hwnd = hwnd
        elif role == "git_host":
            self._git_host_hwnd = hwnd
        else:
            raise ValueError("窗口角色无效")

    def _role_hwnd(self, role):
        if role == "recv":
            return self.hwnd
        if role == "git_host":
            return self._git_host_hwnd
        raise ValueError("窗口角色无效")

    def set_window(self, hwnd, role="recv"):
        hwnd = int(hwnd)
        windows = wincap.list_windows()
        selected = next((item for item in windows if int(item["hwnd"]) == hwnd), None)
        self._set_role_hwnd(role, hwnd)
        if selected:
            self._window_targets[role] = wincap.window_target(selected)
        return {"ok": True, "hwnd": hwnd,
                "target": self._window_targets.get(role)}

    def restore_window_target(self, role, target):
        self._set_role_hwnd(role, None)
        self._window_targets[role] = wincap.window_target(target or {})
        hwnd = self._resolve_target_hwnd(role, force=True)
        return {"ok": bool(hwnd), "hwnd": hwnd or 0}

    def _resolve_target_hwnd(self, role, force=False):
        current = self._role_hwnd(role)
        target = self._window_targets.get(role)
        if current and (wincap.window_exists(current) or not target):
            return current
        self._set_role_hwnd(role, None)
        if not target:
            return None
        now = time.monotonic()
        if not force and now < self._window_reconnect_after[role]:
            return None
        self._window_reconnect_after[role] = now + 1.0
        match = wincap.resolve_window_target(target, wincap.list_windows())
        if match:
            self._set_role_hwnd(role, int(match["hwnd"]))
        return self._role_hwnd(role)

    def start_recv(self):
        if not self._resolve_target_hwnd("recv", force=True):
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

    # 捕获异常时错误信息最多每 2 秒推送一次前端, 避免每轮 (0.06s) 刷 DOM。
    RECV_ERROR_THROTTLE = 2.0

    def _recv_loop(self):
        last_error_at = float("-inf")
        while not self._recv_stop.is_set():
            try:
                hwnd = self._resolve_target_hwnd("recv")
                if not hwnd:
                    raise RuntimeError("等待锁定窗口重新打开")
                img = wincap.grab_window(hwnd)
                if img is None:
                    self.hwnd = None
                    raise RuntimeError("等待锁定窗口重新打开")
                self.receiver.feed(img)
                last_error_at = float("-inf")  # 恢复正常 -> 下次异常立即提示
            except Exception as e:
                now = time.monotonic()
                if now - last_error_at >= self.RECV_ERROR_THROTTLE:
                    last_error_at = now
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
            # 隧道运行时只展示消息，避免自动写入覆盖其剪贴板控制通道。
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

    # --- Git Smart HTTP 隧道 ---
    def git_tunnel_start_host(self, hwnd=None):
        if hwnd:
            self.set_window(hwnd, "git_host")
            target_hwnd = self._git_host_hwnd
        else:
            target_hwnd = (self._git_host_hwnd
                           or self._resolve_target_hwnd("git_host", force=True))
        if not target_hwnd and self.hwnd:
            target_hwnd = self.hwnd
        if not target_hwnd:
            return {"error": "请先选择云桌面窗口"}
        if self.git_host and self.git_host.status().get("running"):
            return {"ok": True, "status": self.git_host.status()}
        self.git_host = HostTunnel(
            frame_reader=self._git_tunnel_read_frames,
            clipboard_writer=self._set_tunnel_clipboard,
            focus_window=self._focus_git_window,
            status=self._git_tunnel_status,
        )
        try:
            self.git_host.start()
            return {"ok": True, "status": self.git_host.status()}
        except Exception as e:
            self.git_host = None
            return {"error": f"启动宿主机代理失败: {e}"}

    def git_tunnel_stop_host(self):
        if self.git_host:
            self.git_host.stop()
            self.git_host = None
        return {"ok": True}

    def git_tunnel_start_cloud(self, base_url):
        if self.git_cloud and self.git_cloud.status().get("running"):
            return {"ok": True, "status": self.git_cloud.status()}
        try:
            self.git_cloud = CloudTunnel(
                base_url,
                clipboard_reader=read_clipboard_text,
                page_player=self._git_tunnel_play_pages,
                status=self._git_tunnel_status,
            )
            self.git_cloud.start()
            return {"ok": True, "status": self.git_cloud.status()}
        except Exception as e:
            self.git_cloud = None
            return {"error": f"Git 基地址无效或启动失败: {e}"}

    def git_tunnel_stop_cloud(self):
        if self.git_cloud:
            self.git_cloud.stop()
            if self.git_cloud.status().get("running"):
                return {"ok": False, "error": "云端转发正在结束，请稍后再试"}
            self.git_cloud = None
        return {"ok": True}

    def git_tunnel_status(self):
        return {
            "host": self.git_host.status() if self.git_host else {"running": False},
            "cloud": self.git_cloud.status() if self.git_cloud else {"running": False},
        }

    def _git_tunnel_read_frames(self):
        hwnd = self._git_host_hwnd
        if not hwnd:
            hwnd = self._resolve_target_hwnd("git_host")
        if not hwnd:
            return []
        img = wincap.grab_window(hwnd)
        if img is None:
            self._git_host_hwnd = None
            return []
        return P.decode_qr_all(img)

    def _focus_git_window(self):
        hwnd = (self._git_host_hwnd
                or self._resolve_target_hwnd("git_host", force=True))
        return bool(hwnd and wincap.focus_window(hwnd))

    def _git_tunnel_status(self, message):
        try:
            _js(f"onGitTunnelStatus({_js_str(str(message))})")
        except Exception:
            pass

    def _git_tunnel_play_pages(self, pages, status, req_id, is_cancelled=None):
        is_cancelled = is_cancelled or (lambda: False)
        if is_cancelled() or not pages:
            return
        self.open_overlay()
        with self._qr_output_lock:
            self._play_git_qr_pages(pages, status, req_id, is_cancelled)

    def _play_git_qr_pages(self, pages, status, req_id, is_cancelled):
        loops = 6 if len(pages) == 1 else 5
        interval = GIT_QR_SINGLE_SECONDS if len(pages) == 1 else GIT_QR_FRAME_SECONDS
        encoder = lambda page: P.encode_qr_png(page, error="l", scale=5, border=3)
        with GitQrPageCache(encoder) as cache:
            dataurl = None
            for cycle in range(loops):
                for offset, page in enumerate(pages):
                    if is_cancelled():
                        return
                    signal = read_clipboard_text() or ""
                    if signal in (f"ASGT1:DONE:{req_id}", f"ASGT1:CANCEL:{req_id}"):
                        return
                    if dataurl is None:
                        dataurl = cache.get(offset, page)
                    started = time.monotonic()
                    publish_send_frame(dataurl, f"{status} · {offset + 1}/{len(pages)}")
                    has_next = cycle < loops - 1 or offset < len(pages) - 1
                    if not has_next:
                        continue
                    next_index = (offset + 1) % len(pages)
                    dataurl = cache.get(next_index, pages[next_index])
                    time.sleep(max(0, interval - (time.monotonic() - started)))

    def copy_text(self, text):
        """逐条复制: 前端点消息旁的复制图标, 把该条文本重新写入剪贴板。"""
        try:
            if not self._set_clipboard(text):
                return {"ok": False, "error": "Git 隧道运行中，剪贴板已受保护"}
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
        APP_TITLE,
        url=_resource_path("ui.html"),
        js_api=api,
        width=566,
        height=1174,
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
