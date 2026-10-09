"""Backend kernel yang menjalankan setiap kernel sebagai proses anak.

Dipakai Local Runner untuk RuntimeProfile yang bukan interpreter agent itu
sendiri (ADR-041 §4). Satu kernel = satu proses ``python -m
workbench_jupyter.worker`` memakai interpreter profilnya. Dibanding kernel
in-process, proses anak dapat:

- memakai pustaka yang tidak pernah dipasang ke interpreter agent;
- dihentikan saat melewati batas waktu, bukan dibiarkan berjalan selamanya;
- di-interrupt (POSIX: SIGINT; Windows: ``CTRL_BREAK_EVENT`` ke grup prosesnya)
  tanpa ikut mematikan agent, dan dimulai ulang bila sel mengabaikannya.

Proses dijalankan tanpa shell, dengan argumen tetap, hak pengguna biasa, dan
direktori kerja yang ditentukan pemanggil (akar workspace mahasiswa).
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import JupyterError, JupyterTimeoutError, KernelNotFoundError
from .models import (
    DEFAULT_EXECUTE_TIMEOUT_SECONDS,
    DEFAULT_START_TIMEOUT_SECONDS,
    ErrorOutput,
    ExecutionResult,
    KernelInfo,
    KernelState,
)

#: Waktu yang diberikan kepada sel untuk berhenti sendiri setelah SIGINT,
#: sebelum prosesnya dimatikan.
INTERRUPT_GRACE_SECONDS = 5.0

#: ``ename`` hasil sel yang kernelnya terpaksa dimulai ulang karena tidak
#: berhenti setelah interrupt (job: ``failed`` + ``cancel_failed``).
KERNEL_RESTARTED = "KernelRestarted"

#: Folder yang memuat paket ``workbench_jupyter`` ini -- ditambahkan ke
#: PYTHONPATH proses anak agar ``-m workbench_jupyter.worker`` ditemukan
#: tanpa memasang apa pun ke venv profil.
_AKAR_PAKET = str(Path(__file__).resolve().parents[1])


@dataclass
class _Proses:
    popen: subprocess.Popen
    antrean: "queue.Queue[dict[str, Any] | None]"
    nama: str
    info: dict[str, Any] = field(default_factory=dict)
    #: Sedang menjalankan sel (hanya utas eksekusi yang membaca antrean).
    mengeksekusi: bool = False
    #: ``time.monotonic()`` saat interrupt pertama dikirim untuk sel aktif.
    diinterupsi_pada: float | None = None
    #: Direktori kerja kernel ini (dipakai lagi saat dimulai ulang).
    cwd: str | None = None
    #: Penulisan ke stdin kernel datang dari beberapa utas (eksekusi, restart,
    #: stop); satu baris protokol tidak boleh terselip di tengah baris lain.
    kunci_tulis: threading.Lock = field(default_factory=threading.Lock)


class SubprocessKernelBackend:
    """``JupyterBackend`` dengan satu proses anak per kernel."""

    def __init__(
        self,
        python: str | os.PathLike[str],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        start_timeout: float = DEFAULT_START_TIMEOUT_SECONDS,
        stderr: Any = None,
    ) -> None:
        self._python = str(python)
        self._cwd = str(cwd) if cwd is not None else None
        self._env_tambahan = dict(env or {})
        self._start_timeout = start_timeout
        self._stderr = stderr
        self._kernel: dict[str, _Proses] = {}
        #: Menjaga ``_kernel`` dan tanda ``mengeksekusi``: backend dipanggil dari
        #: utas transport relay, pekerja job, dan pipa IDE (ADR-072 §3.5).
        self._kunci = threading.RLock()

    # ------------------------------------------------------------------
    # Proses

    def _lingkungan(self, nama: str, cwd_kernel: str | None = None) -> dict[str, str]:
        env = dict(os.environ)
        if cwd_kernel:
            # Kernel mata kuliah per-course (ADR-050): bobot pralatih torch dibaca
            # dari data/raw/torch yang disiapkan dataset.materialize (DL-04),
            # bukan diunduh diam-diam ke folder home.
            env["TORCH_HOME"] = os.path.join(cwd_kernel, "data", "raw", "torch")
        sebelumnya = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(
            [_AKAR_PAKET] + [p for p in sebelumnya.split(os.pathsep) if p])
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # Kernel tanpa layar: figure ditangkap sebagai PNG, bukan jendela.
        env.setdefault("MPLBACKEND", "Agg")
        env["WORKBENCH_KERNEL_NAME"] = nama
        env.update(self._env_tambahan)
        return env

    def _spawn(self, nama: str, cwd_kernel: str | None = None) -> _Proses:
        pilihan = cwd_kernel or self._cwd
        cwd = pilihan if pilihan and os.path.isdir(pilihan) else None
        # Windows: grup proses sendiri agar CTRL_BREAK_EVENT hanya mengenai
        # kernel ini, bukan konsol agent (ADR-049 §3).
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        try:
            popen = subprocess.Popen(
                [self._python, "-m", "workbench_jupyter.worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
                cwd=cwd,
                env=self._lingkungan(nama, cwd_kernel),
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
            )
        except OSError as exc:
            raise JupyterError(f"Interpreter kernel tidak dapat dijalankan: {exc}") from exc

        antrean: "queue.Queue[dict[str, Any] | None]" = queue.Queue()

        def _baca() -> None:
            assert popen.stdout is not None
            for baris in popen.stdout:
                try:
                    pesan = json.loads(baris)
                except ValueError:
                    continue
                if isinstance(pesan, dict):
                    antrean.put(pesan)
            antrean.put(None)  # proses berakhir

        threading.Thread(target=_baca, name="kernel-reader", daemon=True).start()
        proses = _Proses(popen=popen, antrean=antrean, nama=nama, cwd=cwd_kernel)
        try:
            siap = self._tunggu(proses, None, self._start_timeout)
        except JupyterTimeoutError:
            siap = None
        if siap is None or siap.get("type") != "ready":
            self._matikan(proses)
            raise JupyterError(
                "Kernel tidak siap dalam "
                f"{self._start_timeout:.0f} detik. Jalankan Periksa pada aplikasi "
                "DSWorkbench untuk melihat keadaan lingkungan."
            )
        proses.info = siap
        return proses

    @staticmethod
    def _tunggu(proses: _Proses, rid: str | None, timeout: float) -> dict[str, Any] | None:
        """Pesan berikutnya untuk ``rid`` (atau pesan apa pun bila ``None``)."""
        batas = time.monotonic() + timeout
        while True:
            sisa = batas - time.monotonic()
            if sisa <= 0:
                raise JupyterTimeoutError("waktu tunggu kernel habis")
            try:
                pesan = proses.antrean.get(timeout=sisa)
            except queue.Empty:
                raise JupyterTimeoutError("waktu tunggu kernel habis") from None
            if pesan is None:
                return None
            if rid is None or pesan.get("id") == rid:
                return pesan

    @staticmethod
    def _kirim(proses: _Proses, pesan: dict[str, Any]) -> None:
        try:
            assert proses.popen.stdin is not None
            with proses.kunci_tulis:
                proses.popen.stdin.write(json.dumps(pesan) + "\n")
                proses.popen.stdin.flush()
        except (OSError, ValueError) as exc:
            raise JupyterError("Kernel sudah berhenti. Mulai ulang kernel.") from exc

    @staticmethod
    def _matikan(proses: _Proses) -> None:
        if proses.popen.poll() is not None:
            return
        proses.popen.kill()
        try:
            proses.popen.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            pass

    def _proses(self, kernel_id: str) -> _Proses:
        with self._kunci:
            proses = self._kernel.get(kernel_id)
        if proses is None:
            raise KernelNotFoundError(kernel_id)
        return proses

    # ------------------------------------------------------------------
    # JupyterBackend

    def ping(self) -> bool:
        return True

    def start_kernel(self, *, name: str | None = None,
                     cwd: str | os.PathLike[str] | None = None) -> KernelInfo:
        nama = name or "python3-proses"
        proses = self._spawn(nama, str(cwd) if cwd is not None else None)
        kid = f"proc-{uuid.uuid4().hex[:12]}"
        with self._kunci:
            self._kernel[kid] = proses
        return KernelInfo(id=kid, name=nama, state=KernelState.IDLE,
                          execution_state=KernelState.IDLE)

    def stop_kernel(self, kernel_id: str) -> None:
        proses = self._proses(kernel_id)
        self._hentikan_sel_aktif(proses)
        with self._kunci:
            proses = self._kernel.pop(kernel_id, None) or proses
        try:
            self._kirim(proses, {"id": "stop", "op": "shutdown"})
            proses.popen.wait(timeout=3)
        except (JupyterError, subprocess.TimeoutExpired):
            pass
        self._matikan(proses)

    def interrupt_kernel(self, kernel_id: str) -> None:
        """Minta sel aktif berhenti; aman dipanggil dari utas lain.

        Utas eksekusi yang mengawasi tenggang: bila sel tidak berhenti dalam
        ``INTERRUPT_GRACE_SECONDS``, proses kernel dimatikan dan dimulai ulang.
        """
        proses = self._proses(kernel_id)
        if proses.popen.poll() is not None:
            return
        if proses.mengeksekusi and proses.diinterupsi_pada is None:
            proses.diinterupsi_pada = time.monotonic()
        self._kirim_interrupt(proses)

    @staticmethod
    def _kirim_interrupt(proses: _Proses) -> None:
        try:
            if os.name == "nt":  # pragma: no cover - hanya Windows
                proses.popen.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                proses.popen.send_signal(signal.SIGINT)
        except (OSError, ValueError):
            # Sinyal gagal terkirim: tenggang habis lalu kernel dimulai ulang.
            pass

    def _hentikan_sel_aktif(self, proses: _Proses) -> None:
        """Restart/stop saat sel berjalan: interrupt lalu tunggu utas eksekusi.

        Hanya utas eksekusi yang boleh membaca antrean kernel; restart yang
        ikut membaca akan saling mencuri jawaban.
        """
        if not proses.mengeksekusi:
            return
        if proses.diinterupsi_pada is None:
            proses.diinterupsi_pada = time.monotonic()
        self._kirim_interrupt(proses)
        batas = time.monotonic() + INTERRUPT_GRACE_SECONDS + 2
        while proses.mengeksekusi and time.monotonic() < batas:
            time.sleep(0.05)

    def restart_kernel(self, kernel_id: str) -> KernelInfo:
        proses = self._proses(kernel_id)
        self._hentikan_sel_aktif(proses)
        proses = self._proses(kernel_id)  # mungkin sudah diganti utas eksekusi
        if proses.popen.poll() is None:
            rid = uuid.uuid4().hex
            try:
                self._kirim(proses, {"id": rid, "op": "restart"})
                if self._tunggu(proses, rid, 10) is not None:
                    return KernelInfo(id=kernel_id, name=proses.nama,
                                      state=KernelState.IDLE,
                                      execution_state=KernelState.IDLE)
            except (JupyterError, JupyterTimeoutError):
                pass
        self._matikan(proses)
        baru = self._spawn(proses.nama, proses.cwd)
        with self._kunci:
            self._kernel[kernel_id] = baru
        return KernelInfo(id=kernel_id, name=proses.nama, state=KernelState.IDLE,
                          execution_state=KernelState.IDLE)

    def kernel_status(self, kernel_id: str) -> KernelInfo:
        proses = self._proses(kernel_id)
        hidup = proses.popen.poll() is None
        keadaan = KernelState.IDLE if hidup else KernelState.DEAD
        return KernelInfo(id=kernel_id, name=proses.nama, state=keadaan,
                          execution_state=keadaan)

    #: Backend ini dapat meneruskan keluaran bertahap (``on_stream``).
    supports_streaming = True

    def execute(
        self,
        kernel_id: str,
        code: str,
        *,
        timeout: float | None = DEFAULT_EXECUTE_TIMEOUT_SECONDS,
        on_stream: Callable[[str, str], None] | None = None,
    ) -> ExecutionResult:
        """Eksekusi satu sel di proses kernel.

        Satu utas ini saja yang membaca antrean kernel selama sel berjalan;
        batas waktu dan interrupt dari utas lain diawasi di sini (ADR-049 §3):
        interrupt → tunggu ``INTERRUPT_GRACE_SECONDS`` → kernel dimatikan dan
        dimulai ulang dengan id yang sama.
        """
        proses = self._proses(kernel_id)
        if proses.popen.poll() is not None:
            raise JupyterError(
                "Kernel sudah berhenti (mungkin kehabisan memori). Tekan Restart "
                "untuk memulai kernel baru."
            )
        rid = uuid.uuid4().hex
        with self._kunci:
            # Lane job agent sudah menolak sel kedua (``kernel_busy``); ini sabuk
            # pengaman agar dua utas tidak pernah saling mencuri jawaban kernel.
            if proses.mengeksekusi:
                raise JupyterError("Sel lain masih berjalan di kernel ini.")
            proses.diinterupsi_pada = None
            proses.mengeksekusi = True
        try:
            self._kirim(proses, {"id": rid, "op": "execute", "code": code})
            return self._awasi(kernel_id, proses, rid, timeout, on_stream)
        finally:
            proses.mengeksekusi = False
            proses.diinterupsi_pada = None

    def _awasi(self, kernel_id: str, proses: _Proses, rid: str, timeout: float | None,
               on_stream: Callable[[str, str], None] | None) -> ExecutionResult:
        tenggat = time.monotonic() + timeout if timeout is not None and timeout > 0 else None
        lewat_batas = False
        while True:
            sekarang = time.monotonic()
            if tenggat is not None and sekarang >= tenggat and not lewat_batas:
                lewat_batas = True
                if proses.diinterupsi_pada is None:
                    proses.diinterupsi_pada = sekarang
                self._kirim_interrupt(proses)
            if (proses.diinterupsi_pada is not None
                    and sekarang >= proses.diinterupsi_pada + INTERRUPT_GRACE_SECONDS):
                return self._mulai_ulang_paksa(kernel_id, proses, lewat_batas)
            try:
                pesan = proses.antrean.get(timeout=0.2)
            except queue.Empty:
                continue
            if pesan is None:
                raise JupyterError(
                    "Kernel berhenti saat menjalankan sel (mungkin kehabisan memori). "
                    "Tekan Restart untuk memulai kernel baru."
                )
            if pesan.get("id") != rid:
                continue
            jenis = pesan.get("type")
            if jenis == "stream":
                if on_stream is not None:
                    try:
                        on_stream(str(pesan.get("name") or "stdout"), str(pesan.get("text") or ""))
                    except Exception:  # noqa: BLE001 - penerima rusak tidak menggagalkan sel
                        on_stream = None
                continue
            if jenis != "result" or not isinstance(pesan.get("result"), Mapping):
                raise JupyterError(str(pesan.get("detail") or "jawaban kernel tidak sah"))
            if lewat_batas:
                raise JupyterTimeoutError(
                    "Sel melewati batas waktu dan dihentikan. Variabel yang sudah ada "
                    "tetap tersimpan di kernel.")
            return ExecutionResult.from_public(pesan["result"])

    def _mulai_ulang_paksa(self, kernel_id: str, proses: _Proses,
                           lewat_batas: bool) -> ExecutionResult:
        """Sel tidak berhenti setelah diminta: matikan, lalu kernel baru ber-id sama."""
        self._matikan(proses)
        try:
            baru = self._spawn(proses.nama, proses.cwd)
            with self._kunci:
                self._kernel[kernel_id] = baru
            nasib = "kernel dimulai ulang"
        except JupyterError:
            with self._kunci:
                self._kernel.pop(kernel_id, None)
            nasib = "kernel dihentikan dan gagal dimulai ulang"
        pesan = (f"Sel tidak berhenti dalam {INTERRUPT_GRACE_SECONDS:.0f} detik setelah "
                 f"diminta; {nasib}. Jalankan ulang sel dari atas.")
        if lewat_batas:
            raise JupyterTimeoutError(f"Sel melewati batas waktu. {pesan}")
        return ExecutionResult(
            status="error",
            outputs=(ErrorOutput(ename=KERNEL_RESTARTED, evalue=pesan, traceback=()),),
        )

    def close(self) -> None:
        """Hentikan seluruh kernel (dipanggil saat agent berhenti)."""
        with self._kunci:
            semua = list(self._kernel)
        for kid in semua:
            try:
                self.stop_kernel(kid)
            except KernelNotFoundError:
                pass


__all__ = ["SubprocessKernelBackend", "INTERRUPT_GRACE_SECONDS", "KERNEL_RESTARTED"]
