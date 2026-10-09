"""Probe sumber daya lokal untuk ``agent.resources``.

Hanya pustaka standar. Angka yang dihasilkan adalah perkiraan yang cukup untuk
preflight UI, bukan pengukuran presisi untuk penagihan.
"""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any


def collect_resources(*, workspace_hint: Path | None = None) -> dict[str, Any]:
    """Kumpulkan ringkasan CPU, memori, dan disk."""
    disk_path = workspace_hint or Path.home()
    try:
        usage = shutil.disk_usage(disk_path)
        disk = {
            "path": str(disk_path),
            "totalBytes": usage.total,
            "freeBytes": usage.free,
        }
    except OSError:
        disk = {"path": str(disk_path), "totalBytes": None, "freeBytes": None}

    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "cpu": {
            "count": os.cpu_count(),
        },
        "memory": _memory(),
        "disk": disk,
        "docker": _docker_available(),
        "interpreter": _interpreter(),
        "profiles": _profiles(),
    }


def _profiles() -> list[dict[str, Any]]:
    """Profil lingkungan yang terpasang di komputer ini (ADR-041 §5).

    Pairing tidak sama dengan "torch siap": mahasiswa Deep Learning memasang
    profilnya sendiri. Halaman Local Runner membaca daftar ini agar dapat
    mengatakannya. Hanya id, nama, dan status -- tanpa jalur.
    """
    from .env_setup import status_profil

    try:
        return status_profil()
    except (OSError, RuntimeError, ValueError):
        return []


def _interpreter() -> dict[str, Any]:
    """Apakah sel notebook akan dieksekusi oleh lingkungan praktikum.

    ``LocalPythonBackend`` menjalankan sel dengan ``exec`` di dalam proses
    agent, jadi pertanyaan ini menentukan apakah ``import pandas`` akan
    berhasil. Dahulu jawabannya tidak pernah sampai ke server, sehingga agent
    yang berjalan pada Python yang salah tampak sehat sepenuhnya.

    Jalur lengkapnya sengaja tidak dikirim: ia memuat nama folder rumah
    mahasiswa dan tidak menambah apa pun pada diagnosis. Untuk jalurnya,
    ``workbench-agent doctor`` mencetaknya secara lokal.
    """
    from .env_setup import (
        akar_agent_dari_modul,
        interpreter_praktikum_aktif,
        jalur_python_aplikasi,
        jalur_python_venv,
    )

    try:
        akar = akar_agent_dari_modul()
        aktif = interpreter_praktikum_aktif(akar)
        if jalur_python_venv(akar).is_file():
            jenis = "venv"
        elif jalur_python_aplikasi(akar).is_file():
            jenis = "embedded"
        else:
            jenis = "belum dipasang"
    except OSError:
        return {"isPracticum": None, "kind": None,
                "version": platform.python_version()}

    return {
        "isPracticum": aktif,
        "kind": jenis,
        "version": platform.python_version(),
    }


def _memory() -> dict[str, Any]:
    """Baca memori bila sistem menyediakannya; jika tidak, jujur bilang tidak tahu."""
    if hasattr(os, "sysconf"):
        try:
            page = os.sysconf("SC_PAGE_SIZE")
            phys = os.sysconf("SC_PHYS_PAGES")
            if isinstance(page, int) and isinstance(phys, int) and page > 0 and phys > 0:
                return {"totalBytes": page * phys}
        except (ValueError, OSError, AttributeError):
            pass
    return {"totalBytes": None}


def _docker_available() -> dict[str, Any]:
    """Apakah perintah ``docker`` ada di PATH -- bukan apakah daemon hidup.

    Memeriksa daemon membutuhkan pemanggilan yang bisa menggantung; itu urusan
    provider Docker (Component 05), bukan denyut nadi agent.
    """
    path = shutil.which("docker")
    return {"cliPresent": path is not None, "path": path}
