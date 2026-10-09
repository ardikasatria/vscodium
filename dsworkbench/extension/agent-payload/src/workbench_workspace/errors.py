"""Kesalahan Workspace Manager.

Pesan ditulis dalam Bahasa Indonesia karena akhirnya dibaca mahasiswa dan
asisten melalui UI. Identifier tetap Bahasa Inggris.

Pembagiannya mengikuti sebab, bukan tempat terjadinya:

* :class:`InvalidPathError` -- path tidak berbentuk sah sejak teksnya.
* :class:`PathEscapeError` -- path keluar dari akar workspace.
* :class:`ReadOnlyError` -- path sah, tetapi berada di area yang tidak boleh
  ditulis.
* :class:`LimitExceededError` -- operasi melampaui batas ukuran atau jumlah.

Keempatnya sengaja dibedakan: UI perlu menjelaskan "path tidak boleh keluar
dari workspace" secara berbeda dari "data mentah tidak boleh diubah". Yang
kedua adalah aturan akademik, bukan kesalahan mahasiswa.

Pesan kesalahan **tidak pernah memuat absolute path komputer**. Mahasiswa
melihat path relatif terhadap akar workspace; bocornya struktur direktori host
ke UI maupun log adalah kebocoran informasi yang tidak perlu.
"""

from __future__ import annotations


class WorkspaceError(Exception):
    """Kegagalan yang menghentikan operasi workspace."""


class PathPolicyError(WorkspaceError):
    """Induk seluruh penolakan path.

    Ditangkap bersama-sama oleh pemanggil yang hanya perlu tahu bahwa path
    ditolak, tanpa peduli sebab persisnya.
    """

    def __init__(self, path: str, message: str, suggestion: str | None = None):
        self.path = path
        self.suggestion = suggestion
        teks = f"'{path}': {message}"
        if suggestion:
            teks += f" -- {suggestion}"
        super().__init__(teks)


class InvalidPathError(PathPolicyError):
    """Path tidak berbentuk sah: kosong, absolute, memuat null byte, dan sejenisnya."""


class PathEscapeError(PathPolicyError):
    """Path menunjuk keluar akar workspace.

    Termasuk lolosnya lewat symlink, yang tidak terlihat dari teks path.
    """


class ReadOnlyError(PathPolicyError):
    """Penulisan ke area read-only.

    Data mentah dipasang read-only oleh infrastructure layer; penolakan di sini
    adalah lapis tambahan agar kesalahan terdeteksi lebih awal dan dengan pesan
    yang dapat dipahami, bukan sebagai ``Permission denied``.
    """


class LimitExceededError(WorkspaceError):
    """Operasi melampaui batas yang ditetapkan kebijakan.

    Batas ada agar satu berkas besar tidak menghabiskan memori local agent.
    """

    def __init__(self, message: str, *, limit: int | None = None, actual: int | None = None):
        self.limit = limit
        self.actual = actual
        super().__init__(message)


class WriteConflictError(WorkspaceError):
    """Berkas di disk berubah sejak versi yang dimuat penulis.

    Penulisan bersyarat (``expected_sha256``) menolak alih-alih menimpa:
    mahasiswa mungkin menyunting berkas yang sama di Jupyter lokal, atau
    ``git pull`` baru saja memperbaruinya (ADR-050 §2). ``current_sha256``
    ``None`` berarti berkasnya sudah tidak ada.
    """

    def __init__(self, message: str, *, current_sha256: str | None,
                 size: int | None = None, mtime: float | None = None):
        self.current_sha256 = current_sha256
        self.size = size
        self.mtime = mtime
        super().__init__(message)


class AlreadyExistsError(WorkspaceError):
    """Berkas sudah ada dan penulis meminta tidak menimpanya."""


class WorkspaceNotFoundError(WorkspaceError):
    """Workspace yang diminta tidak ada."""


class MetadataError(WorkspaceError):
    """Metadata workspace tidak dapat dibaca atau rusak.

    Sengaja tidak diperbaiki diam-diam: metadata yang rusak berarti ada yang
    tidak beres, dan menimpanya dapat menghilangkan jejak pekerjaan mahasiswa.
    """
