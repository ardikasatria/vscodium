"""Dataset Manager -- penyiapan dataset praktikum dengan verifikasi checksum.

Implementasi nyata port ``DatasetStore`` milik Workbench Core. Tiga janji yang
dipegang paket ini:

**Dataset tidak pernah dipakai sebelum terverifikasi.** Bahan dari sumber
ditulis ke direktori staging, diperiksa ukuran dan checksum-nya secara
streaming, baru kemudian dipromosikan menjadi material raw dengan satu operasi
atomik.

**Penyiapan bersifat idempoten.** Pemanggilan kedua atas dataset yang sama tidak
mengambil bahan apa pun dan tidak menulis satu byte pun.

**Perubahan pada data mentah terdeteksi.** Mode berkas read-only menghalangi
perubahan yang tidak disengaja, tetapi jaminan yang sesungguhnya adalah
checksum: apa pun yang berubah akan ketahuan.

Pemakaian:

    from workbench_datasets import DatasetManager

    manager = DatasetManager("~/.workbench/datasets")
    status = manager.ensure(module.dataset, package_root="courses/data-wrangling")

    if status.ready:
        manager.materialize(module.dataset, workspace_files)

Paket ini **tidak** mengimpor ``workbench_core`` maupun ``workbench_labspec``.
Kesesuaian dengan port bersifat struktural dan diuji pada
``tests/test_port_conformance.py``. Lihat ADR-013.

Resolusi path di dalam workspace sepenuhnya diserahkan kepada Workspace
Manager; paket ini tidak membuat kebijakan path sendiri.
"""

from __future__ import annotations

from .errors import (
    ChecksumMismatchError,
    ConcurrentPreparationError,
    DatasetError,
    IncompleteDatasetError,
    ManifestError,
    ReadOnlyViolationError,
    SizeMismatchError,
    SourceError,
    VerificationError,
)
from .manager import (
    DEFAULT_MOUNT,
    DatasetManager,
    MaterializeResult,
)
from .model import (
    MANIFEST_FILENAME,
    MANIFEST_VERSION,
    PROFILES,
    DatasetIdentity,
    DatasetManifest,
    DatasetStatus,
    FileEntry,
    entries_from_checksums,
    normalise_checksum,
    parse_checksum_file,
)
from .artifacts import ArtifactSource, Cancelled, validate_sources
from .sources import (
    DatasetSource,
    FixtureSource,
    LocalDirectorySource,
    SourceResult,
)
from .store import DatasetStore, StoredDataset, is_read_only
from .verify import (
    VerificationReport,
    build_entries,
    hash_file,
    verify_directory,
)

__all__ = [
    "ArtifactSource",
    "Cancelled",
    "validate_sources",
    "ChecksumMismatchError",
    "ConcurrentPreparationError",
    "DEFAULT_MOUNT",
    "DatasetError",
    "DatasetIdentity",
    "DatasetManager",
    "DatasetManifest",
    "DatasetSource",
    "DatasetStatus",
    "DatasetStore",
    "FileEntry",
    "FixtureSource",
    "IncompleteDatasetError",
    "LocalDirectorySource",
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "ManifestError",
    "MaterializeResult",
    "PROFILES",
    "ReadOnlyViolationError",
    "SizeMismatchError",
    "SourceError",
    "SourceResult",
    "StoredDataset",
    "VerificationError",
    "VerificationReport",
    "build_entries",
    "entries_from_checksums",
    "hash_file",
    "is_read_only",
    "normalise_checksum",
    "parse_checksum_file",
    "verify_directory",
]

__version__ = "0.1.0"
