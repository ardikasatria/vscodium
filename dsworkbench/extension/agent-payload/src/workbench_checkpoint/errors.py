"""Kesalahan Checkpoint Runner.

Dibedakan menurut tindakan yang perlu diambil. Pesan tidak memuat absolute
path host -- struktur direktori komputer mahasiswa bukan urusan UI.
"""

from __future__ import annotations


class CheckpointError(Exception):
    """Kegagalan yang menghentikan pemeriksaan sebelum hasil butir terbentuk."""


class UnknownHandlerError(CheckpointError):
    """Handler tidak ada dalam allowlist.

    Course package tidak boleh memperkenalkan handler baru lewat YAML.
    """

    def __init__(self, handler: str, *, known: tuple[str, ...] = ()):
        self.handler = handler
        self.known = known
        daftar = ", ".join(known) if known else "(kosong)"
        super().__init__(
            f"Handler checkpoint '{handler}' tidak dikenal. "
            f"Allowlist: {daftar}."
        )


class InvalidArtifactError(CheckpointError):
    """Path artefak tidak sah (traversal, absolut, atau kosong)."""


class ArtifactMissingError(CheckpointError):
    """Berkas pemeriksa tidak ditemukan di course package."""


class InvalidParamsError(CheckpointError):
    """Parameter handler tidak lengkap atau tipenya salah."""


class WorkspaceMissingError(CheckpointError):
    """Workspace mahasiswa belum ada atau pathnya tidak dapat dipakai."""
