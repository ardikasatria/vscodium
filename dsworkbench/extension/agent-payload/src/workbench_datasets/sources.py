"""Sumber bahan dataset.

Sumber adalah satu-satunya pihak yang membawa byte ke dalam Dataset Manager.
Memisahkannya menjadi interface membuat tiga hal mungkin: menguji seluruh alur
tanpa jaringan, menambahkan sumber baru tanpa menyentuh verifier, dan menjaga
agar tidak ada sumber yang menulis langsung ke material yang sudah
terverifikasi.

Kontraknya sengaja sempit:

    fetch(identity, destination) -> SourceResult

Sumber menulis **hanya** ke ``destination``, yang selalu berupa direktori
staging kosong milik Dataset Manager. Ia tidak tahu di mana material raw
disimpan, dan tidak dapat menyentuhnya.

Sumber yang tersedia pada Component 04:

``LocalDirectorySource``
    Menyalin dari direktori di dalam course package. Inilah sumber yang nyata
    dan dapat dipakai sekarang: kedua pilot menyimpan dataset dan generator
    di dalam repository praktikum masing-masing.

``FixtureSource``
    Menulis isi dari peta ``path -> bytes``. Untuk test, tanpa menyentuh
    jaringan maupun course package.

Yang **belum** ada, dan disebut terbuka agar tidak disangka hilang:

``GeneratorSource``
    Menjalankan generator dataset course. Ditunda karena berarti mengeksekusi
    skrip dari course package, dan itu memerlukan handler allowlist seperti
    checkpoint (ADR-006) -- bukan ``subprocess`` bebas. Dijadwalkan pada
    Component 20 bersama profil NusaMart.

``HttpArtifactSource``
    Kini ada sebagai ``artifacts.ArtifactSource`` (DL-04): artefak manifest
    bersertifikat SHA-256, pengambilan disuntikkan pemanggil (agent: HTTPS atau
    endpoint package). Cache lokal = store Dataset Manager.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable

from .errors import SourceError
from .model import DatasetIdentity, DatasetManifest


@dataclass(frozen=True)
class SourceResult:
    """Apa yang dihasilkan sumber setelah menulis ke direktori staging.

    ``manifest`` diisi bila sumber memang membawa manifest tepercaya -- misalnya
    course package yang menyertakan ``dataset.json`` atau ``CHECKSUM.txt``.
    Bila ``None``, Dataset Manager menghitung checksum sendiri dari isi yang
    baru ditulis.

    ``generated`` menandai bahwa bahan dibuat saat itu juga, bukan disalin dari
    artefak yang sudah ada. Nilainya diteruskan ke ``DatasetStatus.generated``
    dan akhirnya tampil di UI.
    """

    manifest: DatasetManifest | None = None
    generated: bool = False
    provenance: Mapping[str, str] = field(default_factory=dict)
    detail: str | None = None


@runtime_checkable
class DatasetSource(Protocol):
    """Penyedia bahan dataset."""

    name: str

    def fetch(self, identity: DatasetIdentity, destination: Path) -> SourceResult:
        """Tulis bahan dataset ke ``destination``.

        ``destination`` sudah ada, kosong, dan milik Dataset Manager. Sumber
        tidak boleh menulis ke luar direktori itu.

        Melempar :class:`SourceError` bila bahan tidak dapat disediakan.
        """
        ...


# ----------------------------------------------------------------------


class LocalDirectorySource:
    """Menyalin dataset dari direktori di dalam course package.

    Manifest dibaca bila course package menyediakannya. Dua bentuk didukung:
    ``dataset.json`` milik Workbench, dan ``CHECKSUM.txt`` bergaya ``sha256sum``
    yang sudah dipakai repository sumber Data Wrangling.

    Salinan dilakukan tanpa mengikuti symlink. Material dataset tidak boleh
    memuat tautan: isi di ujung tautan tidak ikut terverifikasi, dan tautan yang
    menunjuk ke luar course package adalah jalan keluar yang tidak diinginkan.
    """

    name = "local-directory"

    #: Nama berkas yang dianggap manifest, bukan bagian dari dataset.
    MANIFEST_NAMES = ("dataset.json", "CHECKSUM.txt")

    def __init__(self, root: Path | str, *, follow_symlinks: bool = False):
        self._root = Path(root)
        self._follow = follow_symlinks

    def fetch(self, identity: DatasetIdentity, destination: Path) -> SourceResult:
        if not self._root.is_dir():
            raise SourceError(
                f"direktori sumber dataset '{identity.ref}' tidak ada di course package"
            )

        manifest, dilewati = self._read_manifest(identity)
        disalin = self._copy_tree(self._root, destination, skip=dilewati)

        if disalin == 0:
            raise SourceError(
                f"direktori sumber dataset '{identity.ref}' tidak memuat satu berkas pun"
            )

        return SourceResult(
            manifest=manifest,
            generated=False,
            provenance={"source": self.name},
            detail=f"{disalin} berkas disalin dari course package",
        )

    # ------------------------------------------------------------------

    def _read_manifest(
        self, identity: DatasetIdentity
    ) -> tuple[DatasetManifest | None, frozenset[str]]:
        """Baca manifest bawaan course package, bila ada."""
        from .model import entries_from_checksums, parse_checksum_file
        from .verify import file_sizes

        json_path = self._root / "dataset.json"
        if json_path.is_file():
            manifest = DatasetManifest.from_json(json_path.read_text(encoding="utf-8"))
            if manifest.identity != identity:
                raise SourceError(
                    f"manifest course package menyebut {manifest.identity}, "
                    f"sedangkan yang diminta {identity}"
                )
            return manifest, frozenset({"dataset.json"})

        checksum_path = self._root / "CHECKSUM.txt"
        if checksum_path.is_file():
            checksums = parse_checksum_file(checksum_path.read_text(encoding="utf-8"))
            ukuran = file_sizes(self._root, exclude=frozenset(self.MANIFEST_NAMES))
            entri = entries_from_checksums(checksums, ukuran)
            manifest = DatasetManifest.of(
                identity, entri, provenance={"source": self.name, "manifest": "CHECKSUM.txt"}
            )
            return manifest, frozenset({"CHECKSUM.txt"})

        # Tanpa manifest bawaan, checksum dihitung dari isi yang disalin.
        return None, frozenset()

    def _copy_tree(self, source: Path, destination: Path, *, skip: frozenset[str]) -> int:
        jumlah = 0
        for masuk in sorted(source.iterdir(), key=lambda p: p.name):
            rel = masuk.name
            if rel in skip:
                continue
            if masuk.is_symlink() and not self._follow:
                raise SourceError(
                    f"'{rel}' adalah symlink; material dataset tidak boleh memuat tautan"
                )
            tujuan = destination / rel
            if masuk.is_dir():
                tujuan.mkdir(parents=True, exist_ok=True)
                jumlah += self._copy_tree(masuk, tujuan, skip=frozenset())
            elif masuk.is_file():
                shutil.copyfile(masuk, tujuan)
                jumlah += 1
            else:
                raise SourceError(f"'{rel}' bukan berkas biasa maupun direktori")
        return jumlah


class FixtureSource:
    """Sumber untuk test: menulis isi dari peta ``path -> bytes``.

    Tidak menyentuh jaringan maupun filesystem di luar ``destination``.
    """

    name = "fixture"

    def __init__(
        self,
        files: Mapping[str, bytes],
        *,
        manifest: DatasetManifest | None = None,
        generated: bool = False,
        fail_with: Exception | None = None,
        truncate_after: int | None = None,
    ):
        self._files = dict(files)
        self._manifest = manifest
        self._generated = generated
        self._fail_with = fail_with
        self._truncate_after = truncate_after
        self.calls = 0

    def fetch(self, identity: DatasetIdentity, destination: Path) -> SourceResult:
        self.calls += 1

        for i, (rel, isi) in enumerate(sorted(self._files.items())):
            # Menulis sebagian lalu gagal: meniru unduhan terputus dan disk
            # penuh, dua keadaan yang harus tidak meninggalkan material rusak.
            if self._truncate_after is not None and i >= self._truncate_after:
                break
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(isi)

        if self._fail_with is not None:
            raise self._fail_with

        return SourceResult(
            manifest=self._manifest,
            generated=self._generated,
            provenance={"source": self.name},
        )
