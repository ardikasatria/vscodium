"""Checkpoint Runner -- handler registry untuk pemeriksa machine-checkable.

Capability reusable. Paket ini tidak mengenal nama course; LabSpec memilih
handler dari allowlist, dan pemetaan ke implementasi berada di sini
(ADR-006, ADR-026).

Pemakaian:

    from workbench_checkpoint import CheckpointRequest, RegistryCheckpointRunner

    runner = RegistryCheckpointRunner(workspace_base="~/workbench")
    result = runner.run(CheckpointRequest(
        handler="python-check",
        artifact="checks/check_module_01.py",
        timeout_seconds=180,
        params={"moduleDir": "modul-01-eksplorasi", "runNotebook": False},
        workspace_root="/path/ke/workspace",
        package_root="/path/ke/course-package",
    ))
"""

from __future__ import annotations

from .errors import (
    ArtifactMissingError,
    CheckpointError,
    InvalidArtifactError,
    InvalidParamsError,
    UnknownHandlerError,
    WorkspaceMissingError,
)
from .models import (
    KNOWN_HANDLERS,
    CheckpointFinding,
    CheckpointRequest,
    CheckpointResult,
    CheckpointStatus,
)
from .parse import (
    aggregate_status,
    parse_python_check_output,
    parse_sql_check_output,
    summarise,
)
from .process import ProcessOutcome, ProcessRunner
from .runner import RegistryCheckpointRunner
from .sql_exec import (
    PsqlCliExecutor,
    SqlConnectionConfig,
    SqlExecutor,
    UnavailableSqlExecutor,
)

__all__ = [
    "ArtifactMissingError",
    "CheckpointError",
    "CheckpointFinding",
    "CheckpointRequest",
    "CheckpointResult",
    "CheckpointStatus",
    "InvalidArtifactError",
    "InvalidParamsError",
    "KNOWN_HANDLERS",
    "ProcessOutcome",
    "ProcessRunner",
    "PsqlCliExecutor",
    "RegistryCheckpointRunner",
    "SqlConnectionConfig",
    "SqlExecutor",
    "UnavailableSqlExecutor",
    "UnknownHandlerError",
    "WorkspaceMissingError",
    "aggregate_status",
    "parse_python_check_output",
    "parse_sql_check_output",
    "summarise",
]

__version__ = "0.1.0"
