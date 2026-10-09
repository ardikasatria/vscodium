"""Sumber dataset dari artefak bersertifikat checksum (DL-04).

Manifest dataset Workbench boleh membawa bagian ``sources``: daftar artefak
yang harus diambil untuk menyusun material raw. Setiap artefak menyebut asal,
ukuran, SHA-256, dan cara membongkarnya::

    {"kind": "url", "url": "https://…/train-images-idx3-ubyte.gz",
     "sha256": "sha256:…", "sizeBytes": 26421880,
     "unpack": "gunzip", "to": "FashionMNIST/raw/train-images-idx3-ubyte"}
    {"kind": "package", "path": "datasets/releases/x.zip", …, "unpack": "zip", "to": "."}

Tiga janji:

**Tidak ada byte yang dipakai sebelum terverifikasi.** Artefak diunduh ke
berkas sementara dengan batas ukuran, di-hash secara streaming, dan baru
dibongkar bila ukuran dan SHA-256 cocok. Hasil bongkaran diverifikasi lagi oleh
``DatasetManager`` terhadap daftar ``files`` manifest sebelum dipromosikan.

**Pengambilan disuntikkan.** Modul ini tidak membuka jaringan sendiri: pemanggil
memberi ``fetch(spec, dest, progress)``. Agent memakai HTTPS atau endpoint
package Control API; test memakai fungsi palsu. Karena itu paket ini tetap
pustaka standar saja dan tidak pernah memanggil internet dalam test.

**Pembongkaran tidak keluar staging.** Nama anggota zip diperiksa (tanpa path
absolut, ``..``, symlink), ukuran total dibatasi, dan gunzip dibatasi ukuran
berkas tujuan yang tercatat di manifest.
"""

from __future__ import annotations

import gzip
import hashlib
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from .errors import SourceError
from .model import DatasetIdentity, DatasetManifest, normalise_checksum
from .sources import SourceResult

#: ``fetch(spec, dest, progress)`` menulis artefak ``spec`` ke ``dest``;
#: ``progress(byte_bertambah)`` dipanggil selama pengambilan.
Fetcher = Callable[[Mapping[str, Any], Path, Callable[[int], None]], None]

#: Batas keras satu artefak dan total bongkaran (laptop mahasiswa).
MAX_ARTIFACT_BYTES = 2 * 1024 ** 3
MAX_UNPACKED_BYTES = 4 * 1024 ** 3

UNPACK = ("none", "gunzip", "zip")
KINDS = ("url", "package")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")


class Cancelled(SourceError):
    """Pengambilan dihentikan pemanggil (mis. tombol Hentikan)."""


def _rel(path: str, *, allow_dot: bool = False) -> PurePosixPath:
    """Path relatif yang aman di dalam staging."""
    if allow_dot and path in (".", ""):
        return PurePosixPath(".")
    p = PurePosixPath(path)
    if (not path or p.is_absolute() or "\\" in path or ".." in p.parts
            or any(part in ("", ".") for part in path.split("/"))):
        raise SourceError(f"path artefak tidak sah: {path!r}")
    return p


