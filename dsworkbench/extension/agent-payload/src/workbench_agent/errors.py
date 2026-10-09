"""Kesalahan agent.

Pesan berbahasa Indonesia karena akhirnya dibaca mahasiswa di terminal.
Pengenal ``code`` stabil agar UI kelak dapat memetakannya tanpa bergantung
pada teks.
"""

from __future__ import annotations


class AgentError(Exception):
    """Induk seluruh kegagalan agent."""

    code = "kesalahan_agent"

    def __init__(self, message: str, *, detail: str | None = None):
        self.detail = detail
        super().__init__(message)


class NotPairedError(AgentError):
    """Belum ada kredensial perangkat di penyimpanan lokal."""

    code = "belum_dipasangkan"

    def __init__(self) -> None:
        super().__init__(
            "Agent belum dipasangkan. Jalankan: workbench-agent pair <kode>"
        )


class OperationRejectedError(AgentError):
    """Operasi ditolak allowlist agent."""

    code = "operasi_ditolak"


class TransportError(AgentError):
    """Control plane tidak dapat dihubungi atau menjawab tidak sah."""

    code = "transport_gagal"


class AuthenticationFailedError(AgentError):
    """Kredensial perangkat ditolak -- dicabut, dirotasi, atau rusak."""

    code = "autentikasi_gagal"

    def __init__(self) -> None:
        super().__init__(
            "Kredensial perangkat ditolak Control API. "
            "Pasangkan ulang dengan kode pairing yang baru."
        )
