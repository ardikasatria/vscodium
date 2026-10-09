"""Dataset Manager.

Implementasi nyata port ``DatasetStore`` milik Workbench Core. Seperti
Workspace Manager, kesesuaiannya **struktural**: paket ini tidak mengimpor
``workbench_core`` (ADR-013, mengikuti ADR-008).

Alur ``ensure``:

```text
sudah ada material raw?
  ├── ya  -> verifikasi cepat (daftar berkas + ukuran)
  │           ├── lolos -> siap, tanpa mengambil bahan lagi
  │           └── gagal -> material dibuang, siapkan ulang
  └── tidak
        ↓
      kunci direktori (satu penyiapan per dataset per komputer)
        ↓
      sumber menulis ke staging
        ↓
      manifest: dari sumber bila ada, selain itu dihitung dari isi
        ↓
      verifikasi penuh dengan checksum streaming
        ├── gagal -> staging dibuang, material lama tidak tersentuh
        └── lolos -> promosi atomik ke raw, mode read-only
```

Yang membuat alur ini idempoten: penyiapan kedua atas dataset yang sama tidak
mengambil bahan apa pun dan tidak menulis satu byte pun. Yang membuatnya aman:
material raw tidak pernah diganti sebelum penggantinya terverifikasi utuh.

Job, event, dan penanganan kegagalan di tingkat sesi sudah ditangani Core
(``session.ensure_dataset``). Paket ini tidak membuat job sendiri; ia
mengembalikan status bertipe dan melempar kesalahan bertipe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import (
    DatasetError,
    ManifestError,
    SourceError,
    VerificationError,
)
from .model import (
    MANIFEST_FILENAME,
    DatasetIdentity,
    DatasetManifest,
    DatasetStatus,
    status_from_manifest,
)
from .sources import DatasetSource, LocalDirectorySource, SourceResult
from .store import MANIFEST_EXCLUDE, DatasetStore, StoredDataset
from .verify import build_entries, verify_directory

#: Path relatif tempat dataset dipasang di dalam workspace. Sama dengan yang
#: dideklarasikan kedua pilot pada ``spec.workspace.readOnly``.
DEFAULT_MOUNT = "data/raw"

#: Subdirektori dataset di dalam course package, bila sumbernya tidak ditentukan.
DEFAULT_PACKAGE_SUBDIR = "datasets/raw"

SourceFactory = Callable[[DatasetIdentity, str | None], DatasetSource]


@dataclass(frozen=True)
class MaterializeResult:
    """Hasil memasang dataset ke satu workspace."""

    mount: str
    files_written: int
    bytes_written: int
    checksum: str
    already_present: bool = False


class DatasetManager:
    """Penyedia dataset praktikum pada satu komputer mahasiswa."""

    def __init__(
        self,
        store: DatasetStore | Path | str,
        *,
        source_factory: SourceFactory | None = None,
        verify_on_open: bool = True,
    ):
        self._store = store if isinstance(store, DatasetStore) else DatasetStore(store)
        self._source_factory = source_factory or _default_source_factory
        self._verify_on_open = verify_on_open

    @property
    def store(self) -> DatasetStore:
        return self._store

    # ------------------------------------------------------------------
    # Port DatasetStore
    # ------------------------------------------------------------------

    def ensure(self, dataset: Any, *, package_root: str | None = None) -> DatasetStatus:
        """Pastikan dataset ada dan benar checksum-nya.

        Menerima ``DatasetRef`` milik Core atau objek sebentuk. Idempoten:
        pemanggilan kedua atas dataset yang sama tidak mengambil bahan apa pun.
        """
        identity = DatasetIdentity.from_ref(dataset)
        read_only = bool(getattr(dataset, "read_only", True))

        siap = self._open_if_valid(identity)
        if siap is not None:
            return status_from_manifest(
                siap.manifest, read_only=read_only, generated=False,
                detail="dataset sudah tersedia dan terverifikasi",
            )

        tersimpan, dibangkitkan = self._prepare(identity, dataset, package_root)
        return status_from_manifest(
            tersimpan.manifest, read_only=read_only, generated=dibangkitkan,
            detail=f"{len(tersimpan.manifest.files)} berkas terverifikasi",
        )

    # ------------------------------------------------------------------
    # Di luar port
    # ------------------------------------------------------------------

    def verify(self, dataset: Any, *, deep: bool = True) -> DatasetManifest:
        """Verifikasi material raw terhadap manifestnya.

        Melempar bila material belum ada atau tidak cocok. Dipakai checkpoint
        untuk memastikan data mentah tidak berubah.
        """
        identity = DatasetIdentity.from_ref(dataset)
        manifest = self._store.read_manifest(identity)
        self._store.verify_raw(identity, manifest, deep=deep)
        return manifest

    def manifest_of(self, dataset: Any) -> DatasetManifest:
        return self._store.read_manifest(DatasetIdentity.from_ref(dataset))

    def is_ready(self, dataset: Any) -> bool:
        return self._open_if_valid(DatasetIdentity.from_ref(dataset)) is not None

    def materialize(
        self,
        dataset: Any,
        files: Any,
        *,
        mount: str = DEFAULT_MOUNT,
        overwrite: bool = False,
    ) -> MaterializeResult:
        """Pasang dataset ke satu workspace.

        ``files`` adalah ``WorkspaceFiles`` milik Workspace Manager. Seluruh
        resolusi path dilakukan olehnya -- paket ini **tidak** membuat kebijakan
        path sendiri (lihat ADR-013).

        Yang dipasang adalah **salinan**, bukan symlink ke material raw.
        Symlink yang menunjuk keluar akar workspace justru ditolak resolver
        (ADR-009), dan salinan membuat checkpoint dapat memeriksa apakah
        mahasiswa mengubah data mentahnya sendiri -- persis yang dilakukan
        ``check_modul_01.py`` pada repository sumber.

        ``overwrite=False`` tidak menimpa berkas yang sudah ada. Bila seluruh
        berkas sudah ada, hasilnya ``already_present`` dan tidak ada yang
        ditulis.
        """
        identity = DatasetIdentity.from_ref(dataset)
        tersimpan = self._require_ready(identity)
        akar = self._store.raw_path(identity)

        # ``data/raw`` dinyatakan read-only bagi mahasiswa, tetapi seseorang
        # harus menaruh datanya di sana lebih dulu. View penyediaan melepas
        # kebijakan read-only course sambil mempertahankan seluruh pemeriksaan
        # keamanan path -- resolusi path tetap milik Workspace Manager.
        penyedia = files.provisioning_view()

        ditulis = 0
        byte = 0
        for entri in tersimpan.manifest.files:
            tujuan = f"{mount.rstrip('/')}/{entri.path}"
            hasil = penyedia.copy_into(akar / entri.path, tujuan, overwrite=overwrite)
            if hasil is not None:
                ditulis += 1
                byte += entri.size_bytes

        return MaterializeResult(
            mount=mount,
            files_written=ditulis,
            bytes_written=byte,
            checksum=tersimpan.manifest.checksum,
            already_present=ditulis == 0,
        )

    def verify_workspace_copy(
        self, dataset: Any, files: Any, *, mount: str = DEFAULT_MOUNT
    ) -> None:
        """Periksa salinan di dalam workspace masih cocok dengan manifest.

        Inilah yang membedakan "data mentah read-only" dari sekadar harapan:
        bila mahasiswa mengubah berkasnya, perubahan itu terdeteksi -- bukan
        dicegah oleh permission, melainkan ketahuan oleh checksum.
        """
        identity = DatasetIdentity.from_ref(dataset)
        manifest = self._store.read_manifest(identity)
        akar_mount = files.resolver.resolve(mount.rstrip("/")).absolute
        verify_directory(akar_mount, manifest, exclude=MANIFEST_EXCLUDE, deep=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _open_if_valid(self, identity: DatasetIdentity) -> StoredDataset | None:
        """Kembalikan material yang sudah ada bila masih sahih."""
        if not self._store.has_raw(identity):
            return None
        try:
            manifest = self._store.read_manifest(identity)
            self._store.verify_raw(identity, manifest, deep=self._verify_on_open)
        except (ManifestError, VerificationError):
            # Material yang ada tidak sahih. Dibuang agar penyiapan ulang
            # berjalan bersih; ini tidak menyentuh workspace mahasiswa.
            self._store.remove_raw(identity)
            return None
        return StoredDataset(identity=identity, manifest=manifest,
                             location=self._store.location(identity))

    def _prepare(
        self, identity: DatasetIdentity, dataset: Any, package_root: str | None
    ) -> tuple[StoredDataset, bool]:
        sumber = self._source_factory(identity, package_root)

        with self._store.lock(identity):
            # Proses lain mungkin menyelesaikan penyiapan selagi kita menunggu
            # kunci. Periksa sekali lagi sebelum bekerja.
            siap = self._open_if_valid(identity)
            if siap is not None:
                return siap, False

            staging = self._store.new_staging(identity)
            try:
                hasil = sumber.fetch(identity, staging)
                manifest = self._manifest_for(identity, staging, hasil)
                verify_directory(staging, manifest, exclude=MANIFEST_EXCLUDE, deep=True)
                tersimpan = self._store.promote(identity, staging, manifest)
            except BaseException:
                # Apa pun yang gagal, staging tidak boleh tertinggal dan
                # material raw yang lama tidak boleh tersentuh.
                self._store.discard(staging)
                raise
            return tersimpan, hasil.generated

    def _manifest_for(
        self, identity: DatasetIdentity, staging: Path, hasil: SourceResult
    ) -> DatasetManifest:
        """Manifest dari sumber bila ada; selain itu dihitung dari isi staging."""
        if hasil.manifest is not None:
            if hasil.manifest.identity != identity:
                raise SourceError(
                    f"manifest sumber menyebut {hasil.manifest.identity}, "
                    f"sedangkan yang diminta {identity}"
                )
            return hasil.manifest

        entri = build_entries(staging, exclude=MANIFEST_EXCLUDE)
        if not entri:
            raise SourceError(
                f"sumber '{identity.ref}' tidak menghasilkan satu berkas pun"
            )
        provenance = dict(hasil.provenance)
        provenance.setdefault("checksum", "dihitung dari isi")
        return DatasetManifest.of(identity, entri, provenance=provenance)

    def _require_ready(self, identity: DatasetIdentity) -> StoredDataset:
        siap = self._open_if_valid(identity)
        if siap is None:
            raise DatasetError(
                f"dataset '{identity.ref}' belum siap; panggil ensure() lebih dulu"
            )
        return siap


def _default_source_factory(
    identity: DatasetIdentity, package_root: str | None
) -> DatasetSource:
    """Sumber bawaan: direktori dataset di dalam course package.

    Tanpa ``package_root``, tidak ada tempat yang sah untuk mengambil bahan.
    Menebak lokasi lain akan berarti membaca dari luar course package.
    """
    if not package_root:
        raise SourceError(
            f"dataset '{identity.ref}' memerlukan package_root; "
            "course package adalah satu-satunya sumber bawaan"
        )
    akar = Path(package_root).expanduser() / DEFAULT_PACKAGE_SUBDIR / identity.ref
    return LocalDirectorySource(akar)
