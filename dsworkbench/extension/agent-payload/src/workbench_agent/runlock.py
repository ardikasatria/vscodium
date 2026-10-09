"""Satu ``agent run`` per direktori keadaan (satu perangkat).

Dua agent dengan kredensial perangkat yang sama berebut operasi dari antrean
yang sama: sel notebook bergantian jatuh ke kernel yang tidak dikenal agent
lain ("Kernel … tidak ditemukan") lalu ke kernel yang kehilangan variabel.
Itu terjadi bila jendela DSWorkbench mati mendadak dan meninggalkan agent
anaknya, atau bila agent ZIP dan aplikasi desktop dijalankan bersamaan
(ditemukan di uji DL-05). Kunci ini menutup celah yang tidak dilihat kunci
jendela aplikasi (``app/core/lock.py``, threat T18).

Kunci OS (``flock`` / ``msvcrt.locking``) lepas sendiri bila proses mati,
jadi tidak ada berkas basi yang harus dihapus tangan.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Kode keluar ``agent run`` bila agent lain sudah memegang perangkat ini.
EXIT_SUDAH_BERJALAN = 3

NAMA_BERKAS = "agent-run.lock"


class RunLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def pemegang(self) -> str:
        """PID yang tercatat di berkas kunci (untuk pesan), bila ada."""
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None


__all__ = ["EXIT_SUDAH_BERJALAN", "NAMA_BERKAS", "RunLock"]
