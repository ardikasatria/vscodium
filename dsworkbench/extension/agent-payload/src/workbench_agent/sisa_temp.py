"""Pembersihan sisa direktori sementara pemeriksa checkpoint (ADR-072 §3.5).

``checkpoint.run`` menulis bundel pemeriksa ke ``workbench-checkpoint-*`` di
direktori sementara sistem dan menghapusnya setelah selesai. Bila agent
dimatikan di tengah pemeriksaan, direktori itu tertinggal -- beserta skrip
pemeriksa di dalamnya. Agent menghapus sisa semacam itu setiap kali mulai.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

#: Sama dengan ``prefix`` pada ``tempfile.TemporaryDirectory`` di ``checkpoint.run``.
AWALAN_CHECKPOINT = "workbench-checkpoint-"

#: Pemeriksa berjalan paling lama 180 detik; satu jam jelas sudah sisa.
UMUR_MIN_DETIK = 3600.0


def bersihkan_sisa_checkpoint(*, direktori: str | os.PathLike[str] | None = None,
                              umur_min: float = UMUR_MIN_DETIK,
                              sekarang: float | None = None) -> int:
    """Hapus ``workbench-checkpoint-*`` yang lebih tua dari ``umur_min`` detik.

    Hanya direktori sungguhan (bukan symlink) berawalan itu, langsung di bawah
    direktori sementara, milik pengguna yang sama. Mengembalikan jumlah yang
    dihapus. Tidak pernah melempar: kegagalan di sini tidak boleh menahan agent.
    """
    akar = Path(direktori) if direktori is not None else Path(tempfile.gettempdir())
    sekarang = time.time() if sekarang is None else sekarang
    uid = os.getuid() if hasattr(os, "getuid") else None
    dihapus = 0
    try:
        entri = list(os.scandir(akar))
    except OSError:
        return 0
    for e in entri:
        if not e.name.startswith(AWALAN_CHECKPOINT):
            continue
        try:
            st = os.lstat(e.path)
        except OSError:
            continue
        # lstat: symlink (ke direktori mana pun) tidak pernah diikuti.
        if not stat.S_ISDIR(st.st_mode):
            continue
        if uid is not None and st.st_uid != uid:
            continue
        if sekarang - st.st_mtime < umur_min:
            continue
        try:
            # rmtree tidak mengikuti symlink di dalam pohon; ia menghapus tautannya.
            shutil.rmtree(e.path)
            dihapus += 1
        except OSError:
            continue
    return dihapus


__all__ = ["AWALAN_CHECKPOINT", "UMUR_MIN_DETIK", "bersihkan_sisa_checkpoint"]
