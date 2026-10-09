"""Bentuk data Checkpoint Runner.

Sepadan dengan ``workbench_core.ports`` untuk CheckpointStatus,
CheckpointFinding, dan CheckpointResult. Kesesuaian dijaga
``tests/test_port_conformance.py``, bukan oleh impor Core.

``CheckpointRequest`` di sini sengaja **datar**: agent dan CLI menyusun field
eksplisit. Bentuk berlapis milik Core (``module.checkpoint``) dinormalisasi
oleh :func:`workbench_checkpoint.normalize.normalize_request`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class CheckpointStatus(str, Enum):
    """Hasil pemeriksaan otomatis.

    ``SKIPPED`` ada karena pemeriksaan yang tidak dapat dijalankan bukanlah
    pemeriksaan yang gagal -- keduanya tidak boleh tertukar di UI.
    """

    PASSED = "LULUS"
    FAILED = "GAGAL"
    SKIPPED = "LEWAT"
    ERROR = "KESALAHAN"


@dataclass(frozen=True)
class CheckpointFinding:
    """Satu butir hasil pemeriksaan."""

    status: CheckpointStatus
    title: str
    detail: str | None = None


@dataclass(frozen=True)
class CheckpointResult:
    """Hasil terstruktur, bukan teks bebas.

    Checkpoint tidak pernah mengembalikan jawaban, dan bukan nilai akhir.
    """

    status: CheckpointStatus
    summary: str
    findings: Sequence[CheckpointFinding] = ()
    duration_seconds: float | None = None

    @property
    def passed(self) -> bool:
        return self.status is CheckpointStatus.PASSED

    def to_public(self) -> dict[str, Any]:
        """Bentuk aman untuk relay / UI."""
        return {
            "status": self.status.value,
            "summary": self.summary,
            "passed": self.passed,
            "durationSeconds": self.duration_seconds,
            "findings": [
                {
                    "status": f.status.value,
                    "title": f.title,
                    "detail": f.detail,
                }
                for f in self.findings
            ],
        }


@dataclass(frozen=True)
class CheckpointRequest:
    """Permintaan menjalankan satu checkpoint.

    ``workspace_root`` dan ``package_root`` adalah path absolut yang **sudah**
    di-resolve pemanggil (Workspace Manager / agent). Paket ini tidak membuat
    kebijakan path sendiri; ia hanya menolak traversal pada field relatif.
    """

    handler: str
    artifact: str
    timeout_seconds: int
    params: Mapping[str, Any] = field(default_factory=dict)
    workspace_root: str = ""
    package_root: str | None = None
    module_id: str = ""
    runtime_id: str | None = None
    runtime_endpoints: Mapping[str, str] = field(default_factory=dict)


#: Handler yang diizinkan. Harus selaras dengan LabSpec CHECKPOINT_HANDLERS.
KNOWN_HANDLERS = ("python-check", "sql-check")
