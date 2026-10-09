"""Unggah berkas besar ke workspace dalam potongan (ADR-050 §6).

Frame relay dan badan HTTP Control API dibatasi 1 MiB. Notebook dengan
beberapa gambar keluaran dapat melebihinya, jadi browser mengirimnya dalam
potongan ``CHUNK_BYTES`` lewat ``workspace.upload_begin`` → ``upload_chunk``
→ ``upload_commit``.

Potongan ditampung di berkas sementara **di luar workspace** (direktori
sementara sistem): staging tidak boleh terlihat sebagai berkas mahasiswa,
tidak boleh ikut ter-commit, dan direktori internal ``.workbench`` memang
terkunci bagi API berkas. Saat commit, ukuran dan SHA-256 diverifikasi, lalu
isi ditulis lewat ``WorkspaceFiles.write_bytes`` -- sehingga kebijakan path,
read-only, batas tulis, penulisan atomik, dan cek konflik tetap satu jalur.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

#: Ukuran potongan mentah. Setelah base64 (±683 KiB) masih jauh di bawah
#: batas frame 1 MiB bersama envelope JSON-nya.
CHUNK_BYTES = 512 * 1024

#: Unggahan yang tidak di-commit dibuang setelah sekian detik.
UPLOAD_TTL_SECONDS = 15 * 60

#: Unggahan aktif maksimum per agent: cukup untuk beberapa tab, tidak cukup
#: untuk memenuhi disk dengan staging.
MAX_ACTIVE_UPLOADS = 8


class UploadError(Exception):
    """Unggahan ditolak; pesannya aman ditampilkan kepada mahasiswa."""

    def __init__(self, message: str, *, code: str):
        self.code = code
        super().__init__(message)


@dataclass
class _Unggahan:
    upload_id: str
    path: str
    size: int
    sha256: str
    base_sha256: str | None
    if_absent: bool
    staging: Path
    #: Akar tempat unggahan dimulai (id mata kuliah per-course, atau None).
    scope: str | None
    dibuat: float
    diterima: set[int] = field(default_factory=set)

    @property
    def jumlah_potongan(self) -> int:
        return max(1, -(-self.size // CHUNK_BYTES))


class UploadStore:
    """Penampung unggahan aktif. Aman dipanggil dari beberapa utas."""

    def __init__(self, *, max_size: int, clock: Callable[[], float] = time.monotonic,
                 staging_dir: Path | None = None):
        self._max_size = max_size
        self._clock = clock
        self._dir = staging_dir
        self._aktif: dict[str, _Unggahan] = {}
        self._kunci = threading.Lock()

    def begin(self, *, path: str, size: int, sha256: str,
              base_sha256: str | None, if_absent: bool, scope: str | None = None) -> _Unggahan:
        if size < 0 or size > self._max_size:
            raise UploadError(
                f"Ukuran {size} byte di luar batas tulis {self._max_size} byte.",
                code="too_large")
        with self._kunci:
            self._sapu()
            if len(self._aktif) >= MAX_ACTIVE_UPLOADS:
                raise UploadError(
                    "Terlalu banyak unggahan berjalan. Tunggu sebentar lalu simpan lagi.",
                    code="busy")
            fd, nama = tempfile.mkstemp(prefix="workbench-upload-", suffix=".part",
                                        dir=str(self._dir) if self._dir else None)
            os.close(fd)
            u = _Unggahan(
                upload_id=secrets.token_urlsafe(16), path=path, size=size,
                sha256=sha256, base_sha256=base_sha256, if_absent=if_absent,
                staging=Path(nama), dibuat=self._clock(), scope=scope,
            )
            self._aktif[u.upload_id] = u
            return u

    def chunk(self, upload_id: str, index: int, data: bytes) -> int:
        with self._kunci:
            u = self._ambil(upload_id)
            if index < 0 or index >= u.jumlah_potongan:
                raise UploadError(f"Potongan {index} di luar rentang.", code="bad_chunk")
            awal = index * CHUNK_BYTES
            harap = min(CHUNK_BYTES, u.size - awal)
            if len(data) != harap:
                raise UploadError(
                    f"Potongan {index} berukuran {len(data)} byte, seharusnya {harap}.",
                    code="bad_chunk")
            with u.staging.open("r+b") as f:
                f.seek(awal)
                f.write(data)
            u.diterima.add(index)
            return len(u.diterima)

    def take_complete(self, upload_id: str) -> tuple[_Unggahan, bytes]:
        """Keluarkan unggahan yang lengkap dan terverifikasi (staging dibuang)."""
        with self._kunci:
            u = self._ambil(upload_id)
            self._aktif.pop(upload_id, None)
        try:
            if len(u.diterima) != u.jumlah_potongan and u.size > 0:
                raise UploadError("Unggahan belum lengkap.", code="incomplete")
            data = u.staging.read_bytes()[: u.size]
            if len(data) != u.size or hashlib.sha256(data).hexdigest() != u.sha256:
                raise UploadError(
                    "Isi unggahan tidak cocok dengan hash yang dinyatakan.", code="hash_mismatch")
            return u, data
        finally:
            _hapus(u.staging)

    def active(self) -> int:
        with self._kunci:
            self._sapu()
            return len(self._aktif)

    def _ambil(self, upload_id: str) -> _Unggahan:
        self._sapu()
        u = self._aktif.get(upload_id)
        if u is None:
            raise UploadError("Unggahan tidak dikenal atau sudah kedaluwarsa.", code="unknown_upload")
        return u

    def _sapu(self) -> None:
        batas = self._clock() - UPLOAD_TTL_SECONDS
        for uid in [k for k, u in self._aktif.items() if u.dibuat < batas]:
            _hapus(self._aktif.pop(uid).staging)


def _hapus(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


__all__ = ["CHUNK_BYTES", "MAX_ACTIVE_UPLOADS", "UPLOAD_TTL_SECONDS", "UploadError", "UploadStore"]
