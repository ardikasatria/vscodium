"""``dataset.materialize`` / ``dataset.verify`` di Local Runner (DL-04).

Manifest dataset datang dari Control API (disisipkan ke envelope dari course
package; peramban tidak pernah mengirim URL). Agent mengambil artefaknya —
HTTPS atau berkas package lewat Control API — memverifikasi SHA-256, menyusun
material di store Dataset Manager, lalu menyalinnya ke ``data/raw`` akar
workspace mata kuliah. Operasi ini *long* (lane ``network``, ADR-049): progres
tampil bertahap dan tombol Hentikan membatalkan unduhan.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import OperationRejectedError

try:
    from workbench_datasets import ArtifactSource, Cancelled, DatasetManager
    from workbench_datasets.errors import DatasetError
    from workbench_datasets.model import DatasetManifest
except ImportError:  # pragma: no cover - lingkungan tanpa paket datasets
    ArtifactSource = Cancelled = DatasetManager = None  # type: ignore[assignment, misc]
    DatasetError = DatasetManifest = None  # type: ignore[assignment, misc]

#: False bila agent dipasang tanpa paket ``workbench_datasets`` (payload lama):
#: kemampuan ``dataset.v1`` tidak diumumkan dan operasinya ditolak dengan jelas.
TERSEDIA = ArtifactSource is not None


def _wajib_tersedia() -> None:
    if not TERSEDIA:
        raise OperationRejectedError(
            "Local Runner ini belum membawa paket dataset. Perbarui aplikasinya "
            "dari halaman Local Runner.")

#: Lokasi di akar workspace mata kuliah (ADR-009: read-only bagi mahasiswa).
MOUNT = "data/raw"

UrlFetcher = Callable[[str, Path, Callable[[int], None]], None]
PackageFetcher = Callable[[str, str, Path, Callable[[int], None]], None]


@dataclass(frozen=True)
class _Ref:
    ref: str
    version: str
    profile: str
    seed: int
    read_only: bool = True


def _manifest_dari(payload: Mapping[str, Any]) -> tuple[_Ref, dict[str, Any]]:
    data = payload.get("manifest")
    ds = payload.get("dataset")
    if not isinstance(data, Mapping) or not isinstance(ds, Mapping):
        raise OperationRejectedError(
            "manifest dataset tidak ada di permintaan (disisipkan Control API).")
    try:
        ref = _Ref(str(ds["ref"]), str(ds["version"]), str(ds.get("profile") or "small"),
                   int(ds.get("seed") or 0))
    except (KeyError, TypeError, ValueError):
        raise OperationRejectedError("identitas dataset tidak lengkap.") from None
    if (data.get("ref"), str(data.get("version"))) != (ref.ref, ref.version):
        raise OperationRejectedError("manifest tidak cocok dengan dataset modul.")
    return ref, dict(data)


def salinan_cocok(files: Any, data: Mapping[str, Any], *, deep: bool) -> tuple[int, int]:
    """(jumlah cocok, jumlah berkas) salinan workspace terhadap manifest."""
    manifest = DatasetManifest.from_mapping(data)
    cocok = 0
    for e in manifest.files:
        try:
            p = files.resolver.resolve(f"{MOUNT}/{e.path}").absolute
        except Exception:  # noqa: BLE001 - path manifest tak sah = tidak cocok
            continue
        if not p.is_file() or p.stat().st_size != e.size_bytes:
            continue
        if deep:
            h = hashlib.sha256()
            with p.open("rb") as f:
                for potong in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(potong)
            if "sha256:" + h.hexdigest() != e.sha256:
                continue
        cocok += 1
    return cocok, len(manifest.files)


def materialize_work(
    payload: Mapping[str, Any],
    files: Any,
    *,
    store_dir: Path,
    fetch_url: UrlFetcher,
    fetch_package: PackageFetcher | None,
    course_id: str,
):
    """Fungsi kerja job (dipanggil JobRunner)."""
    from .operations import HandlerResult

    _wajib_tersedia()
    ref, data = _manifest_dari(payload)

    def work(ctx) -> Any:
        cocok, total = salinan_cocok(files, data, deep=True)
        if cocok == total:
            ctx.emit("stdout", f"Dataset {ref.ref} sudah ada di laptop dan terverifikasi.\n")
            return HandlerResult(status="ok", payload={
                "ref": ref.ref, "version": ref.version, "mount": MOUNT,
                "filesWritten": 0, "bytesWritten": 0, "alreadyPresent": True})

        tercetak = {"mb": 0}

        def fetch(spec, dest, progress):
            diterima = {"n": 0}

            def maju(n: int) -> None:
                diterima["n"] += n
                progress(n)
                mb = diterima["n"] // (5 * 1024 * 1024)
                if mb > tercetak["mb"]:
                    tercetak["mb"] = mb
                    ctx.emit("stdout", f"  … {diterima['n'] / 1e6:.0f} MB\n")

            tercetak["mb"] = 0
            if spec["kind"] == "url":
                fetch_url(str(spec["url"]), dest, maju)
            else:
                if fetch_package is None:
                    raise OperationRejectedError("agent belum terhubung ke Control API.")
                fetch_package(course_id, str(spec["path"]), dest, maju)

        mgr = DatasetManager(
            store_dir,
            source_factory=lambda _ident, _pkg: ArtifactSource(
                data, fetch,
                progress=lambda msg: ctx.emit("stdout", msg + "\n"),
                cancelled=lambda: ctx.cancel_requested),
        )
        try:
            mgr.ensure(ref)
            ctx.emit("stdout", f"Menyalin ke {MOUNT}/ …\n")
            hasil = mgr.materialize(ref, files, mount=MOUNT, overwrite=True)
        except Cancelled:
            return HandlerResult(status="cancelled", payload={"ref": ref.ref},
                                 detail="Penyiapan dataset dihentikan.")
        except DatasetError as exc:
            return HandlerResult(status="failed", payload={"ref": ref.ref}, detail=str(exc))
        except OperationRejectedError as exc:
            return HandlerResult(status="failed", payload={"ref": ref.ref}, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - jaringan, disk penuh, dll.
            return HandlerResult(status="failed", payload={"ref": ref.ref},
                                 detail=f"Penyiapan dataset gagal: {exc}")
        ctx.emit("stdout", f"Selesai: {hasil.files_written} berkas di {MOUNT}/.\n")
        return HandlerResult(status="ok", payload={
            "ref": ref.ref, "version": ref.version, "mount": MOUNT,
            "filesWritten": hasil.files_written, "bytesWritten": hasil.bytes_written,
            "alreadyPresent": False, "checksum": hasil.checksum})

    return ref, work


def verify(payload: Mapping[str, Any], files: Any):
    """Status cepat (tanpa hash): apakah berkas dataset sudah ada di laptop."""
    from .operations import HandlerResult

    _wajib_tersedia()
    ref, data = _manifest_dari(payload)
    cocok, total = salinan_cocok(files, data, deep=False)
    return HandlerResult(status="ok", payload={
        "ref": ref.ref, "version": ref.version, "mount": MOUNT,
        "present": cocok, "total": total, "ready": cocok == total})


__all__ = ["MOUNT", "materialize_work", "salinan_cocok", "verify"]
