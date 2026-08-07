"""Git Smart HTTP 隧道服务。"""
from __future__ import annotations

import http.client
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .git_tunnel_protocol import (
    GitTunnelRequest,
    GitTunnelResponse,
    IntegrityError,
    ProtocolError,
    ResponseCollector,
    decode_request,
    encode_request,
    encode_response,
    make_ack,
    parse_ack,
    response_chunk_size,
)

REQUEST_BODY_LIMIT = 16 * 1024 * 1024
RESPONSE_BODY_LIMIT = 16 * 1024 * 1024
_RESPONSE_CHUNK_SIZE = response_chunk_size("l")
MAX_RESPONSE_PAGES = 1 + (RESPONSE_BODY_LIMIT + _RESPONSE_CHUNK_SIZE - 1) // _RESPONSE_CHUNK_SIZE
ACK_WAIT_SECONDS = 3.0
MAX_ACK_ATTEMPTS = 4
ROLLING_TIMEOUT_SECONDS = 60.0
# 排队等待上限：超过则直接抛弃该请求，避免堆积时串行累加导致无响应
QUEUE_WAIT_SECONDS = 12.0
# http.client 连接阶段超时（响应整体上限由 ROLLING_TIMEOUT_SECONDS 兜底）
CONNECT_TIMEOUT_SECONDS = 15.0
CAPTURE_INTERVAL_SECONDS = 0.1
# 抓帧循环的目标节拍: 抓一次云桌面窗口并解码可能耗 0.3~0.8 秒,
# 若只固定 sleep 0.1 秒, 循环占用率接近 100%, 会把界面拖成无响应。
CAPTURE_CYCLE_SECONDS = 0.3
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class GitTunnelServiceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.body = message.encode("utf-8")


def sanitize_target_base(value: str) -> tuple[str, int]:
    parsed = urlsplit((value or "").strip())
    if parsed.scheme != "http" or not parsed.hostname or parsed.port is None:
        raise ValueError("Git 基地址必须是 http://host:port")
    if parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Git 基地址不能包含账号、路径、查询或片段")
    return parsed.hostname, int(parsed.port)


def _header_pairs(headers) -> list[list[str]]:
    return [[str(k), str(v)] for k, v in headers]


def filter_request_headers(headers) -> list[list[str]]:
    blocked = HOP_BY_HOP | {"host", "content-length"}
    return [[k, v] for k, v in _header_pairs(headers) if k.lower() not in blocked]


def filter_response_headers(headers) -> list[list[str]]:
    blocked = HOP_BY_HOP | {"content-length"}
    return [[k, v] for k, v in _header_pairs(headers) if k.lower() not in blocked]


def _read_limited_response(resp, limit: int) -> bytes:
    body = resp.read(limit + 1)
    if len(body) > limit:
        limit_mib = limit // (1024 * 1024)
        raise GitTunnelServiceError(507, f"Git 响应超过 {limit_mib} MiB 上限")
    return body


def forward_http_request(req: GitTunnelRequest, target: tuple[str, int],
                         max_body: int = RESPONSE_BODY_LIMIT) -> GitTunnelResponse:
    if req.method not in ("GET", "POST"):
        raise GitTunnelServiceError(405, "Git 隧道仅支持 GET/POST")
    if req.body is not None and len(req.body) > REQUEST_BODY_LIMIT:
        raise GitTunnelServiceError(413, "Git 请求体过大，超过 Git 隧道上限")
    headers = {k: v for k, v in filter_request_headers(req.headers)}
    conn = http.client.HTTPConnection(target[0], target[1], timeout=CONNECT_TIMEOUT_SECONDS)
    try:
        conn.request(req.method, req.path, body=req.body, headers=headers)
        resp = conn.getresponse()
        body = _read_limited_response(resp, max_body)
        return GitTunnelResponse(resp.status, filter_response_headers(resp.getheaders()), body)
    finally:
        conn.close()


def _error_response(err: GitTunnelServiceError) -> GitTunnelResponse:
    return GitTunnelResponse(err.status, [["Content-Type", "text/plain; charset=utf-8"]], err.body)


class _LoopbackServer(ThreadingHTTPServer):
    allow_reuse_address = True


