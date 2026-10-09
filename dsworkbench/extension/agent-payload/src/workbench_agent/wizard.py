"""Wizard Local Runner: satu program, satu menu.

Sebelumnya paket unduhan berisi enam skrip -- pasangkan, jalankan, periksa,
masing-masing untuk dua sistem operasi. Mahasiswa harus tahu mana yang harus
diklik dan dalam urutan apa, dan urutan itu hanya ada di berkas BACA-SAYA yang
jarang dibuka.

Wizard ini menggantikannya: satu titik masuk, menu bernomor, dan setiap pilihan
menjelaskan akibatnya sebelum dijalankan.

**Mengapa menu teks, bukan jendela.** Jendela memerlukan ``tkinter``, dan
tkinter tidak selalu ikut Python -- ia tidak ada pada Python Homebrew di macOS
maupun pada banyak pemasangan Linux. Program yang hanya berjalan bila sebuah
pustaka kebetulan ada bukan program yang dapat diandalkan untuk satu kelas.
Menu teks berjalan di mana pun Python berjalan.

Bila ``tkinter`` memang ada, :func:`jalankan` akan memakai jendela lebih dulu
dan jatuh ke menu teks bila gagal -- lihat :func:`_coba_gui`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import AGENT_VERSION, AgentConfig
from .env_setup import (
    akar_agent_dari_modul,
    ensure_env,
    interpreter_praktikum_aktif,
    jalankan_di_interpreter_praktikum,
    paket_hilang,
    muat_profil,
    python_praktikum,
    reset_env,
    status_profil,
)
from .errors import AgentError
from .state import StateStore

GARIS = "─" * 58


def _url_bawaan() -> str:
    """URL control plane yang tertulis saat paket diunduh."""
    return (os.environ.get("WORKBENCH_URL") or "").strip() or "https://sditera.cloud"


def _config(url: str) -> AgentConfig:
    return AgentConfig(control_plane_url=url)


# ----------------------------------------------------------------------
# Keadaan
# ----------------------------------------------------------------------


def keadaan(url: str) -> dict[str, object]:
    """Ringkasan keadaan yang dapat diperiksa tanpa jaringan."""
    akar = akar_agent_dari_modul()
    py = python_praktikum(akar)
    sudah_venv = py is not None

    hilang: list[str] = []
    if py is not None:
        hilang = paket_hilang(py)

    terpasang = False
    nama = None
    try:
        state = StateStore(_config(url).resolved_state_dir()).load()
        terpasang = state is not None
        nama = getattr(state, "name", None) if state else None
    except Exception:  # noqa: BLE001 - keadaan lokal, bukan alur kritis
        terpasang = False

    return {
        "akar": akar,
        "url": url,
        "venvAda": sudah_venv,
        "paketHilang": hilang,
        "lingkunganSiap": sudah_venv and not hilang,
        "terpasangkan": terpasang,
        "namaPerangkat": nama,
        # True bila Python dipasang sendiri oleh peluncur, bukan milik komputer.
        "embedded": bool(py) and ".python" in str(py),
        # Profil selain bawaan: opsional, hanya untuk mata kuliah yang memakainya.
        "profilLain": [b for b in _status_profil_aman() if not b["default"]],
    }


def _status_profil_aman() -> list[dict[str, object]]:
    try:
        return status_profil()
    except (OSError, RuntimeError, ValueError):
        return []


def _ringkas(k: dict[str, object]) -> str:
    baris = []
    baris.append(f"  Control plane   : {k['url']}")
    baris.append(f"  Folder agent    : {k['akar']}")
    if k["lingkunganSiap"]:
        jenis = "Python bawaan aplikasi" if k.get("embedded") else "Python komputer ini"
        baris.append(f"  Lingkungan      : siap ({jenis})")
    elif not k["venvAda"]:
        baris.append("  Lingkungan      : belum disiapkan (pilih 1)")
    else:
        hilang = ", ".join(k["paketHilang"])  # type: ignore[arg-type]
        baris.append(f"  Lingkungan      : RUSAK — gagal diimpor: {hilang}")
    if k["terpasangkan"]:
        nama = k["namaPerangkat"] or "perangkat ini"
        baris.append(f"  Pairing         : sudah ({nama})")
    else:
        baris.append("  Pairing         : belum (pilih 2)")
    for lain in k.get("profilLain") or ():  # type: ignore[union-attr]
        if lain["ready"]:
            keadaan = "siap"
        elif lain["installed"]:
            keadaan = "perlu dipasang ulang (pilih 7)"
        else:
            keadaan = "belum dipasang (opsional, pilih 7)"
        label = str(lain["name"]).split(" (", 1)[0][:15]
        baris.append(f"  {label:<15} : {keadaan}")
    return "\n".join(baris)


# ----------------------------------------------------------------------
# Tindakan
# ----------------------------------------------------------------------


def periksa(url: str) -> int:
    from .cli import main as cli_main

    k = keadaan(url)
    print(GARIS)
    print("PERIKSA")
    print(GARIS)
    print(_ringkas(k))
    print()

    if not k["lingkunganSiap"]:
        print("Lingkungan praktikum belum siap.")
        if k["paketHilang"]:
            print("Paket yang gagal diimpor:", ", ".join(k["paketHilang"]))  # type: ignore[arg-type]
            print("Sebab terlazimnya: tidak ada wheel yang cocok untuk versi")
            print(f"Python Anda ({sys.version.split()[0]}). Pilih 1 untuk mencoba lagi.")
        print()

    print("Memeriksa koneksi ke control plane…")
    return cli_main(["--url", url, "doctor"])


def siapkan(url: str, *, ulang: bool = False) -> int:
    print(GARIS)
    print("PASANG ULANG LINGKUNGAN" if ulang else "SIAPKAN LINGKUNGAN")
    print(GARIS)
    if ulang:
        print("Lingkungan lama dihapus, lalu dipasang ulang dari awal.")
        print("Unduhan pip memakai cache, jadi ini lebih cepat daripada")
        print("pemasangan pertama.")
        print()
    print("Memasang pustaka praktikum ke folder agent. Sekali saja, butuh")
    print("jaringan, dan dapat berlangsung beberapa menit.")
    print()
    try:
        py = reset_env() if ulang else ensure_env()
    except (AgentError, RuntimeError, FileNotFoundError, OSError) as exc:
        print()
        print("GAGAL menyiapkan lingkungan:")
        print(f"  {exc}")
        return 1
    print()
    print(f"Selesai. Interpreter praktikum: {py}")
    return 0


def siapkan_profil_lain(url: str, pilihan: str | None = None,
                        setuju: str | None = None) -> int:
    """Pasang lingkungan mata kuliah selain Data Science, atas permintaan.

    Tidak pernah dijalankan otomatis: unduhannya ratusan MB, dan mahasiswa
    yang tidak mengambil mata kuliah itu tidak memerlukannya (ADR-041 §5).
    """
    _ = url
    print(GARIS)
    print("LINGKUNGAN MATA KULIAH LAIN")
    print(GARIS)
    print("Setiap mata kuliah memakai lingkungan Python-nya sendiri. Pasang")
    print("hanya yang Anda ambil; lingkungan Data Science tidak berubah.")
    print()
    semua = muat_profil()
    lain = [b for b in _status_profil_aman() if not b["default"]]
    if not lain:
        print("Agent ini tidak mengenal lingkungan lain.")
        return 0
    for i, b in enumerate(lain, start=1):
        keadaan = "siap" if b["ready"] else (
            "perlu dipasang ulang" if b["installed"] else "belum dipasang")
        print(f"  {i}. {b['name']}  [{keadaan}]")
    print("  0. Kembali")
    print()
    try:
        pilih = pilihan if pilihan is not None else input("Pilih: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 1
    if pilih in ("", "0"):
        return 0
    if not pilih.isdigit() or not 1 <= int(pilih) <= len(lain):
        print("Pilihan tidak dikenal.")
        return 1
    b = lain[int(pilih) - 1]
    p = semua[str(b["id"])]

    ulang = bool(b["ready"])
    print()
    print(f"{p.nama}")
    if ulang:
        print("Lingkungan ini sudah siap. Memasang ulang menghapusnya lalu")
        print("memasangnya dari awal (unduhan memakai cache pip).")
    else:
        unduh = p.perkiraan.get("downloadMiB")
        disk = p.perkiraan.get("diskGiB")
        menit = p.perkiraan.get("minutes")
        print("Pemasangan ini terpisah dari lingkungan Data Science, karena")
        print("pustakanya besar dan tidak dipakai mata kuliah lain.")
        if unduh or disk or menit:
            print(f"  perkiraan unduhan : ±{unduh} MB")
            print(f"  ruang disk        : ±{disk} GiB")
            print(f"  waktu             : {menit} menit, tergantung jaringan")
        print("Pastikan laptop tersambung ke jaringan yang stabil.")
    print()
    try:
        jawab = setuju if setuju is not None else input(
            ("Pasang ulang" if ulang else "Pasang sekarang") + "? [y/N]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 1
    if jawab.lower() not in ("y", "ya", "yes"):
        print("Dibatalkan. Tidak ada yang dipasang.")
        return 0
    try:
        py = (reset_env(profile=p.id) if ulang else ensure_env(profile=p.id))
    except (AgentError, RuntimeError, FileNotFoundError, OSError) as exc:
        print()
        print(f"GAGAL menyiapkan lingkungan {p.nama}:")
        print(f"  {exc}")
        return 1
    print()
    print(f"Selesai. Lingkungan {p.nama} siap: {py}")
    print("Bila agent sedang berjalan, tidak perlu dimulai ulang: kernel")
    print("berikutnya untuk mata kuliah ini langsung memakainya.")
    return 0


def pasangkan(url: str, kode: str | None = None) -> int:
    from .cli import main as cli_main

    print(GARIS)
    print("PASANGKAN")
    print(GARIS)
    print("Kode pairing ada di halaman Local Runner Workbench.")
    print("Tekan 'Mulai pairing' di sana; kode berlaku 5 menit.")
    print()
    if kode is None:
        try:
            kode = input("Kode pairing: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
    if not kode:
        print("Kode kosong. Tidak ada yang dikerjakan.")
        return 1
    return cli_main(["--url", url, "pair", kode])


def _pastikan_lingkungan(url: str) -> bool:
    """Siapkan lingkungan bila belum siap, sebelum tindakan yang membutuhkannya.

    Menjalankan agent di atas lingkungan yang belum lengkap akan berhasil --
    sampai sel pertama yang mengimpor pandas. Lebih baik berhenti di sini,
    ketika sebabnya masih jelas.
    """
    k = keadaan(url)
    if k["lingkunganSiap"]:
        return True
    print("Lingkungan praktikum belum siap; menyiapkannya lebih dulu.")
    print()
    return siapkan(url) == 0


def jalankan_agent(url: str) -> int:
    from .cli import main as cli_main

    print(GARIS)
    print("JALANKAN")
    print(GARIS)
    if not _pastikan_lingkungan(url):
        print()
        print("Agent tidak dijalankan karena lingkungan belum siap.")
        print("Pilih 4 (Periksa) untuk melihat sebabnya.")
        return 1

    print("Agent berjalan selama jendela ini terbuka.")
    print("Tekan Ctrl+C untuk berhenti.")
    print()

    # Sel notebook dieksekusi di dalam proses agent (LocalPythonBackend memakai
    # `exec`, bukan subprocess). Jadi agent wajib berjalan pada interpreter yang
    # memegang pustaka praktikum -- kalau tidak, `import pandas` di sel pertama
    # akan mencari pustaka di tempat yang tidak pernah dipasangi.
    try:
        kode = jalankan_di_interpreter_praktikum(argv=["--url", url, "run"])
        if kode is not None:
            print()
            print("Agent dihentikan.")
            return kode
        return cli_main(["--url", url, "run"])
    except KeyboardInterrupt:
        print()
        print("Agent dihentikan.")
        return 0


# ----------------------------------------------------------------------
# Pintasan desktop
# ----------------------------------------------------------------------

ASSETS = "assets"
ICNS = "workbench-agent.icns"
ICO = "workbench-agent.ico"

_INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Data Science Workbench</string>
  <key>CFBundleDisplayName</key><string>Data Science Workbench</string>
  <key>CFBundleIdentifier</key><string>id.ac.itera.sd.workbench.agent</string>
  <key>CFBundleVersion</key><string>{versi}</string>
  <key>CFBundleShortVersionString</key><string>{versi}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>run</string>
  <key>CFBundleIconFile</key><string>workbench-agent</string>
</dict>
</plist>
"""

