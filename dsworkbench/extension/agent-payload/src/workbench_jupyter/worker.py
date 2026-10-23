"""Proses kernel untuk RuntimeProfile yang bukan interpreter agent.

    python -m workbench_jupyter.worker

Dijalankan :class:`~workbench_jupyter.subprocess_backend.SubprocessKernelBackend`
memakai interpreter profilnya (mis. ``.venv-python-deep-learning``), sehingga
``import torch`` di sel menemukan pustaka profil itu tanpa agent sendiri
pernah memuatnya (ADR-041 §4). Isi kernelnya adalah :class:`LocalPythonBackend`
yang sama dengan kernel in-process -- keluaran MIME, ekspresi terakhir, dan
figure matplotlib berperilaku identik.

Protokol: satu objek JSON per baris.

    masuk  {"id": "...", "op": "execute", "code": "..."}
           {"id": "...", "op": "restart"} | {"op": "ping"} | {"op": "shutdown"}
    keluar {"type": "ready", "python": "3.12.13", "pid": 123}
           {"id": "...", "type": "stream", "name": "stdout", "text": "..."}   (bertahap, ADR-049 §6)
           {"id": "...", "type": "result", "result": {...ExecutionResult...}}
           {"id": "...", "type": "ok"} | {"id": "...", "type": "error", "detail": "..."}

Kanal protokol adalah salinan stdout yang diambil sebelum apa pun berjalan;
file descriptor 1 lalu dialihkan ke stderr. Dengan begitu ``print`` dari
pustaka C -- yang menulis langsung ke fd 1 dan tidak tertangkap
``redirect_stdout`` -- tidak pernah merusak protokol.

Interrupt: POSIX = SIGINT dari induk. Windows = ``CTRL_BREAK_EVENT`` ke grup
proses kernel (induk membuatnya dengan ``CREATE_NEW_PROCESS_GROUP``); handler
konsol di sini meneruskannya sebagai SIGINT sehingga ``KeyboardInterrupt``
naik di utas utama, tempat sel berjalan, dan ``time.sleep`` ikut terbangun --
sama seperti Ctrl+C (ADR-049 §3).

Modul ini hanya memakai pustaka standar dan paket ``workbench_jupyter``.
"""

from __future__ import annotations

import json
import os
import platform
import signal
import sys
from typing import Any, TextIO

from .local_python import LocalPythonBackend
from .models import ErrorOutput, ExecutionResult


def _kanal_protokol() -> TextIO:
    kanal = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    try:
        os.dup2(2, 1)
    except OSError:  # pragma: no cover - fd 2 tertutup; tetap berjalan
        pass
    return kanal


def _kirim(kanal: TextIO, pesan: dict[str, Any]) -> None:
    kanal.write(json.dumps(pesan, ensure_ascii=False) + "\n")
    kanal.flush()


#: Callback ctypes harus tetap hidup selama proses: bila dibuang GC, Windows
#: akan memanggil memori yang sudah dibebaskan.
_PENANGAN_KONSOL: Any = None


def _pasang_interrupt_windows() -> None:
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is None:
        return

    def _ke_keyboard_interrupt(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(sigbreak, _ke_keyboard_interrupt)
    _pasang_penangan_konsol()


def _pasang_penangan_konsol() -> None:  # pragma: no cover - hanya Windows
    """``CTRL_BREAK_EVENT`` → ``raise(SIGINT)`` di tingkat C.

    Handler SIGBREAK Python di atas baru berjalan di antara bytecode, dan
    CPython di Windows hanya membangunkan ``time.sleep`` (serta tunggu lain di
    utas utama) untuk SIGINT. Tanpa ini sel yang sedang ``time.sleep`` tidak
    berhenti, tenggang induk habis, dan kernel dimulai ulang -- Hentikan
    terlihat sebagai galat ``KernelRestarted``. Windows memanggil handler
    konsol ini di utas baru; ``signal.raise_signal(SIGINT)`` menjalankan
    handler C CPython untuk SIGINT, yang menyalakan event bangun utas utama
    lalu menjadwalkan ``KeyboardInterrupt`` -- sama seperti Ctrl+C di konsol.
    """
    global _PENANGAN_KONSOL
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return
    ctrl_break_event = 1

    def _penangan(jenis: int) -> bool:
        # Sel yang mengabaikan SIGINT (SIG_IGN/SIG_DFL): serahkan ke handler
        # SIGBREAK seperti sebelumnya, jangan sampai raise(SIGINT) mematikan proses.
        if jenis != ctrl_break_event or not callable(signal.getsignal(signal.SIGINT)):
            return False
        try:
            signal.raise_signal(signal.SIGINT)
        except (OSError, ValueError):
            return False
        return True

    penangan = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)(_penangan)
    if ctypes.windll.kernel32.SetConsoleCtrlHandler(penangan, True):
        _PENANGAN_KONSOL = penangan


def _dihentikan(hitungan: int | None) -> ExecutionResult:
    return ExecutionResult(
        status="error",
        execution_count=hitungan,
        outputs=(ErrorOutput(
            ename="KeyboardInterrupt",
            evalue="Eksekusi sel dihentikan.",
            traceback=(),
        ),),
    )


def main() -> int:
    kanal = _kanal_protokol()
    _pasang_interrupt_windows()
    masuk = sys.stdin
    backend = LocalPythonBackend()
    kernel = backend.start_kernel(name=os.environ.get("WORKBENCH_KERNEL_NAME") or None).id
    _kirim(kanal, {"type": "ready", "python": platform.python_version(),
                   "pid": os.getpid()})

    while True:
        try:
            baris = masuk.readline()
        except KeyboardInterrupt:
            # Interrupt yang tiba saat kernel menganggur tidak berarti apa-apa.
            continue
        if not baris:
            return 0
        try:
            pesan = json.loads(baris)
        except ValueError:
            _kirim(kanal, {"type": "error", "detail": "baris protokol bukan JSON"})
            continue
        if not isinstance(pesan, dict):
            continue
        rid = pesan.get("id")
        op = pesan.get("op")

        if op == "execute":
            kode = pesan.get("code")
            if not isinstance(kode, str):
                _kirim(kanal, {"id": rid, "type": "error", "detail": "code wajib teks"})
                continue
            def _stream(nama: str, teks: str, _rid: Any = rid) -> None:
                _kirim(kanal, {"id": _rid, "type": "stream", "name": nama, "text": teks})

            try:
                # Batas waktu dijaga induk (interrupt lalu kill); di sini tanpa batas.
                hasil = backend.execute(kernel, kode, timeout=None, on_stream=_stream)
            except KeyboardInterrupt:
                hasil = _dihentikan(None)
            _kirim(kanal, {"id": rid, "type": "result", "result": hasil.to_public()})
        elif op == "restart":
            backend.restart_kernel(kernel)
            _kirim(kanal, {"id": rid, "type": "ok"})
        elif op == "ping":
            _kirim(kanal, {"id": rid, "type": "ok"})
        elif op == "shutdown":
            _kirim(kanal, {"id": rid, "type": "ok"})
            return 0
        else:
            _kirim(kanal, {"id": rid, "type": "error", "detail": f"op tidak dikenal: {op!r}"})


if __name__ == "__main__":
    raise SystemExit(main())
