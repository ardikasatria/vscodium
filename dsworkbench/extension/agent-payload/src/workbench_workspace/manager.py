"""Workspace Manager.

Implementasi nyata port ``WorkspaceStore`` milik Workbench Core. Kesesuaiannya
bersifat **struktural**: paket ini tidak mengimpor ``workbench_core`` sama
sekali.

Itu bukan kebetulan, melainkan keputusan yang ditulis Core sendiri pada
``ports.py``:

    port dinyatakan sebagai ``Protocol``, sehingga implementasi tidak perlu
    mengimpor Core -- local agent tidak menanggung dependency control plane.

Konsekuensinya, ``WorkspaceRequest`` dan ``WorkspaceHandle`` didefinisikan
ulang di sini dengan bentuk yang sama. Risiko bentuknya menyimpang dijaga oleh
``tests/test_port_conformance.py``, yang membandingkannya dengan Core yang
sesungguhnya bila paket itu memang tersedia.

Dua janji yang dipegang modul ini:

**Workspace bertahan melewati runtime.** ``ensure`` bersifat idempoten dan
tidak pernah menimpa pekerjaan mahasiswa. Container boleh dibuat dan
dihancurkan berkali-kali; notebook tetap ada.

**Absolute path tidak pernah keluar.** ``WorkspaceHandle.location`` adalah path
relatif terhadap direktori dasar workspace. Struktur direktori komputer
mahasiswa bukan urusan control plane, dan tidak perlu melintasi relay.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .errors import (
    InvalidPathError,
    PathEscapeError,
    WorkspaceError,
    WorkspaceNotFoundError,
)
from .files import WorkspaceFiles
from .metadata import (
    METADATA_FILENAME,
    WorkspaceMetadata,
    metadata_path,
    read_metadata,
)
from .policy import (
    INTERNAL_DIR,
    Limits,
    PolicySource,
    ReadOnlyPolicy,
    read_only_entries,
    starter_entries,
)
from .resolver import PathResolver

#: Nama direktori tempat workspace yang diarsipkan dipindahkan.
ARCHIVE_DIR = ".archive"


# ----------------------------------------------------------------------
# Bentuk yang sepadan dengan port Core
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceRequest:
    """Permintaan penyiapan workspace. Sepadan dengan ``ports.WorkspaceRequest``."""

    workspace_id: str
    module_id: str
    course_id: str
    course_version: str
    policy: PolicySource | None = None
    package_root: str | None = None


@dataclass(frozen=True)
class WorkspaceHandle:
    """Workspace yang siap dipakai. Sepadan dengan ``ports.WorkspaceHandle``."""

    workspace_id: str
    location: str
    created: bool
    starter_written: tuple[str, ...] = ()
    read_only: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkspaceInfo:
    """Keterangan lengkap satu workspace, untuk UI dan diagnosis."""

    workspace_id: str
    location: str
    state: str
    module_id: str
    course_id: str
    course_version: str
    created_at: str
    updated_at: str
    read_only: tuple[str, ...] = ()
    file_count: int = 0
    size_bytes: int = 0


# ----------------------------------------------------------------------


class WorkspaceManager:
    """Pengelola seluruh workspace pada satu komputer mahasiswa.

    ``base`` adalah direktori dasar milik local agent. Seluruh workspace berada
    di bawahnya, satu direktori per ``workspace_id``.
    """

    def __init__(self, base: Path | str, *, limits: Limits | None = None):
        self._base = Path(base).expanduser()
        self._limits = limits or Limits()
        # Resolver tingkat dasar: menjaga workspace_id tidak menjadi jalan
        # keluar. Identifier datang dari Core, tetapi memeriksanya tetap murah
        # dan menghilangkan satu asumsi.
        self._base.mkdir(parents=True, exist_ok=True)
        self._base_resolver = PathResolver(self._base, limits=self._limits)

    @property
    def base(self) -> Path:
        return self._base

    # ------------------------------------------------------------------
    # Port WorkspaceStore
    # ------------------------------------------------------------------

    def ensure(self, request: WorkspaceRequest) -> WorkspaceHandle:
        """Siapkan workspace bila belum ada; jangan menimpa pekerjaan yang ada.

        Idempoten. Pemanggilan kedua atas permintaan yang sama tidak menulis
        ulang berkas awal dan tidak mengubah satu byte pun milik mahasiswa.
        """
        lokasi = self._location_of(request.workspace_id)
        akar = self._base / lokasi
        baru = not akar.exists()

        akar.mkdir(parents=True, exist_ok=True)
        (akar / INTERNAL_DIR).mkdir(parents=True, exist_ok=True)

        files = self._files_for(akar, request.policy)
        meta = self._load_or_create_metadata(akar, request, baru)

        ditulis = self._write_starters(files, request, meta)

        meta.read_only = list(read_only_entries(request.policy))
        meta.state = "ACTIVE"
        meta.touch()
        self._save_metadata(akar, meta)

        return WorkspaceHandle(
            workspace_id=request.workspace_id,
            location=lokasi,
            created=baru,
            starter_written=tuple(ditulis),
            read_only=tuple(meta.read_only),
        )

    def archive(self, workspace_id: str) -> None:
        """Arsipkan workspace. Tidak menghapus berkas mahasiswa.

        Workspace dipindahkan ke ``.archive/`` dan statusnya ditandai. Ia tetap
        dapat dibaca dan dipulihkan; tidak ada berkas yang dihapus di sini
        maupun di tempat lain pada modul ini.
        """
        lokasi = self._location_of(workspace_id)
        akar = self._base / lokasi
        if not akar.is_dir():
            raise WorkspaceNotFoundError(f"workspace '{workspace_id}' tidak ditemukan")

        try:
            meta = read_metadata(metadata_path(akar, INTERNAL_DIR))
            meta.state = "ARCHIVED"
            meta.touch()
            self._save_metadata(akar, meta)
        except WorkspaceError:
            # Metadata rusak tidak boleh menghalangi pengarsipan; berkas
            # mahasiswa lebih penting daripada catatan tentangnya.
            pass

        tujuan_dasar = self._base / ARCHIVE_DIR
        tujuan_dasar.mkdir(parents=True, exist_ok=True)
        tujuan = tujuan_dasar / lokasi
        if tujuan.exists():
            cap = datetime.now().strftime("%Y%m%d-%H%M%S")
            tujuan = tujuan_dasar / f"{lokasi}-{cap}"
        shutil.move(str(akar), str(tujuan))

    # ------------------------------------------------------------------
    # Di luar port
    # ------------------------------------------------------------------

    def files(self, workspace_id: str, policy: PolicySource | None = None) -> WorkspaceFiles:
        """Operasi berkas untuk satu workspace yang sudah ada."""
        akar = self._root_of(workspace_id)
        if policy is None:
            policy = _PolicyFromMetadata(self._read_meta(akar).read_only)
        return self._files_for(akar, policy)

    def info(self, workspace_id: str) -> WorkspaceInfo:
        """Keterangan workspace beserta hitungan berkas dan ukurannya."""
        akar = self._root_of(workspace_id)
        meta = self._read_meta(akar)
        jumlah, ukuran = _measure(akar)
        return WorkspaceInfo(
            workspace_id=meta.workspace_id,
            location=self._location_of(workspace_id),
            state=meta.state,
            module_id=meta.module_id,
            course_id=meta.course_id,
            course_version=meta.course_version,
            created_at=meta.created_at,
            updated_at=meta.updated_at,
            read_only=tuple(meta.read_only),
            file_count=jumlah,
            size_bytes=ukuran,
        )

    def exists(self, workspace_id: str) -> bool:
        try:
            return (self._base / self._location_of(workspace_id)).is_dir()
        except WorkspaceError:
            return False

    def list_workspaces(self) -> list[str]:
        """Identifier seluruh workspace aktif, tidak termasuk yang diarsipkan."""
        hasil: list[str] = []
        for anak in sorted(self._base.iterdir()):
            if not anak.is_dir() or anak.name.startswith("."):
                continue
            if (anak / INTERNAL_DIR / METADATA_FILENAME).is_file():
                hasil.append(anak.name)
        return hasil

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _location_of(self, workspace_id: str) -> str:
        """Path relatif satu workspace terhadap direktori dasar.

        Identifier diperlakukan sebagai path yang tidak dipercaya: ia harus
        berupa satu komponen, tanpa pemisah dan tanpa ``..``.
        """
        try:
            rel = self._base_resolver.resolve(workspace_id).relative
        except (InvalidPathError, PathEscapeError) as exc:
            raise WorkspaceError(f"workspace_id tidak sah: {exc}") from exc
        if len(rel.parts) != 1:
            raise WorkspaceError(
                f"workspace_id '{workspace_id}' tidak boleh memuat pemisah path"
            )
        return rel.parts[0]

    def _root_of(self, workspace_id: str) -> Path:
        akar = self._base / self._location_of(workspace_id)
        if not akar.is_dir():
            raise WorkspaceNotFoundError(f"workspace '{workspace_id}' tidak ditemukan")
        return akar

    def _files_for(self, root: Path, policy: PolicySource | None) -> WorkspaceFiles:
        resolver = PathResolver(
            root,
            read_only=ReadOnlyPolicy.from_policy(policy),
            limits=self._limits,
        )
        return WorkspaceFiles(resolver)

    def _read_meta(self, root: Path) -> WorkspaceMetadata:
        return read_metadata(metadata_path(root, INTERNAL_DIR))

    def _save_metadata(self, root: Path, meta: WorkspaceMetadata) -> None:
        # Ditulis langsung, bukan lewat WorkspaceFiles: direktori internal
        # justru berada di area read-only menurut kebijakan, dan memang tidak
        # boleh ditulis lewat jalur yang sama dengan berkas mahasiswa.
        target = metadata_path(root, INTERNAL_DIR)
        target.parent.mkdir(parents=True, exist_ok=True)
        from .files import _atomic_write  # impor lokal agar tidak melingkar

        _atomic_write(target, meta.to_json().encode("utf-8"))

    def _load_or_create_metadata(
        self, root: Path, request: WorkspaceRequest, baru: bool
    ) -> WorkspaceMetadata:
        target = metadata_path(root, INTERNAL_DIR)
        if target.is_file():
            meta = read_metadata(target)
            if meta.workspace_id != request.workspace_id:
                raise WorkspaceError(
                    f"direktori memuat workspace '{meta.workspace_id}', "
                    f"bukan '{request.workspace_id}'"
                )
            if meta.module_id != request.module_id:
                raise WorkspaceError(
                    f"workspace '{meta.workspace_id}' milik module '{meta.module_id}', "
                    f"tidak dapat dipakai ulang untuk '{request.module_id}'"
                )
            # Versi course boleh naik; itu pembaruan materi, bukan konflik.
            meta.course_version = request.course_version
            return meta

        meta = WorkspaceMetadata(
            workspace_id=request.workspace_id,
            module_id=request.module_id,
            course_id=request.course_id,
            course_version=request.course_version,
        )
        meta.touch()
        return meta

    def _write_starters(
        self, files: WorkspaceFiles, request: WorkspaceRequest, meta: WorkspaceMetadata
    ) -> list[str]:
        """Salin berkas awal dari course package.

        Berkas yang sudah ada **tidak** ditimpa. Inilah yang membuat ``ensure``
        aman dipanggil berulang kali: mahasiswa yang sudah mengerjakan
        notebook tidak kehilangan pekerjaannya ketika module diaktifkan ulang.
        """
        daftar = starter_entries(request.policy)
        if not daftar:
            return []
        if not request.package_root:
            raise WorkspaceError(
                "kebijakan menyebut berkas awal, tetapi package_root tidak diberikan"
            )

        paket = Path(request.package_root).expanduser()
        if not paket.is_dir():
            raise WorkspaceError("package_root bukan direktori yang dapat dibaca")

        # Resolver terpisah untuk course package: path berkas awal berasal dari
        # LabSpec, dan LabSpec bukan sumber yang lebih dipercaya daripada
        # masukan mahasiswa.
        paket_resolver = PathResolver(paket, limits=self._limits)

        ditulis: list[str] = []
        for entri in daftar:
            sumber = paket_resolver.resolve(entri)
            if not sumber.absolute.is_file():
                raise WorkspaceError(
                    f"berkas awal '{sumber.text}' tidak ada di dalam course package"
                )
            hasil = files.copy_into(sumber.absolute, str(sumber.relative), overwrite=False)
            if hasil is not None:
                ditulis.append(hasil.text)

        for jalur in ditulis:
            if jalur not in meta.starter_written:
                meta.starter_written.append(jalur)
        return ditulis


@dataclass(frozen=True)
class _PolicyFromMetadata:
    """Kebijakan minimal yang dibangun ulang dari metadata tersimpan."""

    read_only_paths: Sequence[str] = ()
    starter: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()

    @property
    def read_only(self) -> Sequence[str]:
        return self.read_only_paths


def _measure(root: Path) -> tuple[int, int]:
    """Hitung jumlah berkas dan total ukuran, tanpa mengikuti symlink."""
    jumlah = 0
    ukuran = 0
    for induk, _, berkas in root.walk() if hasattr(root, "walk") else _walk(root):
        for nama in berkas:
            p = induk / nama
            if p.is_symlink():
                continue
            try:
                ukuran += p.stat().st_size
                jumlah += 1
            except OSError:
                continue
    return jumlah, ukuran


def _walk(root: Path):
    """Padanan ``Path.walk`` untuk Python yang belum memilikinya."""
    import os

    for induk, direktori, berkas in os.walk(root):
        yield Path(induk), direktori, berkas
