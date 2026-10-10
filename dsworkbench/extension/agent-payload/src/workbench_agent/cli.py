"""Antarmuka baris perintah ``workbench-agent``.

    workbench-agent pair <kode> [--url URL] [--name NAMA]
    workbench-agent adopt [--replace] --url URL     (kredensial JSON dari stdin)
    workbench-agent run [--url URL] [--no-default-profile] [--stdio]
    workbench-agent once [--url URL]
    workbench-agent status
    workbench-agent doctor
    workbench-agent ensure-env [--profile PROFIL] [--reset]
    workbench-agent profiles
    workbench-agent unpair
    workbench-agent self-revoke [--github] [--device-id ID]
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path
from typing import Sequence

from .client import ControlPlaneClient, HttpError
from .config import AGENT_VERSION, AgentConfig
from .env_setup import (
    akar_agent_dari_modul,
    ensure_env,
    interpreter_praktikum_aktif,
    jalankan_di_interpreter_praktikum,
    paket_hilang,
    python_aplikasi_perlu_diganti,
    python_praktikum,
    reset_env,
    status_profil,
)
from .errors import AgentError, AuthenticationFailedError, NotPairedError, TransportError
from .resources import collect_resources
from .runlock import EXIT_SUDAH_BERJALAN, NAMA_BERKAS, RunLock
from .runner import Agent, akar_workspace, default_allowlist, load_state, pair_device, tetapkan_akun
from .sisa_temp import bersihkan_sisa_checkpoint
from .state import DeviceState, StateStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="workbench-agent",
        description="Local Runner Data Science Workbench",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {AGENT_VERSION}")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="direktori penyimpanan kredensial (bawaan: XDG atau ~/.workbench-agent)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="URL Control API (bawaan: http://127.0.0.1:8000)",
    )

    sub = parser.add_subparsers(dest="perintah", required=True)

    p_pair = sub.add_parser("pair", help="pasangkan komputer ini dengan kode pairing")
    p_pair.add_argument("code", help="kode pairing dari halaman Workbench")
    p_pair.add_argument("--name", default=None, help="nama perangkat")

    p_adopt = sub.add_parser(
        "adopt",
        help="terima kredensial perangkat hasil masuk aplikasi DSWorkbench (JSON dari stdin)",
    )
    p_adopt.add_argument(
        "--replace", action="store_true",
        help="ganti kredensial perangkat lain yang sudah tersimpan di komputer ini",
    )

    p_run = sub.add_parser("run", help="jalankan agent terus-menerus")
    p_run.add_argument(
        "--no-default-profile", action="store_true",
        help="jalankan tanpa lingkungan profil bawaan (mis. hanya Deep Learning); "
             "kernel profil bawaan ditolak",
    )
    p_run.add_argument(
        "--stdio", action="store_true",
        help="layani aplikasi DSWorkbench (IDE) lewat stdin/stdout, JSON per baris; "
             "teks untuk manusia pergi ke stderr",
    )
    sub.add_parser("once", help="hubungkan, kerjakan satu putaran poll, lalu keluar")
    sub.add_parser("status", help="tampilkan keadaan pairing lokal")
    sub.add_parser("doctor", help="periksa koneksi ke Control API dan sumber daya lokal")
    p_env = sub.add_parser(
        "ensure-env",
        help="siapkan lingkungan pustaka praktikum (bawaan: Data Science)",
    )
    p_env.add_argument(
        "--profile", default=None,
        help="profil lingkungan lain, mis. python-deep-learning; "
             "tanpa ini hanya profil bawaan yang dipasang",
    )
    p_env.add_argument("--reset", action="store_true",
                       help="hapus lingkungan profil itu lalu pasang ulang")
    sub.add_parser("profiles", help="tampilkan profil lingkungan dan keadaannya")
    sub.add_parser("unpair", help="hapus kredensial lokal (tidak mencabut di VM)")
    p_cabut = sub.add_parser(
        "self-revoke",
        help="cabut perangkat ini di server dengan kredensialnya sendiri, lalu hapus "
             "kredensial lokal (mode lab: menyapu sisa sesi sebelumnya)",
    )
    p_cabut.add_argument(
        "--github", action="store_true",
        help="cabut juga tautan GitHub akun itu dan hapus semua token GitHub di "
             "direktori keadaan",
    )
    p_cabut.add_argument(
        "--device-id", default=None,
        help="hanya bertindak bila kredensial lokal memang milik perangkat ini "
             "(sesi yang sedang ditutup); perangkat lain tidak disentuh",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)
    config = AgentConfig(
        control_plane_url=args.url or "http://127.0.0.1:8000",
        state_dir=args.state_dir,
    )

    try:
        if args.perintah == "pair":
            return _cmd_pair(config, args.code, args.name)
        if args.perintah == "adopt":
            if not args.url:
                print("gagal: adopt membutuhkan --url server Workbench.", file=sys.stderr)
                return 1
            return _cmd_adopt(config, replace=args.replace)
        if args.perintah == "run":
            if args.stdio:
                return _cmd_run(config, tanpa_bawaan=args.no_default_profile, stdio=True)
            return _cmd_run(config, tanpa_bawaan=args.no_default_profile)
        if args.perintah == "once":
            return _cmd_once(config)
        if args.perintah == "status":
            return _cmd_status(config)
        if args.perintah == "doctor":
            return _cmd_doctor(config)
        if args.perintah == "ensure-env":
            return _cmd_ensure_env(args.profile, reset=args.reset,
                                   state_dir=config.resolved_state_dir())
        if args.perintah == "profiles":
            return _cmd_profiles()
        if args.perintah == "unpair":
            return _cmd_unpair(config)
        if args.perintah == "self-revoke":
            return _cmd_self_revoke(config, github=args.github, hanya=args.device_id)
    except AuthenticationFailedError as exc:
        print(f"gagal: {exc}", file=sys.stderr)
        return 2
    except (AgentError, HttpError, TransportError, ValueError, OSError) as exc:
        print(f"gagal: {exc}", file=sys.stderr)
        return 1
    return 1


def _cmd_ensure_env(profil: str | None = None, *, reset: bool = False,
                    state_dir: Path | None = None) -> int:
    py = (reset_env(profile=profil) if reset else ensure_env(profile=profil, state_dir=state_dir))
    print(py)
    return 0


def _cmd_profiles() -> int:
    for baris in status_profil(periksa_impor=True):
        if baris["ready"]:
            keadaan = "siap"
        elif baris["installed"]:
            hilang = ", ".join(baris.get("missing") or []) or "penanda tidak cocok"
            keadaan = f"perlu dipasang ulang ({hilang})"
        else:
            keadaan = "belum dipasang"
        tanda = " (bawaan)" if baris["default"] else ""
        print(f"{baris['id']:<24} {keadaan}{tanda}")
    return 0


def _cmd_pair(config: AgentConfig, code: str, name: str | None) -> int:
    state = pair_device(config, code, name=name)
    print(f"terpasang: {state.device_id} ({state.name})")
    print(f"kredensial disimpan di {config.resolved_state_dir() / 'device.json'}")
    print("jalankan: workbench-agent run")
    return 0


#: Kode keluar ``adopt`` bila komputer ini sudah menyimpan kredensial perangkat
#: lain dan ``--replace`` tidak diberikan. Aplikasi menanyai pengguna dulu.
EXIT_SUDAH_DIPASANGKAN = 4

#: Batas masukan ``adopt`` (byte). Satu objek JSON kecil; lebih dari ini salah alamat.
_BATAS_ADOPT = 64 * 1024

_KUNCI_ADOPT = ("deviceId", "credential", "name", "os", "arch")


def _cabut_pendahulu(config: AgentConfig, lama: DeviceState, baru: DeviceState,
                     *, buat_klien=None) -> str:
    """Minta server mencabut perangkat ``lama`` yang baru saja digantikan ``baru``.

    Dipanggil ``adopt --replace`` SETELAH ``device.json`` baru tersimpan, jadi
    kegagalan apa pun di sini tidak membatalkan masuk. Kredensial lama hanya
    dikirim ke server yang menerbitkannya, dan tidak pernah dicetak.

    Hasil: ``dicabut`` | ``diabaikan`` (server tidak mencabut: bukan milik akun
    yang sama, sudah dicabut, atau tidak dikenal) | ``tidak-didukung`` (server
    belum punya rutenya) | ``lain-server`` | ``tak-terjangkau``.
    """
    if lama.control_plane_url.rstrip("/") != config.control_plane_url.rstrip("/"):
        return "lain-server"
    klien = (buat_klien or ControlPlaneClient)(config.control_plane_url, timeout=_BATAS_CABUT_DIRI)
    try:
        dicabut = klien.supersede(
            device_id=baru.device_id, credential=baru.credential,
            old_device_id=lama.device_id, old_credential=lama.credential)
    except HttpError as exc:
        return "tidak-didukung" if exc.status in (404, 405) else "tak-terjangkau"
    except (AgentError, OSError):
        return "tak-terjangkau"
    return "dicabut" if dicabut else "diabaikan"


def _cmd_adopt(config: AgentConfig, *, replace: bool = False, masukan=None,
               buat_klien=None) -> int:
    """Simpan kredensial perangkat hasil masuk aplikasi (ADR-072 §6).

    Aplikasi DSWorkbench (IDE) memperoleh ``device.id`` + ``credential`` dari
    ``POST /api/app/login/poll`` dan menyerahkannya di sini lewat **stdin**:
    satu objek JSON ``{deviceId, credential, name, os, arch, account}``. Bukan
    lewat argv -- argv terlihat di daftar proses. Hasilnya sama dengan ``pair``:
    ``device.json`` berizin 0600 di direktori keadaan.

    Kredensialnya tidak pernah dicetak, juga saat gagal.

    ``revokeReplaced: true`` di masukan (hanya berarti bersama ``--replace``):
    setelah kredensial baru tersimpan, server diminta mencabut perangkat lama
    yang digantikan, dibuktikan dengan kredensial lama itu sendiri
    (:func:`_cabut_pendahulu`). Hasilnya dicetak sebagai baris kedua stdout,
    ``perangkat-lama: <hasil> <id>``. Agent lama mengabaikan kunci itu dan
    tidak mencetak barisnya.
    """
    sumber = masukan if masukan is not None else sys.stdin
    mentah = sumber.read(_BATAS_ADOPT + 1)
    if isinstance(mentah, bytes):
        mentah = mentah.decode("utf-8", errors="replace")
    if len(mentah) > _BATAS_ADOPT:
        print("gagal: masukan adopt terlalu besar.", file=sys.stderr)
        return 1
    try:
        data = json.loads(mentah)
    except ValueError:
        # Isi masukan sengaja tidak diulang: bisa memuat kredensial.
        print("gagal: masukan adopt bukan JSON yang sah.", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("gagal: masukan adopt harus berupa objek JSON.", file=sys.stderr)
        return 1
    for kunci in _KUNCI_ADOPT:
        nilai = data.get(kunci)
        if not isinstance(nilai, str) or not nilai.strip() or len(nilai) > 512:
            print(f"gagal: field '{kunci}' wajib berupa teks.", file=sys.stderr)
            return 1
    akun = data.get("account")
    if akun is not None and (not isinstance(akun, str) or len(akun) > 256):
        print("gagal: field 'account' harus berupa teks.", file=sys.stderr)
        return 1

    store = StateStore(config.resolved_state_dir())
    lama: DeviceState | None = None
    if store.exists():
        try:
            lama = store.load()
        except (ValueError, OSError):
            lama = None  # berkas rusak: tidak ada yang layak dipertahankan
        if lama is not None and lama.device_id != data["deviceId"] and not replace:
            print(
                "Komputer ini sudah dipasangkan sebagai perangkat lain "
                f"({lama.device_id}"
                + (f", akun {lama.account}" if lama.account else "")
                + "). Jalankan lagi dengan --replace untuk menggantinya.",
                file=sys.stderr,
            )
            return EXIT_SUDAH_DIPASANGKAN
    if replace:
        # Mengganti kredensial di bawah agent yang sedang berjalan membuat
        # agent itu terus memakai kredensial lama sampai dimatikan.
        kunci = RunLock(config.resolved_state_dir() / NAMA_BERKAS)
        if not kunci.acquire():
            print(
                "Agent untuk perangkat ini sedang berjalan. Tutup dulu jendela "
                "DSWorkbench atau agent yang lain, lalu ulangi.",
                file=sys.stderr,
            )
            return EXIT_SUDAH_BERJALAN
        kunci.release()

    from datetime import datetime, timezone

    state = DeviceState(
        device_id=data["deviceId"],
        credential=data["credential"],
        control_plane_url=config.control_plane_url,
        name=data["name"].strip(),
        os=data["os"].strip(),
        arch=data["arch"].strip(),
        agent_version=AGENT_VERSION,
        paired_at=datetime.now(timezone.utc).isoformat(),
        account=akun.strip() if isinstance(akun, str) and akun.strip() else None,
    )
    store.save(state)
    print(f"terpasang: {state.device_id} ({state.name})")
    # Hanya di jalur --replace: di situ sudah dipastikan tidak ada agent yang
    # masih berjalan dengan kredensial lama.
    if (replace and data.get("revokeReplaced") is True and lama is not None
            and lama.device_id != state.device_id):
        hasil = _cabut_pendahulu(config, lama, state, buat_klien=buat_klien)
        print(f"perangkat-lama: {hasil} {lama.device_id}")
    return 0


def _pastikan_interpreter(perintah: list[str], *, kanal=None) -> int | None:
    """Pastikan agent berjalan pada interpreter pemegang pustaka praktikum.

    Sel notebook dieksekusi ``exec`` di dalam proses ini (LocalPythonBackend),
    sehingga interpreter proses ini adalah kernel yang dipakai mahasiswa.
    Memperingatkan saja tidak cukup: bila Python sistem kebetulan memiliki numpy
    dan pandas -- lazim pada komputer ber-Anaconda -- peringatan tidak akan
    muncul dan sel tetap berjalan atas pustaka yang salah tanpa satu pun tanda.

    Mengembalikan kode keluar bila pekerjaan sudah dijalankan oleh proses anak,
    atau ``None`` bila pemanggil boleh melanjutkan sendiri.

    ``kanal`` (mode ``--stdio``): kanal protokol IDE yang sudah diamankan dari
    fd 0/1 proses ini; proses anak menerimanya kembali sebagai fd 0/1-nya.
    """
    if interpreter_praktikum_aktif():
        return None

    if kanal is not None:
        kode = jalankan_di_interpreter_praktikum(
            argv=perintah, stdin=kanal.fd_masuk, stdout=kanal.fd_keluar)
    else:
        kode = jalankan_di_interpreter_praktikum(argv=perintah)
    if kode is not None:
        return kode

    akar = akar_agent_dari_modul()
    if python_praktikum(akar) is None:
        print(
            "Lingkungan praktikum belum dipasang, jadi sel notebook tidak akan "
            "menemukan pandas maupun numpy.\n"
            "  perbaikan: jalankan DSWorkbench (menu 1 — Siapkan lingkungan), "
            "atau `workbench-agent ensure-env`.",
            file=sys.stderr,
        )
        return 2

    hilang = paket_hilang()
    print(
        "Agent tidak berjalan pada interpreter praktikum dan perpindahan "
        "otomatis gagal.\n"
        f"  interpreter sekarang : {sys.executable}\n"
        f"  seharusnya           : {python_praktikum(akar)}\n"
        + (f"  paket hilang         : {', '.join(hilang)}\n" if hilang else "")
        + "  perbaikan: jalankan DSWorkbench (menu 6 — Pasang ulang).",
        file=sys.stderr,
    )
    return 2


def _cmd_run(config: AgentConfig, *, tanpa_bawaan: bool = False,
             stdio: bool = False) -> int:
    kanal = None
    if stdio:
        # ADR-072 §3.5: sebelum apa pun sempat menulis, fd 1 asli diambil
        # sebagai kanal protokol dan fd 1/``sys.stdout`` dialihkan ke stderr.
        # Seluruh ``print`` di bawah ini otomatis menjadi teks stderr.
        from .stdio import amankan_kanal

        kanal = amankan_kanal()
    # Tanpa profil bawaan, interpreter yang menjalankan agent sengaja bukan
    # interpreter praktikum DW; kernel profil lain tetap memakai interpreter
    # profilnya sendiri (ADR-041 §4), dan kernel DW ditolak.
    if not tanpa_bawaan:
        perintah = ["--url", config.control_plane_url, "run"]
        if config.state_dir is not None:
            # Tanpa ini proses pengganti membaca direktori keadaan bawaan:
            # perangkat lain, atau "belum dipasangkan".
            perintah[:0] = ["--state-dir", str(config.state_dir)]
        if stdio:
            perintah.append("--stdio")
        kode = _pastikan_interpreter(perintah, kanal=kanal)
        if kode is not None:
            return kode
    # Sisa pemeriksa checkpoint dari agent yang dimatikan di tengah jalan.
    bersihkan_sisa_checkpoint()
    try:
        state = load_state(config)
    except NotPairedError:
        if kanal is None:
            raise
        from .stdio import layani_tanpa_pairing

        print("agent belum dipasangkan; menunggu aplikasi menutup pipa", flush=True)
        return layani_tanpa_pairing(kanal)
    # Folder kerja, token GitHub, dan basis data mengikuti akun pairing ini.
    tetapkan_akun(state.account)
    kunci = RunLock(config.resolved_state_dir() / NAMA_BERKAS)
    if not kunci.acquire():
        pid = kunci.pemegang()
        pesan = (
            "Agent lain untuk perangkat ini sudah berjalan"
            + (f" (PID {pid})" if pid else "")
            + ". Tutup jendela DSWorkbench atau agent ZIP yang lain dulu; "
            "dua agent berebut sel notebook yang sama."
        )
        print(pesan, file=sys.stderr, flush=True)
        if kanal is not None:
            from .stdio import KODE_AGENT_SUDAH_BERJALAN

            kanal.kirim({"type": "error", "code": KODE_AGENT_SUDAH_BERJALAN,
                         "detail": pesan, "pid": pid})
        return EXIT_SUDAH_BERJALAN
    # URL di berkas keadaan menang: pairing mungkin dilakukan ke host lain.
    efektif = AgentConfig(
        control_plane_url=state.control_plane_url,
        state_dir=config.resolved_state_dir(),
        poll_interval_seconds=config.poll_interval_seconds,
        heartbeat_interval_seconds=config.heartbeat_interval_seconds,
        backoff_initial_seconds=config.backoff_initial_seconds,
        backoff_max_seconds=config.backoff_max_seconds,
        backoff_multiplier=config.backoff_multiplier,
        request_timeout_seconds=config.request_timeout_seconds,
    )
    # "terhubung" baru dicetak Agent setelah connect benar-benar berhasil;
    # sebelum itu yang jujur hanyalah "menghubungi".
    agent = Agent(efektif, state,
                  allowlist=default_allowlist(default_kernel=not tanpa_bawaan),
                  announce=lambda pesan: print(pesan, flush=True))
    print(f"agent {AGENT_VERSION} menghubungi {efektif.control_plane_url}", flush=True)
    print(f"perangkat {state.device_id} — Ctrl+C untuk berhenti", flush=True)
    if state.account:
        print(f"akun {state.account} — folder kerja {akar_workspace()}", flush=True)
    _sinyal_sebagai_interupsi()
    if kanal is not None:
        return _layani_stdio(agent, kanal, state, kunci)
    try:
        agent.run_forever()
    except KeyboardInterrupt:
        try:
            agent.client.disconnect(
                device_id=state.device_id, credential=state.credential
            )
        except Exception:
            pass
        print("\ndihentikan")
    finally:
        # ADR-053 §6: basis data praktikum ikut berhenti bersama agent.
        layanan = getattr(agent.allowlist, "layanan", None)
        if layanan is not None:
            try:
                n = layanan.hentikan_semua()
                if n:
                    print(f"{n} basis data praktikum dihentikan", flush=True)
            except Exception:  # pragma: no cover - jangan menahan penutupan agent
                pass
        kunci.release()
    return 0


def _layani_stdio(agent: Agent, kanal, state, kunci: RunLock) -> int:
    """Mode ``run --stdio``: pipa IDE di utas utama, relay di utas latar.

    Utas utama membaca stdin sampai EOF (aplikasi menutup pipa) -- itulah cara
    berhenti yang rapi, kode keluar 0. Loop relay yang sama dengan mode biasa
    berjalan di utas latar untuk operasi kelas B dan klien web.
    """
    import threading

    from .stdio import PipaIde

    pipa = PipaIde(agent, kanal)
    agent._on_relay = pipa.lapor_relay

    def _relay() -> None:
        try:
            agent.run_forever()
        except AuthenticationFailedError as exc:
            # Kredensial dicabut: relay berhenti, pipa tetap melayani sampai
            # amplop habis; aplikasi diberi tahu agar meminta pairing ulang.
            print(f"gagal: {exc}", file=sys.stderr, flush=True)
            pipa.lapor_relay("disconnected", alasan="auth_failed")
        except Exception as exc:  # noqa: BLE001 - utas latar tidak boleh mati diam-diam
            print(f"relay berhenti: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            pipa.lapor_relay("disconnected", alasan="relay_stopped")

    pipa.halo(paired=True)
    utas = threading.Thread(target=_relay, name="relay", daemon=True)
    utas.start()
    try:
        pipa.layani()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            pipa.tutup()
        except Exception:  # pragma: no cover - penutupan tidak boleh tersangkut
            pass
        agent.stop()

        def _putus() -> None:
            try:
                agent.client.disconnect(device_id=state.device_id,
                                        credential=state.credential)
            except Exception:
                pass

        # Aplikasi menunggu proses ini selesai: jangan tertahan server yang lambat.
        pamit = threading.Thread(target=_putus, name="relay-pamit", daemon=True)
        pamit.start()
        pamit.join(3.0)
        layanan = getattr(agent.allowlist, "layanan", None)
        if layanan is not None:
            try:
                n = layanan.hentikan_semua()
                if n:
                    print(f"{n} basis data praktikum dihentikan", flush=True)
            except Exception:  # pragma: no cover
                pass
        kunci.release()
        print("pipa ditutup aplikasi; agent berhenti", flush=True)
    return 0


def _sinyal_sebagai_interupsi() -> None:
    """SIGTERM (POSIX) dan CTRL_BREAK (Windows) diperlakukan seperti Ctrl+C.

    Handler bawaan keduanya mengakhiri Python tanpa melewati ``finally``,
    sehingga klaster PostgreSQL praktikum (ADR-053 §6) tertinggal hidup dan
    memegang port 5433/5434. Aplikasi desktop menghentikan agent dengan
    SIGINT → SIGTERM (macOS/Linux) atau CTRL_BREAK (Windows).
    """
    def _interupsi(_signum, _frame):  # pragma: no cover - dipicu OS
        raise KeyboardInterrupt

    for nama in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, nama, None)
        if sig is not None:
            try:
                signal.signal(sig, _interupsi)
            except (ValueError, OSError):  # bukan thread utama
                pass


def _cmd_once(config: AgentConfig) -> int:
    perintah = ["--url", config.control_plane_url, "once"]
    if config.state_dir is not None:
        perintah[:0] = ["--state-dir", str(config.state_dir)]
    kode = _pastikan_interpreter(perintah)
    if kode is not None:
        return kode
    state = load_state(config)
    efektif = AgentConfig(
        control_plane_url=state.control_plane_url,
        state_dir=config.resolved_state_dir(),
    )
    agent = Agent(efektif, state)
    jumlah = agent.run_once()
    print(f"selesai: {jumlah} operasi dikerjakan")
    return 0


def _cmd_status(config: AgentConfig) -> int:
    store = StateStore(config.resolved_state_dir())
    if not store.exists():
        print("status: belum dipasangkan")
        return 0
    state = store.load()
    print("status: dipasangkan")
    print(f"  deviceId     : {state.device_id}")
    print(f"  name         : {state.name}")
    print(f"  os/arch      : {state.os}/{state.arch}")
    print(f"  agentVersion : {state.agent_version}")
    print(f"  controlPlane : {state.control_plane_url}")
    print(f"  pairedAt     : {state.paired_at}")
    print(f"  stateFile    : {store.path}")
    return 0


def _cmd_doctor(config: AgentConfig) -> int:
    print(f"agent version : {AGENT_VERSION}")
    print(f"state dir     : {config.resolved_state_dir()}")
    store = StateStore(config.resolved_state_dir())
    print(f"paired        : {'ya' if store.exists() else 'tidak'}")

    url = config.control_plane_url
    if store.exists():
        url = store.load().control_plane_url
    print(f"control plane : {url}")

    klien = ControlPlaneClient(url, timeout=config.request_timeout_seconds)
    try:
        health = klien.health()
        print(f"api health    : {health.get('status', '?')}")
        relay = health.get("relay") or {}
        if isinstance(relay, dict):
            print(f"relay conns   : {relay.get('connections', '?')}")
    except TransportError as exc:
        print(f"api health    : gagal ({exc})")
        return 1

    resources = collect_resources()
    print("resources:")
    print(json.dumps(resources, ensure_ascii=False, indent=2))

    hilang = paket_hilang()
    if hilang:
        print(f"pustaka praktikum: kurang ({', '.join(hilang)})")
        print("  perbaikan: jalankan pasangkan.command / ensure-env, lalu jalankan.command")
        return 1
    print("pustaka praktikum: ok (numpy, pandas, …)")
    for baris in status_profil(periksa_impor=True):
        if baris["default"]:
            continue
        print(f"profil {baris['id']:<22}: "
              + ("siap" if baris["ready"] else
                 "belum dipasang (opsional; hanya untuk mata kuliah yang memakainya)"
                 if not baris["installed"] else "perlu dipasang ulang"))
    akar = akar_agent_dari_modul()
    seharusnya = python_praktikum(akar)
    print(f"interpreter ini : {sys.executable}")
    print(f"interpreter sel : {seharusnya or 'belum dipasang'}")
    if python_aplikasi_perlu_diganti(akar):
        print(
            "peringatan: Python di folder aplikasi adalah sisa pemasangan lama "
            "yang tidak lengkap"
        )
        print("  perbaikan: tutup lalu buka lagi DSWorkbench — ia menggantinya sendiri")
    if not interpreter_praktikum_aktif(akar):
        print(
            "peringatan: sel notebook akan dieksekusi oleh interpreter di atas, "
            "bukan oleh lingkungan praktikum."
        )
        print("  perbaikan: jalankan DSWorkbench, atau `workbench-agent run`")
        return 1
    return 0


def _cmd_unpair(config: AgentConfig) -> int:
    store = StateStore(config.resolved_state_dir())
    if not store.exists():
        print("tidak ada kredensial lokal")
        return 0
    store.clear()
    print("kredensial lokal dihapus")
    print("catatan: pencabutan di VM dilakukan dari halaman Workbench")
    return 0


#: Batas waktu HTTP ``self-revoke`` (detik). Dijalankan saat aplikasi mulai dan
#: saat keluar: tidak boleh menahan keduanya lama bila server tidak terjangkau.
_BATAS_CABUT_DIRI = 8.0


def _hapus_github(state_dir: Path, state: DeviceState | None, *, buat_auth=None) -> dict:
    """Cabut tautan GitHub akun pairing, lalu hapus SEMUA ``github.json`` di ``state_dir``.

    Akun pairing: token dicabut di GitHub bila terbaca (mekanisme ``revoke``
    yang sama dengan operasi relay ``github.auth_revoke``), lalu berkasnya
    dihapus. Berkas akun lain (sisa sesi yang lebih lama) hanya dapat dihapus:
    di Windows rahasianya terbungkus DPAPI dengan id perangkat yang sudah tidak
    diketahui. Token tidak pernah dicetak.
    """
    from .akun import direktori_akun
    from .githubauth import FILE_NAME, GitHubAuth

    kandidat = [state_dir / FILE_NAME]
    try:
        kandidat += sorted((state_dir / "akun").glob(f"*/{FILE_NAME}"))
    except OSError:
        pass
    # Dihitung sebelum ``revoke``: ia sendiri menghapus berkas akun pairing.
    ada = [berkas for berkas in kandidat if berkas.is_file()]
    jauh = False
    if state is not None:
        # ``klaim=False``: menyapu tidak boleh menjadikan akun ini pemilik direktori.
        dir_akun = direktori_akun(state_dir, state.account, state_dir / "akun", klaim=False)
        if (dir_akun / FILE_NAME).is_file():
            try:
                auth = (buat_auth or GitHubAuth)(dir_akun, state.device_id)
                jauh = bool(auth.revoke().get("revokedRemote"))
            except Exception:  # noqa: BLE001 - penghapusan lokal di bawah tetap berjalan
                jauh = False
    for berkas in ada:
        try:
            berkas.unlink()
        except OSError:  # termasuk sudah dihapus ``revoke``
            pass
    dihapus = sum(1 for berkas in ada if not berkas.exists())
    return {"revokedRemote": jauh, "removedFiles": dihapus}


def _cmd_self_revoke(config: AgentConfig, *, github: bool = False, hanya: str | None = None,
                     buat_klien=None, buat_auth=None) -> int:
    """Sapu sisa sesi di komputer bersama (mode lab; ``docs/ide-mode-lab.md``).

    1. tidak boleh ada agent yang sedang berjalan untuk direktori keadaan ini
       (kode keluar 3): sesi itu masih hidup di jendela lain;
    2. ``--github``: tautan GitHub dicabut dan berkas tokennya dihapus;
    3. server diminta mencabut perangkat ini dengan kredensialnya sendiri
       (``POST /api/agent/self-revoke``) -- token aplikasi yang terikat padanya
       ikut mati;
    4. ``device.json`` dihapus, **apa pun** jawaban server. Server tak
       terjangkau: token lab mati sendiri dan perangkatnya disapu server.

    Satu baris JSON di stdout melaporkan hasilnya; kredensial tidak pernah dicetak.
    ``server``: ``revoked`` | ``already`` (sudah dicabut/tidak dikenal) |
    ``unsupported`` (server belum punya rute ini) | ``unreachable`` | ``none``
    (tidak ada kredensial lokal).

    ``hanya`` (``--device-id``): pembersihan sesi tertentu yang berjalan
    terlambat (penjaga sesi aplikasi yang sudah ditutup) tidak boleh mencabut
    perangkat sesi BERIKUTNYA. Bila kredensial lokal milik perangkat lain,
    tidak ada yang disentuh (``local: "other"``).
    """
    state_dir = config.resolved_state_dir()
    store = StateStore(state_dir)
    kunci = RunLock(state_dir / NAMA_BERKAS)
    if not kunci.acquire():
        print("Agent untuk perangkat ini sedang berjalan; tidak ada yang dicabut.",
              file=sys.stderr)
        return EXIT_SUDAH_BERJALAN
    try:
        state: DeviceState | None = None
        ada = store.exists()
        if ada:
            try:
                state = store.load()
            except (ValueError, OSError):
                state = None  # berkas rusak: tidak ada yang dapat dicabut di server
        hasil: dict = {
            "deviceId": state.device_id if state else None,
            "account": state.account if state else None,
            "server": "none",
            "local": "none",
        }
        if hanya is not None and ada and (state is None or state.device_id != hanya):
            hasil["local"] = "other"
            print(json.dumps(hasil, ensure_ascii=False))
            return 0
        if github:
            hasil["github"] = _hapus_github(state_dir, state, buat_auth=buat_auth)
        if state is not None:
            klien = (buat_klien or ControlPlaneClient)(
                state.control_plane_url, timeout=_BATAS_CABUT_DIRI)
            try:
                klien.self_revoke(device_id=state.device_id, credential=state.credential)
                hasil["server"] = "revoked"
            except AuthenticationFailedError:
                hasil["server"] = "already"
            except HttpError as exc:
                hasil["server"] = "unsupported" if exc.status in (404, 405) else "unreachable"
            except (TransportError, OSError):
                hasil["server"] = "unreachable"
        if ada:
            store.clear()
            hasil["local"] = "cleared"
        print(json.dumps(hasil, ensure_ascii=False))
        return 0
    finally:
        kunci.release()


if __name__ == "__main__":
    raise SystemExit(main())
