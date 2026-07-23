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
from .sender import Sender, load_file, load_text
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


# 关键: window 存成模块级变量, 绝不挂到 Api 实例属性上。
# pywebview 会递归遍历 js_api 对象的所有属性来暴露方法, 若 Api 持有 window
# (内部是 .NET 对象), 遍历时会触发 Rectangle.op_Equality 比较 -> pythonnet
# 无限递归, 主线程 CPU 打满, 窗口"未响应"。模块级变量不会被 introspect, 版本无关。
_window = None


def _js(code: str):
    if _window is not None:
        _window.evaluate_js(code)


class Api:
    def __init__(self):
        # 发送态
        self.sender = None
        self._send_stop = threading.Event()
        self._send_thread = None
        self.fps = 8
        self._picked_file = None
        self._send_start_index = 1
        # 接收态 (仅窗口捕获)
        self.receiver = None
        self.hwnd = None          # 锁定的目标窗口句柄
        self._recv_stop = threading.Event()
        self._recv_thread = None
        self._messages = []       # 已接收的文本消息 (供逐条复制)

    # ---------------- 发送 ----------------
    def pick_file(self):
        res = _window.create_file_dialog(webview.OPEN_DIALOG)
        if not res:
            return None
        self._picked_file = res[0]
        return os.path.basename(self._picked_file)

    def clear_file(self):
        """删除已选附件。"""
        self._picked_file = None
        return {"ok": True}

    def start_send(self, text, grid, err, fps, start_index=1):
        # 只做轻量校验后立即返回, 真正的构建 (读文件/切片) 放后台线程,
        # 避免大文件在主线程阻塞导致 UI "未响应"。
        if self._picked_file:
            src = ("file", self._picked_file)
        elif text and text.strip():
            src = ("text", text)
        else:
            return {"error": "请先输入文本或选择文件"}
        # 需求3: 广播中再次点"发送"= 热切换。先停掉正在跑的广播线程并等它退出,
        # 再用新内容重启, 避免两个 _send_loop 同时往 UI 推帧。
        self._send_stop.set()
        old = self._send_thread
        if old and old.is_alive() and old is not threading.current_thread():
            old.join()
        self.sender = None
        self.fps = max(1, int(fps))
        self._send_start_index = max(1, int(start_index))
        self._send_stop.clear()
        self._send_thread = threading.Thread(
            target=self._build_and_send,
            args=(src, err, int(grid), int(start_index)),
            daemon=True,
        )
        self._send_thread.start()
        return {"ok": True}

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
            return
        _js(f"onSendReady({self.sender.total}, {self.sender.start_index})")
        self._send_loop()

    def set_fps(self, fps):
        self.fps = max(1, int(fps))

    def pause_send(self):
        self._send_stop.set()
        old = self._send_thread
        if old and old.is_alive() and old is not threading.current_thread():
            old.join()
        return {"ok": True}

    def resume_send(self, start_index):
        if not self.sender:
            return {"error": "当前没有可继续的广播任务"}
        requested = max(1, int(start_index))
        if requested != self._send_start_index:
            self._send_start_index = self.sender.seek(requested)
        if self._send_thread and self._send_thread.is_alive():
            return {"ok": True, "start_index": self._send_start_index}
        self._send_stop.clear()
        self._send_thread = threading.Thread(target=self._send_loop, daemon=True)
        self._send_thread.start()
        return {"ok": True, "start_index": self._send_start_index}

    def stop_send(self):
        """兼容旧前端：停止语义降级为暂停，不销毁 Sender。"""
        return self.pause_send()

    def _send_loop(self):
        while not self._send_stop.is_set() and self.sender:
            s = self.sender
            img = s.next_image()
            dataurl = _img_to_dataurl(img)
            status = f"[{s.name}] {s.status()} · 已广播 {s.sent_frames} 帧"
            _js(f"pushQR({_js_str(dataurl)}, {_js_str(status)})")
            time.sleep(1.0 / max(1, self.fps))

    # ---------------- 接收 (仅窗口捕获) ----------------
    def list_windows(self):
        """返回可选窗口列表, 供前端下拉选择。"""
        return wincap.list_windows()

    def set_window(self, hwnd):
        """锁定某个窗口做后台捕获。"""
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
                set_clipboard(content)
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
                _js(f"onComplete(true, false, {_js_str(info)})")
            except Exception as e:
                info = f"保存失败: {e} · 临时文件已保留"
                _js(f"onComplete(false, false, {_js_str(info)})")

    def copy_text(self, text):
        """逐条复制: 前端点消息旁的复制图标, 把该条文本重新写入剪贴板。"""
        try:
            set_clipboard(text)
            return {"ok": True}
        except Exception:
            return {"ok": False}


def main():
    # 设为 per-monitor DPI-aware: 否则高分屏下系统会虚拟化坐标 (如 2560 缩成 1920),
    # 导致 ImageGrab 截图与 GetWindowRect 窗口坐标错位, 框选/选窗点不准。
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
    webview.start()


if __name__ == "__main__":
    main()
