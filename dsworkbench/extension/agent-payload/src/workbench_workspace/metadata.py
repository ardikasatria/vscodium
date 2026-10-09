"""Metadata workspace.

Disimpan sebagai JSON di ``.workbench/workspace.json`` di dalam workspace itu
sendiri. Alasannya: workspace harus dapat dikenali ulang setelah local agent
dipasang ulang, komputer berganti, atau direktori dipindahkan. Metadata yang
hanya hidup di database agent akan hilang pada ketiga keadaan itu.

Berkas ini berada di area read-only bagi mahasiswa (lihat ``policy.py``):
metadata bukan milik mereka untuk diubah, dan mengubahnya dapat memutus
keterkaitan workspace dengan module yang benar.

Bentuknya sengaja rata dan berversi. Ketika Component 02 menambahkan
reproducibility manifest, ia menambah kunci baru -- bukan mengganti berkas ini.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import MetadataError

#: Versi bentuk metadata. Dinaikkan bila bentuknya berubah tidak kompatibel.
METADATA_VERSION = 1

#: Nama berkas metadata di dalam direktori internal.
METADATA_FILENAME = "workspace.json"


@dataclass
class WorkspaceMetadata:
    """Identitas dan keadaan satu workspace.

    ``state`` memakai nilai yang sama dengan ``WorkspaceState`` milik Core
    (``CREATED``, ``ACTIVE``, ``ARCHIVED``) tanpa mengimpornya, sesuai
    keputusan bahwa implementasi port tidak bergantung pada Core.
    """

    workspace_id: str
    module_id: str
    course_id: str
    course_version: str
    state: str = "CREATED"
    created_at: str = ""
    updated_at: str = ""
    starter_written: list[str] = field(default_factory=list)
    read_only: list[str] = field(default_factory=list)
    metadata_version: int = METADATA_VERSION

    # ------------------------------------------------------------------

    def touch(self, *, now: datetime | None = None) -> None:
        saat = (now or datetime.now(timezone.utc)).isoformat()
        if not self.created_at:
            self.created_at = saat
        self.updated_at = saat

    def to_json(self) -> str:
        # sort_keys agar berkas deterministik: dua workspace dengan isi sama
        # menghasilkan byte yang sama, sehingga diff dan checksum bermakna.
        return json.dumps(asdict(self), indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "WorkspaceMetadata":
        versi = data.get("metadata_version")
        if versi != METADATA_VERSION:
            raise MetadataError(
                f"metadata_version {versi!r} tidak dikenal; "
                f"versi yang didukung adalah {METADATA_VERSION}"
            )
        dikenal = {f for f in cls.__dataclass_fields__}
        asing = set(data) - dikenal
        if asing:
            raise MetadataError(
                "metadata memuat kunci yang tidak dikenal: " + ", ".join(sorted(asing))
            )
        wajib = ("workspace_id", "module_id", "course_id", "course_version")
        hilang = [k for k in wajib if not data.get(k)]
        if hilang:
            raise MetadataError("metadata tidak lengkap: " + ", ".join(hilang))
        return cls(**data)


def metadata_path(workspace_root: Path, internal_dir: str) -> Path:
    return workspace_root / internal_dir / METADATA_FILENAME


def read_metadata(path: Path) -> WorkspaceMetadata:
    try:
        teks = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise MetadataError(f"metadata workspace tidak ditemukan di {path.name}") from exc
    except UnicodeDecodeError as exc:
        raise MetadataError("metadata workspace bukan teks UTF-8 yang sah") from exc

    try:
        data = json.loads(teks)
    except json.JSONDecodeError as exc:
        raise MetadataError(
            f"metadata workspace bukan JSON yang sah (baris {exc.lineno}, kolom {exc.colno})"
        ) from exc

    if not isinstance(data, dict):
        raise MetadataError("metadata workspace harus berupa objek JSON")
    return WorkspaceMetadata.from_mapping(data)
