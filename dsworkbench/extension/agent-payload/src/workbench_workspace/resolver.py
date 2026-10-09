"""Resolusi path yang aman.

Inti keamanan Workspace Manager. Setiap path yang berasal dari mahasiswa, dari
LabSpec, atau dari relay melewati berkas ini sebelum menyentuh filesystem.

Yang ditolak:

============================  ==================================================
Bentuk                        Contoh
============================  ==================================================
path kosong                   ``""``
null byte                     ``"laporan.csv\\x00.png"``
karakter kendali              ``"lap\\nporan.csv"``
absolute path POSIX           ``"/etc/shadow"``
absolute path Windows         ``"C:\\\\Windows\\\\system32"``
UNC path                      ``"\\\\\\\\server\\\\share"``
pemisah backslash             ``"notebooks\\\\modul-01.ipynb"``
komponen ``..``               ``"../../etc/passwd"``
awalan home directory         ``"~/.ssh/id_rsa"``
symlink yang keluar akar      ``"tautan"`` -> ``/etc``
komponen terlalu panjang      255+ karakter
path terlalu panjang          1024+ karakter
============================  ==================================================

Dua hal yang **tidak** dapat dicegah lapis ini, dan karena itu disebutkan
terbuka:

**Hard link.** Hard link ke berkas di luar workspace tidak terlihat oleh
``realpath``: ia bukan symlink, dan inode-nya sah berada di dalam akar.
Pertahanannya bukan di sini, melainkan pada mount read-only dan pemisahan
volume yang menjadi tugas Local Docker Provider (Component 05).

**TOCTOU.** Antara pemeriksaan dan pemakaian, komponen path dapat diganti
menjadi symlink. Jendelanya dipersempit dengan penulisan atomik pada direktori
yang sama (lihat ``files.py``), tetapi tidak tertutup sepenuhnya oleh kode
tingkat aplikasi. Local agent berjalan sebagai pengguna biasa di komputer
mahasiswa sendiri, sehingga ancaman ini bersifat "mahasiswa mengelabui dirinya
sendiri" -- merugikan dirinya, bukan mahasiswa lain.

Isolasi antar-mahasiswa tidak bergantung pada berkas ini. Ia dijamin oleh
pemisahan device dan otorisasi pada relay (Component 06 dan 07).
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import InvalidPathError, PathEscapeError, ReadOnlyError
from .policy import Limits, ReadOnlyPolicy

#: Komponen yang tidak pernah boleh muncul pada path relatif.
_FORBIDDEN_COMPONENTS = {"..", ""}

#: Nama yang dipesan Windows. Ditolak agar workspace tetap dapat dipakai lintas
#: sistem operasi; berkas bernama ``CON`` tidak dapat dibuat di sana.
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass(frozen=True)
class ResolvedPath:
    """Hasil resolusi yang sudah dinyatakan aman.

    ``relative`` adalah path sebagaimana diminta, sesudah dinormalkan. Itulah
    yang ditampilkan kembali kepada mahasiswa, agar mereka melihat path yang
    mereka tulis sendiri.

    ``real_relative`` adalah path setelah symlink di dalam workspace diikuti.
    Kebijakan read-only diperiksa terhadap **keduanya**: tanpa itu, symlink
    ``pintas -> data/raw`` di dalam workspace akan menjadi jalan pintas menulis
    ke data mentah.

    ``absolute`` hanya dipakai di dalam local agent untuk menyentuh filesystem
    dan tidak pernah keluar dari paket ini.
    """

    relative: PurePosixPath
    absolute: Path
    read_only: bool
    real_relative: PurePosixPath | None = None

    @property
    def canonical(self) -> PurePosixPath:
        """Path kanonik: hasil mengikuti symlink bila berbeda dari yang diminta."""
        return self.real_relative or self.relative

    @property
    def text(self) -> str:
        """Bentuk teks path relatif; inilah yang aman ditampilkan."""
        return str(self.relative)

    def __str__(self) -> str:
        return self.text


class PathResolver:
    """Menerjemahkan path mahasiswa menjadi path nyata di dalam satu akar.

    Satu resolver melayani satu akar. Ia tidak pernah menerima akar dari
    pemanggil pada saat resolusi, sehingga tidak ada cara mengubah akar lewat
    masukan pengguna.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        read_only: ReadOnlyPolicy | None = None,
        limits: Limits | None = None,
    ):
        # Akar ikut di-realpath: pada macOS ``/tmp`` adalah symlink ke
        # ``/private/tmp``, dan tanpa ini setiap perbandingan akan gagal.
        self._root = Path(os.path.realpath(str(root)))
        self._read_only = read_only or ReadOnlyPolicy()
        self._limits = limits or Limits()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def read_only_policy(self) -> ReadOnlyPolicy:
        return self._read_only

    @property
    def limits(self) -> Limits:
        return self._limits

    # ------------------------------------------------------------------
    # API utama
    # ------------------------------------------------------------------

    def resolve(self, user_path: str, *, for_write: bool = False) -> ResolvedPath:
        """Resolusi satu path terhadap akar.

        ``for_write=True`` menolak path yang berada di area read-only. Path yang
        belum ada tetap dapat di-resolve -- itu justru keadaan normal saat
        membuat berkas baru.
        """
        relative = self._check_text(user_path)
        absolute = self._check_containment(relative, user_path)

        # Path kanonik setelah symlink di dalam workspace diikuti. Berbeda dari
        # `relative` hanya bila ada symlink di jalurnya.
        try:
            nyata = PurePosixPath(absolute.relative_to(self._root).as_posix())
        except ValueError:  # pragma: no cover - sudah dijamin _check_containment
            nyata = relative

        # Diperiksa terhadap keduanya. Memeriksa path yang diminta saja membuat
        # symlink di dalam workspace menjadi jalan pintas menembus read-only;
        # memeriksa yang kanonik saja melewatkan kebijakan yang memang ditulis
        # untuk nama yang terlihat mahasiswa.
        prefix = self._read_only.covers(str(relative)) or self._read_only.covers(str(nyata))
        is_read_only = prefix is not None

        if for_write and is_read_only:
            raise ReadOnlyError(
                str(relative),
                f"berada di dalam area read-only '{prefix}'",
                "data mentah dan metadata workspace tidak boleh diubah; "
                "simpan hasil kerja di area yang dapat ditulis",
            )

        return ResolvedPath(
            relative=relative, absolute=absolute, read_only=is_read_only,
            real_relative=nyata,
        )

    def resolve_many(
        self, user_paths: list[str] | tuple[str, ...], *, for_write: bool = False
    ) -> list[ResolvedPath]:
        return [self.resolve(p, for_write=for_write) for p in user_paths]

    def is_inside(self, absolute: Path | str) -> bool:
        """Apakah sebuah absolute path berada di dalam akar setelah symlink diikuti."""
        nyata = Path(os.path.realpath(str(absolute)))
        return self._within_root(nyata)

    def relative_of(self, absolute: Path | str) -> PurePosixPath:
        """Path relatif dari sebuah absolute path yang sudah dipastikan aman."""
        nyata = Path(os.path.realpath(str(absolute)))
        if not self._within_root(nyata):
            raise PathEscapeError(str(absolute), "berada di luar akar workspace")
        return PurePosixPath(nyata.relative_to(self._root).as_posix())

    # ------------------------------------------------------------------
    # Lapis 1 -- bentuk teks
    # ------------------------------------------------------------------

    def _check_text(self, user_path: str) -> PurePosixPath:
        return check_path_text(user_path, self._limits)

    # ------------------------------------------------------------------
    # Lapis 2 -- pengurungan nyata di filesystem
    # ------------------------------------------------------------------

    def _check_containment(self, relative: PurePosixPath, original: str) -> Path:
        """Pastikan path tetap di dalam akar setelah symlink diikuti.

        ``realpath`` mengikuti symlink pada seluruh komponen yang ada, dan
        membiarkan sisanya apa adanya. Itu tepat: berkas yang belum ada tidak
        dapat menjadi symlink, dan teksnya sudah dipastikan bebas dari ``..``.
        """
        kandidat = self._root / Path(*relative.parts)
        nyata = Path(os.path.realpath(str(kandidat)))

        if not self._within_root(nyata):
            # Pesan tidak menyebut tujuan symlink: itu absolute path host.
            raise PathEscapeError(
                str(relative),
                "path menunjuk ke luar akar workspace",
                "kemungkinan ada symlink yang mengarah keluar workspace",
            )
        return nyata

    def _within_root(self, resolved: Path) -> bool:
        if resolved == self._root:
            return True
        try:
            resolved.relative_to(self._root)
            return True
        except ValueError:
            return False


