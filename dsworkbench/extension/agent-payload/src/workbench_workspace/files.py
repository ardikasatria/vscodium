"""Operasi berkas di dalam satu workspace.

Setiap operasi melewati :class:`~workbench_workspace.resolver.PathResolver`.
Tidak ada jalan pintas: kelas ini tidak menerima absolute path, dan tidak
pernah mengembalikannya.

Tiga hal yang membedakan modul ini dari pemakaian ``pathlib`` biasa:

**Penulisan bersifat atomik.** Berkas ditulis ke berkas sementara pada
direktori yang sama, lalu dipindahkan dengan ``os.replace``. Notebook yang
ditulis saat autosave dan listrik padam di tengahnya tidak menjadi berkas
setengah jadi. Berkas sementara berada di direktori yang sama agar perpindahan
tidak melintasi filesystem -- ``os.replace`` hanya atomik di dalam satu
filesystem.

**Batas ukuran ditegakkan sebelum membaca.** Ukuran diperiksa lewat ``stat``,
bukan setelah berkas masuk memori. Dataset praktikum berukuran megabyte, dan
local agent berjalan di laptop mahasiswa.

**Symlink tidak diikuti keluar.** Penelusuran direktori memakai ``os.scandir``
tanpa mengikuti symlink, dan setiap entri diperiksa ulang terhadap akar.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import (
    AlreadyExistsError,
    InvalidPathError,
    LimitExceededError,
    PathEscapeError,
    ReadOnlyError,
    WorkspaceError,
    WriteConflictError,
)
from .policy import ReadOnlyPolicy
from .resolver import PathResolver, ResolvedPath


@dataclass(frozen=True)
class Entry:
    """Satu entri pada daftar isi direktori.

    ``path`` selalu relatif terhadap akar workspace. Tidak ada absolute path
    yang keluar dari modul ini.
    """

    path: str
    name: str
    is_dir: bool
    size_bytes: int
    read_only: bool
    is_symlink: bool = False

    @property
    def kind(self) -> str:
        return "direktori" if self.is_dir else "berkas"


@dataclass(frozen=True)
class FileStat:
    """Ukuran, waktu ubah, dan SHA-256 isi satu berkas (path relatif)."""

    path: str
    size: int
    mtime: float
    sha256: str


#: Satu kunci untuk seluruh penulisan bersyarat di proses ini. Cek hash dan
#: rename harus satu langkah terhadap penulis lain di agent yang sama (dua
#: tab peramban, simpan otomatis bersamaan dengan simpan manual).
_KUNCI_TULIS = threading.Lock()


def _sha256_berkas(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for potong in iter(lambda: f.read(1024 * 1024), b""):
            h.update(potong)
    return h.hexdigest()


class WorkspaceFiles:
    """Operasi berkas yang terkurung pada satu akar workspace."""

    def __init__(self, resolver: PathResolver):
        self._r = resolver

    @property
    def resolver(self) -> PathResolver:
        return self._r

    def provisioning_view(self) -> "WorkspaceFiles":
        """View tanpa kebijakan read-only course, untuk penyediaan platform.

        Keamanan path dan kebijakan isi adalah dua hal yang berbeda. Penolakan
        traversal, symlink escape, dan absolute path berlaku untuk siapa pun --
        view ini tetap memakai resolver dengan akar yang sama dan seluruh
        pemeriksaan itu. Yang dilepas hanya kebijakan read-only course.

        Alasannya sederhana: ``data/raw`` dinyatakan read-only **bagi
        mahasiswa**, tetapi seseorang harus menaruh datanya di sana lebih dulu.
        Tanpa view ini, Dataset Manager tidak dapat memasang dataset ke
        workspace tanpa membuat kebijakan path tandingannya sendiri -- persis
        yang tidak boleh terjadi.

        Direktori internal ``.workbench`` **tetap** terkunci. Metadata workspace
        punya penulisnya sendiri dan tidak boleh diubah lewat API berkas, oleh
        siapa pun.

        View ini untuk operasi platform (penyediaan dataset, pemulihan
        snapshot), bukan untuk meneruskan permintaan mahasiswa.
        """
        return WorkspaceFiles(
            PathResolver(
                self._r.root,
                read_only=ReadOnlyPolicy.of([]),
                limits=self._r.limits,
            )
        )

    # ------------------------------------------------------------------
    # Membaca
    # ------------------------------------------------------------------

    def exists(self, path: str) -> bool:
        try:
            return self._r.resolve(path).absolute.exists()
        except (InvalidPathError, PathEscapeError):
            return False

    def read_bytes(self, path: str, *, max_bytes: int | None = None) -> bytes:
        """Baca berkas utuh, dengan batas ukuran yang ditegakkan lebih dulu."""
        target = self._r.resolve(path)
        self._require_file(target)
        batas = max_bytes if max_bytes is not None else self._r.limits.max_read_bytes
        ukuran = target.absolute.stat().st_size
        if ukuran > batas:
            raise LimitExceededError(
                f"'{target.text}' berukuran {ukuran} byte, melebihi batas baca {batas} byte; "
                "gunakan pratinjau sebagian, jangan memuat seluruh berkas",
                limit=batas, actual=ukuran,
            )
        return target.absolute.read_bytes()

    def read_text(self, path: str, *, encoding: str = "utf-8", max_bytes: int | None = None) -> str:
        data = self.read_bytes(path, max_bytes=max_bytes)
        try:
            return data.decode(encoding)
        except UnicodeDecodeError as exc:
            raise WorkspaceError(
                f"'{path}' bukan teks {encoding} yang sah pada posisi {exc.start}"
            ) from exc

    def read_head(self, path: str, *, max_bytes: int = 64 * 1024) -> bytes:
        """Baca bagian awal berkas saja.

        Inilah yang dipakai pratinjau CSV dan Parquet: berkas besar tidak boleh
        dimuat seluruhnya hanya untuk menampilkan beberapa baris pertama.
        """
        target = self._r.resolve(path)
        self._require_file(target)
        with target.absolute.open("rb") as f:
            return f.read(max_bytes)

    def stat(self, path: str) -> FileStat:
        """Ukuran, mtime, dan SHA-256 berkas utuh.

        Hash dihitung bertahap; batas baca tetap berlaku agar berkas raksasa
        tidak di-hash hanya karena diminta.
        """
        target = self._r.resolve(path)
        self._require_file(target)
        info = target.absolute.stat()
        batas = self._r.limits.max_read_bytes
        if info.st_size > batas:
            raise LimitExceededError(
                f"'{target.text}' berukuran {info.st_size} byte, melebihi batas baca {batas} byte",
                limit=batas, actual=info.st_size,
            )
        return FileStat(target.text, info.st_size, info.st_mtime,
                        _sha256_berkas(target.absolute))

    def read_range(self, path: str, offset: int, length: int) -> bytes:
        """Baca ``length`` byte mulai ``offset``; berkas utuh tetap dibatasi."""
        if offset < 0 or length < 0:
            raise WorkspaceError("offset dan length tidak boleh negatif")
        target = self._r.resolve(path)
        self._require_file(target)
        ukuran = target.absolute.stat().st_size
        batas = self._r.limits.max_read_bytes
        if ukuran > batas:
            raise LimitExceededError(
                f"'{target.text}' berukuran {ukuran} byte, melebihi batas baca {batas} byte",
                limit=batas, actual=ukuran,
            )
        with target.absolute.open("rb") as f:
            f.seek(offset)
            return f.read(length)

    def size_of(self, path: str) -> int:
        target = self._r.resolve(path)
        self._require_file(target)
        return target.absolute.stat().st_size

    # ------------------------------------------------------------------
    # Menulis
    # ------------------------------------------------------------------

    def write_bytes(self, path: str, data: bytes, *, overwrite: bool = True,
                    expected_sha256: str | None = None) -> ResolvedPath:
        """Tulis berkas secara atomik.

        Menolak bila path berada di area read-only, dan -- ketika
        ``overwrite=False`` -- bila berkas sudah ada (:class:`AlreadyExistsError`).
        Yang kedua dipakai saat menyalin berkas awal: pekerjaan mahasiswa tidak
        boleh tertimpa.

        ``expected_sha256`` membuat penulisan bersyarat: bila isi berkas di
        disk tidak lagi berhash itu (atau berkasnya hilang), penulisan ditolak
        dengan :class:`WriteConflictError` dan berkas tidak disentuh.
        """
        target = self._r.resolve(path, for_write=True)
        batas = self._r.limits.max_write_bytes
        if len(data) > batas:
            raise LimitExceededError(
                f"'{target.text}' berukuran {len(data)} byte, melebihi batas tulis {batas} byte",
                limit=batas, actual=len(data),
            )
        if target.absolute.is_dir():
            raise WorkspaceError(f"'{target.text}' adalah direktori")

        with _KUNCI_TULIS:
            ada = target.absolute.exists()
            if ada and not overwrite:
                raise AlreadyExistsError(f"'{target.text}' sudah ada dan tidak ditimpa")
            if expected_sha256 is not None:
                if not ada:
                    raise WriteConflictError(
                        f"'{target.text}' sudah tidak ada di disk", current_sha256=None)
                sekarang = _sha256_berkas(target.absolute)
                if sekarang != expected_sha256:
                    info = target.absolute.stat()
                    raise WriteConflictError(
                        f"'{target.text}' berubah di disk sejak dimuat",
                        current_sha256=sekarang, size=info.st_size, mtime=info.st_mtime,
                    )
            target.absolute.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target.absolute, data)
        return target

    def write_text(self, path: str, text: str, *, encoding: str = "utf-8",
                   overwrite: bool = True) -> ResolvedPath:
        return self.write_bytes(path, text.encode(encoding), overwrite=overwrite)

    def mkdir(self, path: str, *, parents: bool = True) -> ResolvedPath:
        target = self._r.resolve(path, for_write=True)
        if target.absolute.exists() and not target.absolute.is_dir():
            raise WorkspaceError(f"'{target.text}' sudah ada dan bukan direktori")
        target.absolute.mkdir(parents=parents, exist_ok=True)
        return target

    def delete(self, path: str, *, recursive: bool = False) -> ResolvedPath:
        """Hapus berkas atau direktori.

        Direktori tidak kosong hanya dihapus bila ``recursive=True``. Ini bukan
        sekadar kehati-hatian: penghapusan tak sengaja atas direktori kerja
        adalah kehilangan pekerjaan mahasiswa yang tidak dapat dipulihkan.
        """
        target = self._r.resolve(path, for_write=True)
        if not target.absolute.exists() and not target.absolute.is_symlink():
            raise WorkspaceError(f"'{target.text}' tidak ada")
        if target.absolute.is_dir() and not target.absolute.is_symlink():
            isi = any(target.absolute.iterdir())
            if isi and not recursive:
                raise WorkspaceError(
                    f"'{target.text}' masih berisi; "
                    "sebutkan recursive bila memang hendak menghapus seluruh isinya"
                )
            shutil.rmtree(target.absolute) if isi else target.absolute.rmdir()
        else:
            target.absolute.unlink()
        return target

    def move(self, source: str, destination: str, *, overwrite: bool = False) -> ResolvedPath:
        asal = self._r.resolve(source, for_write=True)
        tujuan = self._r.resolve(destination, for_write=True)
        if not asal.absolute.exists():
            raise WorkspaceError(f"'{asal.text}' tidak ada")
        if tujuan.absolute.exists() and not overwrite:
            raise WorkspaceError(f"'{tujuan.text}' sudah ada")
        tujuan.absolute.parent.mkdir(parents=True, exist_ok=True)
        os.replace(asal.absolute, tujuan.absolute)
        return tujuan

    def copy_into(self, source_absolute: Path, destination: str, *,
                  overwrite: bool = False) -> ResolvedPath | None:
        """Salin berkas dari luar workspace ke dalamnya.

        Dipakai saat menyalin berkas awal dari course package. Sumbernya berada
        di luar akar workspace, sehingga ia **tidak** di-resolve oleh resolver
        workspace; pemanggil bertanggung jawab memastikan sumbernya sah -- lihat
        ``manager.py``, yang memakai resolver terpisah untuk course package.

        Mengembalikan ``None`` bila tujuan sudah ada dan ``overwrite=False``.
        Itu bukan kegagalan: berkas awal memang tidak boleh menimpa pekerjaan
        mahasiswa.
        """
        tujuan = self._r.resolve(destination, for_write=True)
        if tujuan.absolute.exists() and not overwrite:
            return None
        if not source_absolute.is_file():
            raise WorkspaceError(f"sumber '{source_absolute.name}' bukan berkas biasa")
        tujuan.absolute.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(tujuan.absolute, source_absolute.read_bytes())
        return tujuan

    # ------------------------------------------------------------------
    # Menelusuri
    # ------------------------------------------------------------------

    def list_dir(self, path: str = ".", *, include_hidden: bool = False) -> list[Entry]:
        """Daftar isi satu direktori, tidak rekursif."""
        target = self._resolve_dir(path)
        hasil: list[Entry] = []
        batas = self._r.limits.max_entries

        with os.scandir(target.absolute) as it:
            for masuk in it:
                if not include_hidden and masuk.name.startswith("."):
                    continue
                if len(hasil) >= batas:
                    raise LimitExceededError(
                        f"direktori memuat lebih dari {batas} entri; "
                        "persempit penelusuran",
                        limit=batas,
                    )
                entri = self._entry_of(masuk, target.relative)
                if entri is not None:
                    hasil.append(entri)

        hasil.sort(key=lambda e: (not e.is_dir, e.name.casefold()))
        return hasil

    def tree(self, path: str = ".", *, max_depth: int | None = None,
             include_hidden: bool = False) -> list[Entry]:
        """Daftar isi rekursif dengan batas kedalaman.

        Symlink direktori tidak ditelusuri. Selain mencegah lolosnya penelusuran
        keluar akar, ini juga menghindari perulangan tak berujung pada symlink
        yang menunjuk dirinya sendiri.
        """
        batas_dalam = max_depth if max_depth is not None else self._r.limits.max_depth
        batas_entri = self._r.limits.max_entries
        hasil: list[Entry] = []

        def telusuri(rel: PurePosixPath, dalam: int) -> None:
            if dalam > batas_dalam:
                return
            for entri in self.list_dir(str(rel), include_hidden=include_hidden):
                if len(hasil) >= batas_entri:
                    raise LimitExceededError(
                        f"penelusuran melebihi {batas_entri} entri; persempit cakupan",
                        limit=batas_entri,
                    )
                hasil.append(entri)
                if entri.is_dir and not entri.is_symlink:
                    telusuri(PurePosixPath(entri.path), dalam + 1)

        awal = self._resolve_dir(path)
        telusuri(awal.relative, 1)
        return hasil

    # ------------------------------------------------------------------
    # Pembantu
    # ------------------------------------------------------------------

    def _resolve_dir(self, path: str) -> ResolvedPath:
        """Resolusi direktori; '.' berarti akar workspace."""
        if path in (".", "", "./"):
            akar = self._r.root
            return ResolvedPath(
                relative=PurePosixPath("."), absolute=akar,
                read_only=False,
            )
        target = self._r.resolve(path)
        if not target.absolute.exists():
            raise WorkspaceError(f"'{target.text}' tidak ada")
        if not target.absolute.is_dir():
            raise WorkspaceError(f"'{target.text}' bukan direktori")
        return target

    def _entry_of(self, masuk: os.DirEntry, parent: PurePosixPath) -> Entry | None:
        rel = masuk.name if str(parent) == "." else f"{parent}/{masuk.name}"

        # Entri yang menunjuk keluar akar tidak ditampilkan sama sekali.
        # Menampilkannya berarti membocorkan keberadaan berkas di luar workspace.
        if masuk.is_symlink() and not self._r.is_inside(masuk.path):
            return None

        try:
            is_dir = masuk.is_dir(follow_symlinks=False) or (
                masuk.is_symlink() and Path(masuk.path).is_dir()
            )
            ukuran = 0 if is_dir else masuk.stat(follow_symlinks=False).st_size
        except OSError:
            return None

        return Entry(
            path=rel,
            name=masuk.name,
            is_dir=is_dir,
            size_bytes=ukuran,
            read_only=self._r.read_only_policy.covers(rel) is not None,
            is_symlink=masuk.is_symlink(),
        )

    def _require_file(self, target: ResolvedPath) -> None:
        if not target.absolute.exists():
            raise WorkspaceError(f"'{target.text}' tidak ada")
        if target.absolute.is_dir():
            raise WorkspaceError(f"'{target.text}' adalah direktori, bukan berkas")


def _atomic_write(destination: Path, data: bytes) -> None:
    """Tulis lalu pindahkan, pada filesystem yang sama.

    ``os.replace`` bersifat atomik hanya di dalam satu filesystem; karena itu
    berkas sementara dibuat pada direktori tujuan, bukan di ``/tmp``.
    """
    fd, sementara = tempfile.mkstemp(
        dir=str(destination.parent), prefix=f".{destination.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(sementara, destination)
    except BaseException:
        # Berkas sementara tidak boleh tertinggal bila penulisan gagal.
        try:
            os.unlink(sementara)
        except OSError:
            pass
        raise
