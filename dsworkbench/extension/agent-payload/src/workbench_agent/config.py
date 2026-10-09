"""Konfigurasi agent.

Tidak memuat secret. Kredensial perangkat disimpan terpisah oleh
:class:`~workbench_agent.state.StateStore`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 0.2.0: paket unduhan mulai menyertakan workbench_checkpoint dan
# workbench_workspace, dan agent mengikat operasi keduanya. Versi dinaikkan
# supaya agent lama dapat dikenali -- pada 0.1.0 keduanya identik dari sisi
# server, dan itulah sebabnya salinan usang tidak pernah terdeteksi.
# 0.2.1: LocalPython mengevaluasi ekspresi terakhir + MIME HTML/PNG
# (tabel pandas, plot) seperti Jupyter.
# 0.3.0: rilis pertama aplikasi desktop Data Science Workbench (installer +
# dsw-updater, Prompt 12). Versi ini sama dengan tag rilis dan versi web.
AGENT_VERSION = "0.4.10"


def default_state_dir() -> Path:
    """Direktori keadaan agent.

    Mengikuti XDG bila tersedia; selain itu ``~/.workbench-agent``. Path absolut
    host pengguna tidak pernah di-hard-code ke dalam repository.
    """
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "workbench-agent"
    return Path.home() / ".workbench-agent"


@dataclass(frozen=True)
class AgentConfig:
    """Parameter yang mengatur perilaku agent."""

    #: URL dasar Control API. Produksi: lewat reverse proxy HTTPS.
    #: Pengembangan: ``http://127.0.0.1:8000``.
    control_plane_url: str = "http://127.0.0.1:8000"

    state_dir: Path | None = None

    #: Jeda antar-poll ketika antrean kosong dan long-poll tidak dipakai.
    poll_interval_seconds: float = 1.0

    #: Long-poll saat agent menganggur: server menahan poll hingga ada operasi
    #: atau selama ini (server boleh memangkasnya). Server lama mengabaikannya
    #: dan agent kembali ke ``poll_interval_seconds``. 0 mematikan.
    long_poll_seconds: float = 20.0

    #: Cadangan bila Control API belum memberi tahu interval heartbeat.
    heartbeat_interval_seconds: float = 30.0

    #: Backoff reconnect.
    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    backoff_multiplier: float = 2.0

    #: Timeout HTTP per permintaan.
    request_timeout_seconds: float = 30.0

    #: Nama yang dilaporkan saat pairing, bila pemanggil tidak mengisi.
    default_device_name: str = "Komputer mahasiswa"

    def resolved_state_dir(self) -> Path:
        return self.state_dir if self.state_dir is not None else default_state_dir()

    def __post_init__(self) -> None:
        url = self.control_plane_url.rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise ValueError(
                f"control_plane_url '{self.control_plane_url}' harus memuat skema"
            )
        object.__setattr__(self, "control_plane_url", url)