class HostTunnel:
    def __init__(self, frame_reader, clipboard_writer, focus_window=None,
                 listen=("127.0.0.1", 9999), status=None):
        self.frame_reader = frame_reader
        self.clipboard_writer = clipboard_writer
        self.focus_window = focus_window or (lambda: None)
        self.listen = listen
        self.status_callback = status or (lambda _msg: None)
        self._server = None
        self._threads = []
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._request_lock = threading.Lock()
        self._collector = None
        self._acked = set()
        self._last_progress = 0.0

    def start(self):
        if self._server:
            return
        self._stop.clear()
        self._server = _LoopbackServer(self.listen, self._make_handler())
        self._threads = [
            threading.Thread(target=self._server.serve_forever, daemon=True),
            threading.Thread(target=self._capture_loop, daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        self.status_callback("Git 隧道宿主机代理已启动")

    def stop(self):
        self._stop.set()
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        with self._cond:
            self._cond.notify_all()
        self.status_callback("Git 隧道宿主机代理已停止")

    def status(self):
        return {"running": self._server is not None, "listen": f"{self.listen[0]}:{self.listen[1]}"}

    def _make_handler(self):
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parent._handle_http(self)

            def do_POST(self):
                parent._handle_http(self)

            def log_message(self, *_):
                pass

        return Handler

    def _handle_http(self, handler):
        try:
            length = int(handler.headers.get("Content-Length", 0))
            if length > REQUEST_BODY_LIMIT:
                raise GitTunnelServiceError(413, "Git 请求体过大，超过 Git 隧道上限")
            body = handler.rfile.read(length) if length else None
            req = GitTunnelRequest(str(uuid.uuid4()), 1, handler.command, handler.path,
                                   filter_request_headers(handler.headers.items()), body)
            resp = self._send_over_qr(req)
        except GitTunnelServiceError as err:
            resp = _error_response(err)
        self._write_http_response(handler, resp)

    def _send_over_qr(self, req: GitTunnelRequest) -> GitTunnelResponse:
        # 排队超时抛弃：QR 通道单工，必须串行；但堆积时新请求等待超过上限直接 503，
        # 不再无限挂起，避免 N 个卡死请求的串行累加导致整体无响应。
        if not self._request_lock.acquire(timeout=QUEUE_WAIT_SECONDS):
            raise GitTunnelServiceError(503, "Git 隧道繁忙，请求已丢弃")
        try:
            with self._cond:
                collector = ResponseCollector(req.id)
                self._collector = collector
                self._last_progress = time.monotonic()
                self._cond.notify_all()
            try:
                self._wait_ack(req)
                return self._wait_response(req.id)
            finally:
                with self._cond:
                    if self._collector is collector:
                        self._collector = None
                    self._cond.notify_all()
        finally:
            self._request_lock.release()

    def _wait_ack(self, req):
        for attempt in range(1, MAX_ACK_ATTEMPTS + 1):
            if self._stop.is_set():
                raise GitTunnelServiceError(499, "Git 隧道已停止")
            msg = encode_request(GitTunnelRequest(req.id, attempt, req.method, req.path, req.headers, req.body))
            self.clipboard_writer(msg)
            deadline = time.monotonic() + ACK_WAIT_SECONDS
            with self._cond:
                while time.monotonic() < deadline:
                    if self._stop.is_set():
                        raise GitTunnelServiceError(499, "Git 隧道已停止")
                    if req.id in self._acked or self._collector_has_pages():
                        return
                    self._cond.wait(0.1)
        raise GitTunnelServiceError(502, "云桌面 Git 隧道未确认请求")

    def _wait_response(self, req_id):
        with self._cond:
            while not self._stop.is_set():
                if self._collector and self._collector.complete:
                    self.clipboard_writer(f"ASGT1:DONE:{req_id}")
                    response = self._collector.response
                    if response.status >= 400:
                        detail = " ".join(response.body.decode("utf-8", "replace").split())[:200]
                        self.status_callback(f"Git {response.status}: {detail or '请求失败'}")
                    return response
                if time.monotonic() - self._last_progress > ROLLING_TIMEOUT_SECONDS:
                    raise GitTunnelServiceError(504, "等待 Git 响应超时")
                self._cond.wait(0.5)
        self.clipboard_writer(f"ASGT1:CANCEL:{req_id}")
        raise GitTunnelServiceError(499, "Git 隧道已停止")

    def _capture_loop(self):
        while not self._stop.is_set():
            with self._cond:
                while (not self._stop.is_set()
                       and not self._capture_pending()):
                    self._cond.wait()
            if self._stop.is_set():
                return
            started = time.monotonic()
            try:
                for frame in self.frame_reader() or []:
                    self._handle_frame(frame)
            except Exception:
                pass
            # 按实际耗时补齐节拍: 抓帧+解码可能耗数百毫秒, 必须留出空闲,
            # 否则本进程 CPU 被打满, WebView UI 抢不到时间片而无响应。
            self._stop.wait(max(CAPTURE_INTERVAL_SECONDS,
                                CAPTURE_CYCLE_SECONDS - (time.monotonic() - started)))

    def _capture_pending(self):
        return bool(self._collector and not self._collector.complete)

    def _handle_frame(self, frame):
        ack = parse_ack(frame)
        with self._cond:
            if ack:
                self._acked.add(ack)
                self._cond.notify_all()
                return
            if not self._collector:
                return
            try:
                if self._collector.add_page(frame):
                    self._last_progress = time.monotonic()
                    self._cond.notify_all()
            except (ProtocolError, IntegrityError):
                pass

    def _collector_has_pages(self):
        return bool(self._collector and (self._collector.meta or self._collector.chunks))

    def _write_http_response(self, handler, resp: GitTunnelResponse):
        handler.send_response(resp.status)
        for key, value in filter_response_headers(resp.headers):
            handler.send_header(key, value)
        handler.send_header("Content-Length", str(len(resp.body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        if resp.body:
            handler.wfile.write(resp.body)


class CloudTunnel:
    def __init__(self, target_base, clipboard_reader, page_player, status=None):
        self.target = sanitize_target_base(target_base)
        self.clipboard_reader = clipboard_reader
        self.page_player = page_player
        self.status_callback = status or (lambda _msg: None)
        self._stop = threading.Event()
        self._thread = None
        self._seen = set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.status_callback("Git 隧道云桌面转发已启动")

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
            if not self._thread.is_alive():
                self._thread = None
        self.status_callback("Git 隧道云桌面转发已停止")

    def status(self):
        return {"running": bool(self._thread and self._thread.is_alive()),
                "target": f"http://{self.target[0]}:{self.target[1]}",
                "max_pages": MAX_RESPONSE_PAGES,
                "response_limit_mib": RESPONSE_BODY_LIMIT // (1024 * 1024)}

    def _loop(self):
        last_text = None
        while not self._stop.is_set():
            try:
                text = self.clipboard_reader() or ""
                if text and text != last_text and text.startswith("ASGT1:REQ:"):
                    self._handle_clipboard(text)
                    last_text = text
            except Exception as e:
                self._safe_status(f"Git 隧道云端错误: {e}")
            time.sleep(0.2)

    def _handle_clipboard(self, text):
        try:
            req = decode_request(text)
            if req.id in self._seen:
                return
            self.page_player(
                [make_ack(req.id)], f"Git ACK {req.path}", req.id, self._stop.is_set
            )
            if self._stop.is_set():
                return
            try:
                resp = forward_http_request(req, self.target)
            except GitTunnelServiceError as err:
                resp = _error_response(err)
            if self._stop.is_set():
                return
            pages = encode_response(req.id, resp.status, resp.headers, resp.body)
            if len(pages) > MAX_RESPONSE_PAGES:
                message = f"Git 响应需要 {len(pages)} 页，超过 {MAX_RESPONSE_PAGES} 页上限"
                resp = _error_response(GitTunnelServiceError(507, message))
                pages = encode_response(req.id, resp.status, resp.headers, resp.body)
            self.page_player(
                pages, f"Git {resp.status} {req.path}", req.id, self._stop.is_set
            )
            self._seen.add(req.id)
        except Exception as e:
            self._safe_status(f"Git 隧道请求无效: {e}")

    def _safe_status(self, message):
        try:
            self.status_callback(message)
        except Exception:
            pass
