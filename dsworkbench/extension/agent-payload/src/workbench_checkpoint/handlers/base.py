"""Kontrak handler checkpoint."""

from __future__ import annotations

from typing import Protocol

from ..models import CheckpointRequest, CheckpointResult


class CheckpointHandler(Protocol):
    """Satu implementasi handler allowlist."""

    name: str

    def run(self, request: CheckpointRequest) -> CheckpointResult: ...
