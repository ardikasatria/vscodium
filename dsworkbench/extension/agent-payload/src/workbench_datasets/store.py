"""Penyimpanan dataset pada komputer mahasiswa.

Tiga lapis yang sengaja dipisahkan:

``.staging/``
    Tempat sumber menulis. Isinya belum terverifikasi dan boleh rusak. Setiap
    penyiapan memakai direktori staging sendiri, dan direktori itu dihapus
    apa pun hasilnya.

``cache/``
    Artefak sumber yang disimpan agar tidak perlu diambil ulang. Inilah yang
    membuat praktikum tetap berjalan tanpa jaringan setelah persiapan selesai
    (prompt1 §13). Boleh dihapus kapan saja; hilangnya hanya berarti bahan
    harus diambil ulang.

``raw/``
    Material yang **sudah terverifikasi** dan diperlakukan immutable. Hanya
    direktori staging yang lolos verifikasi yang dipromosikan ke sini.

Promosi dilakukan dengan ``os.replace`` atas direktori. Pada satu filesystem,
operasi itu atomik: material raw tidak pernah berada dalam keadaan setengah
jadi. Itulah alasan staging berada di dalam akar dataset store, bukan di
``/tmp`` -- perpindahan lintas filesystem tidak atomik.

Tentang read-only, apa adanya: setelah promosi, berkas diberi mode ``0444`` dan
direktori ``0555``. Ini **bukan** batas keamanan terhadap pemilik komputer, yang
selalu dapat mengubah mode berkasnya sendiri. Yang dijamin paket ini adalah
dua hal yang memang dapat dijamin: tidak ada operasi Workbench yang menulis ke
material raw, dan perubahan apa pun terdeteksi oleh checksum. Penegakan yang
sesungguhnya adalah mount read-only pada Component 05.
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from .errors import (
    ConcurrentPreparationError,
    DatasetError,
    ManifestError,
    ReadOnlyViolationError,
)
from .model import (
    MANIFEST_FILENAME,
    DatasetIdentity,
    DatasetManifest,
)
from .verify import VerificationReport, build_entries, verify_directory

STAGING_DIR = ".staging"
CACHE_DIR = "cache"
RAW_DIR = "raw"
LOCK_DIR = ".locks"

#: Berkas manifest tidak ikut diverifikasi sebagai isi dataset.
MANIFEST_EXCLUDE = frozenset({MANIFEST_FILENAME})


@dataclass(frozen=True)
class StoredDataset:
    """Material dataset yang sudah terverifikasi dan tersimpan."""

    identity: DatasetIdentity
    manifest: DatasetManifest
    location: str

    @property
    def checksum(self) -> str:
        return self.manifest.checksum


class _DirectoryLock:
    """Kunci antar-proses berbasis pembuatan direktori.

    ``mkdir`` bersifat atomik pada POSIX maupun Windows: hanya satu proses yang
    berhasil membuat direktori yang sama. Ini cukup untuk mencegah dua
    penyiapan dataset yang sama berjalan bersamaan pada satu komputer, dan
    tidak memerlukan dependency apa pun.

    Kunci yang tertinggal karena proses mati dibersihkan dengan menghapusnya
    secara manual; ia tidak kedaluwarsa sendiri. Itu disengaja -- menghapus
    kunci secara otomatis berdasarkan umur dapat memotong penyiapan yang
    sebenarnya masih berjalan.
    """

    def __init__(self, path: Path):
        self._path = path
        self._held = False

    def __enter__(self) -> "_DirectoryLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._path.mkdir()
        except FileExistsError as exc:
            raise ConcurrentPreparationError(
                "dataset ini sedang disiapkan oleh proses lain; "
                "tunggu sampai selesai lalu coba lagi"
            ) from exc
        self._held = True
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._held:
            try:
                self._path.rmdir()
            except OSError:
                pass


class DatasetStore:
    """Tata letak dataset pada satu komputer.

    Kelas ini hanya mengurus tempat dan keadaan berkas. Keputusan kapan
    mengambil bahan dan kapan memverifikasinya ada pada
    :class:`~workbench_datasets.manager.DatasetManager`.
    """

    def __init__(self, root: Path | str):
        self._root = Path(root).expanduser()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    # ------------------------------------------------------------------
    # Tata letak
    # ------------------------------------------------------------------

    def raw_path(self, identity: DatasetIdentity) -> Path:
        return self._root / RAW_DIR / identity.key

    def cache_path(self, identity: DatasetIdentity) -> Path:
        return self._root / CACHE_DIR / identity.key

    def location(self, identity: DatasetIdentity) -> str:
        """Path relatif terhadap akar store.

        Relatif, bukan absolute: struktur direktori komputer mahasiswa tidak
        perlu keluar dari paket ini, apalagi melintasi relay.
        """
        return f"{RAW_DIR}/{identity.key}"

    def lock(self, identity: DatasetIdentity) -> _DirectoryLock:
        aman = identity.key.replace("/", "__")
        return _DirectoryLock(self._root / LOCK_DIR / aman)

    # ------------------------------------------------------------------
    # Keadaan
    # ------------------------------------------------------------------

    def has_raw(self, identity: DatasetIdentity) -> bool:
        return (self.raw_path(identity) / MANIFEST_FILENAME).is_file()

    def read_manifest(self, identity: DatasetIdentity) -> DatasetManifest:
        target = self.raw_path(identity) / MANIFEST_FILENAME
        try:
            teks = target.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ManifestError(
                f"manifest dataset '{identity.ref}' tidak ditemukan"
            ) from exc
        except UnicodeDecodeError as exc:
            raise ManifestError("manifest dataset bukan teks UTF-8 yang sah") from exc

        manifest = DatasetManifest.from_json(teks)
        if manifest.identity != identity:
            raise ManifestError(
                f"manifest tersimpan menyebut {manifest.identity}, "
                f"sedangkan yang diminta {identity}"
            )
        return manifest

    def verify_raw(
        self, identity: DatasetIdentity, manifest: DatasetManifest, *, deep: bool = True
    ) -> VerificationReport:
        return verify_directory(
            self.raw_path(identity), manifest, exclude=MANIFEST_EXCLUDE, deep=deep
        )

    # ------------------------------------------------------------------
    # Penyiapan
    # ------------------------------------------------------------------

    def new_staging(self, identity: DatasetIdentity) -> Path:
        """Direktori staging kosong, di filesystem yang sama dengan ``raw/``."""
        dasar = self._root / STAGING_DIR
        dasar.mkdir(parents=True, exist_ok=True)
        aman = identity.key.replace("/", "__")
        # os.getpid ikut agar dua proses tidak memakai direktori yang sama
        # walaupun kunci entah bagaimana terlewati.
        for percobaan in range(1000):
            kandidat = dasar / f"{aman}.{os.getpid()}.{percobaan}"
            try:
                kandidat.mkdir()
                return kandidat
            except FileExistsError:
                continue
        raise DatasetError("tidak dapat membuat direktori staging")

    def promote(self, identity: DatasetIdentity, staging: Path,
                manifest: DatasetManifest) -> StoredDataset:
        """Pindahkan staging yang sudah terverifikasi menjadi material raw.

        Manifest ditulis **sebelum** perpindahan, sehingga material raw tidak
        pernah ada tanpa manifestnya. ``has_raw`` memeriksa keberadaan manifest
        justru karena itu: direktori tanpa manifest bukan material yang sah.
        """
        (staging / MANIFEST_FILENAME).write_text(manifest.to_json(), encoding="utf-8")

        tujuan = self.raw_path(identity)
        tujuan.parent.mkdir(parents=True, exist_ok=True)

        if tujuan.exists():
            # Material lama disingkirkan lebih dulu, bukan ditimpa: os.replace
            # atas direktori yang tidak kosong gagal pada sebagian platform.
            usang = tujuan.with_name(tujuan.name + f".usang.{os.getpid()}")
            _make_writable(tujuan)
            os.replace(tujuan, usang)
            try:
                os.replace(staging, tujuan)
            finally:
                shutil.rmtree(usang, ignore_errors=True)
        else:
            os.replace(staging, tujuan)

        _make_read_only(tujuan)
        return StoredDataset(identity=identity, manifest=manifest,
                             location=self.location(identity))

    def discard(self, staging: Path) -> None:
        """Buang direktori staging. Aman dipanggil berkali-kali."""
        shutil.rmtree(staging, ignore_errors=True)

    def sweep_staging(self) -> int:
        """Bersihkan staging yang tertinggal dari proses yang mati.

        Hanya menyentuh direktori di bawah ``.staging/``; material raw dan
        cache tidak pernah tersentuh.
        """
        dasar = self._root / STAGING_DIR
        if not dasar.is_dir():
            return 0
        jumlah = 0
        for anak in dasar.iterdir():
            if anak.is_dir():
                shutil.rmtree(anak, ignore_errors=True)
                jumlah += 1
        return jumlah

    def remove_raw(self, identity: DatasetIdentity) -> None:
        """Hapus material raw satu dataset.

        Dipakai ketika verifikasi ulang gagal dan material harus disiapkan
        ulang. **Tidak** menyentuh workspace mahasiswa.
        """
        target = self.raw_path(identity)
        if not target.exists():
            return
        if not _within(target, self._root / RAW_DIR):
            raise ReadOnlyViolationError(
                "penghapusan di luar direktori raw ditolak"
            )
        _make_writable(target)
        shutil.rmtree(target, ignore_errors=True)


# ----------------------------------------------------------------------
# Mode berkas
# ----------------------------------------------------------------------


def _make_read_only(root: Path) -> None:
    """Berkas 0444, direktori 0555. Best-effort, dan itu disengaja.

    Pemilik komputer selalu dapat mengembalikan mode berkasnya sendiri. Mode
    ini menghalangi perubahan yang tidak disengaja dan membuat maksudnya jelas;
    ia bukan batas keamanan. Kegagalan mengubah mode -- pada filesystem yang
    tidak mendukungnya, misalnya -- tidak menggagalkan penyiapan.
    """
    for induk, direktori, berkas in os.walk(root, topdown=False):
        for nama in berkas:
            _chmod(Path(induk) / nama, 0o444)
        for nama in direktori:
            _chmod(Path(induk) / nama, 0o555)
    _chmod(root, 0o555)


def _make_writable(root: Path) -> None:
    """Kembalikan hak tulis agar direktori dapat dihapus atau digantikan."""
    _chmod(root, 0o755)
    for induk, direktori, berkas in os.walk(root):
        for nama in direktori:
            _chmod(Path(induk) / nama, 0o755)
        for nama in berkas:
            _chmod(Path(induk) / nama, 0o644)


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except (OSError, NotImplementedError):
        # Filesystem yang tidak mendukung mode POSIX tidak boleh membuat
        # penyiapan dataset gagal; checksum tetap menjadi jaminannya.
        pass


def is_read_only(path: Path) -> bool:
    """Apakah berkas ditandai tidak dapat ditulis pemiliknya."""
    try:
        return not bool(path.stat().st_mode & stat.S_IWUSR)
    except OSError:
        return False


def _within(path: Path, root: Path) -> bool:
    try:
        Path(os.path.realpath(path)).relative_to(Path(os.path.realpath(root)))
        return True
    except ValueError:
        return False
