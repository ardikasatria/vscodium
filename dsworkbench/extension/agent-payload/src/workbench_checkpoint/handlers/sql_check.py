"""Handler ``sql-check``.

Menjalankan skrip SQL course package lewat :class:`SqlExecutor` terhadap
service yang dideklarasikan di LabSpec. Cerminan
``pergudangandata/Praktikum/sql/modul-01/check.sql``.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..errors import ArtifactMissingError, InvalidParamsError
from ..models import (
    CheckpointFinding,
    CheckpointRequest,
    CheckpointResult,
    CheckpointStatus,
)
from ..parse import aggregate_status, parse_sql_check_output, summarise
from ..paths import resolve_under
from ..sql_exec import SqlExecutor, UnavailableSqlExecutor


class SqlCheckHandler:
    """Implementasi handler ``sql-check``."""

    name = "sql-check"

    def __init__(self, *, executor: SqlExecutor | None = None) -> None:
        self._executor = executor if executor is not None else UnavailableSqlExecutor()

    def run(self, request: CheckpointRequest) -> CheckpointResult:
        t0 = time.monotonic()
        try:
            service, database, script = self._prepare(request)
        except (InvalidParamsError, ArtifactMissingError) as exc:
            return CheckpointResult(
                status=CheckpointStatus.ERROR,
                summary=str(exc),
                findings=(
                    CheckpointFinding(
                        status=CheckpointStatus.ERROR,
                        title="persiapan sql-check",
                        detail=str(exc),
                    ),
                ),
                duration_seconds=time.monotonic() - t0,
            )

        if not request.runtime_endpoints.get(service):
            return CheckpointResult(
                status=CheckpointStatus.SKIPPED,
                summary=(
                    f"Service '{service}' belum berjalan; "
                    "checkpoint SQL dilewati."
                ),
                findings=(
                    CheckpointFinding(
                        status=CheckpointStatus.SKIPPED,
                        title=f"service {service}",
                        detail="Jalankan runtime modul sebelum memeriksa.",
                    ),
                ),
                duration_seconds=time.monotonic() - t0,
            )

        outcome = self._executor.run_script(
            service=service,
            database=database,
            script_path=script,
            timeout_seconds=float(request.timeout_seconds),
            endpoints=request.runtime_endpoints,
            runtime_id=request.runtime_id,
        )
        duration = time.monotonic() - t0

        if outcome.exit_code == 127 or (
            isinstance(self._executor, UnavailableSqlExecutor)
        ):
            return CheckpointResult(
                status=CheckpointStatus.SKIPPED,
                summary="Eksekutor SQL belum tersedia di lingkungan ini.",
                findings=(
                    CheckpointFinding(
                        status=CheckpointStatus.SKIPPED,
                        title="sql executor",
                        detail=outcome.stderr or None,
                    ),
                ),
                duration_seconds=duration,
            )

        if outcome.timed_out:
            return CheckpointResult(
                status=CheckpointStatus.ERROR,
                summary=(
                    f"Checkpoint SQL melebihi batas waktu "
                    f"{request.timeout_seconds} detik."
                ),
                findings=(
                    CheckpointFinding(
                        status=CheckpointStatus.ERROR,
                        title="batas waktu",
                        detail=outcome.stderr or None,
                    ),
                ),
                duration_seconds=duration,
            )

        findings = parse_sql_check_output(outcome.combined)
        if not findings:
            if outcome.exit_code != 0:
                return CheckpointResult(
                    status=CheckpointStatus.ERROR,
                    summary="psql gagal dan tidak menghasilkan butir pemeriksaan.",
                    findings=(
                        CheckpointFinding(
                            status=CheckpointStatus.ERROR,
                            title="psql",
                            detail=_clip(outcome.stderr or outcome.stdout),
                        ),
                    ),
                    duration_seconds=duration,
                )
            return CheckpointResult(
                status=CheckpointStatus.ERROR,
                summary="Keluaran SQL tidak memuat butir status.",
                findings=(
                    CheckpointFinding(
                        status=CheckpointStatus.ERROR,
                        title="keluaran tidak dapat diurai",
                        detail=_clip(outcome.stdout),
                    ),
                ),
                duration_seconds=duration,
            )

        status = aggregate_status(findings)
        return CheckpointResult(
            status=status,
            summary=summarise(status, findings),
            findings=findings,
            duration_seconds=duration,
        )

    def _prepare(self, request: CheckpointRequest) -> tuple[str, str, Path]:
        params = request.params or {}
        service = params.get("service")
        database = params.get("database")
        if not isinstance(service, str) or not service.strip():
            raise InvalidParamsError(
                "params.service wajib diisi untuk handler sql-check."
            )
        if not isinstance(database, str) or not database.strip():
            raise InvalidParamsError(
                "params.database wajib diisi untuk handler sql-check."
            )
        if not request.package_root:
            raise ArtifactMissingError(
                "package_root wajib diisi agar skrip SQL dapat ditemukan."
            )
        script = resolve_under(
            Path(request.package_root), request.artifact, field="artifact"
        )
        if not script.is_file():
            raise ArtifactMissingError(
                f"Skrip SQL tidak ditemukan: {request.artifact}"
            )
        return service.strip(), database.strip(), script


def _clip(text: str | None, limit: int = 240) -> str | None:
    if not text:
        return None
    one = " ".join(text.split())
    if len(one) <= limit:
        return one
    return one[: limit - 1] + "…"
