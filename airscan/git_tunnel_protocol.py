"""Git Smart HTTP 隧道的轻量消息协议。"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import struct
import uuid
from dataclasses import dataclass

from . import protocol as P

CLIPBOARD_PREFIX = "ASGT1:"
REQ_PREFIX = CLIPBOARD_PREFIX + "REQ:"
ACK_PREFIX = b"ASGT1:ACK:"
META_PREFIX = b"ASGT1:META:"
DATA_PREFIX = b"ASGT1:DATA:"
VERSION = 1
ALLOWED_METHODS = {"GET", "POST"}


def is_clipboard_message(text) -> bool:
    """判断文本是否属于 Git 隧道的剪贴板控制通道。"""
    return isinstance(text, str) and text.startswith(CLIPBOARD_PREFIX)


class ProtocolError(ValueError):
    """消息格式不符合 Git tunnel 协议。"""


class IntegrityError(ProtocolError):
    """响应页齐全，但内容校验失败。"""


@dataclass(frozen=True)
class GitTunnelRequest:
    id: str
    attempt: int
    method: str
    path: str
    headers: list
    body: bytes | None = None


@dataclass(frozen=True)
class GitTunnelResponse:
    status: int
    headers: list
    body: bytes


def _b64_encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64_decode(text: str) -> bytes:
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except Exception as e:
        raise ProtocolError("Base64 无效") from e


def _validate_id(req_id: str) -> str:
    try:
        return str(uuid.UUID(str(req_id)))
    except Exception as e:
        raise ProtocolError("请求 ID 无效") from e


def _validate_headers(headers) -> list:
    if not isinstance(headers, list):
        raise ProtocolError("headers 必须是列表")
    out = []
    for item in headers:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ProtocolError("header 必须是键值对")
        k, v = item
        if not isinstance(k, str) or not isinstance(v, str):
            raise ProtocolError("header 键值必须是字符串")
        out.append([k, v])
    return out


def _validate_request(req: GitTunnelRequest) -> GitTunnelRequest:
    req_id = _validate_id(req.id)
    method = str(req.method).upper()
    if method not in ALLOWED_METHODS:
        raise ProtocolError("仅支持 GET/POST")
    if not isinstance(req.path, str) or not req.path.startswith("/"):
        raise ProtocolError("path 必须以 / 开头")
    attempt = int(req.attempt)
    if attempt < 1:
        raise ProtocolError("attempt 必须为正数")
    body = req.body
    if body is not None and not isinstance(body, bytes):
        raise ProtocolError("body 必须是 bytes 或 None")
    return GitTunnelRequest(req_id, attempt, method, req.path, _validate_headers(req.headers), body)


def encode_request(req: GitTunnelRequest) -> str:
    payload = {
        "version": VERSION,
        "id": req.id,
        "attempt": req.attempt,
        "method": req.method,
        "path": req.path,
        "headers": req.headers,
        "body": _b64_encode(req.body) if req.body is not None else None,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return REQ_PREFIX + _b64_encode(raw)


def decode_request(text: str) -> GitTunnelRequest:
    if not isinstance(text, str) or not text.startswith(REQ_PREFIX):
        raise ProtocolError("不是 Git tunnel 请求")
    try:
        obj = json.loads(_b64_decode(text[len(REQ_PREFIX):]).decode("utf-8"))
    except ProtocolError:
        raise
    except Exception as e:
        raise ProtocolError("请求 JSON 无效") from e
    if obj.get("version") != VERSION:
        raise ProtocolError("协议版本无效")
    body = obj.get("body")
    req = GitTunnelRequest(
        id=obj.get("id"),
        attempt=obj.get("attempt", 1),
        method=obj.get("method", "GET"),
        path=obj.get("path", "/"),
        headers=obj.get("headers", []),
        body=_b64_decode(body) if body is not None else None,
    )
    return _validate_request(req)


def make_ack(req_id: str) -> bytes:
    return ACK_PREFIX + _validate_id(req_id).encode("ascii")


def parse_ack(frame: bytes) -> str | None:
    if not isinstance(frame, (bytes, bytearray)) or not frame.startswith(ACK_PREFIX):
        return None
    try:
        return _validate_id(bytes(frame[len(ACK_PREFIX):]).decode("ascii"))
    except ProtocolError:
        return None


def response_chunk_size(error: str = "l") -> int:
    overhead = len(DATA_PREFIX) + 36 + 1 + 4
    return max(1, P.CAPACITY.get(error.lower(), P.CAPACITY["l"]) - overhead)


def encode_response(req_id: str, status: int, headers: list, body: bytes,
                    chunk_size: int | None = None) -> list[bytes]:
    req_id = _validate_id(req_id)
    if not isinstance(body, bytes):
        raise ProtocolError("响应 body 必须是 bytes")
    chunk_size = int(chunk_size or response_chunk_size("l"))
    compressed = gzip.compress(body)
    use_gzip = len(compressed) < len(body) * 0.95
    wire = compressed if use_gzip else body
    chunks = [wire[i:i + chunk_size] for i in range(0, len(wire), chunk_size)] or [b""]
    meta = {
        "version": VERSION,
        "id": req_id,
        "status": int(status),
        "headers": _validate_headers(headers),
        "chunks": len(chunks),
        "gzip": use_gzip,
        "raw_len": len(body),
        "wire_len": len(wire),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    pages = [META_PREFIX + json.dumps(meta, separators=(",", ":")).encode("utf-8")]
    pages.extend(DATA_PREFIX + req_id.encode("ascii") + b":" + struct.pack(">I", i) + c
                 for i, c in enumerate(chunks))
    return pages


class ResponseCollector:
    def __init__(self, req_id: str):
        self.req_id = _validate_id(req_id)
        self.meta = None
        self.chunks = {}
        self.response = None

    @property
    def complete(self) -> bool:
        return self.response is not None

    def add_page(self, frame: bytes) -> bool:
        if frame.startswith(META_PREFIX):
            self.meta = self._parse_meta(frame[len(META_PREFIX):])
        elif frame.startswith(DATA_PREFIX):
            seq, chunk = self._parse_data(frame[len(DATA_PREFIX):])
            self.chunks.setdefault(seq, chunk)
        else:
            return False
        self._finish_if_ready()
        return True

    def _parse_meta(self, raw: bytes) -> dict:
        try:
            meta = json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise ProtocolError("响应 meta 无效") from e
        if meta.get("version") != VERSION or _validate_id(meta.get("id")) != self.req_id:
            raise ProtocolError("响应 meta 请求 ID 无效")
        meta["headers"] = _validate_headers(meta.get("headers", []))
        return meta

    def _parse_data(self, raw: bytes) -> tuple[int, bytes]:
        try:
            rid, rest = raw.split(b":", 1)
        except ValueError as e:
            raise ProtocolError("响应 data 页无效") from e
        if _validate_id(rid.decode("ascii")) != self.req_id or len(rest) < 4:
            raise ProtocolError("响应 data 请求 ID 无效")
        return struct.unpack(">I", rest[:4])[0], rest[4:]

    def _finish_if_ready(self):
        if self.response is not None or not self.meta:
            return
        total = int(self.meta.get("chunks", 0))
        if total <= 0 or any(i not in self.chunks for i in range(total)):
            return
        wire = b"".join(self.chunks[i] for i in range(total))
        body = gzip.decompress(wire) if self.meta.get("gzip") else wire
        if hashlib.sha256(body).hexdigest() != self.meta.get("sha256"):
            raise IntegrityError("响应 SHA-256 校验失败")
        self.response = GitTunnelResponse(int(self.meta["status"]), self.meta["headers"], body)
