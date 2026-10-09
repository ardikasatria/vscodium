"""Normalisasi permintaan Core berlapis menjadi CheckpointRequest datar."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .errors import InvalidParamsError, WorkspaceMissingError
from .models import CheckpointRequest


def normalize_request(
    request: Any,
    *,
    workspace_base: Path | str | None = None,
) -> CheckpointRequest:
    """Terima bentuk datar atau bentuk Core (``module.checkpoint``).

    ``workspace_base`` dipakai bila request Core hanya punya
    ``workspace.location`` relatif. Agent biasanya sudah mengirim
    ``workspace_root`` absolut.
    """
    if isinstance(request, CheckpointRequest):
        return request

    if hasattr(request, "handler") and hasattr(request, "artifact"):
        return CheckpointRequest(
            handler=str(request.handler),
            artifact=str(request.artifact),
            timeout_seconds=int(getattr(request, "timeout_seconds", 120)),
            params=dict(getattr(request, "params", {}) or {}),
            workspace_root=str(getattr(request, "workspace_root", "") or ""),
            package_root=getattr(request, "package_root", None),
            module_id=str(getattr(request, "module_id", "") or ""),
            runtime_id=getattr(request, "runtime_id", None),
            runtime_endpoints=dict(getattr(request, "runtime_endpoints", {}) or {}),
        )

    module = getattr(request, "module", None)
    if module is None:
        raise InvalidParamsError(
            "Permintaan checkpoint tidak dikenali: butuh handler/artifact "
            "atau module.checkpoint."
        )
    cp = getattr(module, "checkpoint", None)
    if cp is None:
        raise InvalidParamsError(
            f"Module '{getattr(module, 'id', '?')}' tidak mendeklarasikan checkpoint."
        )

    workspace = getattr(request, "workspace", None)
    location = getattr(workspace, "location", None) if workspace else None
    workspace_root = _resolve_workspace_root(location, workspace_base)

    runtime = getattr(request, "runtime", None)
    endpoints: Mapping[str, str] = {}
    runtime_id = None
    if runtime is not None:
        runtime_id = getattr(runtime, "runtime_id", None)
        endpoints = dict(getattr(runtime, "endpoints", {}) or {})

    return CheckpointRequest(
        handler=str(cp.handler),
        artifact=str(cp.artifact),
        timeout_seconds=int(cp.timeout_seconds),
        params=dict(cp.params or {}),
        workspace_root=workspace_root,
        package_root=getattr(request, "package_root", None),
        module_id=str(getattr(module, "id", "") or ""),
        runtime_id=runtime_id,
        runtime_endpoints=endpoints,
    )


def _resolve_workspace_root(
    location: str | None,
    workspace_base: Path | str | None,
) -> str:
    if not location:
        raise WorkspaceMissingError(
            "Workspace belum disiapkan; checkpoint membutuhkan lokasi kerja."
        )
    lokasi = Path(location)
    if lokasi.is_absolute():
        return str(lokasi)
    if workspace_base is None:
        raise WorkspaceMissingError(
            "workspace_base wajib diisi bila location workspace relatif."
        )
    return str(Path(workspace_base).expanduser().resolve() / lokasi)