def check_path_text(user_path: str, limits: Limits | None = None) -> PurePosixPath:
    """Periksa bentuk teks satu path relatif, tanpa menyentuh filesystem.

    Dipakai ``PathResolver`` sebagai lapis pertama, dan dipakai sendiri untuk
    menyaring nama entri arsip sebelum ekstraksi dimulai.
    """
    limits = limits or Limits()
    if not isinstance(user_path, str):
        raise InvalidPathError(
            str(user_path), f"path harus berupa teks, bukan {type(user_path).__name__}"
        )

    if "\x00" in user_path:
        # Null byte memotong path pada lapis C; pemeriksaan di Python akan
        # melihat teks yang berbeda dari yang dipakai kernel.
        raise InvalidPathError("(disembunyikan)", "path memuat null byte")

    mentah = user_path.strip()
    if not mentah:
        raise InvalidPathError(user_path, "path tidak boleh kosong")

    if len(mentah) > limits.max_path_length:
        raise InvalidPathError(
            _potong(mentah),
            f"panjang path {len(mentah)} melebihi batas {limits.max_path_length}",
        )

    for ch in mentah:
        if unicodedata.category(ch) == "Cc":
            raise InvalidPathError(
                _potong(mentah), "path memuat karakter kendali",
                "gunakan nama berkas tanpa karakter kendali atau baris baru",
            )

    if mentah.startswith("~"):
        raise InvalidPathError(
            mentah, "path merujuk home directory",
            "gunakan path relatif terhadap akar workspace",
        )

    if mentah.startswith("\\\\"):
        raise InvalidPathError(mentah, "path berbentuk UNC")

    if len(mentah) >= 2 and mentah[1] == ":" and mentah[0].isalpha():
        raise InvalidPathError(
            mentah, "path berbentuk absolute path Windows",
            "gunakan path relatif terhadap akar workspace",
        )

    if "\\" in mentah:
        raise InvalidPathError(
            mentah, "path memakai pemisah '\\'",
            "gunakan '/' agar path berlaku di semua sistem operasi",
        )

    if mentah.startswith("/"):
        raise InvalidPathError(
            mentah, "path berupa absolute path",
            "gunakan path relatif terhadap akar workspace",
        )

    bagian: list[str] = []
    for komponen in mentah.split("/"):
        if komponen == ".":
            continue
        if komponen in _FORBIDDEN_COMPONENTS:
            if komponen == "..":
                raise InvalidPathError(
                    mentah, "path memuat komponen '..'",
                    "path tidak boleh keluar dari akar workspace",
                )
            continue  # '//' berlebih; abaikan
        if len(komponen) > limits.max_component_length:
            raise InvalidPathError(
                _potong(mentah),
                f"komponen path melebihi {limits.max_component_length} karakter",
            )
        if komponen.split(".")[0].upper() in _RESERVED_NAMES:
            raise InvalidPathError(
                mentah, f"'{komponen}' adalah nama yang dipesan Windows",
                "pilih nama lain agar workspace tetap dapat dibuka di Windows",
            )
        bagian.append(komponen)

    if not bagian:
        raise InvalidPathError(
            user_path, "path menunjuk akar workspace itu sendiri",
            "sebutkan berkas atau direktori di dalamnya",
        )

    return PurePosixPath("/".join(bagian))


def _potong(teks: str, batas: int = 80) -> str:
    """Potong teks panjang agar pesan kesalahan tetap terbaca."""
    return teks if len(teks) <= batas else teks[:batas] + "..."


# ----------------------------------------------------------------------
# Arsip
# ----------------------------------------------------------------------


def safe_archive_members(names: list[str] | tuple[str, ...]) -> list[PurePosixPath]:
    """Saring nama entri arsip sebelum diekstrak.

    Dipakai ketika berkas awal atau snapshot datang sebagai arsip. Aturannya
    sama dengan resolusi path biasa, tetapi diterapkan pada daftar nama sebelum
    satu byte pun ditulis -- ekstraksi yang sudah berjalan setengah lebih sulit
    dibersihkan daripada dicegah.

    Melempar :class:`InvalidPathError` pada entri pertama yang tidak sah.
    """
    return [check_path_text(nama) for nama in names]
