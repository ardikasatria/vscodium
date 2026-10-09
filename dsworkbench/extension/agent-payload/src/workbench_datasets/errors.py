"""Kesalahan Dataset Manager.

Pesan ditulis dalam Bahasa Indonesia karena akhirnya dibaca mahasiswa dan
asisten. Identifier tetap Bahasa Inggris.

Pembagiannya mengikuti tindakan yang perlu diambil, bukan tempat terjadinya:

* :class:`SourceError` -- sumber tidak dapat menyediakan bahannya. Tindakan:
  periksa course package atau sediakan artefak secara offline.
* :class:`ChecksumMismatchError` -- bahan ada, tetapi isinya bukan yang
  diharapkan. Tindakan: jangan dipakai; ini bisa berarti berkas rusak, salah
  versi, atau data mentah sudah diubah.
* :class:`IncompleteDatasetError` -- ada berkas yang hilang atau berlebih.
* :class:`ReadOnlyViolationError` -- ada yang mencoba mengubah material yang
  sudah dinyatakan immutable.
* :class:`ConcurrentPreparationError` -- penyiapan dataset yang sama sedang
  berjalan di proses lain.

Pesan tidak pernah memuat absolute path komputer. Dataset store berada di
direktori milik local agent, dan strukturnya bukan urusan UI maupun relay.
"""

from __future__ import annotations


class DatasetError(Exception):
    """Kegagalan yang menghentikan operasi dataset."""


class SourceError(DatasetError):
    """Sumber tidak dapat menyediakan bahan dataset.

    Termasuk artefak yang tidak ada di course package, sumber yang menolak
    permintaan, dan sumber yang menghasilkan bahan kosong.
    """


class ManifestError(DatasetError):
    """Manifest dataset tidak dapat dibaca atau bentuknya salah.

    Sengaja tidak diperbaiki diam-diam: manifest adalah satu-satunya acuan
    apakah dataset benar, dan menebak isinya menghilangkan gunanya.
    """


class VerificationError(DatasetError):
    """Induk seluruh kegagalan verifikasi."""


class ChecksumMismatchError(VerificationError):
    """Isi berkas tidak cocok dengan checksum yang diharapkan.

    Menyimpan kedua nilai agar diagnosis dapat dilakukan, tetapi pesan yang
    ditampilkan tetap ringkas -- checksum penuh tidak menolong mahasiswa.
    """

    def __init__(self, path: str, expected: str, actual: str):
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"'{path}' tidak cocok dengan checksum acuan "
            f"(diharapkan {_ringkas(expected)}, ditemukan {_ringkas(actual)}); "
            "berkas mungkin rusak, salah versi, atau sudah diubah"
        )


class SizeMismatchError(VerificationError):
    """Ukuran berkas berbeda dari yang dicatat manifest.

    Diperiksa sebelum checksum karena jauh lebih murah: berkas yang ukurannya
    sudah salah tidak perlu dibaca sampai habis.
    """

    def __init__(self, path: str, expected: int, actual: int):
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"'{path}' berukuran {actual} byte, seharusnya {expected} byte"
        )


class IncompleteDatasetError(VerificationError):
    """Berkas yang dicatat manifest tidak lengkap, atau ada yang berlebih."""

    def __init__(self, missing: tuple[str, ...] = (), unexpected: tuple[str, ...] = ()):
        self.missing = missing
        self.unexpected = unexpected
        bagian = []
        if missing:
            bagian.append("hilang: " + ", ".join(missing[:5]) + ("..." if len(missing) > 5 else ""))
        if unexpected:
            bagian.append("berlebih: " + ", ".join(unexpected[:5]) + ("..." if len(unexpected) > 5 else ""))
        super().__init__("isi dataset tidak sesuai manifest -- " + "; ".join(bagian))


class ReadOnlyViolationError(DatasetError):
    """Percobaan mengubah material dataset yang sudah immutable.

    Dataset Manager tidak menyediakan operasi tulis ke material raw. Kesalahan
    ini muncul bila ada yang memanggil jalur yang seharusnya tidak ada, dan
    berfungsi sebagai jaring pengaman, bukan sebagai batas keamanan utama.
    """


class ConcurrentPreparationError(DatasetError):
    """Dataset yang sama sedang disiapkan proses lain.

    Bukan kegagalan permanen. Pemanggil boleh menunggu lalu mencoba lagi;
    penyiapan yang sudah selesai akan langsung terdeteksi sebagai siap.
    """


def _ringkas(checksum: str) -> str:
    """Potong checksum agar pesan tetap terbaca."""
    if checksum.startswith("sha256:"):
        checksum = checksum[len("sha256:"):]
    return checksum[:12] + "..." if len(checksum) > 12 else checksum