_RUN_APP = """#!/bin/bash
# Pintasan Local Runner -- Data Science Workbench.
# Dibuat oleh wizard agent; aman dihapus kapan saja.
cd {folder} || exit 1
exec {peluncur}
"""

_PINTASAN_BAT = """@echo off
rem Pintasan Local Runner -- Data Science Workbench.
rem Dibuat oleh wizard agent; aman dihapus kapan saja.
cd /d {folder}
call {peluncur}
"""


def _folder_desktop() -> Path | None:
    """Folder Desktop pengguna, bila memang ada.

    Nama foldernya tidak diterjemahkan oleh macOS maupun Windows -- yang
    diterjemahkan hanya tampilannya di Finder/Explorer. Bila toh tidak ada,
    pintasan tidak dipaksakan: berkas yang mendarat di tempat tak terduga
    lebih membingungkan daripada tidak ada pintasan.
    """
    kandidat = Path.home() / "Desktop"
    return kandidat if kandidat.is_dir() else None


def _pintasan_macos(akar: Path, desktop: Path) -> Path:
    """Bundel .app supaya pintasan membawa logo, bukan ikon skrip polos.

    Sebuah berkas .command tidak dapat membawa ikon sendiri tanpa perkakas
    tambahan; bundel .app bisa, dan ia hanya berupa struktur folder biasa.
    """
    peluncur = akar / "DSWorkbench.command"
    bundel = desktop / "DSWorkbench.app"
    macos = bundel / "Contents" / "MacOS"
    resources = bundel / "Contents" / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    (bundel / "Contents" / "Info.plist").write_text(
        _INFO_PLIST.format(versi=AGENT_VERSION), encoding="utf-8")

    run = macos / "run"
    run.write_text(
        _RUN_APP.format(folder=f'"{akar}"', peluncur=f'"{peluncur}"'),
        encoding="utf-8")
    run.chmod(0o755)

    ikon = akar / ASSETS / ICNS
    if ikon.is_file():
        shutil.copyfile(ikon, resources / "workbench-agent.icns")
    return bundel


