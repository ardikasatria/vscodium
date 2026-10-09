"""Identitas dan manifest dataset.

Dua objek yang menentukan seluruh paket ini:

:class:`DatasetIdentity`
    Apa yang membuat dua dataset dianggap sama. Jawabannya bukan hanya nama:
    dataset yang sama dengan seed berbeda menghasilkan data yang berbeda, dan
    profil ``small`` bukan versi terpotong dari ``medium``. Keempat unsur
    -- ref, version, profile, seed -- ikut menentukan identitas, dan karena itu
    ikut menentukan tempat penyimpanannya.

:class:`DatasetManifest`
    Daftar berkas beserta ukuran dan checksum-nya, ditambah checksum agregat
    yang mewakili dataset secara keseluruhan. Inilah acuan tunggal apakah
    material yang ada memang material yang benar.

Checksum agregat dihitung dari daftar berkas, bukan dari menggabungkan isinya.
Dengan begitu ia tetap dapat dihitung tanpa membaca ulang seluruh dataset, dan
tetap berubah bila ada berkas yang ditambahkan, dihilangkan, atau diubah.

Tidak ada timestamp di dalam manifest. Manifest harus deterministik: dataset
yang sama pada dua komputer harus menghasilkan berkas manifest yang identik
byte demi byte, sehingga ia sendiri dapat diperiksa checksum-nya.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Iterable, Mapping, Sequence

from .errors import ManifestError

#: Versi bentuk manifest. Dinaikkan bila bentuknya berubah tidak kompatibel.
MANIFEST_VERSION = 1

#: Nama berkas manifest di dalam material raw yang sudah terverifikasi.
MANIFEST_FILENAME = "dataset.json"

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: Profil yang dikenal. Sama dengan allowlist LabSpec; ditulis ulang di sini
#: agar paket ini tidak mengimpor LabSpec maupun Core (lihat ADR-013).
PROFILES = ("small", "medium", "large")


def normalise_checksum(value: str) -> str:
    """Terima ``sha256:<hex>`` maupun ``<hex>``; kembalikan bentuk berawalan."""
    teks = value.strip().lower()
    if teks.startswith("sha256:"):
        teks = teks[len("sha256:"):]
    if not SHA256_PATTERN.match(teks):
        raise ManifestError(f"checksum '{value}' bukan SHA-256 heksadesimal 64 karakter")
    return f"sha256:{teks}"


@dataclass(frozen=True)
class DatasetIdentity:
    """Apa yang membuat dua dataset dianggap dataset yang sama."""

    ref: str
    version: str
    profile: str
    seed: int

    def __post_init__(self) -> None:
        if not SLUG_PATTERN.match(self.ref):
            raise ManifestError(
                f"ref dataset '{self.ref}' harus huruf kecil, angka, dan tanda hubung"
            )
        if not self.version.strip():
            raise ManifestError("version dataset tidak boleh kosong")
        if self.profile not in PROFILES:
            raise ManifestError(
                f"profil '{self.profile}' tidak dikenal; pilihan: " + ", ".join(PROFILES)
            )
        if self.seed < 0:
            raise ManifestError("seed tidak boleh negatif")

    @property
    def key(self) -> str:
        """Kunci penyimpanan. Dipakai sebagai path relatif di dataset store.

        Seed ikut masuk karena seed yang berbeda menghasilkan data yang
        berbeda; menyimpannya di tempat yang sama akan membuat dataset saling
        menimpa tanpa ada yang menyadarinya.
        """
        return f"{self.ref}/{self.version}/{self.profile}/seed-{self.seed}"

    @classmethod
    def from_ref(cls, dataset: Any) -> "DatasetIdentity":
        """Bangun dari ``DatasetRef`` milik Core, tanpa mengimpor Core.

        ``profile`` dan ``raw_policy`` pada Core berupa enum; nilainya diambil
        lewat ``.value`` bila ada.
        """
        return cls(
            ref=str(dataset.ref),
            version=str(dataset.version),
            profile=_enum_value(dataset.profile),
            seed=int(dataset.seed),
        )

    def __str__(self) -> str:
        return f"{self.ref}@{self.version} ({self.profile}, seed {self.seed})"


@dataclass(frozen=True)
class FileEntry:
    """Satu berkas di dalam dataset."""

    path: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if not self.path or self.path.startswith("/") or ".." in self.path.split("/"):
            raise ManifestError(f"path berkas '{self.path}' tidak sah di dalam manifest")
        if self.size_bytes < 0:
            raise ManifestError(f"ukuran '{self.path}' tidak boleh negatif")
        object.__setattr__(self, "sha256", normalise_checksum(self.sha256))

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sizeBytes": self.size_bytes, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FileEntry":
        try:
            return cls(
                path=str(data["path"]),
                size_bytes=int(data["sizeBytes"]),
                sha256=str(data["sha256"]),
            )
        except KeyError as exc:
            raise ManifestError(f"entri berkas kehilangan kunci {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise ManifestError(f"entri berkas tidak sah: {exc}") from exc


@dataclass(frozen=True)
class DatasetManifest:
    """Daftar berkas dataset beserta checksum agregatnya."""

    identity: DatasetIdentity
    files: tuple[FileEntry, ...] = ()
    provenance: Mapping[str, str] = field(default_factory=dict)
    manifest_version: int = MANIFEST_VERSION

    def __post_init__(self) -> None:
        jalur = [f.path for f in self.files]
        ganda = {p for p in jalur if jalur.count(p) > 1}
        if ganda:
            raise ManifestError(
                "manifest memuat path ganda: " + ", ".join(sorted(ganda))
            )
        # Urutan disimpan tetap agar checksum agregat deterministik.
        object.__setattr__(self, "files", tuple(sorted(self.files, key=lambda f: f.path)))

    # ------------------------------------------------------------------

    @property
    def checksum(self) -> str:
        """Checksum agregat yang mewakili seluruh dataset.

        Dihitung dari daftar berkas, bukan dari isinya. Ia berubah bila ada
        berkas ditambahkan, dihilangkan, diubah ukurannya, atau diubah isinya.
        """
        h = sha256()
        h.update(f"{MANIFEST_VERSION}\n".encode("utf-8"))
        h.update(f"{self.identity.key}\n".encode("utf-8"))
        for f in self.files:
            h.update(f"{f.path}\0{f.size_bytes}\0{f.sha256}\n".encode("utf-8"))
        return f"sha256:{h.hexdigest()}"

    @property
    def total_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files)

    def entry(self, path: str) -> FileEntry | None:
        for f in self.files:
            if f.path == path:
                return f
        return None

    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Bentuk JSON deterministik.

        Kunci terurut dan tanpa timestamp, sehingga dua komputer yang
        menyiapkan dataset yang sama menghasilkan berkas yang identik.
        """
        data = {
            "manifestVersion": self.manifest_version,
            "ref": self.identity.ref,
            "version": self.identity.version,
            "profile": self.identity.profile,
            "seed": self.identity.seed,
            "checksum": self.checksum,
            "totalBytes": self.total_bytes,
            "files": [f.to_dict() for f in self.files],
            "provenance": dict(sorted(self.provenance.items())),
        }
        return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "DatasetManifest":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ManifestError(
                f"manifest bukan JSON yang sah (baris {exc.lineno}, kolom {exc.colno})"
            ) from exc
        if not isinstance(data, dict):
            raise ManifestError("manifest harus berupa objek JSON")
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DatasetManifest":
        versi = data.get("manifestVersion")
        if versi != MANIFEST_VERSION:
            raise ManifestError(
                f"manifestVersion {versi!r} tidak dikenal; "
                f"versi yang didukung adalah {MANIFEST_VERSION}"
            )
        wajib = ("ref", "version", "profile", "seed", "files")
        hilang = [k for k in wajib if k not in data]
        if hilang:
            raise ManifestError("manifest tidak lengkap: " + ", ".join(hilang))

        berkas_mentah = data["files"]
        if not isinstance(berkas_mentah, list):
            raise ManifestError("'files' pada manifest harus berupa daftar")

        manifest = cls(
            identity=DatasetIdentity(
                ref=str(data["ref"]),
                version=str(data["version"]),
                profile=str(data["profile"]),
                seed=int(data["seed"]),
            ),
            files=tuple(FileEntry.from_dict(f) for f in berkas_mentah),
            provenance={str(k): str(v) for k, v in (data.get("provenance") or {}).items()},
        )

        # Checksum yang tercatat harus cocok dengan yang dihitung ulang.
        # Manifest yang disunting tangan tidak boleh lolos diam-diam.
        tercatat = data.get("checksum")
        if tercatat is not None and normalise_checksum(str(tercatat)) != manifest.checksum:
            raise ManifestError(
                "checksum yang tercatat pada manifest tidak cocok dengan isinya; "
                "manifest kemungkinan disunting tanpa menghitung ulang"
            )
        return manifest

    @classmethod
    def of(cls, identity: DatasetIdentity, entries: Iterable[FileEntry],
           *, provenance: Mapping[str, str] | None = None) -> "DatasetManifest":
        return cls(identity=identity, files=tuple(entries),
                   provenance=dict(provenance or {}))


