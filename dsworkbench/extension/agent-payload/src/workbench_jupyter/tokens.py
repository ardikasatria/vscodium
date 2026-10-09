"""Pembuatan token Jupyter.

Token digenerate agent/CLI, diteruskan ke container sebagai environment, dan
dipakai klien HTTP. Ia bukan kode pairing dan bukan kredensial perangkat.
"""

from __future__ import annotations

import secrets


def generate_token(*, nbytes: int = 24) -> str:
    """Token URL-safe untuk Jupyter ServerApp.token."""
    if nbytes < 16:
        raise ValueError("token Jupyter minimal 16 byte entropy")
    return secrets.token_urlsafe(nbytes)