def _pintasan_windows(akar: Path, desktop: Path) -> Path:
    """`.lnk` lewat PowerShell supaya ikonnya ikut; `.bat` sebagai cadangan.

    Berkas .bat tidak dapat membawa ikon sendiri. Shortcut .lnk bisa, dan
    PowerShell ada pada setiap Windows 10/11 -- tetapi bila ia diblokir
    kebijakan, pintasan biasa tetap lebih baik daripada tidak ada.
    """
    peluncur = akar / "DSWorkbench.bat"
    ikon = akar / ASSETS / ICO
    lnk = desktop / "DSWorkbench.lnk"

    skrip = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        "$s.TargetPath = '{target}';"
        "$s.WorkingDirectory = '{cwd}';"
        "$s.Description = 'Local Runner Data Science Workbench';"
        "{ikon}"
        "$s.Save()"
    ).format(
        lnk=str(lnk).replace("'", "''"),
        target=str(peluncur).replace("'", "''"),
        cwd=str(akar).replace("'", "''"),
        ikon=(f"$s.IconLocation = '{str(ikon).replace(chr(39), chr(39) * 2)}';"
              if ikon.is_file() else ""),
    )
    try:
        hasil = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", skrip],
            capture_output=True, text=True, check=False, timeout=60,
        )
        if hasil.returncode == 0 and lnk.is_file():
            return lnk
    except (OSError, subprocess.SubprocessError):
        pass

    cadangan = desktop / "DSWorkbench.bat"
    cadangan.write_text(
        _PINTASAN_BAT.format(folder=f'"{akar}"', peluncur=f'"{peluncur}"'),
        encoding="utf-8")
    return cadangan