def _enum_value(value: Any) -> str:
    """Ambil ``.value`` bila objeknya enum; selain itu ubah menjadi teks."""
    return str(getattr(value, "value", value))


def parse_checksum_file(text: str) -> dict[str, str]:
    """Baca berkas ``CHECKSUM.txt`` bergaya ``sha256sum``.

    Bentuknya ``<hex>  <nama berkas>`` per baris. Repository sumber Data
    Wrangling memakai bentuk ini, dan migrasi pada Component 16 memerlukannya
    untuk menurunkan manifest tanpa menghitung ulang seluruh dataset.

    Baris kosong dan komentar diabaikan. Nama berkas tidak boleh berupa path
    yang keluar dari direktori dataset.
    """
    hasil: dict[str, str] = {}
    for nomor, baris in enumerate(text.splitlines(), start=1):
        teks = baris.strip()
        if not teks or teks.startswith("#"):
            continue
        bagian = teks.split(None, 1)
        if len(bagian) != 2:
            raise ManifestError(f"baris {nomor} pada berkas checksum tidak dapat dibaca")
        digest, nama = bagian[0], bagian[1].strip().lstrip("*")
        if nama.startswith("/") or ".." in nama.split("/"):
            raise ManifestError(f"baris {nomor}: nama berkas '{nama}' tidak sah")
        if nama in hasil:
            raise ManifestError(f"baris {nomor}: '{nama}' tercatat lebih dari sekali")
        hasil[nama] = normalise_checksum(digest)
    if not hasil:
        raise ManifestError("berkas checksum tidak memuat satu entri pun")
    return hasil


