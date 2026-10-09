"""Checkpoint Runner -- registry handler, bukan perintah dari YAML.

Implementasi nyata port ``CheckpointRunner`` milik Workbench Core. Kesesuaian
bersifat struktural: paket ini tidak mengimpor ``workbench_core``.

Alur:

```text
LabSpec memilih handler + params bertipe
        ↓
RegistryCheckpointRunner menolak handler di luar allowlist
        ↓
Handler menyusun argv array (tanpa shell)
        ↓
Parser mengubah keluaran sumber → CheckpointFinding
        ↓
CheckpointResult terstruktur (bukan nilai akhir)
```
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .errors import UnknownHandlerError
from .handlers import CheckpointHandler, default_handlers
from .handlers.python_check import PythonCheckHandler
from .handlers.sql_check import SqlCheckHandler
from .models import (
    KNOWN_HANDLERS,
    CheckpointFinding,
    CheckpointRequest,
    CheckpointResult,
    CheckpointStatus,
)
from .normalize import normalize_request
from .process import ProcessRunner
from .sql_exec import PsqlCliExecutor, SqlConnectionConfig, SqlExecutor


class RegistryCheckpointRunner:
    """Penjalan checkpoint berbasis allowlist handler.

    ``workspace_base`` dipakai ketika permintaan datang dalam bentuk Core
    (``workspace.location`` relatif). Agent biasanya mengirim
    ``CheckpointRequest`` datar dengan ``workspace_root`` absolut.
    """

    def __init__(
        self,
        *,
        handlers: Mapping[str, CheckpointHandler] | None = None,
        workspace_base: Path | str | None = None,
        process: ProcessRunner | None = None,
        sql_executor: SqlExecutor | None = None,
        python: str | None = None,
        sql_config: SqlConnectionConfig | None = None,
    ) -> None:
        process = process or ProcessRunner()
        if handlers is not None:
            self._handlers = dict(handlers)
        else:
            sql = SqlCheckHandler(
                executor=sql_executor
                if sql_executor is not None
                else PsqlCliExecutor(config=sql_config, process=process)
            )
            self._handlers = default_handlers(
                python=PythonCheckHandler(process=process, python=python),
                sql=sql,
            )
        self._workspace_base = (
            Path(workspace_base).expanduser() if workspace_base else None
        )

    @property
    def known_handlers(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def run(self, request: Any) -> CheckpointResult:
        """Jalankan checkpoint. Menerima bentuk datar atau bentuk Core."""
        try:
            req = normalize_request(request, workspace_base=self._workspace_base)
        except Exception as exc:  # noqa: BLE001 - jadi hasil ERROR terstruktur
            return CheckpointResult(
                status=CheckpointStatus.ERROR,
                summary=str(exc),
                findings=(
                    CheckpointFinding(
                        status=CheckpointStatus.ERROR,
                        title="permintaan tidak sah",
                        detail=str(exc),
                    ),
                ),
                duration_seconds=0.0,
            )

        handler = self._handlers.get(req.handler)
        if handler is None:
            known = tuple(sorted(self._handlers)) or KNOWN_HANDLERS
            err = UnknownHandlerError(req.handler, known=known)
            return CheckpointResult(
                status=CheckpointStatus.ERROR,
                summary=str(err),
                findings=(
                    CheckpointFinding(
                        status=CheckpointStatus.ERROR,
                        title="handler tidak dikenal",
                        detail=str(err),
                    ),
                ),
                duration_seconds=0.0,
            )
        return handler.run(req)