def validate_sources(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Periksa bagian ``sources`` manifest; kembalikan salinan yang sudah dinormalkan."""
    raw = data.get("sources")
    if not isinstance(raw, list) or not raw:
        raise SourceError("manifest tidak memuat 'sources' untuk diambil")
    hasil = []
    for i, s in enumerate(raw):
        if not isinstance(s, Mapping):
            raise SourceError(f"sources[{i}] harus objek")
        kind, unpack = s.get("kind"), s.get("unpack", "none")
        if kind not in KINDS:
            raise SourceError(f"sources[{i}].kind harus salah satu dari {KINDS}")
        if unpack not in UNPACK:
            raise SourceError(f"sources[{i}].unpack harus salah satu dari {UNPACK}")
        sha = str(s.get("sha256") or "")
        if not _SHA.match(sha):
            raise SourceError(f"sources[{i}].sha256 wajib 'sha256:<64 heks>'")
        size = s.get("sizeBytes")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_ARTIFACT_BYTES:
            raise SourceError(f"sources[{i}].sizeBytes di luar batas")
        if kind == "url":
            url = str(s.get("url") or "")
            if not url.startswith("https://"):
                raise SourceError(f"sources[{i}].url wajib HTTPS")
        else:
            _rel(str(s.get("path") or ""))
        to = str(s.get("to") or "")
        _rel(to, allow_dot=unpack == "zip")
        hasil.append({**dict(s), "unpack": unpack})
    return hasil


class ArtifactSource:
    """``DatasetSource`` yang menyusun material raw dari artefak manifest."""

    name = "manifest-artifacts"

    def __init__(
        self,
        manifest_data: Mapping[str, Any],
        fetch: Fetcher,
        *,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ):
        self._data = manifest_data
        self._fetch = fetch
        self._progress = progress or (lambda _msg: None)
        self._cancelled = cancelled or (lambda: False)

    def fetch(self, identity: DatasetIdentity, destination: Path) -> SourceResult:
        manifest = DatasetManifest.from_mapping(self._data)
        if manifest.identity != identity:
            raise SourceError(
                f"manifest menyebut {manifest.identity}, sedangkan yang diminta {identity}")
        sources = validate_sources(self._data)
        ukuran_tujuan = {f.path: f.size_bytes for f in manifest.files}
        destination.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".artefak-", dir=destination.parent) as tmp:
            for i, spec in enumerate(sources):
                self._cek_batal()
                label = spec.get("url") or spec.get("path")
                nama = PurePosixPath(str(label)).name
                self._progress(f"Mengambil {nama} ({spec['sizeBytes'] / 1e6:.1f} MB)")
                berkas = Path(tmp) / f"artefak-{i}"
                self._ambil(spec, berkas)
                self._bongkar(spec, berkas, destination, ukuran_tujuan)
                self._progress(f"Terverifikasi: {nama}")
        return SourceResult(
            manifest=manifest,
            generated=False,
            provenance={"source": self.name},
            detail=f"{len(sources)} artefak terverifikasi",
        )

    # ------------------------------------------------------------------

    def _cek_batal(self) -> None:
        if self._cancelled():
            raise Cancelled("penyiapan dataset dihentikan")

    def _ambil(self, spec: Mapping[str, Any], berkas: Path) -> None:
        batas = int(spec["sizeBytes"])
        diterima = [0]

        def _maju(n: int) -> None:
            diterima[0] += n
            if diterima[0] > batas:
                raise SourceError("artefak lebih besar dari ukuran yang tercatat")
            self._cek_batal()

        self._fetch(spec, berkas, _maju)
        if not berkas.is_file():
            raise SourceError("artefak tidak ditulis pengambil")
        ukuran = berkas.stat().st_size
        if ukuran != batas:
            raise SourceError(f"ukuran artefak {ukuran} byte, seharusnya {batas}")
        h = hashlib.sha256()
        with berkas.open("rb") as f:
            for potong in iter(lambda: f.read(1024 * 1024), b""):
                h.update(potong)
        if "sha256:" + h.hexdigest() != normalise_checksum(str(spec["sha256"])):
            raise SourceError("checksum artefak tidak cocok; berkas ditolak")

    def _bongkar(self, spec: Mapping[str, Any], berkas: Path, tujuan: Path,
                 ukuran_tujuan: Mapping[str, int]) -> None:
        cara, ke = spec["unpack"], str(spec.get("to") or ".")
        if cara == "none":
            akhir = tujuan / _rel(ke)
            akhir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(berkas), akhir)
        elif cara == "gunzip":
            akhir = tujuan / _rel(ke)
            batas = ukuran_tujuan.get(ke, MAX_UNPACKED_BYTES)
            akhir.parent.mkdir(parents=True, exist_ok=True)
            ditulis = 0
            with gzip.open(berkas, "rb") as sumber, akhir.open("wb") as keluar:
                for potong in iter(lambda: sumber.read(1024 * 1024), b""):
                    ditulis += len(potong)
                    if ditulis > batas:
                        raise SourceError("hasil gunzip melebihi ukuran tercatat")
                    keluar.write(potong)
                    self._cek_batal()
        else:
            dasar = tujuan / _rel(ke, allow_dot=True)
            total = 0
            with zipfile.ZipFile(berkas) as z:
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        raise SourceError(f"arsip memuat symlink: {info.filename}")
                    rel = _rel(info.filename)
                    total += info.file_size
                    if total > MAX_UNPACKED_BYTES:
                        raise SourceError("isi arsip melebihi batas")
                    akhir = dasar / rel
                    akhir.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as sumber, akhir.open("wb") as keluar:
                        shutil.copyfileobj(sumber, keluar, 1024 * 1024)
                    self._cek_batal()


__all__ = ["ArtifactSource", "Cancelled", "Fetcher", "MAX_ARTIFACT_BYTES", "validate_sources"]
