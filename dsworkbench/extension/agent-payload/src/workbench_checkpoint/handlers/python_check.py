"""Handler ``python-check``.

Menjalankan skrip Python course package dengan argv bertipe -- cerminan
``datawrangling/Praktikum/checks/check_modul_01.py``:

    python <artifact> --dir <moduleDir> [--checksum <path>] [--jalankan]

Keluaran ``[LULUS]/[GAGAL]/[LEWAT]`` diurai menjadi butir terstruktur.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from ..errors import (
    ArtifactMissingError,
    InvalidParamsError,
    WorkspaceMissingError,
)
from ..models import (
    CheckpointFinding,
    CheckpointRequest,
    CheckpointResult,
    CheckpointStatus,
)
from ..parse import aggregate_status, parse_python_check_output, summarise
from ..paths import assert_relative_safe, resolve_under
from ..process import ProcessRunner


class PythonCheckHandler:
    """Implementasi handler ``python-check``."""

    name = "python-check"

    def __init__(
        self,
        *,
        process: ProcessRunner | None = None,
        python: str | None = None,
    ) -> None:
        self._process = process or ProcessRunner()
        self._python = python or sys.executable

    def run(self, request: CheckpointRequest) -> CheckpointResult:
        t0 = time.monotonic()
        try:
            argv, cwd = self._build(request)
        except (InvalidParamsError, ArtifactMissingError, WorkspaceMissingError) as exc:
            return CheckpointResult(
                status=CheckpointStatus.ERROR,
                summary=str(exc),
                findings=(
                    CheckpointFinding(
                        status=CheckpointStatus.ERROR,
                        title="persiapan python-check",
                        detail=str(exc),
                    ),
                ),
                duration_seconds=time.monotonic() - t0,
            )

        outcome = self._process.run(
            argv, cwd=cwd, timeout_seconds=float(request.timeout_seconds)
        )
        duration = time.monotonic() - t0

        if outcome.timed_out:
            return CheckpointResult(
                status=CheckpointStatus.ERROR,
                summary=(
                    f"Checkpoint melebihi batas waktu "
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

        if outcome.exit_code == 127:
            return CheckpointResult(
                status=CheckpointStatus.ERROR,
                summary="Penafsir Python tidak ditemukan untuk menjalankan pemeriksa.",
                findings=(
                    CheckpointFinding(
                        status=CheckpointStatus.ERROR,
                        title="python tidak tersedia",
                        detail=outcome.stderr or None,
                    ),
                ),
                duration_seconds=duration,
            )

        findings = parse_python_check_output(outcome.combined)
        if not findings:
            # Exit 1 tanpa baris status: skrip gagal sebelum mencatat butir.
            detail = (outcome.stderr or outcome.stdout or "").strip() or None
            return CheckpointResult(
                status=CheckpointStatus.ERROR,
                summary="Keluaran pemeriksa Python tidak memuat butir status.",
                findings=(
                    CheckpointFinding(
                        status=CheckpointStatus.ERROR,
                        title="keluaran tidak dapat diurai",
                        detail=_clip_detail(detail),
                    ),
                ),
                duration_seconds=duration,
            )

        status = aggregate_status(findings)
        # Exit code sumber: 1 bila ada GAGAL. Kita percaya butir, bukan hanya kode.
        return CheckpointResult(
            status=status,
            summary=summarise(status, findings),
            findings=findings,
            duration_seconds=duration,
        )

    def _build(self, request: CheckpointRequest) -> tuple[list[str], Path]:
        params = request.params or {}
        module_dir = params.get("moduleDir")
        if not isinstance(module_dir, str) or not module_dir.strip():
            raise InvalidParamsError(
                "params.moduleDir wajib diisi untuk handler python-check."
            )
        assert_relative_safe(module_dir, field="params.moduleDir")

        if not request.workspace_root:
            raise WorkspaceMissingError("workspace_root wajib diisi.")
        cwd = Path(request.workspace_root)
        if not cwd.is_dir():
            raise WorkspaceMissingError(
                "Direktori workspace tidak ditemukan atau belum disiapkan."
            )

        if not request.package_root:
            raise ArtifactMissingError(
                "package_root wajib diisi agar artefak pemeriksa dapat ditemukan."
            )
        artifact = resolve_under(
            Path(request.package_root), request.artifact, field="artifact"
        )
        if not artifact.is_file():
            raise ArtifactMissingError(
                f"Berkas pemeriksa tidak ditemukan: {request.artifact}"
            )

        argv = [self._python, str(artifact), "--dir", module_dir]

        checksum = params.get("checksumRef")
        if checksum is not None:
            if not isinstance(checksum, str):
                raise InvalidParamsError("params.checksumRef harus berupa teks.")
            checksum_path = resolve_under(
                Path(request.package_root), checksum, field="params.checksumRef"
            )
            argv.extend(["--checksum", str(checksum_path)])

        if params.get("runNotebook") is True:
            argv.append("--jalankan")

        return argv, cwd


def _clip_detail(text: str | None, limit: int = 240) -> str | None:
    if not text:
        return None
    one = " ".join(text.split())
    if len(one) <= limit:
        return one
    return one[: limit - 1] + "…"
