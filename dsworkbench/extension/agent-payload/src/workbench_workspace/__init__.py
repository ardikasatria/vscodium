"""Workspace Manager -- workspace mahasiswa dan kebijakan filesystem yang aman.

Implementasi nyata port ``WorkspaceStore`` milik Workbench Core. Dua janji yang
dipegang paket ini:

**Pekerjaan mahasiswa bertahan melewati runtime.** Container boleh dibuat dan
dihancurkan berkali-kali; notebook, SQL, dan laporan tetap ada. ``ensure``
bersifat idempoten dan tidak pernah menimpa berkas yang sudah ada.

**Tidak ada path yang keluar dari akar workspace.** Setiap path melewati
:class:`~workbench_workspace.resolver.PathResolver` sebelum menyentuh
filesystem, dan absolute path komputer tidak pernah keluar dari paket ini.

Pemakaian:

    from workbench_workspace import WorkspaceManager, WorkspaceRequest

    manager = WorkspaceManager(base="~/.workbench/workspaces")
    handle = manager.ensure(WorkspaceRequest(
        workspace_id="ws-abc123",
        module_id="dw-module-01",
        course_id="data-wrangling",
        course_version="2026.1",
        policy=policy,
        package_root="courses/data-wrangling",
    ))

    files = manager.files(handle.workspace_id, policy)
    files.write_text("output/laporan.md", "# Laporan\\n")

Paket ini **tidak** mengimpor ``workbench_core``. Kesesuaian dengan port
bersifat struktural dan diuji pada ``tests/test_port_conformance.py``.
"""

from __future__ import annotations

from .errors import (
    AlreadyExistsError,
    InvalidPathError,
    LimitExceededError,
    MetadataError,
    PathEscapeError,
    PathPolicyError,
    ReadOnlyError,
    WorkspaceError,
    WorkspaceNotFoundError,
    WriteConflictError,
)
from .files import Entry, FileStat, WorkspaceFiles
from .manager import (
    WorkspaceHandle,
    WorkspaceInfo,
    WorkspaceManager,
    WorkspaceRequest,
)
from .metadata import METADATA_VERSION, WorkspaceMetadata
from .policy import INTERNAL_DIR, Limits, ReadOnlyPolicy, WorkspaceSettings
from .resolver import PathResolver, ResolvedPath, check_path_text, safe_archive_members

__all__ = [
    "AlreadyExistsError",
    "Entry",
    "FileStat",
    "INTERNAL_DIR",
    "InvalidPathError",
    "Limits",
    "LimitExceededError",
    "METADATA_VERSION",
    "MetadataError",
    "PathEscapeError",
    "PathPolicyError",
    "PathResolver",
    "ReadOnlyError",
    "ReadOnlyPolicy",
    "ResolvedPath",
    "WorkspaceError",
    "WorkspaceFiles",
    "WorkspaceHandle",
    "WorkspaceInfo",
    "WorkspaceManager",
    "WorkspaceMetadata",
    "WorkspaceNotFoundError",
    "WorkspaceRequest",
    "WorkspaceSettings",
    "WriteConflictError",
    "check_path_text",
    "safe_archive_members",
]

__version__ = "0.1.0"