def buat_pintasan(url: str) -> int:
    print(GARIS)
    print("PINTASAN DESKTOP")
    print(GARIS)

    desktop = _folder_desktop()
    if desktop is None:
        print("Folder Desktop tidak ditemukan, jadi pintasan tidak dibuat.")
        print(f"Jalankan agent langsung dari: {akar_agent_dari_modul()}")
        return 1

    akar = akar_agent_dari_modul()
    windows = os.name == "nt"
    peluncur = akar / ("DSWorkbench.bat" if windows
                       else "DSWorkbench.command")
    if not peluncur.is_file():
        print(f"Peluncur tidak ditemukan: {peluncur}")
        print("Unduh ulang paket agent dari halaman Local Runner.")
        return 1

    try:
        hasil = (_pintasan_windows(akar, desktop) if windows
                 else _pintasan_macos(akar, desktop))
    except OSError as exc:
        print(f"Pintasan tidak dapat dibuat: {exc}")
        return 1

    print(f"Pintasan dibuat: {hasil}")
    ikon = akar / ASSETS / (ICO if windows else ICNS)
    if not ikon.is_file():
        print("Catatan: berkas ikon tidak ada, jadi pintasan memakai ikon bawaan.")
    print()
    print("Mulai sekarang cukup klik dua kali pintasan itu dari Desktop.")
    if not windows:
        print()
        print("macOS mungkin menanyakan izin sekali pada pembukaan pertama.")
        print("Lihat bagian Gatekeeper pada BACA-SAYA.txt bila itu terjadi.")
    return 0


