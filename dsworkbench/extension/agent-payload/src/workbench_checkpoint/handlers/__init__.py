"""Registry handler checkpoint."""

from __future__ import annotations

from typing import Mapping

from .base import CheckpointHandler
from .python_check import PythonCheckHandler
from .sql_check import SqlCheckHandler

__all__ = [
    "CheckpointHandler",
    "PythonCheckHandler",
    "SqlCheckHandler",
    "default_handlers",
]


def default_handlers(
    *,
    python: PythonCheckHandler | None = None,
    sql: SqlCheckHandler | None = None,
) -> dict[str, CheckpointHandler]:
    """Bangun registry bawaan allowlist LabSpec."""
    return {
        "python-check": python or PythonCheckHandler(),
        "sql-check": sql or SqlCheckHandler(),
    }
