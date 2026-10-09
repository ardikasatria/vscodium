"""Klien WebSocket minimum untuk kanal kernel Jupyter.

Hanya yang dibutuhkan ``execute_request`` / ``execute_reply`` / IOPub.
Bukan klien WebSocket umum.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import struct
from typing import Any, Mapping
from urllib.parse import urlencode, urlparse, urlunparse

from .errors import JupyterProtocolError, JupyterTimeoutError
from .models import ConnectionInfo


class KernelChannel:
    """Satu koneksi WebSocket ke ``/api/kernels/{id}/channels``."""

    def __init__(
        self,
        connection: ConnectionInfo,
        kernel_id: str,
        *,
        session_id: str | None = None,
        timeout: float = 60.0,
    ):
        self.connection = connection
        self.kernel_id = kernel_id
        self.session_id = session_id or os.urandom(8).hex()
        self.timeout = timeout
        self._sock: socket.socket | ssl.SSLSocket | None = None

    def __enter__(self) -> "KernelChannel":
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        parsed = urlparse(self.connection.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = urlencode({"session_id": self.session_id})
        path = f"/api/kernels/{self.kernel_id}/channels?{query}"
        ws_url = urlunparse((scheme, parsed.netloc, path, "", "", ""))
        self._sock = _handshake(
            ws_url,
            extra_headers={
                "Authorization": f"token {self.connection.token}",
            },
            timeout=self.timeout,
        )

    def send_json(self, message: Mapping[str, Any]) -> None:
        if self._sock is None:
            raise JupyterProtocolError("kanal kernel belum terhubung")
        _send_text(self._sock, json.dumps(message, ensure_ascii=False))

    def recv_json(self, *, timeout: float | None = None) -> dict[str, Any]:
        if self._sock is None:
            raise JupyterProtocolError("kanal kernel belum terhubung")
        self._sock.settimeout(self.timeout if timeout is None else timeout)
        try:
            teks = _recv_text(self._sock)
        except socket.timeout as exc:
            raise JupyterTimeoutError(
                "Menunggu pesan kernel melebihi batas waktu."
            ) from exc
        try:
            data = json.loads(teks)
        except json.JSONDecodeError as exc:
            raise JupyterProtocolError("pesan kernel bukan JSON") from exc
        if not isinstance(data, dict):
            raise JupyterProtocolError("pesan kernel bukan objek")
        return data

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def _handshake(
    ws_url: str,
    *,
    extra_headers: Mapping[str, str],
    timeout: float,
) -> socket.socket | ssl.SSLSocket:
    parsed = urlparse(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    kunci = base64.b64encode(os.urandom(16)).decode("ascii")
    baris = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}:{port}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {kunci}",
        "Sec-WebSocket-Version: 13",
    ]
    for nama, nilai in extra_headers.items():
        baris.append(f"{nama}: {nilai}")
    baris.append("")
    baris.append("")
    permintaan = "\r\n".join(baris).encode("ascii")

    mentah = socket.create_connection((host, port), timeout=timeout)
    sock: socket.socket | ssl.SSLSocket = mentah
    if parsed.scheme == "wss":
        konteks = ssl.create_default_context()
        sock = konteks.wrap_socket(mentah, server_hostname=host)

    sock.sendall(permintaan)
    jawaban = _recv_until(sock, b"\r\n\r\n")
    status = jawaban.split(b"\r\n", 1)[0]
    if b"101" not in status:
        sock.close()
        raise JupyterProtocolError(
            f"upgrade WebSocket ditolak: {status.decode('ascii', 'replace')}"
        )

    diharapkan = base64.b64encode(
        hashlib.sha1(
            (kunci + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
        ).digest()
    )
    if diharapkan not in jawaban:
        sock.close()
        raise JupyterProtocolError("Sec-WebSocket-Accept tidak cocok")
    return sock


def _recv_until(sock: socket.socket | ssl.SSLSocket, penanda: bytes) -> bytes:
    buf = bytearray()
    while penanda not in buf:
        potong = sock.recv(4096)
        if not potong:
            raise JupyterProtocolError("koneksi WebSocket ditutup saat handshake")
        buf.extend(potong)
        if len(buf) > 65_536:
            raise JupyterProtocolError("header WebSocket terlalu besar")
    return bytes(buf)


def _send_text(sock: socket.socket | ssl.SSLSocket, teks: str) -> None:
    payload = teks.encode("utf-8")
    header = bytearray([0x81])  # FIN + text
    n = len(payload)
    mask_bit = 0x80  # klien wajib mask
    if n < 126:
        header.append(mask_bit | n)
    elif n < 65536:
        header.append(mask_bit | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(mask_bit | 127)
        header.extend(struct.pack("!Q", n))
    mask = os.urandom(4)
    header.extend(mask)
    terlarang = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(header + terlarang)


def _recv_text(sock: socket.socket | ssl.SSLSocket) -> str:
    while True:
        header = _recv_exact(sock, 2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        n = header[1] & 0x7F
        if n == 126:
            n = struct.unpack("!H", _recv_exact(sock, 2))[0]
        elif n == 127:
            n = struct.unpack("!Q", _recv_exact(sock, 8))[0]
        mask = _recv_exact(sock, 4) if masked else b""
        payload = _recv_exact(sock, n)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:  # close
            raise JupyterProtocolError("kanal kernel ditutup server")
        if opcode == 0x9:  # ping → pong
            _send_pong(sock, payload)
            continue
        if opcode == 0xA:  # pong
            continue
        if opcode != 0x1:
            continue
        return payload.decode("utf-8")


def _send_pong(sock: socket.socket | ssl.SSLSocket, payload: bytes) -> None:
    header = bytearray([0x8A, 0x80 | len(payload)])
    mask = os.urandom(4)
    header.extend(mask)
    data = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(header + data)


def _recv_exact(sock: socket.socket | ssl.SSLSocket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        potong = sock.recv(n - len(buf))
        if not potong:
            raise JupyterProtocolError("koneksi WebSocket terputus")
        buf.extend(potong)
    return bytes(buf)
