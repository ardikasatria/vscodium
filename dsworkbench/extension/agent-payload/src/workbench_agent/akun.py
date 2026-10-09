"""Folder kerja dan data rahasia per akun Workbench di satu akun komputer.

Satu akun komputer (Windows/macOS/Linux) bisa dipakai bergantian oleh beberapa
akun Workbench: laptop pinjaman, atau lab yang memakai akun umum. Tanpa
pemisahan, akun berikutnya mewarisi notebook, laporan, basis data praktikum,
dan token GitHub akun sebelumnya.

Aturannya sengaja tanpa memindahkan data yang sudah ada:

- akun **pertama** yang dikenal di sebuah direktori menjadi pemiliknya (penanda
  ``.akun-workbench.json``) dan terus memakai direktori itu apa adanya;
- akun **lain** mendapat direktori sendiri di sampingnya
  (``workbench-workspace-akun/<akun>/`` untuk folder kerja,
  ``<state>/akun/<akun>/`` untuk data agent).

Ini pemisahan oleh aplikasi, bukan batas keamanan sistem operasi: siapa pun
yang masuk ke akun komputer yang sama tetap dapat membuka folder lain lewat
File Explorer. Pemisahan yang sesungguhnya adalah akun komputer per mahasiswa.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

PENANDA = ".akun-workbench.json"
_AMAN = re.compile(r"[^A-Za-z0-9._@-]+")


def nama_aman(akun: object) -> str | None:
    """Nama akun sebagai satu komponen path yang aman, atau ``None``."""
    if not isinstance(akun, str):
        return None
    bersih = _AMAN.sub("_", akun.strip()).strip("._")[:80]
    return bersih or None


def pemilik(dasar: Path) -> str | None:
    try:
        data = json.loads((dasar / PENANDA).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return nama_aman(data.get("akun")) if isinstance(data, dict) else None


def _klaim(dasar: Path, akun: str) -> bool:
    """Catat ``akun`` sebagai pemilik ``dasar``. Gagal menulis = tidak diklaim."""
    try:
        dasar.mkdir(parents=True, exist_ok=True)
        sementara = dasar / (PENANDA + ".tmp")
        sementara.write_text(json.dumps({"akun": akun}) + "\n", encoding="utf-8")
        os.replace(sementara, dasar / PENANDA)
        return True
    except OSError:
        return False


def direktori_akun(dasar: Path, akun: object, lain: Path, *, klaim: bool = True) -> Path:
    """Direktori milik ``akun``: ``dasar`` bagi pemiliknya, ``lain/<akun>`` bagi yang lain.

    ``akun`` kosong (agent lama yang belum tahu akunnya) selalu ``dasar`` dan
    tidak mengklaim apa pun. ``klaim=False`` hanya menghitung jalurnya (untuk
    tampilan), tanpa menulis penanda.
    """
    nama = nama_aman(akun)
    if nama is None:
        return dasar
    punya = pemilik(dasar)
    if punya is None:
        # Tanpa penanda: akun ini yang pertama. Bila penanda tidak dapat ditulis,
        # tetap pakai ``dasar`` agar pekerjaan yang ada tidak "hilang".
        if klaim:
            _klaim(dasar, nama)
        return dasar
    if punya == nama:
        return dasar
    return lain / nama


def akar_workspace_akun(dasar: Path, akun: object, *, klaim: bool = True) -> Path:
    return direktori_akun(dasar, akun, dasar.with_name(dasar.name + "-akun"), klaim=klaim)


def state_akun(state_dir: Path, akun: object) -> Path:
    return direktori_akun(state_dir, akun, state_dir / "akun")
