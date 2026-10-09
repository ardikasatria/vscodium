"""Kesalahan backend Jupyter."""

from __future__ import annotations


class JupyterError(Exception):
    """Induk seluruh kegagalan backend Jupyter."""

    code = "jupyter_gagal"

    def __init__(self, message: str, *, detail: str | None = None):
        self.detail = detail
        super().__init__(message)


class KernelNotFoundError(JupyterError):
    code = "kernel_tidak_ditemukan"

    def __init__(self, kernel_id: str):
        super().__init__(
            f"Kernel '{kernel_id}' tidak ditemukan.",
            detail="kernel mungkin sudah dihentikan",
        )
        self.kernel_id = kernel_id


class JupyterTimeoutError(JupyterError):
    code = "jupyter_timeout"

    def __init__(self, message: str = "Operasi Jupyter melebihi batas waktu."):
        super().__init__(message)


class JupyterProtocolError(JupyterError):
    code = "jupyter_protokol"
