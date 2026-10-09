"""Verifikasi isi dataset.

Seluruh hashing dilakukan secara **streaming**. Dataset praktikum Data
Wrangling berukuran sekitar 8 MB, dan profil ``medium`` NusaMart dapat mencapai
beberapa GB; membaca berkas utuh ke memori hanya untuk menghitung checksum akan
mematikan local agent di laptop mahasiswa.

Urutan pemeriksaan dipilih dari yang paling murah:

1. **Daftar berkas.** Berkas yang hilang atau berlebih terdeteksi dari
   ``scandir``, tanpa membaca isi apa pun.
2. **Ukuran.** Satu ``stat`` per berkas. Berkas yang ukurannya sudah salah
   tidak perlu dibaca sampai habis.
3. **Checksum.** Baru di sini isi berkas dibaca.

Urutan ini bukan sekadar optimasi. Ketika mahasiswa tidak sengaja menimpa
``transaksi.csv``, pesan "berukuran 12 byte, seharusnya 3.456.789 byte" jauh
lebih menolong daripada dua checksum yang berbeda.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Callable, Iterator

from .errors import (
    ChecksumMismatchError,
    IncompleteDatasetError,
    SizeMismatchError,
)
from .model import DatasetManifest, FileEntry

#: Ukuran potongan pembacaan. 1 MiB cukup besar agar syscall tidak mendominasi,
#: dan cukup kecil agar jejak memori tetap tak berarti.
CHUNK_BYTES = 1024 * 1024


def hash_file(path: Path, *, chunk_bytes: int = CHUNK_BYTES) -> str:
    """Hitung SHA-256 satu berkas tanpa memuatnya ke memori."""
    h = sha256()
    with path.open("rb") as f:
        while True:
            potongan = f.read(chunk_bytes)
            if not potongan:
                break
            h.update(potongan)
    return f"sha256:{h.hexdigest()}"


def hash_bytes(data: bytes) -> str:
    return f"sha256:{sha256(data).hexdigest()}"


def iter_files(root: Path) -> Iterator[Path]:
    """Telusuri berkas biasa di bawah ``root``, tanpa mengikuti symlink.

    Symlink dilewati sepenuhnya: material dataset yang terverifikasi tidak
    boleh memuat tautan, karena isi di ujung tautan tidak ikut terverifikasi.
    """
    for induk, direktori, berkas in os.walk(root, followlinks=False):
        # Jangan turun ke direktori yang sebenarnya symlink.
        direktori[:] = [d for d in direktori if not Path(induk, d).is_symlink()]
        for nama in sorted(berkas):
            p = Path(induk) / nama
            if p.is_symlink():
                continue
            yield p


def relative_paths(root: Path, *, exclude: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """Path relatif seluruh berkas di bawah ``root``, terurut."""
    hasil = []
    for p in iter_files(root):
        rel = p.relative_to(root).as_posix()
        if rel in exclude:
            continue
        hasil.append(rel)
    return tuple(sorted(hasil))


@dataclass(frozen=True)
class VerificationReport:
    """Hasil verifikasi satu direktori terhadap manifest."""

    verified: tuple[str, ...] = ()
    bytes_read: int = 0

    @property
    def file_count(self) -> int:
        return len(self.verified)


def verify_directory(
    root: Path,
    manifest: DatasetManifest,
    *,
    exclude: frozenset[str] = frozenset(),
    deep: bool = True,
    on_file: Callable[[str], None] | None = None,
) -> VerificationReport:
    """Verifikasi isi ``root`` terhadap ``manifest``.

    ``deep=False`` hanya memeriksa daftar berkas dan ukurannya. Dipakai untuk
    pemeriksaan cepat saat dataset yang sudah terverifikasi dibuka kembali;
    verifikasi penuh tetap dilakukan sebelum material dipromosikan.

    Melempar pada ketidakcocokan pertama. Dataset yang sebagian benar tidak
    lebih berguna daripada dataset yang salah, dan melanjutkan pemeriksaan
    hanya menghabiskan waktu.
    """
    ada = set(relative_paths(root, exclude=exclude))
    diharapkan = set(manifest.paths)

    hilang = tuple(sorted(diharapkan - ada))
    berlebih = tuple(sorted(ada - diharapkan))
    if hilang or berlebih:
        raise IncompleteDatasetError(missing=hilang, unexpected=berlebih)

    terverifikasi: list[str] = []
    total = 0

    for entri in manifest.files:
        target = root / entri.path
        ukuran = target.stat().st_size
        if ukuran != entri.size_bytes:
            raise SizeMismatchError(entri.path, entri.size_bytes, ukuran)

        if deep:
            digest = hash_file(target)
            if digest != entri.sha256:
                raise ChecksumMismatchError(entri.path, entri.sha256, digest)
            total += ukuran

        terverifikasi.append(entri.path)
        if on_file is not None:
            on_file(entri.path)

    return VerificationReport(verified=tuple(terverifikasi), bytes_read=total)


def build_entries(
    root: Path, *, exclude: frozenset[str] = frozenset()
) -> tuple[FileEntry, ...]:
    """Susun entri manifest dari isi direktori yang sebenarnya.

    Dipakai ketika sumber tidak menyediakan manifest -- misalnya generator yang
    baru saja menghasilkan dataset. Checksum dihitung di sini, sekali, dan
    dipakai sebagai acuan seterusnya.
    """
    entri: list[FileEntry] = []
    for p in iter_files(root):
        rel = p.relative_to(root).as_posix()
        if rel in exclude:
            continue
        entri.append(
            FileEntry(path=rel, size_bytes=p.stat().st_size, sha256=hash_file(p))
        )
    return tuple(sorted(entri, key=lambda e: e.path))


def file_sizes(root: Path, *, exclude: frozenset[str] = frozenset()) -> dict[str, int]:
    """Peta path relatif ke ukuran, untuk menyusun manifest dari CHECKSUM.txt."""
    hasil: dict[str, int] = {}
    for p in iter_files(root):
        rel = p.relative_to(root).as_posix()
        if rel in exclude:
            continue
        hasil[rel] = p.stat().st_size
    return hasil
