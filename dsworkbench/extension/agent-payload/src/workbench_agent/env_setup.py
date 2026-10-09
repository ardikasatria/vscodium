"""Siapkan lingkungan Python praktikum per RuntimeProfile.

Tanpa ini agent bisa pairing dan Run cell, lalu gagal pada ``import numpy``
karena interpreter yang menjalankan agent adalah Python sistem kosong.

Satu profil, satu lingkungan (ADR-041). Profil bawaan ``python-data-science``
memakai tata letak lama -- ``.venv`` atau ``.python`` milik aplikasi -- dan
berkas requirements yang byte-identik dengan ``requirements.txt`` lama,
sehingga mahasiswa Data Wrangling yang sudah terpasang tidak memasang ulang
apa pun. Profil lain (mis. ``python-deep-learning``) selalu mendapat venv
sendiri ``.venv-<profil>/`` dan hanya dipasang bila diminta dengan eksplisit.
Tidak ada venv gabungan.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MARKER = ".workbench-reqs.sha256"

#: Profil yang dipasang ``ensure-env`` tanpa argumen dan yang dijalankan
#: di dalam proses agent. Peluncur lama tidak pernah menyebut profil.
PROFIL_BAWAAN = "python-data-science"

#: Manifest profil di dalam folder agent (ZIP) maupun ``agent/local-runner``.
MANIFEST_REL = Path("requirements") / "profiles.json"

#: Bentuk id profil yang diterima dari luar (browser, argumen CLI).
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

#: Satu-satunya indeks paket selain PyPI yang boleh dipakai, dan hanya lewat
#: ``--index-url --no-deps`` untuk paket yang disebut manifest.
INDEKS_DIIZINKAN = ("https://download.pytorch.org/whl/",)


class ProfilTidakDikenal(ValueError):
    """Id profil tidak ada di manifest agent -- ditolak, bukan jatuh ke DW."""


class ProfilBelumSiap(RuntimeError):
    """Profil dikenal tetapi lingkungannya belum dipasang atau berubah."""

    def __init__(self, profil_id: str, pesan: str):
        super().__init__(pesan)
        self.profil_id = profil_id


def _jalur_marker(akar: Path) -> Path:
    """Penanda diletakkan di sebelah interpreter yang dipakai.

    Dengan begitu menghapus `.venv` juga menghapus penandanya, dan lingkungan
    tidak pernah mengaku siap atas interpreter yang sudah tidak ada.
    """
    if (akar / ".venv").is_dir():
        return akar / ".venv" / MARKER
    return akar / ".python" / MARKER

#: Paket yang wajib terimpor setelah instalasi profil bawaan.
#: ``certifi`` ikut karena agent sendiri memakainya untuk TLS (ADR-041 §3);
#: ia sudah terpasang lewat requirements DW, jadi tidak memicu unduhan.
PAKET_WAJIB = ("numpy", "pandas", "matplotlib", "sklearn", "yaml", "certifi")


#: Akar lingkungan praktikum bila kode agent dijalankan dari tempat lain
#: (ADR-072: payload Local Runner di dalam ekstensi IDE memakai ``.venv*``,
#: ``requirements/``, dan penanda milik pemasangan DSWorkbench yang ada).
VAR_AKAR_LINGKUNGAN = "WORKBENCH_AGENT_ENV_ROOT"


def akar_agent_dari_modul() -> Path:
    """Folder ZIP ``workbench-agent/`` (induk dari ``src/``).

    ``WORKBENCH_AGENT_ENV_ROOT`` mengalihkannya ke direktori lain yang sudah ada;
    nilai kosong, relatif, atau bukan direktori diabaikan agar salah atur tidak
    membuat agent mencari lingkungan di tempat sembarang.
    """
    alih = os.environ.get(VAR_AKAR_LINGKUNGAN, "").strip()
    if alih:
        calon = Path(alih)
        if calon.is_absolute() and calon.is_dir():
            return calon.resolve()
    # …/workbench-agent/src/workbench_agent/env_setup.py → parents[2]
    return Path(__file__).resolve().parents[2]


def jalur_python_venv(akar: Path) -> Path:
    if os.name == "nt":
        return akar / ".venv" / "Scripts" / "python.exe"
    return akar / ".venv" / "bin" / "python"


def jalur_python_aplikasi(akar: Path) -> Path:
    """Python milik aplikasi, dipasang sendiri oleh peluncur.

    Sejak peluncur Windows memakai pemasang resmi (bukan lagi distribusi
    *embeddable*), Python ini lengkap: ada ``include/pyconfig.h``, ``libs/``,
    ``venv``, dan ``ensurepip``. Pustaka praktikum tetap dipasang langsung ke
    dalamnya, bukan ke venv terpisah -- ia sudah terkurung di foldernya sendiri
    dan tidak menyentuh Python lain di komputer mahasiswa, jadi satu lapisan
    lagi tidak menambah apa pun.
    """
    return akar / ".python" / ("python.exe" if os.name == "nt" else "bin/python")


#: Nama lama; dipertahankan agar pemanggil luar tidak patah.
jalur_python_embedded = jalur_python_aplikasi


def python_praktikum(akar: Path) -> Path | None:
    """Interpreter yang memegang pustaka praktikum, apa pun caranya dipasang."""
    venv = jalur_python_venv(akar)
    if venv.is_file():
        return venv
    milik_aplikasi = jalur_python_aplikasi(akar)
    if milik_aplikasi.is_file():
        return milik_aplikasi
    return None


def python_lengkap(python: Path | str) -> bool:
    """Apakah interpreter ini membawa header untuk membangun paket.

    Ditanyakan kepada interpreternya sendiri lewat ``sysconfig``, bukan ditebak
    dari tata letak folder: ``include/`` berada di tempat yang berbeda antara
    Windows dan POSIX, dan menebaknya akan menjawab salah di salah satu.

    Dipakai untuk mengenali sisa distribusi *embeddable* dari versi aplikasi
    terdahulu. Distribusi itu lolos setiap pemeriksaan versi -- ia memang Python
    3.12 64-bit -- tetapi tidak membawa ``pyconfig.h``, tidak membawa ``venv``,
    dan berkas ``._pth`` miliknya membuat ``PYTHONPATH`` diabaikan.
    """
    kode = (
        "import os,sys,sysconfig;"
        "sys.exit(0 if os.path.isfile("
        "os.path.join(sysconfig.get_path('include'),'pyconfig.h')) else 1)"
    )
    try:
        return subprocess.run(
            [str(python), "-c", kode],
            capture_output=True, text=True, check=False,
        ).returncode == 0
    except OSError:
        return False


def python_aplikasi_perlu_diganti(akar: Path) -> bool:
    """True bila ``.python`` ada tetapi merupakan sisa pemasangan tidak lengkap."""
    py = jalur_python_aplikasi(akar)
    return py.is_file() and not python_lengkap(py)


def _memakai_python_aplikasi(akar: Path) -> bool:
    """True bila pustaka harus dipasang ke Python milik aplikasi.

    Ditentukan dari keberadaannya, bukan dari tebakan: bila peluncur sempat
    memasangnya, komputer ini memang tidak punya Python yang cocok sendiri.
    """
    return (not jalur_python_venv(akar).is_file()
            and jalur_python_aplikasi(akar).is_file())


def _berkas_requirements(req: Path) -> list[Path]:
    """Berkas requirements beserta setiap ``-r`` yang dirujuknya, berurutan.

    Rujukan hanya boleh menunjuk berkas di folder yang sama dengan ``req``:
    berkas di ZIP adalah seluruh dunia yang perlu diketahui agent.
    """
    urutan: list[Path] = []
    dasar = req.parent.resolve()

    def _kunjungi(berkas: Path) -> None:
        if berkas in urutan:
            return
        urutan.append(berkas)
        for baris in berkas.read_text(encoding="utf-8").splitlines():
            mentah = baris.split("#", 1)[0].strip()
            for awalan in ("-r ", "--requirement ", "--requirement="):
                if mentah.startswith(awalan):
                    nama = mentah[len(awalan):].strip()
                    rujukan = (berkas.parent / nama).resolve()
                    if rujukan.parent != dasar or not rujukan.is_file():
                        raise FileNotFoundError(
                            f"{berkas.name} merujuk '{nama}', yang tidak ada di "
                            f"{dasar}. Unduh ulang paket agent dari halaman "
                            "Local Runner."
                        )
                    _kunjungi(rujukan)

    _kunjungi(req.resolve())
    return urutan


def _hash_requirements(req: Path) -> str:
    """Sidik isi requirements yang benar-benar dipasang.

    Berkas tanpa ``-r`` di-hash persis seperti dahulu (SHA-256 isinya), jadi
    penanda profil DW yang sudah ada tetap cocok. Berkas dengan rujukan
    di-hash bersama rujukannya: mengubah ``_agent-base.txt`` memicu
    pemasangan ulang pada profil yang memakainya.
    """
    berkas = _berkas_requirements(req)
    if len(berkas) == 1:
        return hashlib.sha256(berkas[0].read_bytes()).hexdigest()
    h = hashlib.sha256()
    for b in berkas:
        h.update(b.name.encode("utf-8") + b"\0" + b.read_bytes() + b"\0")
    return h.hexdigest()


def _venv_selaras(akar: Path, req: Path) -> bool:
    """Apakah lingkungan benar-benar siap dipakai.

    Penanda saja tidak cukup. ``pip install`` dapat keluar dengan kode 0
    sementara sebuah paket gagal dibangun atau terpasang setengah -- numpy
    paling sering, karena ia memerlukan wheel yang cocok dengan versi Python
    dan arsitektur mesin. Bila kesiapan hanya diukur dari penanda, lingkungan
    yang rusak akan dinyatakan "siap" selamanya dan kegagalannya baru muncul
    saat mahasiswa menekan Run.

    Karena itu impor diperiksa juga. Ia memakan waktu beberapa ratus milidetik
    dan hanya berjalan saat penyiapan; itu harga yang pantas untuk tidak
    berbohong tentang kesiapan.
    """
    py = python_praktikum(akar)
    marker = _jalur_marker(akar)
    if py is None or not marker.is_file():
        return False
    try:
        if marker.read_text(encoding="utf-8").strip() != _hash_requirements(req):
            return False
    except OSError:
        return False
    return not paket_hilang(py)


def _jalankan(argv: list[str], *, cwd: Path) -> None:
    """Jalankan perintah penyiapan, dan bawa sebabnya bila gagal.

    Keluaran pip diteruskan ke layar **baris demi baris selagi berjalan** supaya
    mahasiswa (dan panel "Lingkungan" aplikasi DSWorkbench, yang membaca pipa
    ini) melihat kemajuannya; pemasangan beberapa menit tanpa satu baris pun
    tampak seperti macet. Baris terakhirnya juga ikut ke dalam pesan kesalahan
    -- di layar yang sudah penuh, justru baris itulah yang hilang.
    """
    ekor: list[str] = []
    try:
        proses = subprocess.Popen(
            argv, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace",
        )
    except OSError as exc:
        raise RuntimeError(f"perintah tidak dapat dijalankan: {exc}") from exc
    try:
        assert proses.stdout is not None
        for baris in proses.stdout:
            print(baris, end="", flush=True)
            if baris.strip():
                ekor.append(baris.rstrip("\r\n"))
                del ekor[:-6]
    except BaseException:
        # Ctrl+C / pembatalan: jangan tinggalkan pip berjalan tanpa induk.
        proses.kill()
        raise
    finally:
        if proses.stdout is not None:
            proses.stdout.close()
        kode = proses.wait()
    if kode != 0:
        raise RuntimeError(
            f"perintah gagal (kode {kode}): {' '.join(argv)}"
            + ("\n  " + "\n  ".join(ekor) if ekor else "")
        )


# ----------------------------------------------------------------------
# Profil
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Profil:
    """Satu RuntimeProfile yang dapat dipasang Local Runner."""

    id: str
    nama: str
    requirements: Path
    tata_letak_lama: bool = False
    impor_wajib: tuple[str, ...] = ()
    boleh_bangun_sumber: bool = True
    platform_didukung: tuple[str, ...] = ()
    indeks_cpu_linux: str | None = None
    paket_indeks_cpu: tuple[str, ...] = ()
    perkiraan: dict[str, Any] = field(default_factory=dict)
    #: Layanan yang ikut dipasang profil ini (``services.json``, ADR-053).
    layanan: tuple[str, ...] = ()

    @property
    def wajib(self) -> tuple[str, ...]:
        # Profil bawaan membaca PAKET_WAJIB saat dipakai, bukan saat dimuat,
        # agar tetap satu sumber kebenaran bagi pemanggil lama.
        return PAKET_WAJIB if self.tata_letak_lama else self.impor_wajib


def _profil_lama(akar: Path) -> Profil:
    """Profil bawaan untuk folder agent tanpa manifest (salinan lama)."""
    return Profil(
        id=PROFIL_BAWAAN,
        nama="Data Science",
        requirements=akar / "requirements.txt",
        tata_letak_lama=True,
    )


def muat_profil(akar: Path | None = None) -> dict[str, Profil]:
    """Profil yang dikenal agent ini, dari ``requirements/profiles.json``.

    Tanpa manifest, satu-satunya profil adalah profil bawaan dengan
    ``requirements.txt`` di akar -- perilaku agent sebelum ADR-041.
    """
    akar = (akar or akar_agent_dari_modul()).resolve()
    manifest = akar / MANIFEST_REL
    if not manifest.is_file():
        return {PROFIL_BAWAAN: _profil_lama(akar)}

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"Manifest profil {manifest} tidak dapat dibaca ({exc}). "
            "Unduh ulang paket agent dari halaman Local Runner."
        ) from exc

    hasil: dict[str, Profil] = {}
    for pid, isi in (data.get("profiles") or {}).items():
        if not isinstance(pid, str) or not _SLUG.match(pid) or not isinstance(isi, dict):
            continue
        indeks = isi.get("linuxCpuIndex") or {}
        url = indeks.get("url") if isinstance(indeks, dict) else None
        if url is not None and not str(url).startswith(INDEKS_DIIZINKAN):
            raise RuntimeError(
                f"Profil {pid} menunjuk indeks paket yang tidak diizinkan: {url}"
            )
        nama_req = str(isi.get("requirements") or f"{pid}.txt")
        req = (manifest.parent / nama_req).resolve()
        if req.parent != manifest.parent.resolve():
            raise RuntimeError(f"Profil {pid}: berkas requirements di luar folder requirements/")
        lama = bool(isi.get("legacyLayout")) and pid == PROFIL_BAWAAN
        hasil[pid] = Profil(
            id=pid,
            nama=str(isi.get("name") or pid),
            requirements=req,
            tata_letak_lama=lama,
            impor_wajib=tuple(str(x) for x in isi.get("requiredImports") or ()),
            boleh_bangun_sumber=bool(isi.get("allowSourceBuild", True)),
            platform_didukung=tuple(str(x) for x in isi.get("supportedPlatforms") or ()),
            indeks_cpu_linux=str(url) if url else None,
            paket_indeks_cpu=tuple(
                str(x) for x in (indeks.get("packages") or ()) if isinstance(indeks, dict)
            ),
            perkiraan=dict(isi.get("estimate") or {}),
            layanan=tuple(str(x) for x in isi.get("services") or ()),
        )
    if PROFIL_BAWAAN not in hasil:
        hasil[PROFIL_BAWAAN] = _profil_lama(akar)
    return hasil


def profil(akar: Path | None = None, profil_id: str | None = None) -> Profil:
    """Profil ``profil_id`` (bawaan bila ``None``), atau tolak dengan jelas."""
    pid = PROFIL_BAWAAN if profil_id is None else profil_id
    if not isinstance(pid, str) or not _SLUG.match(pid):
        raise ProfilTidakDikenal(f"Nama profil lingkungan tidak sah: {str(pid)[:64]!r}")
    semua = muat_profil(akar)
    if pid not in semua:
        raise ProfilTidakDikenal(
            f"Profil lingkungan '{pid}' tidak dikenal agent ini. "
            f"Yang tersedia: {', '.join(sorted(semua))}. Bila mata kuliah Anda "
            "baru ditambahkan, unduh ulang agent dari halaman Local Runner."
        )
    return semua[pid]


def jalur_venv_profil(akar: Path, profil_id: str) -> Path:
    """Folder venv profil non-bawaan: ``.venv-<profil>``."""
    return akar / f".venv-{profil_id}"


def _jalur_python_di(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def python_profil(akar: Path, p: Profil) -> Path | None:
    """Interpreter profil bila ada (belum tentu siap)."""
    if p.tata_letak_lama:
        return python_praktikum(akar)
    py = _jalur_python_di(jalur_venv_profil(akar, p.id))
    return py if py.is_file() else None


def _jalur_marker_profil(akar: Path, p: Profil) -> Path:
    if p.tata_letak_lama:
        return _jalur_marker(akar)
    return jalur_venv_profil(akar, p.id) / MARKER


def _penanda_cocok(akar: Path, p: Profil) -> bool:
    """Interpreter ada dan penandanya cocok dengan isi requirements -- murah."""
    if python_profil(akar, p) is None:
        return False
    marker = _jalur_marker_profil(akar, p)
    try:
        return (marker.is_file() and p.requirements.is_file()
                and marker.read_text(encoding="utf-8").strip()
                == _hash_requirements(p.requirements))
    except (OSError, FileNotFoundError):
        return False


def python_siap(akar: Path | None, profil_id: str | None) -> Path:
    """Interpreter profil yang siap menjalankan kernel, atau ``ProfilBelumSiap``.

    Hanya memeriksa penanda (cepat); pemeriksaan impor penuh berjalan saat
    pemasangan dan pada ``doctor``.
    """
    akar = (akar or akar_agent_dari_modul()).resolve()
    p = profil(akar, profil_id)
    py = python_profil(akar, p)
    if py is not None and _penanda_cocok(akar, p):
        return py
    if py is None:
        keadaan = "belum dipasang di komputer ini"
    else:
        keadaan = "berubah sejak dipasang (agent diperbarui)"
    raise ProfilBelumSiap(p.id, (
        f"Lingkungan {p.nama} ({p.id}) {keadaan}. Pasangkan agent saja tidak "
        "cukup: setiap mata kuliah memakai pustakanya sendiri. Buka aplikasi "
        "DSWorkbench di komputer Anda, pilih menu 7 (Lingkungan mata kuliah "
        "lain), lalu pilih profil ini. Setelah selesai, jalankan agent lagi "
        "dan tekan Run."
    ))


def status_profil(akar: Path | None = None, *, periksa_impor: bool = False) -> list[dict[str, Any]]:
    """Ringkasan setiap profil untuk wizard, doctor, dan ``agent.resources``.

    Tanpa ``periksa_impor`` jawabannya murah (penanda saja) dan tidak memuat
    jalur apa pun -- aman dikirim ke server.
    """
    akar = (akar or akar_agent_dari_modul()).resolve()
    hasil: list[dict[str, Any]] = []
    for pid, p in sorted(muat_profil(akar).items(),
                         key=lambda kv: (kv[0] != PROFIL_BAWAAN, kv[0])):
        py = python_profil(akar, p)
        siap = _penanda_cocok(akar, p)
        baris: dict[str, Any] = {
            "id": pid,
            "name": p.nama,
            "default": pid == PROFIL_BAWAAN,
            "installed": py is not None,
            "ready": siap,
        }
        if periksa_impor and py is not None:
            hilang = paket_hilang(py, wajib=p.wajib)
            baris["missing"] = hilang
            baris["ready"] = siap and not hilang
        hasil.append(baris)
    return hasil


def kunci_platform() -> str:
    """``win32-amd64``, ``darwin-arm64``, ``linux-x86_64``, ``linux-aarch64``, …"""
    mesin = (platform.machine() or "").lower()
    if sys.platform.startswith("linux") and mesin == "arm64":
        mesin = "aarch64"
    if sys.platform == "win32" and mesin == "x86_64":
        mesin = "amd64"
    plat = "linux" if sys.platform.startswith("linux") else sys.platform
    return f"{plat}-{mesin}"


def _nama_platform(kunci: str) -> str:
    return {
        "darwin-x86_64": "macOS Intel",
        "darwin-arm64": "macOS Apple Silicon",
        "win32-amd64": "Windows 64-bit",
        "win32-arm64": "Windows ARM",
        "linux-x86_64": "Linux 64-bit",
        "linux-aarch64": "Linux ARM64",
    }.get(kunci, kunci)


def ensure_env(
    akar: Path | None = None,
    *,
    quiet: bool = False,
    profile: str | None = None,
    state_dir: Path | None = None,
) -> Path:
    """Siapkan lingkungan satu profil dan kembalikan interpreternya.

    Tanpa ``profile``: profil bawaan (Data Science), persis seperti sebelum
    ADR-041. Profil lain hanya dipasang bila disebut -- peluncur tidak pernah
    melakukannya diam-diam. Profil yang menyatakan ``services`` juga memasang
    biner layanannya (ADR-053) -- di sini, atas tindakan lokal, bukan dari
    peramban.
    """
    akar = (akar or akar_agent_dari_modul()).resolve()
    p = profil(akar, profile)
    if p.tata_letak_lama:
        req = p.requirements
        if not req.is_file() and (akar / "requirements.txt").is_file():
            req = akar / "requirements.txt"
        return _ensure_tata_letak_lama(akar, req, quiet=quiet)
    py = _ensure_profil_terpisah(akar, p, quiet=quiet)
    if p.layanan:
        ensure_layanan(akar, p, state_dir=state_dir, quiet=quiet)
    return py


def ensure_layanan(akar: Path, p: "Profil", *, state_dir: Path | None = None,
                   quiet: bool = False, unduh: Any = None) -> list[Path]:
    """Pasang biner setiap layanan profil (idempoten; unduh hanya bila perlu)."""
    from . import pgservice
    from .config import default_state_dir

    defs = pgservice.muat_layanan(akar)
    state = state_dir or default_state_dir()
    if unduh is None:
        from .client import download_url

        def unduh(url: str, dest: Path) -> None:  # noqa: E306
            download_url(url, dest, timeout=300.0)

    hasil = []
    for sid in p.layanan:
        defn = defs.get(sid)
        if defn is None:
            raise RuntimeError(f"Profil {p.id} menyebut layanan '{sid}' yang tidak ada di services.json.")
        try:
            hasil.append(pgservice.pasang(
                defn, state, kunci_platform(), unduh,
                lapor=(lambda s: None) if quiet else (lambda s: print(f"  {s}"))))
        except pgservice.LayananGagal as exc:
            raise RuntimeError(exc.pesan) from exc
        if not quiet:
            print(f"layanan {defn.nama} siap.")
    return hasil


def _ensure_tata_letak_lama(akar: Path, req: Path, *, quiet: bool) -> Path:
    """Profil bawaan: ``.venv``, atau langsung ke ``.python`` milik aplikasi."""
    if not req.is_file():
        raise FileNotFoundError(
            f"requirements.txt tidak ada di {akar}. "
            "Unduh ulang paket agent dari halaman Local Runner."
        )

    py = jalur_python_venv(akar)
    if _venv_selaras(akar, req):
        if not quiet:
            print(f"lingkungan praktikum siap: {py}")
        return py

    if not quiet:
        print("Menyiapkan lingkungan Python praktikum (sekali saja; butuh jaringan)…")
        print(f"  requirements: {req}")

    if _memakai_python_aplikasi(akar):
        # Komputer ini tidak punya Python yang cocok; peluncur sudah memasang
        # Python milik aplikasi. Pustaka dipasang langsung ke dalamnya.
        py = jalur_python_aplikasi(akar)
        if not python_lengkap(py):
            # Peluncur biasanya sudah menggantinya sebelum sampai ke sini;
            # jalur ini tersisa untuk yang menjalankan agent dengan cara lain.
            raise RuntimeError(
                "Python di folder aplikasi adalah sisa pemasangan lama yang "
                "tidak lengkap.\n"
                f"  Hapus folder ini lalu buka aplikasi lagi: {akar / '.python'}\n"
                "  Peluncur akan memasang penggantinya secara otomatis."
            )
    else:
        venv_dir = akar / ".venv"
        py = jalur_python_venv(akar)
        _siapkan_venv(venv_dir, sys.executable, akar, quiet=quiet)
        if not py.is_file():
            raise RuntimeError(
                f"Lingkungan Python gagal dibuat di {venv_dir}. "
                "Pilih menu 6 untuk menghapusnya dan memasang ulang dari awal."
            )

    _jalankan([str(py), "-m", "pip", "install", "--upgrade", "pip"], cwd=akar)
    _pasang_paket(py, req, akar, quiet=quiet)

    # Verifikasi lebih dulu, penanda kemudian. Urutan sebaliknya membuat
    # instalasi yang gagal tercatat sebagai berhasil, dan percobaan berikutnya
    # melewatinya begitu saja.
    hilang = paket_hilang(py)
    if hilang:
        raise RuntimeError(
            "Instalasi selesai tetapi paket berikut gagal diimpor: "
            + ", ".join(hilang)
            + ". Jalankan Periksa untuk melihat sebabnya; biasanya tidak ada "
            "wheel yang cocok untuk versi Python Anda."
        )

    marker = _jalur_marker(akar)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(_hash_requirements(req) + "\n", encoding="utf-8")

    if not quiet:
        print(f"lingkungan praktikum siap: {py}")
    return py


#: Versi Python terendah yang wheel-nya ada untuk seluruh pin praktikum
#: (numpy 2.5 memerlukan 3.12). Selaraskan dengan ``VERSI_MIN`` desktop dan
#: ``:uji`` pada peluncur ZIP Windows.
PYTHON_MIN = (3, 12)


def _versi_python(python: Path | str) -> tuple[int, int] | None:
    if Path(python) == Path(sys.executable):
        return sys.version_info[:2]
    try:
        hasil = subprocess.run(
            [str(python), "-c", "import sys;print(*sys.version_info[:2])"],
            capture_output=True, text=True, check=False,
        )
        mayor, minor = hasil.stdout.split()[:2]
        return int(mayor), int(minor)
    except (OSError, ValueError):
        return None


def _versi_venv(venv_dir: Path) -> tuple[int, int] | None:
    """Versi Python pembuat venv, dibaca dari ``pyvenv.cfg`` tanpa menjalankannya."""
    try:
        teks = (venv_dir / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return None
    for baris in teks.splitlines():
        kunci, _, nilai = baris.partition("=")
        if kunci.strip() in ("version_info", "version"):
            try:
                mayor, minor = nilai.strip().split(".")[:2]
                return int(mayor), int(minor)
            except ValueError:
                return None
    return None


def _pesan_python_lama(versi: tuple[int, int]) -> str:
    minimum = ".".join(map(str, PYTHON_MIN))
    return (
        f"Lingkungan praktikum memerlukan Python {minimum} atau lebih baru, 64-bit; "
        f"yang ditemukan Python {versi[0]}.{versi[1]}. Pustaka praktikum (mis. "
        f"numpy) tidak menyediakan paket untuk versi itu.\n"
        "  Aplikasi desktop: buka Pemeriksaan sistem lalu tekan “Pasang Python”.\n"
        "  Paket ZIP: unduh ulang dari halaman Local Runner; peluncurnya memasang "
        f"Python {minimum} sendiri bila perlu."
    )


def _siapkan_venv(venv_dir: Path, pembuat: Path | str, akar: Path, *, quiet: bool) -> None:
    """Buat ``venv_dir`` dari ``pembuat``; venv dari Python terlalu lama dibuat ulang."""
    lama = _versi_venv(venv_dir)
    if lama is not None and lama < PYTHON_MIN:
        if not quiet:
            print(f"  lingkungan lama dibuat dengan Python {lama[0]}.{lama[1]}; dibuat ulang…")
        _hapus_direktori(venv_dir)
    py = _jalur_python_di(venv_dir)
    if venv_dir.exists() and not py.is_file():
        # Sisa venv yang gagal atau setengah jadi. `python -m venv` atas
        # direktori seperti ini gagal dengan pesan yang menyesatkan.
        if not quiet:
            print("  lingkungan lama tidak utuh; dibuat ulang…")
        _hapus_direktori(venv_dir)
    if py.is_file():
        return
    versi = _versi_python(pembuat)
    if versi is not None and versi < PYTHON_MIN:
        raise RuntimeError(_pesan_python_lama(versi))
    _jalankan([str(pembuat), "-m", "venv", str(venv_dir)], cwd=akar)


def _python_dasar(akar: Path) -> str:
    """Interpreter untuk membuat venv profil baru.

    Python milik aplikasi dipakai bila ada dan lengkap (komputer ini memang
    tidak punya Python yang cocok sendiri); selain itu interpreter yang sedang
    berjalan. Venv dari venv tetap bersandar pada Python dasarnya.
    """
    aplikasi = jalur_python_aplikasi(akar)
    if aplikasi.is_file() and python_lengkap(aplikasi):
        return str(aplikasi)
    return sys.executable


def _pin_dari(req: Path, nama_paket: tuple[str, ...]) -> list[str]:
    """Baris ``nama==versi`` untuk ``nama_paket``, dicari di req dan rujukannya."""
    dicari = {n.lower() for n in nama_paket}
    pin: dict[str, str] = {}
    for berkas in _berkas_requirements(req):
        for baris in berkas.read_text(encoding="utf-8").splitlines():
            mentah = baris.split("#", 1)[0].strip()
            if "==" not in mentah or mentah.startswith("-"):
                continue
            nama = mentah.split("==", 1)[0].strip().lower()
            if nama in dicari:
                pin[nama] = mentah
    hilang = dicari - set(pin)
    if hilang:
        raise RuntimeError(
            f"{req.name} tidak mematok {', '.join(sorted(hilang))}; "
            "unduh ulang paket agent."
        )
    return [pin[n.lower()] for n in nama_paket]


def perintah_pasang(py: Path | str, p: Profil, *, linux: bool) -> list[list[str]]:
    """Urutan perintah pip untuk profil non-bawaan, tanpa menjalankannya.

    Di Linux, paket yang disebut ``linuxCpuIndex`` dipasang lebih dulu dari
    indeks CPU dengan ``--no-deps`` -- wheel PyPI Linux untuk torch menarik
    paket CUDA berukuran GB. ``--extra-index-url`` tidak pernah dipakai.
    """
    perintah: list[list[str]] = [
        [str(py), "-m", "pip", "install", "--upgrade", "pip"],
    ]
    if linux and p.indeks_cpu_linux and p.paket_indeks_cpu:
        perintah.append([
            str(py), "-m", "pip", "install", "--no-deps", "--only-binary=:all:",
            "--index-url", p.indeks_cpu_linux,
            *_pin_dari(p.requirements, p.paket_indeks_cpu),
        ])
    perintah.append([str(py), "-m", "pip", "install", "-r", str(p.requirements),
                     "--only-binary=:all:"])
    return perintah


def _ensure_profil_terpisah(akar: Path, p: Profil, *, quiet: bool) -> Path:
    """Profil non-bawaan: venv sendiri ``.venv-<profil>``, penanda sendiri."""
    req = p.requirements
    if not req.is_file():
        raise FileNotFoundError(
            f"Berkas {req.name} untuk profil {p.id} tidak ada. "
            "Unduh ulang paket agent dari halaman Local Runner."
        )
    venv_dir = jalur_venv_profil(akar, p.id)
    py = _jalur_python_di(venv_dir)
    if _penanda_cocok(akar, p) and not paket_hilang(py, wajib=p.wajib):
        if not quiet:
            print(f"lingkungan {p.nama} siap: {py}")
        return py

    kunci = kunci_platform()
    if p.platform_didukung and kunci not in p.platform_didukung:
        raise RuntimeError(
            f"Lingkungan {p.nama} tidak dapat dipasang di {_nama_platform(kunci)}: "
            "pustaka yang dibutuhkannya tidak menyediakan paket siap-pasang "
            "untuk komputer ini. Didukung: "
            + ", ".join(_nama_platform(k) for k in p.platform_didukung)
            + ". Hubungi asisten praktikum untuk komputer pengganti."
        )

    perlu_gib = p.perkiraan.get("diskGiB")
    if isinstance(perlu_gib, (int, float)) and perlu_gib > 0:
        try:
            bebas = shutil.disk_usage(akar).free
        except OSError:
            bebas = None
        if bebas is not None and bebas < perlu_gib * 1024**3:
            raise RuntimeError(
                f"Ruang disk tidak cukup untuk lingkungan {p.nama}: tersedia "
                f"{bebas / 1024**3:.1f} GiB, dibutuhkan sekitar {perlu_gib} GiB. "
                "Kosongkan ruang disk lalu coba lagi."
            )

    if not quiet:
        print(f"Menyiapkan lingkungan {p.nama} (sekali saja; butuh jaringan)…")
        unduh = p.perkiraan.get("downloadMiB")
        menit = p.perkiraan.get("minutes")
        if unduh or menit:
            print(f"  perkiraan: unduhan ±{unduh} MB, {menit} menit")
        print(f"  requirements: {req}")
        print(f"  lokasi      : {venv_dir}")

    _siapkan_venv(venv_dir, _python_dasar(akar), akar, quiet=quiet)
    if not py.is_file():
        raise RuntimeError(
            f"Lingkungan Python gagal dibuat di {venv_dir}. Hapus folder itu "
            "lalu coba lagi."
        )
    # Penanda lama dibuang lebih dulu: pemasangan yang terputus di tengah
    # tidak boleh meninggalkan lingkungan yang mengaku siap.
    marker = _jalur_marker_profil(akar, p)
    if marker.is_file():
        marker.unlink()

    linux = sys.platform.startswith("linux")
    for argv in perintah_pasang(py, p, linux=linux):
        try:
            _jalankan(argv, cwd=akar)
        except RuntimeError as exc:
            if p.boleh_bangun_sumber and "-r" in argv:
                _pasang_paket(py, req, akar, quiet=quiet)
                continue
            raise RuntimeError(
                f"Pemasangan lingkungan {p.nama} gagal. Sebab terlazim: tidak ada "
                "paket siap-pasang untuk versi Python atau jenis komputer ini "
                f"(Python {platform.python_version()}, {_nama_platform(kunci)}); "
                "profil ini memerlukan Python 3.12 atau lebih baru, 64-bit. "
                "Pembangunan dari sumber sengaja tidak dicoba untuk profil ini.\n"
                f"  sebab: {exc}"
            ) from exc

    hilang = paket_hilang(py, wajib=p.wajib)
    if hilang:
        raise RuntimeError(
            "Instalasi selesai tetapi paket berikut gagal diimpor: "
            + ", ".join(hilang)
            + ". Jalankan Periksa untuk melihat sebabnya."
        )
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(_hash_requirements(req) + "\n", encoding="utf-8")
    if not quiet:
        print(f"lingkungan {p.nama} siap: {py}")
    return py


def _pasang_paket(py: Path, req: Path, akar: Path, *, quiet: bool) -> None:
    """Pasang pustaka praktikum: wheel lebih dulu, sumber hanya bila terpaksa.

    ``--only-binary=:all:`` membuat pip menolak membangun dari sumber. Itu
    disengaja. Di komputer mahasiswa tidak ada compiler, sehingga kompilasi
    hampir selalu berakhir pada ``fatal error C1083: Cannot open include file:
    'pyconfig.h'`` -- setelah beberapa menit menunggu, dengan pesan yang tidak
    dapat ditindaklanjuti siapa pun. Gagal cepat dengan sebab yang jelas jauh
    lebih berguna.

    Menolak sumber sama sekali akan memutus platform yang memang tidak memiliki
    wheel, jadi percobaan kedua dilakukan tanpa pembatasan -- dengan peringatan
    bahwa ia bisa lama dan mungkin memerlukan perkakas kompilasi.
    """
    dasar = [str(py), "-m", "pip", "install", "-r", str(req)]
    try:
        _jalankan(dasar + ["--only-binary=:all:"], cwd=akar)
        return
    except RuntimeError as exc:
        if not quiet:
            print()
            print("Sebagian pustaka tidak tersedia sebagai paket siap-pasang")
            print("untuk Python ini. Mencoba sekali lagi dengan membangun dari")
            print("sumber; ini dapat berlangsung lama dan mungkin memerlukan")
            print("perkakas kompilasi.")
            print(f"  sebab: {exc}")
            print()
    _jalankan(dasar, cwd=akar)


def _hapus_direktori(jalur: Path) -> None:
    """Hapus direktori lingkungan, dan katakan bila tidak bisa.

    Di Windows sebuah berkas yang sedang dipakai menolak dihapus; kegagalan itu
    perlu terdengar, bukan tertelan, karena pemasangan ulang di atasnya akan
    gagal dengan sebab yang jauh lebih membingungkan.
    """
    import shutil

    if not jalur.exists():
        return
    shutil.rmtree(jalur, ignore_errors=True)
    if jalur.exists():
        raise RuntimeError(
            f"Folder {jalur.name} tidak dapat dihapus. Tutup semua jendela "
            "aplikasi ini yang masih berjalan, lalu coba lagi."
        )


def reset_env(
    akar: Path | None = None,
    *,
    quiet: bool = False,
    profile: str | None = None,
) -> Path:
    """Hapus lingkungan lama satu profil, lalu pasang ulang dari awal.

    Unduhan pip tetap memakai cache-nya sendiri, sehingga pemasangan ulang
    jauh lebih cepat daripada yang pertama. Profil lain tidak disentuh.
    """
    akar = (akar or akar_agent_dari_modul()).resolve()
    p = profil(akar, profile)
    if not p.tata_letak_lama:
        if not quiet:
            print(f"Menghapus lingkungan {p.nama}…")
        _hapus_direktori(jalur_venv_profil(akar, p.id))
        return ensure_env(akar, quiet=quiet, profile=p.id)
    if not quiet:
        print("Menghapus lingkungan lama…")
    _hapus_direktori(akar / ".venv")
    if python_aplikasi_perlu_diganti(akar):
        # Sisa distribusi *embeddable* dari versi aplikasi terdahulu. Ia harus
        # pergi seluruhnya: peluncur memasang penggantinya yang lengkap saat
        # aplikasi dibuka berikutnya.
        if not quiet:
            print("Python lama tidak lengkap; dihapus agar dipasang ulang.")
        _hapus_direktori(akar / ".python")
    else:
        # Python aplikasi yang lengkap tidak ikut dihapus: mengunduhnya ulang
        # tanpa sebab hanya memperlama, dan ia bukan bagian yang rusak.
        penanda = akar / ".python" / MARKER
        if penanda.is_file():
            penanda.unlink()
    return ensure_env(akar, quiet=quiet)


def paket_hilang(
    python: Path | str | None = None,
    *,
    wajib: tuple[str, ...] | None = None,
) -> list[str]:
    """Daftar paket wajib yang gagal diimpor di interpreter ``python``.

    Tanpa ``wajib``: daftar profil bawaan (``PAKET_WAJIB``).
    """
    exe = str(python) if python else sys.executable
    hilang: list[str] = []
    for nama in (PAKET_WAJIB if wajib is None else wajib):
        kode = f"import {nama}"
        hasil = subprocess.run(
            [exe, "-c", kode],
            capture_output=True,
            text=True,
            check=False,
        )
        if hasil.returncode != 0:
            hilang.append(nama)
    return hilang


#: Penanda bahwa proses ini sudah merupakan hasil perpindahan interpreter.
#: Tanpa penanda, interpreter praktikum yang ternyata tidak dapat menjalankan
#: agent akan memanggil dirinya sendiri tanpa henti.
PENANDA_PINDAH = "WORKBENCH_AGENT_INTERPRETER_TETAP"


def interpreter_praktikum_aktif(akar: Path | None = None) -> bool:
    """True bila proses ini dijalankan oleh interpreter pemegang pustaka.

    Ini pertanyaan yang sesungguhnya, dan ia tidak sama dengan "apakah saya di
    dalam ``.venv``". Pada komputer tanpa Python, pustaka praktikum dipasang ke
    dalam Python embedded dan ``.venv`` tidak pernah ada; memeriksa ``.venv``
    saja akan menjawab "bukan" pada mesin yang justru sudah benar.
    """
    akar = (akar or akar_agent_dari_modul()).resolve()
    py = python_praktikum(akar)
    if py is None:
        return False
    try:
        return Path(sys.executable).resolve() == py.resolve()
    except OSError:
        return False


def sedang_di_venv_agent(akar: Path | None = None) -> bool:
    """Nama lama :func:`interpreter_praktikum_aktif`; dipertahankan sementara."""
    return interpreter_praktikum_aktif(akar)


def pindah_ke_interpreter_praktikum(
    akar: Path | None = None,
    *,
    argv: list[str] | None = None,
    modul: str = "workbench_agent.wizard",
    quiet: bool = False,
) -> None:
    """Jalankan ulang proses ini memakai interpreter praktikum, lalu keluar.

    Ini perbaikan pokok atas sel notebook yang berjalan di luar lingkungan yang
    sudah dipasang. :class:`LocalPythonBackend` mengeksekusi sel dengan ``exec``
    **di dalam proses agent** -- tidak ada subprocess, tidak ada pemilihan
    kernel. Interpreter yang menjalankan agent *adalah* kernel notebook.

    Sampai sekarang kebenaran itu hanya dijaga oleh peluncur: berkas ``.command``
    di macOS menukar ``$PY`` ke ``.venv`` sebelum memanggil wizard, sedangkan
    ``.bat`` di Windows tidak pernah melakukannya. Akibatnya sel mahasiswa
    berjalan pada Python sistem, dan bila Python sistem itu kebetulan memiliki
    numpy dan pandas -- lazim pada komputer yang memasang Anaconda -- tidak ada
    satu pun pesan kesalahan muncul; yang terjadi hanyalah sel berjalan atas
    pustaka yang salah.

    Menjaganya di peluncur berarti setiap cara lain untuk memulai agent tetap
    salah diam-diam. Karena itu penjagaan dipindahkan ke sini, ke dalam program
    yang tahu interpreter mana yang benar.

    Dipakai ``subprocess`` alih-alih ``os.execv``: di Windows ``execv``
    mengembalikan ``cmd`` ke prompt sementara proses penggantinya masih menulis
    ke konsol yang sama, dan keluarannya bertumpuk.
    """
    akar = (akar or akar_agent_dari_modul()).resolve()
    if interpreter_praktikum_aktif(akar):
        return
    if os.environ.get(PENANDA_PINDAH) == "1":
        # Sudah pernah berpindah dan masih belum cocok. Berhenti, jangan
        # berputar; sebabnya dilaporkan oleh pemanggil.
        return
    py = python_praktikum(akar)
    if py is None:
        return

    if not quiet:
        print(f"Berpindah ke interpreter praktikum: {py}")
        print()

    lingkungan = dict(os.environ)
    lingkungan[PENANDA_PINDAH] = "1"
    # Peluncur menyetel PYTHONPATH, tetapi agent juga dapat dimulai dengan cara
    # lain. Menyetelnya di sini membuat perpindahan berdiri sendiri.
    sumber = akar / "src"
    if sumber.is_dir():
        sebelumnya = lingkungan.get("PYTHONPATH", "")
        bagian = [str(sumber)] + [p for p in sebelumnya.split(os.pathsep) if p]
        lingkungan["PYTHONPATH"] = os.pathsep.join(bagian)

    perintah = [str(py), "-m", modul, *(argv or [])]
    try:
        hasil = subprocess.run(perintah, cwd=akar, env=lingkungan, check=False)
    except KeyboardInterrupt:
        raise SystemExit(0) from None
    except OSError as exc:
        print(
            f"Interpreter praktikum tidak dapat dijalankan ({exc}). "
            "Pilih menu 6 untuk memasang ulang lingkungan.",
            file=sys.stderr,
        )
        return
    raise SystemExit(hasil.returncode)


def jalankan_di_interpreter_praktikum(
    akar: Path | None = None,
    *,
    argv: list[str] | None = None,
    modul: str = "workbench_agent.cli",
    quiet: bool = False,
    stdin: int | None = None,
    stdout: int | None = None,
) -> int | None:
    """Jalankan satu perintah pada interpreter praktikum, lalu kembali.

    ``stdin``/``stdout``: fd yang diwariskan sebagai fd 0/1 proses anak. Dipakai
    ``run --stdio``: kanal protokol IDE sudah dipindah dari fd 0/1 proses ini,
    jadi harus diteruskan secara eksplisit. Tanpa keduanya perilakunya tetap.

    Berbeda dari :func:`pindah_ke_interpreter_praktikum`, pemanggil tetap hidup.
    Dipakai wizard agar Ctrl+C menghentikan agent dan mengembalikan menu, bukan
    menutup jendela.

    Mengembalikan ``None`` bila proses ini memang sudah memakai interpreter yang
    benar, sehingga pemanggil menjalankannya sendiri tanpa proses tambahan.
    """
    akar = (akar or akar_agent_dari_modul()).resolve()
    if interpreter_praktikum_aktif(akar):
        return None
    py = python_praktikum(akar)
    if py is None or os.environ.get(PENANDA_PINDAH) == "1":
        return None

    if not quiet:
        print(f"Menjalankan pada interpreter praktikum: {py}")
        print()

    lingkungan = dict(os.environ)
    lingkungan[PENANDA_PINDAH] = "1"
    sumber = akar / "src"
    if sumber.is_dir():
        sebelumnya = lingkungan.get("PYTHONPATH", "")
        bagian = [str(sumber)] + [p for p in sebelumnya.split(os.pathsep) if p]
        lingkungan["PYTHONPATH"] = os.pathsep.join(bagian)

    perintah = [str(py), "-m", modul, *(argv or [])]
    kanal = {}
    if stdin is not None:
        kanal["stdin"] = stdin
    if stdout is not None:
        kanal["stdout"] = stdout
    try:
        return subprocess.run(
            perintah, cwd=akar, env=lingkungan, check=False, **kanal
        ).returncode
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        print(
            f"Interpreter praktikum tidak dapat dijalankan ({exc}). "
            "Pilih menu 6 untuk memasang ulang lingkungan.",
            file=sys.stderr,
        )
        return 1
