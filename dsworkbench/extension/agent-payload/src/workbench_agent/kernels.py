"""Pilih tempat kernel dijalankan berdasarkan RuntimeProfile modul.

Interpreter agent adalah kernel profil bawaan (``python-data-science``): sel
DW tetap dieksekusi in-process seperti sebelumnya. Profil lain dijalankan
sebagai proses anak memakai interpreter profilnya sendiri -- agent tidak
pernah mengimpor ``torch`` (ADR-041 §4).

Router tidak mengenal nama mata kuliah. Ia hanya mengenal id profil, dan id
itu hanya diterima bila tertulis di manifest agent.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Callable

from . import env_setup

try:
    from workbench_jupyter import KernelNotFoundError, SubprocessKernelBackend
except ImportError:  # pragma: no cover - lingkungan tanpa paket jupyter
    KernelNotFoundError = KeyError  # type: ignore[misc, assignment]
    SubprocessKernelBackend = None  # type: ignore[assignment, misc]

PenyediaPython = Callable[[str], Path]
PembuatBackend = Callable[[Path], Any]


class ProfileKernelRouter:
    """``JupyterBackend`` yang meneruskan tiap kernel ke backend profilnya."""

    def __init__(
        self,
        bawaan: Any,
        *,
        akar: Path | None = None,
        workspace_root: Path | None = None,
        penyedia_python: PenyediaPython | None = None,
        pembuat_backend: PembuatBackend | None = None,
    ) -> None:
        self._bawaan = bawaan
        self._akar = (akar or env_setup.akar_agent_dari_modul()).resolve()
        self._workspace = workspace_root
        self._penyedia_python = penyedia_python or (
            lambda pid: env_setup.python_siap(self._akar, pid))
        self._pembuat_backend = pembuat_backend or self._backend_proses
        self._per_profil: dict[str, Any] = {}
        self._pemilik: dict[str, tuple[str, Any]] = {}
        #: Router dipanggil dari utas transport relay, pekerja job, dan pipa IDE.
        self._kunci = threading.RLock()

    def _backend_proses(self, python: Path) -> Any:
        if SubprocessKernelBackend is None:  # pragma: no cover
            raise RuntimeError("paket workbench_jupyter tidak lengkap")
        return SubprocessKernelBackend(python, cwd=self._workspace)

    # ------------------------------------------------------------------

    def start_kernel_for_profile(self, profil_id: str | None, *, name: str | None = None,
                                 cwd: Path | None = None, proses_anak: bool = False):
        """Mulai kernel untuk profil; ``ProfilTidakDikenal``/``ProfilBelumSiap``.

        ``cwd``: akar mata kuliah per-course (ADR-050); tanpa itu akar workspace.

        ``proses_anak``: jalur pipa IDE (ADR-072 §4) -- profil bawaan pun
        dijalankan sebagai proses anak, memakai interpreter agent ini (yang
        memang interpreter praktikum profil bawaan). Lewat relay nilainya
        ``False`` dan perilakunya tidak berubah.
        """
        p = env_setup.profil(self._akar, profil_id)
        ekstra = {"cwd": cwd} if cwd is not None else {}
        if p.id == env_setup.PROFIL_BAWAAN:
            # Tanpa profil bawaan (``run --no-default-profile``) penolakannya
            # tetap datang dari backend pengganti, apa pun asal permintaannya.
            if not proses_anak or getattr(self._bawaan, "tanpa_profil_bawaan", False):
                info = self._bawaan.start_kernel(name=name, **ekstra)
                with self._kunci:
                    self._pemilik[info.id] = (p.id, self._bawaan)
                return info
            python = Path(sys.executable)
        else:
            python = self._penyedia_python(p.id)
        with self._kunci:
            backend = self._per_profil.get(p.id)
            if backend is None:
                backend = self._pembuat_backend(python)
                self._per_profil[p.id] = backend
        info = backend.start_kernel(name=name or p.id, **ekstra)
        with self._kunci:
            self._pemilik[info.id] = (p.id, backend)
        return info

    def profile_of(self, kernel_id: str) -> str | None:
        with self._kunci:
            pemilik = self._pemilik.get(kernel_id)
        return pemilik[0] if pemilik else None

    def _backend(self, kernel_id: str) -> Any:
        with self._kunci:
            pemilik = self._pemilik.get(kernel_id)
        if pemilik is None:
            raise KernelNotFoundError(kernel_id)
        return pemilik[1]

    # --- JupyterBackend ------------------------------------------------

    def ping(self) -> bool:
        return bool(self._bawaan.ping())

    def start_kernel(self, *, name: str | None = None):
        return self.start_kernel_for_profile(None, name=name)

    def stop_kernel(self, kernel_id: str) -> None:
        backend = self._backend(kernel_id)
        try:
            backend.stop_kernel(kernel_id)
        finally:
            with self._kunci:
                self._pemilik.pop(kernel_id, None)

    def interrupt_kernel(self, kernel_id: str) -> None:
        self._backend(kernel_id).interrupt_kernel(kernel_id)

    def restart_kernel(self, kernel_id: str):
        return self._backend(kernel_id).restart_kernel(kernel_id)

    def kernel_status(self, kernel_id: str):
        return self._backend(kernel_id).kernel_status(kernel_id)

    #: Streaming diteruskan bila backend kernel itu mendukungnya.
    supports_streaming = True

    def execute(self, kernel_id: str, code: str, *, timeout: float | None = 60.0,
                on_stream=None):
        backend = self._backend(kernel_id)
        if on_stream is not None and getattr(backend, "supports_streaming", False):
            return backend.execute(kernel_id, code, timeout=timeout, on_stream=on_stream)
        return backend.execute(kernel_id, code, timeout=timeout)
