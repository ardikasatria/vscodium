"""`docker` pengganti di Terminal mata kuliah (ADR-053 §7).

Naskah praktikum menjalankan PostgreSQL di Docker dan memanggilnya dengan
``docker compose exec [-T] <layanan> psql ...``. Di Workbench klaster yang sama
dijalankan agent di laptop, jadi perintah itu diterjemahkan ke
``psql -h localhost -p <port> ...``. Peta ``<layanan> -> port`` dibaca dari
``docker.json`` di samping berkas ini, yang ditulis ulang agent setiap kali
layanan dinyalakan. Perintah lain diteruskan ke Docker asli bila terpasang.

Berkas ini disalin apa adanya ke ``.workbench/shell/bin/docker_shim.py`` dan
dijalankan dengan Python venv profil: hanya pustaka standar, tanpa impor dari
paket agent, dan tanpa nama layanan atau port di dalamnya.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

ALAT = ("psql", "pg_dump", "pg_restore", "pg_isready")

_COMPOSE_NILAI = {"-f", "--file", "-p", "--project-name", "--project-directory",
                  "--env-file", "--profile", "--ansi", "--progress", "--parallel"}
_EXEC_NILAI = {"-u", "--user", "-w", "--workdir", "-e", "--env", "--index"}
_EXEC_BENDERA = {"-T", "--no-TTY", "--no-tty", "-i", "--interactive", "-t", "--tty",
                 "-d", "--detach", "--privileged"}
#: Opsi alat PostgreSQL yang membawa nilai: nilainya tidak diperiksa sebagai -h/-p.
_ALAT_NILAI = {"-c", "--command", "-d", "--dbname", "-f", "--file", "-U", "--username",
               "-v", "--set", "--variable", "-o", "--output", "-F", "--field-separator",
               "-R", "--record-separator", "-P", "--pset", "-n", "--schema", "-N",
               "--exclude-schema", "-L", "--log-file"}
_ALAT_KONEKSI = {"-h", "--host", "-p", "--port"}
_COMPOSE_TANPA_DOCKER = {"up", "down", "ps", "start", "stop", "restart", "logs"}
#: Diset untuk Docker asli: pengganti lain yang terpanggil darinya tidak meneruskan lagi.
_PENANDA = "WORKBENCH_DOCKER_SHIM"


class Terjemahan:
    """Hasil penerjemahan argumen ``docker``."""

    def __init__(self, argv: list[str] | None = None, pesan: str | None = None) -> None:
        self.argv = argv
        self.pesan = pesan


def _nilai_opsi(token: str, nama: set[str]) -> tuple[bool, bool]:
    """(cocok, nilai_menyatu) untuk token opsi seperti ``-f x`` atau ``--file=x``."""
    if token in nama:
        return True, False
    if token.startswith("--") and token.split("=", 1)[0] in nama:
        return True, True
    return False, False


def _bendera_gabungan(token: str) -> bool:
    return len(token) > 2 and token[0] == "-" and token[1] != "-" \
        and all(f"-{c}" in _EXEC_BENDERA for c in token[1:])


def pisah_exec(argv: Sequence[str]) -> tuple[str, list[str]] | None:
    """``compose [opsi] exec [opsi] <layanan> <perintah...>`` -> (layanan, perintah)."""
    if not argv or argv[0] != "compose":
        return None
    i, n = 1, len(argv)
    while i < n and argv[i].startswith("-"):
        cocok, menyatu = _nilai_opsi(argv[i], _COMPOSE_NILAI)
        i += 2 if cocok and not menyatu else 1
    if i >= n or argv[i] != "exec":
        return None
    i += 1
    while i < n and argv[i].startswith("-"):
        if argv[i] == "--":
            i += 1
            break
        cocok, menyatu = _nilai_opsi(argv[i], _EXEC_NILAI)
        if cocok:
            i += 1 if menyatu else 2
        elif argv[i] in _EXEC_BENDERA or _bendera_gabungan(argv[i]) or argv[i].startswith("--"):
            i += 1
        else:
            return None
    if i + 1 >= n:
        return None
    return argv[i], list(argv[i + 1:])


def _buang_koneksi(args: Sequence[str]) -> list[str]:
    hasil: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in _ALAT_KONEKSI:
            i += 2
            continue
        if a.startswith(("--host=", "--port=")) or (
                len(a) > 2 and a[:2] in ("-h", "-p") and not a.startswith("--")):
            i += 1
            continue
        hasil.append(a)
        if a in _ALAT_NILAI and i + 1 < len(args):
            hasil.append(args[i + 1])
            i += 1
        i += 1
    return hasil


def _berkas_kontainer(alat: str, args: Sequence[str]) -> str | None:
    """``psql -f /jalur`` yang hanya ada di dalam kontainer naskah."""
    if alat != "psql":
        return None
    for i, a in enumerate(args):
        nilai = None
        if a in ("-f", "--file") and i + 1 < len(args):
            nilai = args[i + 1]
        elif a.startswith("--file="):
            nilai = a.split("=", 1)[1]
        elif a.startswith("-f") and len(a) > 2 and not a.startswith("--"):
            nilai = a[2:]
        if nilai and nilai.startswith("/") and not Path(nilai).exists():
            return nilai
    return None


def terjemahkan(argv: Sequence[str], peta: Mapping[str, Mapping[str, object]]) -> Terjemahan | None:
    """Terjemahan untuk perintah yang dikenal; ``None`` berarti teruskan ke Docker asli."""
    pisah = pisah_exec(argv)
    if pisah is None:
        return None
    layanan, perintah = pisah
    info = peta.get(layanan)
    if not isinstance(info, Mapping):
        return None
    alat, sisa = perintah[0], perintah[1:]
    if alat not in ALAT:
        return Terjemahan(pesan=(
            f"Workbench tidak menjalankan kontainer '{layanan}', jadi '{alat}' tidak tersedia.\n"
            f"Perintah PostgreSQL ({', '.join(ALAT)}) dapat dijalankan langsung di Terminal ini."))
    berkas = _berkas_kontainer(alat, sisa)
    if berkas:
        return Terjemahan(pesan=(
            f"Berkas {berkas} ada di dalam kontainer naskah, bukan di laptop.\n"
            "Di Workbench, pakai tombol Muat data ke basis data sumber di halaman modul,\n"
            "atau jalankan berkas dari folder mata kuliah (psql -f sql/...)."))
    return Terjemahan(argv=[alat, "-h", "localhost", "-p", str(info["port"]),
                            *_buang_koneksi(sisa)])


def _baca_peta(dir_shim: Path) -> dict[str, dict[str, object]]:
    try:
        data = json.loads((dir_shim / "docker.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict) and "port" in v} \
        if isinstance(data, dict) else {}


def _jalur_nyata(p: str | Path) -> str:
    return os.path.normcase(os.path.realpath(p))


def docker_asli(dir_shim: Path, path: str | None = None) -> str | None:
    """``docker`` di PATH selain pengganti ini."""
    if os.environ.get(_PENANDA):
        return None
    sendiri = _jalur_nyata(dir_shim)
    jalur = [p for p in (path if path is not None else os.environ.get("PATH", "")).split(os.pathsep)
             if p and _jalur_nyata(p) != sendiri]
    ketemu = shutil.which("docker", path=os.pathsep.join(jalur)) if jalur else None
    if ketemu is None or _jalur_nyata(Path(ketemu).parent) == sendiri:
        return None
    return ketemu


def _panduan(argv: Sequence[str], peta: Mapping[str, Mapping[str, object]]) -> str:
    daftar = ", ".join(f"{k} -> localhost:{v['port']}" for k, v in sorted(peta.items()))
    peta_teks = f"\nLayanan naskah di laptop ini: {daftar}." if daftar else ""
    if len(argv) > 1 and argv[0] == "compose" and \
            any(a in _COMPOSE_TANPA_DOCKER for a in argv[1:] if not a.startswith("-")):
        return ("Di Workbench, basis data praktikum tidak memakai Docker. Nyalakan, hentikan, dan\n"
                "lihat statusnya dari panel Basis data praktikum di halaman modul." + peta_teks)
    return ("Docker tidak terpasang, dan Workbench tidak menjalankan kontainer.\n"
            "Yang diterjemahkan hanya `docker compose exec <layanan> psql|pg_dump|pg_restore|"
            "pg_isready ...`." + peta_teks)


def main(argv: Sequence[str] | None = None, dir_shim: Path | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dir_shim = dir_shim or Path(__file__).resolve().parent
    peta = _baca_peta(dir_shim)
    t = terjemahkan(argv, peta)
    if t is not None and t.pesan:
        print(t.pesan, file=sys.stderr)
        return 1
    if t is not None and t.argv:
        alat = shutil.which(t.argv[0])
        if alat is None:
            print(f"{t.argv[0]} tidak ditemukan. Buka Terminal dari menu Terminal DSWorkbench "
                  "atau jalankan .workbench/shell/terminal.*.", file=sys.stderr)
            return 1
        try:
            return subprocess.call([alat, *t.argv[1:]])
        except KeyboardInterrupt:
            return 130
    asli = docker_asli(dir_shim)
    if asli:
        try:
            return subprocess.call([asli, *argv], env={**os.environ, _PENANDA: "1"})
        except KeyboardInterrupt:
            return 130
    print(_panduan(argv, peta), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