def entries_from_checksums(
    checksums: Mapping[str, str], sizes: Mapping[str, int]
) -> tuple[FileEntry, ...]:
    """Gabungkan peta checksum dan peta ukuran menjadi entri manifest."""
    hilang = sorted(set(checksums) - set(sizes))
    if hilang:
        raise ManifestError(
            "ukuran tidak diketahui untuk: " + ", ".join(hilang[:5])
        )
    return tuple(
        FileEntry(path=nama, size_bytes=sizes[nama], sha256=digest)
        for nama, digest in sorted(checksums.items())
    )


@dataclass(frozen=True)
class DatasetStatus:
    """Keadaan dataset setelah disiapkan.

    Sepadan dengan ``ports.DatasetStatus`` milik Core. Didefinisikan ulang di
    sini agar paket ini tidak mengimpor Core; kesesuaiannya diuji pada
    ``tests/test_port_conformance.py``. Lihat ADR-013.
    """

    ref: str
    version: str
    ready: bool
    checksum: str | None = None
    read_only: bool = True
    generated: bool = False
    detail: str | None = None


def status_from_manifest(
    manifest: DatasetManifest, *, read_only: bool, generated: bool,
    detail: str | None = None,
) -> DatasetStatus:
    return DatasetStatus(
        ref=manifest.identity.ref,
        version=manifest.identity.version,
        ready=True,
        checksum=manifest.checksum,
        read_only=read_only,
        generated=generated,
        detail=detail,
    )


def sequence_of(value: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(value or ())