# ----------------------------------------------------------------------
# Menu
# ----------------------------------------------------------------------

PILIHAN = (
    ("1", "Siapkan lingkungan  — pasang pustaka praktikum (sekali saja)"),
    ("2", "Pasangkan           — hubungkan komputer ini dengan akun Anda"),
    ("3", "Jalankan            — nyalakan agent, biarkan jendela terbuka"),
    ("4", "Periksa             — diagnosis bila ada yang tidak jalan"),
    ("5", "Pintasan Desktop    — supaya tidak perlu mencari folder ini lagi"),
    ("6", "Pasang ulang        — hapus lingkungan lama, pasang dari awal"),
    ("7", "Lingkungan lain     — mis. Deep Learning; hanya bila Anda mengambilnya"),
    ("0", "Keluar"),
)


def _menu(url: str) -> int:
    while True:
        k = keadaan(url)
        print()
        print(GARIS)
        print(f"  Local Runner — Data Science Workbench   (agent {AGENT_VERSION})")
        print(GARIS)
        print(_ringkas(k))
        print(GARIS)
        for nomor, teks in PILIHAN:
            print(f"  {nomor}. {teks}")
        print(GARIS)

        try:
            pilih = input("Pilih [1-7, 0 keluar]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if pilih == "0":
            return 0
        if pilih == "1":
            siapkan(url)
        elif pilih == "2":
            pasangkan(url)
        elif pilih == "3":
            return jalankan_agent(url)
        elif pilih == "4":
            periksa(url)
        elif pilih == "5":
            buat_pintasan(url)
        elif pilih == "6":
            siapkan(url, ulang=True)
        elif pilih == "7":
            siapkan_profil_lain(url)
        else:
            print("Pilihan tidak dikenal.")
        try:
            input("\nTekan Enter untuk kembali ke menu.")
        except (EOFError, KeyboardInterrupt):
            return 0


def _coba_gui(url: str) -> bool:
    """Buka jendela bila tkinter benar-benar dapat dipakai.

    Mengembalikan False bila tidak, sehingga pemanggil jatuh ke menu teks.
    Kegagalan di sini bukan kesalahan: pada banyak pemasangan Python, tkinter
    memang tidak ikut.
    """
    try:
        import tkinter  # noqa: F401
    except Exception:  # noqa: BLE001 - ketiadaan tkinter bukan kondisi luar biasa
        return False
    try:
        from .wizard_gui import buka  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return False
    try:
        buka(url)
        return True
    except Exception:  # noqa: BLE001
        return False


def jalankan(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    url = _url_bawaan()
    if "--url" in args:
        i = args.index("--url")
        if i + 1 < len(args):
            url = args[i + 1]

    if "--teks" not in args and _coba_gui(url):
        return 0
    return _menu(url)


def main() -> int:
    return jalankan()


if __name__ == "__main__":
    raise SystemExit(main())
