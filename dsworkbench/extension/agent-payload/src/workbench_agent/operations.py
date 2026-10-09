"""Allowlist operasi di sisi agent.

VM punya allowlist sendiri (``workbench_api.relay.Operation``). Agent punya
salinan yang **sama ketatnya**, dan hanya mengerjakan handler yang memang
terdaftar. Operasi yang dikenal VM tetapi belum diimplementasikan di agent
menghasilkan ``failed`` terstruktur -- bukan eksekusi diam-diam, bukan
perintah shell.

Menambah operasi berarti: entri di sini, bentuk payload, dan handler. Itu
memang lebih berat daripada menerima perintah bebas, dan itulah maksudnya.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Protocol

from .config import AGENT_VERSION
from .env_setup import PROFIL_BAWAAN, ProfilBelumSiap, ProfilTidakDikenal
from .errors import OperationRejectedError
from .jobs import JobRunner, JobSpec, LaneBusy
from .local_ops import (
    CAPABILITY_IDE_STDIO,
    KODE_PIPA_TIDAK_AKTIF,
    AmplopTidakSah,
    PenyimpanAmplop,
    asal_operasi,
)
from .resources import collect_resources
from .uploads import CHUNK_BYTES, UploadError, UploadStore

try:
    from workbench_jupyter import (
        ExecutionResult,
        JupyterBackend,
        JupyterError,
        JupyterTimeoutError,
        KernelNotFoundError,
    )
except ImportError:  # pragma: no cover - lingkungan tanpa paket jupyter
    JupyterBackend = Any  # type: ignore[misc, assignment]
    JupyterError = Exception  # type: ignore[misc, assignment]
    JupyterTimeoutError = Exception  # type: ignore[misc, assignment]
    KernelNotFoundError = Exception  # type: ignore[misc, assignment]
    ExecutionResult = Any  # type: ignore[misc, assignment]

try:
    from workbench_workspace import (
        AlreadyExistsError,
        InvalidPathError,
        PathEscapeError,
        PathResolver,
        WorkspaceError,
        WorkspaceFiles,
        WriteConflictError,
    )
except ImportError:  # pragma: no cover - lingkungan tanpa paket workspace
    WorkspaceFiles = Any  # type: ignore[misc, assignment]
    PathResolver = Any  # type: ignore[misc, assignment]
    WorkspaceError = Exception  # type: ignore[misc, assignment]
    InvalidPathError = Exception  # type: ignore[misc, assignment]
    PathEscapeError = Exception  # type: ignore[misc, assignment]
    WriteConflictError = Exception  # type: ignore[misc, assignment]
    AlreadyExistsError = Exception  # type: ignore[misc, assignment]

try:
    from workbench_preview import PreviewError, preview_file
except ImportError:  # pragma: no cover - lingkungan tanpa paket preview
    PreviewError = Exception  # type: ignore[misc, assignment]
    preview_file = None  # type: ignore[assignment]

try:
    from workbench_checkpoint import (
        CheckpointRequest,
        CheckpointStatus,
        RegistryCheckpointRunner,
    )
except ImportError:  # pragma: no cover - lingkungan tanpa paket checkpoint
    CheckpointRequest = Any  # type: ignore[misc, assignment]
    CheckpointStatus = Any  # type: ignore[misc, assignment]
    RegistryCheckpointRunner = None  # type: ignore[assignment]


class KnownOperation(str, Enum):
    """Salinan allowlist yang agent kenali.

    Harus selaras dengan ``workbench_api.relay.Operation``. Nama yang tidak ada
    di sini ditolak sebelum handler mana pun dipanggil.
    """

    WORKSPACE_ENSURE = "workspace.ensure"
    WORKSPACE_ARCHIVE = "workspace.archive"
    WORKSPACE_LIST = "workspace.list"
    WORKSPACE_INFO = "workspace.info"
    WORKSPACE_TREE = "workspace.tree"
    WORKSPACE_READ_FILE = "workspace.read_file"
    WORKSPACE_WRITE_FILE = "workspace.write_file"
    WORKSPACE_PREVIEW = "workspace.preview"
    WORKSPACE_UPLOAD_BEGIN = "workspace.upload_begin"
    WORKSPACE_UPLOAD_CHUNK = "workspace.upload_chunk"
    WORKSPACE_UPLOAD_COMMIT = "workspace.upload_commit"

    DATASET_ENSURE = "dataset.ensure"
    DATASET_VERIFY = "dataset.verify"
    DATASET_MATERIALIZE = "dataset.materialize"

    RUNTIME_VALIDATE = "runtime.validate"
    RUNTIME_PREPARE = "runtime.prepare"
    RUNTIME_START = "runtime.start"
    RUNTIME_STOP = "runtime.stop"
    RUNTIME_DESTROY = "runtime.destroy"
    RUNTIME_STATUS = "runtime.status"

    CHECKPOINT_RUN = "checkpoint.run"

    JUPYTER_KERNEL_START = "jupyter.kernel_start"
    JUPYTER_KERNEL_STOP = "jupyter.kernel_stop"
    JUPYTER_KERNEL_STATUS = "jupyter.kernel_status"
    JUPYTER_INTERRUPT = "jupyter.interrupt"
    JUPYTER_RESTART = "jupyter.restart"
    JUPYTER_EXECUTE = "jupyter.execute"

    SNAPSHOT_LIST = "snapshot.list"
    SNAPSHOT_STATUS = "snapshot.status"
    SNAPSHOT_PREVIEW = "snapshot.preview"
    SNAPSHOT_RESTORE = "snapshot.restore"
    SNAPSHOT_BACKUPS = "snapshot.backups"
    SNAPSHOT_RESTORE_BACKUP = "snapshot.restore_backup"

    # Job berdurasi panjang -- ADR-049 (RUN-01)
    JOB_CANCEL = "job.cancel"
    JOB_STATUS = "job.status"

    EXPORT_START = "export.start"
    EXPORT_STATUS = "export.status"
    EXPORT_CANCEL = "export.cancel"
    EXPORT_FETCH = "export.fetch"

    # Git lokal di akar mata kuliah -- ADR-051 (GH-01), tanpa jaringan
    GIT_STATUS = "git.status"
    GIT_INIT = "git.init"
    GIT_STAGE = "git.stage"
    GIT_UNSTAGE = "git.unstage"
    GIT_COMMIT = "git.commit"
    GIT_LOG = "git.log"
    GIT_DIFF_SUMMARY = "git.diff_summary"
    # Remote GitHub -- GH-03 (push/pull/clone = job, lane network)
    GIT_REMOTE_BIND = "git.remote_bind"
    GIT_PUSH = "git.push"
    GIT_PULL = "git.pull"
    GIT_MERGE = "git.merge"
    GIT_CLONE = "git.clone"

    # Tautan akun GitHub -- ADR-051 (GH-02); token hanya di laptop
    GITHUB_AUTH_START = "github.auth_start"
    GITHUB_AUTH_POLL = "github.auth_poll"
    GITHUB_AUTH_STATUS = "github.auth_status"
    GITHUB_AUTH_REVOKE = "github.auth_revoke"
    GITHUB_REPOS = "github.repos"
    GITHUB_REPO_CREATE = "github.repo_create"

    AGENT_HEALTH = "agent.health"
    AGENT_RESOURCES = "agent.resources"

    # Layanan dikelola agent -- ADR-053 (PGD-02)
    SERVICE_STATUS = "service.status"
    SERVICE_START = "service.start"
    SERVICE_STOP = "service.stop"
    SERVICE_POSTGRES_LOAD = "service.postgres.load"

    # SQL lewat psql bundel -- ADR-054 §2, §6 (PGD-04)
    SQL_EXECUTE = "sql.execute"
    SQL_RUN_FILE = "sql.run_file"
    SQL_CHECK = "sql.check"
    DB_CATALOG = "db.catalog"

    # Amplop sesi jalur pipa IDE -- ADR-072 §6. Hanya dari relay; pipa menolaknya.
    SESSION_ENVELOPE = "session.envelope"

    @classmethod
    def parse(cls, value: Any) -> "KnownOperation":
        if not isinstance(value, str):
            raise OperationRejectedError(
                "Nama operasi harus berupa teks.",
                detail="tipe tidak sah",
            )
        try:
            return cls(value)
        except ValueError:
            raise OperationRejectedError(
                f"Operasi '{value[:64]}' tidak dikenal agent.",
                detail="di luar allowlist",
            ) from None


@dataclass(frozen=True)
class HandlerResult:
    """Hasil satu handler, siap dikirim ke Control API."""

    status: str  # ok | failed | timeout | cancelled
    payload: Mapping[str, Any]
    detail: str | None = None


class OperationHandler(Protocol):
    def __call__(self, payload: Mapping[str, Any]) -> HandlerResult: ...


#: Operasi berkas workspace yang diimplementasikan Component 13 dan diperluas
#: P15 (ADR-050 §6: `ensure`, hash, potongan, unggah). `archive` dan `info`
#: memerlukan WorkspaceManager, bukan sekadar WorkspaceFiles, dan diikat pada
#: component yang merakitnya.
_CHECKPOINT_OPS = frozenset({
    KnownOperation.CHECKPOINT_RUN,
})

_WORKSPACE_FILE_OPS = frozenset({
    KnownOperation.WORKSPACE_LIST,
    KnownOperation.WORKSPACE_TREE,
    KnownOperation.WORKSPACE_READ_FILE,
    KnownOperation.WORKSPACE_WRITE_FILE,
    KnownOperation.WORKSPACE_PREVIEW,
    KnownOperation.WORKSPACE_ENSURE,
    KnownOperation.WORKSPACE_UPLOAD_BEGIN,
    KnownOperation.WORKSPACE_UPLOAD_CHUNK,
    KnownOperation.WORKSPACE_UPLOAD_COMMIT,
})

#: Kemampuan job berdurasi panjang (ADR-049): ``jupyter.execute`` dengan
#: ``job: true`` dijawab segera dengan ``jobId``; ``job.cancel``/``job.status``.
CAPABILITY_RELAY_JOBS = "relay.jobs.v1"

#: Batas keras tenggat eksekusi di agent (API memangkas ke setting lebih dulu).
HARD_TIMEOUT_SECONDS = 14_400

#: Bawaan mode job bila payload tidak menyebut ``timeoutSeconds``.
DEFAULT_JOB_TIMEOUT_SECONDS = 1_800

#: Jalur sinkron lama (web tanpa ``job``): web menunggu ``timeoutSeconds + 30``
#: detik (60 + 30). Sebelum RUN-01 kernel in-process tidak punya batas sama
#: sekali, jadi sel 60-89 detik masih sempat tampil; 85 detik mempertahankannya.
LEGACY_TIMEOUT_SECONDS = 85

#: Kemampuan dataset kanonik (DL-04): ``dataset.materialize`` (job) dan
#: ``dataset.verify``.
CAPABILITY_DATASETS = "dataset.v1"


def _datasets_tersedia() -> bool:
    """Paket ``workbench_datasets`` ikut terpasang (payload agent 0.3.0 belum)."""
    from . import datasets_op

    return datasets_op.TERSEDIA

#: Folder kerja, token GitHub, dan basis data dipisah per akun Workbench (0.4.9).
CAPABILITY_WORKSPACE_ACCOUNT = "workspace.account.v1"

#: Kemampuan git lokal (GH-01): operasi ``git.*`` tanpa jaringan.
CAPABILITY_GIT = "git.v1"
#: ``git.merge`` (0.4.7): UI hanya menawarkan Gabungkan bila agent melaporkannya.
CAPABILITY_GIT_MERGE = "git.merge.v1"

_GIT_OPS = frozenset({
    KnownOperation.GIT_STATUS,
    KnownOperation.GIT_INIT,
    KnownOperation.GIT_STAGE,
    KnownOperation.GIT_UNSTAGE,
    KnownOperation.GIT_COMMIT,
    KnownOperation.GIT_LOG,
    KnownOperation.GIT_DIFF_SUMMARY,
})


#: Kemampuan tautan akun GitHub (GH-02): ``github.auth_*``.
CAPABILITY_GITHUB_AUTH = "github.auth.v1"

_GITHUB_AUTH_OPS = frozenset({
    KnownOperation.GITHUB_AUTH_START,
    KnownOperation.GITHUB_AUTH_POLL,
    KnownOperation.GITHUB_AUTH_STATUS,
    KnownOperation.GITHUB_AUTH_REVOKE,
    KnownOperation.GITHUB_REPOS,
    KnownOperation.GITHUB_REPO_CREATE,
})

#: Operasi git yang butuh jaringan dan akun tertaut (GH-03).
_GIT_REMOTE_OPS = frozenset({
    KnownOperation.GIT_REMOTE_BIND,
    KnownOperation.GIT_PUSH,
    KnownOperation.GIT_PULL,
    KnownOperation.GIT_MERGE,
    KnownOperation.GIT_CLONE,
})

#: Satu kunci untuk index dulwich per proses agent. Operasi pendek menunggu
#: sebentar saja agar loop transport tidak tertahan push/pull yang berjalan.
_KUNCI_GIT = __import__("threading").Lock()
_TUNGGU_KUNCI_PENDEK = 3.0


def _git_tersedia() -> bool:
    """Mesin git ikut di salinan agent ini (payload lama tidak membawa vendor/)."""
    from . import gitlocal

    return gitlocal.tersedia()

#: Bawaan lane network bila payload tidak menyebut ``timeoutSeconds``.
DEFAULT_NETWORK_TIMEOUT_SECONDS = 900

#: Kemampuan berkas v2: `sha256`/`mtime`/potongan pada `read_file`,
#: `baseSha256`/`ifAbsent`/base64 pada `write_file`, `ensure`, dan unggah.
CAPABILITY_WORKSPACE_FILES_V2 = "workspace.files.v2"

#: Kemampuan layanan PostgreSQL dikelola agent (ADR-053): ``service.status``,
#: ``service.start``, ``service.stop``.
CAPABILITY_SERVICES_POSTGRES = "services.postgres.v1"

_SERVICE_OPS = frozenset({
    KnownOperation.SERVICE_STATUS,
    KnownOperation.SERVICE_START,
    KnownOperation.SERVICE_STOP,
})

#: Kemampuan SQL dari web (ADR-054 §2): ``sql.execute``, ``sql.run_file``,
#: ``sql.check`` (job, lane ``sql:<courseId>``) dan ``db.catalog``.
CAPABILITY_SQL = "sql.v1"
#: ``sql.run_file`` menerima ``variables`` (padanan ``psql -v``) dan mengisi sendiri
#: variabel ``sandi`` (kata sandi peran praktikum) untuk skrip penyambung FDW,
#: sehingga skrip semacam itu tidak lagi butuh terminal (0.4.10).
CAPABILITY_SQL_VARS = "sql.vars.v1"
#: ``checkpoint.run`` dengan bundel pemeriksa dari server dan akar workspace
#: ``payload.workspace`` (ADR-026, catatan 2026-10). Agent lama menuntut
#: ``workspaceRoot``/``packageRoot`` absolut; web hanya menawarkan Jalankan
#: bila capability ini dilaporkan.
CAPABILITY_CHECKPOINT_PACKAGE = "checkpoint.package.v1"

#: Operasi SQL berbentuk job. ``db.catalog`` pendek (dibaca di loop transport).
_SQL_JOB_OPS = frozenset({
    KnownOperation.SQL_EXECUTE,
    KnownOperation.SQL_RUN_FILE,
    KnownOperation.SQL_CHECK,
})

#: Id mata kuliah sebagai nama folder: slug katalog, tidak pernah path.
_POLA_COURSE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def course_root_of(payload: Mapping[str, Any]) -> str | None:
    """Id mata kuliah bila akar operasi ini per mata kuliah (ADR-050 §4).

    Nilainya dari ``payload.workspace`` yang disisipkan Control API setelah
    memeriksa keanggotaan -- bukan dari ``courseId`` kiriman peramban.
    ``None`` = akar workspace (tata letak ``flat``).
    """
    ws = payload.get("workspace")
    if not isinstance(ws, Mapping) or ws.get("layout") != "per-course":
        return None
    cid = ws.get("courseId")
    if not isinstance(cid, str) or not _POLA_COURSE_ID.fullmatch(cid):
        raise OperationRejectedError("courseId workspace harus slug mata kuliah.")
    return cid

_JUPYTER_OPS = frozenset({
    KnownOperation.JUPYTER_KERNEL_START,
    KnownOperation.JUPYTER_KERNEL_STOP,
    KnownOperation.JUPYTER_KERNEL_STATUS,
    KnownOperation.JUPYTER_INTERRUPT,
    KnownOperation.JUPYTER_RESTART,
    KnownOperation.JUPYTER_EXECUTE,
})


def _health(_payload: Mapping[str, Any]) -> HandlerResult:
    return HandlerResult(
        status="ok",
        payload={
            "status": "ok",
            "agentVersion": AGENT_VERSION,
            "transport": "http-polling",
        },
    )


def _resources(_payload: Mapping[str, Any]) -> HandlerResult:
    return HandlerResult(status="ok", payload=collect_resources())


#: Paket yang menyediakan handler untuk tiap kelompok operasi. Dipakai hanya
#: untuk menyusun pesan kesalahan yang memberi tahu apa yang kurang.
_PENYEDIA = {
    "jupyter": "workbench_jupyter",
    "checkpoint": "workbench_checkpoint",
    "workspace": "workbench_workspace",
}


def _belum_siap(operation: KnownOperation) -> OperationHandler:
    """Handler untuk operasi yang paket penyedianya tidak terpasang.

    Penyebab yang jauh paling lazim adalah agent versi lama: paket pendamping
    ditambahkan ke paket unduhan belakangan, sehingga salinan yang diunduh
    sebelumnya hanya berisi ``workbench_agent``. Pesannya menyebut tindakan
    yang menyelesaikannya, bukan sekadar menyatakan keadaan.
    """

    def _handler(_payload: Mapping[str, Any]) -> HandlerResult:
        kelompok = operation.value.split(".", 1)[0]
        paket = _PENYEDIA.get(kelompok)
        pesan = (
            f"Agent di komputer Anda belum dapat menjalankan '{operation.value}'. "
            "Unduh ulang agent dari halaman Local Runner, ekstrak menimpa folder "
            "lama, lalu jalankan kembali."
        )
        if paket:
            pesan += f" (Paket '{paket}' tidak ada di salinan agent ini.)"
        return HandlerResult(
            status="failed",
            payload={"operation": operation.value, "missingPackage": paket},
            detail=pesan,
        )

    return _handler


def _lane_kernel(kernel_id: str) -> str:
    return f"kernel:{kernel_id}"


def _jalankan_sel(backend: JupyterBackend, kid: str, code: str, timeout: float | None,
                  on_stream: Any = None) -> HandlerResult:
    """Eksekusi satu sel dan petakan ke HandlerResult (sinkron maupun job)."""
    try:
        if on_stream is not None and getattr(backend, "supports_streaming", False):
            hasil: ExecutionResult = backend.execute(kid, code, timeout=timeout,
                                                     on_stream=on_stream)
        else:
            hasil = backend.execute(kid, code, timeout=timeout)
    except JupyterTimeoutError as exc:
        return HandlerResult(status="timeout", payload={}, detail=str(exc))
    except JupyterError as exc:
        return HandlerResult(status="failed", payload={}, detail=str(exc))
    return HandlerResult(
        status="ok" if hasil.ok else "failed",
        payload={"result": hasil.to_public()},
        detail=hasil.detail,
    )


def _timeout_payload(payload: Mapping[str, Any], *, job_mode: bool) -> float:
    nilai = payload.get("timeoutSeconds")
    if isinstance(nilai, bool) or not isinstance(nilai, (int, float)) or nilai <= 0:
        nilai = DEFAULT_JOB_TIMEOUT_SECONDS if job_mode else 60
    if not job_mode:
        nilai = max(float(nilai), LEGACY_TIMEOUT_SECONDS)
    return float(min(nilai, HARD_TIMEOUT_SECONDS))


def _jupyter_handlers(
    backend: JupyterBackend, jobs: JobRunner | None = None,
    workspace_root: Any = None,
) -> dict[KnownOperation, OperationHandler]:
    """Ikat seluruh operasi jupyter.* ke satu backend."""

    def _cwd_kernel(payload: Mapping[str, Any]) -> Any:
        """Akar mata kuliah per-course sebagai direktori kerja (ADR-050 §3)."""
        cid = course_root_of(payload)
        if cid is None or workspace_root is None:
            return None
        from pathlib import Path

        akar = Path(workspace_root) / cid
        akar.mkdir(parents=True, exist_ok=True)
        return akar

    def _batalkan_job_kernel(kid: str) -> bool:
        """Kernel yang sedang menjalankan job: batalkan lewat JobRunner."""
        if jobs is None:
            return False
        aktif = jobs.lane_active(_lane_kernel(kid))
        if aktif is None:
            return False
        jobs.cancel(aktif.job_id)
        return True

    def _start(payload: Mapping[str, Any]) -> HandlerResult:
        name = payload.get("name") if isinstance(payload.get("name"), str) else None
        profil = payload.get("runtimeProfile")
        if profil is not None and not isinstance(profil, str):
            raise OperationRejectedError("runtimeProfile harus berupa teks.")
        # ADR-041 §4: modul menyebut profilnya; agent memilih interpreter.
        # Tanpa runtimeProfile perilakunya persis seperti sebelumnya.
        mulai = getattr(backend, "start_kernel_for_profile", None)
        cwd = _cwd_kernel(payload)
        ekstra = {"cwd": cwd} if cwd is not None else {}
        if asal_operasi() == "pipa":
            # ADR-072 §4: kernel dari pipa IDE selalu proses anak, juga profil
            # bawaan -- kernel in-process memakai state global proses agent
            # (chdir, sys.stdout) dan tidak aman untuk dua notebook bersamaan.
            if mulai is None:
                return HandlerResult(
                    status="failed", payload={"code": "subprocess_kernel_unavailable"},
                    detail="Backend kernel agent ini tidak dapat menjalankan kernel "
                           "sebagai proses terpisah untuk IDE.")
            ekstra["proses_anak"] = True
        try:
            if mulai is not None:
                info = mulai(profil, name=name, **ekstra)
            elif profil in (None, PROFIL_BAWAAN):
                info = backend.start_kernel(name=name, **ekstra)
            else:
                return HandlerResult(
                    status="failed",
                    payload={"code": "profile_unsupported", "runtimeProfile": profil},
                    detail=(f"Backend kernel agent ini hanya melayani profil "
                            f"{PROFIL_BAWAAN}; profil {profil} memerlukan "
                            "Local Runner tanpa Jupyter Server eksternal."),
                )
        except ProfilTidakDikenal as exc:
            raise OperationRejectedError(str(exc), detail="profil di luar manifest") from exc
        except ProfilBelumSiap as exc:
            return HandlerResult(
                status="failed",
                payload={"code": "profile_not_ready", "runtimeProfile": exc.profil_id},
                detail=str(exc),
            )
        except JupyterError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        publik = dict(info.to_public())
        dipakai = getattr(backend, "profile_of", None)
        publik["runtimeProfile"] = (dipakai(info.id) if dipakai else None) or PROFIL_BAWAAN
        return HandlerResult(status="ok", payload={"kernel": publik})

    def _stop(payload: Mapping[str, Any]) -> HandlerResult:
        kid = _kernel_id(payload)
        _batalkan_job_kernel(kid)
        try:
            backend.stop_kernel(kid)
        except KernelNotFoundError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        except JupyterError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        return HandlerResult(status="ok", payload={"kernelId": kid})

    def _status(payload: Mapping[str, Any]) -> HandlerResult:
        kid = _kernel_id(payload)
        try:
            info = backend.kernel_status(kid)
        except KernelNotFoundError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        except JupyterError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        return HandlerResult(status="ok", payload={"kernel": info.to_public()})

    def _interrupt(payload: Mapping[str, Any]) -> HandlerResult:
        kid = _kernel_id(payload)
        # Kernel dengan job aktif: interrupt = job.cancel (state `cancelled`).
        if _batalkan_job_kernel(kid):
            return HandlerResult(status="ok", payload={"kernelId": kid})
        try:
            backend.interrupt_kernel(kid)
        except JupyterError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        return HandlerResult(status="ok", payload={"kernelId": kid})

    def _restart(payload: Mapping[str, Any]) -> HandlerResult:
        kid = _kernel_id(payload)
        _batalkan_job_kernel(kid)
        try:
            info = backend.restart_kernel(kid)
        except JupyterError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        return HandlerResult(status="ok", payload={"kernel": info.to_public()})

    def _execute(payload: Mapping[str, Any]) -> HandlerResult:
        kid = _kernel_id(payload)
        code = payload.get("code")
        if not isinstance(code, str):
            raise OperationRejectedError("code wajib berupa teks.")
        timeout = payload.get("timeoutSeconds", 60)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            timeout = 60
        return _jalankan_sel(backend, kid, code, float(min(timeout, HARD_TIMEOUT_SECONDS)))

    return {
        KnownOperation.JUPYTER_KERNEL_START: _start,
        KnownOperation.JUPYTER_KERNEL_STOP: _stop,
        KnownOperation.JUPYTER_KERNEL_STATUS: _status,
        KnownOperation.JUPYTER_INTERRUPT: _interrupt,
        KnownOperation.JUPYTER_RESTART: _restart,
        KnownOperation.JUPYTER_EXECUTE: _execute,
    }


class WorkspaceRoots:
    """Akar workspace per payload: akar mata kuliah (per-course) atau akar agent."""

    def __init__(self, akar_files: WorkspaceFiles):
        self.root = akar_files
        self._per_course: dict[str, WorkspaceFiles] = {}
        #: Dipanggil dari utas transport relay, pekerja job, dan pipa IDE.
        self._kunci = threading.Lock()

    def for_payload(self, payload: Mapping[str, Any]) -> WorkspaceFiles:
        cid = course_root_of(payload)
        if cid is None:
            return self.root
        with self._kunci:
            f = self._per_course.get(cid)
            if f is None:
                self.root.mkdir(cid)
                r = self.root.resolver
                f = WorkspaceFiles(PathResolver(r.root / cid, read_only=r.read_only_policy,
                                                limits=r.limits))
                self._per_course[cid] = f
            return f


def _workspace_handlers(
    akar_files: WorkspaceFiles, uploads: UploadStore | None = None,
    roots: "WorkspaceRoots | None" = None,
) -> dict[KnownOperation, OperationHandler]:
    """Ikat operasi berkas workspace ke satu WorkspaceFiles.

    Seluruh resolusi path dilakukan WorkspaceFiles: penolakan traversal,
    symlink escape, dan area read-only berlaku di sini tanpa satu pun
    pemeriksaan tambahan di modul ini. Agent tidak membuat kebijakan path
    tandingan.
    """
    if uploads is None:
        uploads = UploadStore(max_size=akar_files.resolver.limits.max_write_bytes)
    roots = roots or WorkspaceRoots(akar_files)

    def _akar(payload: Mapping[str, Any]) -> WorkspaceFiles:
        """Akar operasi berkas (ADR-050 §4): per mata kuliah bila API menyatakannya."""
        try:
            return roots.for_payload(payload)
        except (InvalidPathError, PathEscapeError) as exc:
            _tolak_pelanggaran(exc)
            raise  # pragma: no cover - _tolak_pelanggaran selalu melempar

    def _path(payload: Mapping[str, Any], *, wajib: bool = True) -> str:
        nilai = payload.get("path")
        if nilai is None and not wajib:
            return "."
        if not isinstance(nilai, str) or not nilai:
            raise OperationRejectedError("path wajib berupa teks.")
        return nilai

    def _tolak_pelanggaran(exc: Exception) -> None:
        """Ubah pelanggaran kebijakan path menjadi penolakan operasi.

        Percobaan keluar dari akar workspace berbeda sifatnya dari kegagalan
        biasa: ia berbentuk serangan, bukan kekeliruan yang dapat dijelaskan
        kepada mahasiswa. Membedakannya membuat audit dapat memisahkan
        keduanya, alih-alih menenggelamkan percobaan traversal di antara
        "berkas tidak ditemukan".
        """
        raise OperationRejectedError(
            "Path tidak diizinkan.", detail=str(exc)
        ) from exc

    def _list(payload: Mapping[str, Any]) -> HandlerResult:
        files = _akar(payload)
        try:
            entri = files.list_dir(
                _path(payload, wajib=False),
                include_hidden=bool(payload.get("includeHidden", False)),
            )
        except (InvalidPathError, PathEscapeError) as exc:
            _tolak_pelanggaran(exc)
        except WorkspaceError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        return HandlerResult(status="ok", payload={
            "entries": [
                {"path": e.path, "name": e.name, "isDir": e.is_dir,
                 "sizeBytes": e.size_bytes, "readOnly": e.read_only}
                for e in entri
            ],
        })

    def _tree(payload: Mapping[str, Any]) -> HandlerResult:
        files = _akar(payload)
        dalam = payload.get("maxDepth")
        try:
            entri = files.tree(
                _path(payload, wajib=False),
                max_depth=dalam if isinstance(dalam, int) and dalam > 0 else None,
                include_hidden=bool(payload.get("includeHidden", False)),
            )
        except (InvalidPathError, PathEscapeError) as exc:
            _tolak_pelanggaran(exc)
        except WorkspaceError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        return HandlerResult(status="ok", payload={
            "entries": [
                {"path": e.path, "name": e.name, "isDir": e.is_dir,
                 "sizeBytes": e.size_bytes, "readOnly": e.read_only}
                for e in entri
            ],
        })

    def _read(payload: Mapping[str, Any]) -> HandlerResult:
        """Baca berkas atau satu potongannya, beserta hash berkas utuh.

        Tanpa ``offset``/``length`` dan untuk berkas ≤ ``CHUNK_BYTES`` yang
        UTF-8 sah, ``content`` berupa teks seperti sebelum v2. Selebihnya
        ``content`` base64 dan pemanggil melanjutkan dari ``offset`` berikut
        sampai ``eof`` (ADR-050 §6).
        """
        files = _akar(payload)
        path = _path(payload)
        offset = payload.get("offset", 0)
        length = payload.get("length", CHUNK_BYTES)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise OperationRejectedError("offset harus bilangan bulat ≥ 0.")
        if not isinstance(length, int) or isinstance(length, bool) or not 0 < length <= CHUNK_BYTES:
            raise OperationRejectedError(f"length harus 1…{CHUNK_BYTES}.")
        try:
            # Resolusi lebih dulu: traversal harus ditolak, bukan dilaporkan "tidak ada".
            ada = files.resolver.resolve(path).absolute.exists()
        except (InvalidPathError, PathEscapeError) as exc:
            _tolak_pelanggaran(exc)
        if not ada:
            # Bukan kegagalan: web memakainya untuk memutuskan menyalin starter.
            return HandlerResult(status="failed", payload={"code": "not_found", "path": path},
                                 detail=f"'{path}' tidak ada di workspace.")
        try:
            st = files.stat(path)
            data = files.read_range(path, offset, length)
        except (InvalidPathError, PathEscapeError) as exc:
            _tolak_pelanggaran(exc)
        except WorkspaceError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        eof = offset + len(data) >= st.size
        utuh = offset == 0 and eof
        isi, encoding = None, "base64"
        if utuh:
            try:
                isi, encoding = data.decode("utf-8"), "utf-8"
            except UnicodeDecodeError:
                pass
        if isi is None:
            isi = base64.b64encode(data).decode("ascii")
        return HandlerResult(status="ok", payload={
            "path": st.path, "content": isi, "encoding": encoding,
            "offset": offset, "size": st.size, "sha256": st.sha256,
            "mtime": st.mtime, "eof": eof,
        })

    def _isi(payload: Mapping[str, Any], kunci: str = "content") -> bytes:
        isi = payload.get(kunci)
        if not isinstance(isi, str):
            raise OperationRejectedError(f"{kunci} wajib berupa teks.")
        encoding = payload.get("encoding", "utf-8")
        if encoding == "utf-8":
            return isi.encode("utf-8")
        if encoding == "base64":
            try:
                return base64.b64decode(isi, validate=True)
            except (binascii.Error, ValueError):
                raise OperationRejectedError(f"{kunci} bukan base64 yang sah.") from None
        raise OperationRejectedError("encoding harus utf-8 atau base64.")

    def _sha(payload: Mapping[str, Any], kunci: str, *, wajib: bool = False) -> str | None:
        nilai = payload.get(kunci)
        if nilai is None and not wajib:
            return None
        if not isinstance(nilai, str) or not re.fullmatch(r"[0-9a-f]{64}", nilai):
            raise OperationRejectedError(f"{kunci} harus SHA-256 heksadesimal.")
        return nilai

    def _tulis(files: WorkspaceFiles, path: str, data: bytes, *, base: str | None,
               if_absent: bool) -> HandlerResult:
        try:
            hasil = files.write_bytes(path, data, overwrite=not if_absent,
                                      expected_sha256=base)
        except (InvalidPathError, PathEscapeError) as exc:
            _tolak_pelanggaran(exc)
        except WriteConflictError as exc:
            # Bukan kegagalan transport: UI menawarkan pilihan (ADR-050 §2).
            return HandlerResult(status="failed", payload={
                "code": "conflict", "currentSha256": exc.current_sha256,
                "size": exc.size, "mtime": exc.mtime,
            }, detail=str(exc))
        except AlreadyExistsError as exc:
            return HandlerResult(status="failed", payload={"code": "exists"}, detail=str(exc))
        except WorkspaceError as exc:
            # Termasuk penolakan area read-only: data mentah tidak boleh
            # diubah lewat jalur ini, dan pesannya sudah dapat dipahami.
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        return HandlerResult(status="ok", payload={
            "path": hasil.text, "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        })

    def _write(payload: Mapping[str, Any]) -> HandlerResult:
        files = _akar(payload)
        return _tulis(files, _path(payload), _isi(payload), base=_sha(payload, "baseSha256"),
                      if_absent=bool(payload.get("ifAbsent", False)))

    def _ensure(payload: Mapping[str, Any]) -> HandlerResult:
        """Pastikan akar mata kuliah ada (per-course). Idempoten; tidak menimpa apa pun."""
        cid = course_root_of(payload)

        def _dengan_jalur(muatan: dict[str, Any], files: WorkspaceFiles) -> dict[str, Any]:
            # ADR-072: aplikasi DSWorkbench membuka akar ini sebagai folder kerja,
            # jadi butuh jalur absolutnya. Hanya untuk pipa IDE (proses di komputer
            # yang sama); lewat relay jalur disk mahasiswa tidak dikirim ke server.
            if asal_operasi() == "pipa":
                muatan["absolutePath"] = str(files.resolver.root)
            return muatan

        if cid is None:
            return HandlerResult(status="ok", payload=_dengan_jalur(
                {"root": ".", "created": False, "layout": "flat"}, akar_files))
        baru = not akar_files.exists(cid)
        try:
            files = _akar(payload)
        except WorkspaceError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        return HandlerResult(status="ok", payload=_dengan_jalur(
            {"root": cid, "created": baru, "layout": "per-course"}, files))

    def _gagal_unggah(exc: UploadError) -> HandlerResult:
        return HandlerResult(status="failed", payload={"code": exc.code}, detail=str(exc))

    def _upload_begin(payload: Mapping[str, Any]) -> HandlerResult:
        files = _akar(payload)
        path = _path(payload)
        size = payload.get("size")
        if not isinstance(size, int) or isinstance(size, bool):
            raise OperationRejectedError("size wajib bilangan bulat.")
        try:
            # Tolak path terlarang sekarang, bukan setelah seluruh potongan tiba.
            files.resolver.resolve(path, for_write=True)
        except (InvalidPathError, PathEscapeError) as exc:
            _tolak_pelanggaran(exc)
        except WorkspaceError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        try:
            u = uploads.begin(path=path, size=size, sha256=_sha(payload, "sha256", wajib=True),
                              base_sha256=_sha(payload, "baseSha256"),
                              if_absent=bool(payload.get("ifAbsent", False)),
                              scope=course_root_of(payload))
        except UploadError as exc:
            return _gagal_unggah(exc)
        return HandlerResult(status="ok", payload={
            "uploadId": u.upload_id, "chunkBytes": CHUNK_BYTES, "chunks": u.jumlah_potongan,
        })

    def _upload_chunk(payload: Mapping[str, Any]) -> HandlerResult:
        uid = payload.get("uploadId")
        index = payload.get("index")
        if not isinstance(uid, str) or not uid:
            raise OperationRejectedError("uploadId wajib diisi.")
        if not isinstance(index, int) or isinstance(index, bool):
            raise OperationRejectedError("index wajib bilangan bulat.")
        data = _isi({"content": payload.get("content"), "encoding": "base64"})
        try:
            diterima = uploads.chunk(uid, index, data)
        except UploadError as exc:
            return _gagal_unggah(exc)
        return HandlerResult(status="ok", payload={"uploadId": uid, "received": diterima})

    def _upload_commit(payload: Mapping[str, Any]) -> HandlerResult:
        files = _akar(payload)
        uid = payload.get("uploadId")
        if not isinstance(uid, str) or not uid:
            raise OperationRejectedError("uploadId wajib diisi.")
        try:
            u, data = uploads.take_complete(uid)
        except UploadError as exc:
            return _gagal_unggah(exc)
        if u.scope != course_root_of(payload):
            return _gagal_unggah(UploadError("Unggahan milik mata kuliah lain.", code="scope_mismatch"))
        return _tulis(files, u.path, data, base=u.base_sha256, if_absent=u.if_absent)

    def _preview(payload: Mapping[str, Any]) -> HandlerResult:
        files = _akar(payload)
        if preview_file is None:  # pragma: no cover - paket preview tak terpasang
            return HandlerResult(
                status="failed", payload={},
                detail="paket pratinjau belum terpasang pada agent ini.",
            )
        rel = _path(payload)
        baris = payload.get("maxRows")
        try:
            target = files.resolver.resolve(rel)
            hasil = preview_file(
                target.absolute, relative=target.text,
                max_rows=baris if isinstance(baris, int) else None,
            )
        except (InvalidPathError, PathEscapeError) as exc:
            _tolak_pelanggaran(exc)
        except WorkspaceError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        except PreviewError as exc:
            return HandlerResult(status="failed", payload={}, detail=str(exc))
        # Pratinjau yang tidak tersedia bukan kegagalan: UI menampilkannya
        # sebagai keterangan, bukan pesan kesalahan merah.
        return HandlerResult(status="ok", payload={"preview": hasil.to_dict()})

    return {
        KnownOperation.WORKSPACE_LIST: _list,
        KnownOperation.WORKSPACE_TREE: _tree,
        KnownOperation.WORKSPACE_READ_FILE: _read,
        KnownOperation.WORKSPACE_WRITE_FILE: _write,
        KnownOperation.WORKSPACE_PREVIEW: _preview,
        KnownOperation.WORKSPACE_ENSURE: _ensure,
        KnownOperation.WORKSPACE_UPLOAD_BEGIN: _upload_begin,
        KnownOperation.WORKSPACE_UPLOAD_CHUNK: _upload_chunk,
        KnownOperation.WORKSPACE_UPLOAD_COMMIT: _upload_commit,
    }


def _kernel_id(payload: Mapping[str, Any]) -> str:
    kid = payload.get("kernelId")
    if not isinstance(kid, str) or not kid:
        raise OperationRejectedError("kernelId wajib diisi.")
    return kid


#: Batas bundel pemeriksa yang dibawa envelope ``checkpoint.run``.
_CEK_MAKS_BERKAS = 64
_CEK_MAKS_BYTES = 1024 * 1024
#: Batas keras waktu pemeriksa: operasi pendek menahan loop transport, dan
#: Control API membuang envelope yang tak terjawab setelah ±120 dtk.
_CEK_MAKS_DETIK = 180


def _bundel_pemeriksa(spec: Mapping[str, Any]) -> list[tuple[str, bytes]]:
    """Validasi ``checkpoint.files`` dari Control API -> ``[(path, isi)]``.

    Path relatif saja (aturan ``workbench_checkpoint.paths``); ``sha256``
    diperiksa bila ada. Agent tidak menerima path absolut dari relay.
    """
    from workbench_checkpoint.errors import InvalidArtifactError
    from workbench_checkpoint.paths import assert_relative_safe

    files = spec.get("files")
    if not isinstance(files, list) or not files or len(files) > _CEK_MAKS_BERKAS:
        raise OperationRejectedError("Berkas pemeriksa tidak disertakan server.")
    hasil: list[tuple[str, bytes]] = []
    total = 0
    for f in files:
        if not isinstance(f, Mapping) or not isinstance(f.get("path"), str) \
                or not isinstance(f.get("content"), str):
            raise OperationRejectedError("Berkas pemeriksa harus {path, content}.")
        try:
            rel = assert_relative_safe(f["path"], field="checkpoint.files.path")
        except InvalidArtifactError as exc:
            raise OperationRejectedError(str(exc)) from None
        isi = f["content"].encode("utf-8")
        total += len(isi)
        if total > _CEK_MAKS_BYTES:
            raise OperationRejectedError("Bundel pemeriksa melebihi 1 MiB.")
        sidik = f.get("sha256")
        if sidik is not None and sidik != hashlib.sha256(isi).hexdigest():
            raise OperationRejectedError(f"Sidik berkas pemeriksa tidak cocok: {rel}")
        hasil.append((rel, isi))
    return hasil


def _checkpoint_handlers(runner: Any, roots: "WorkspaceRoots | None" = None
                         ) -> dict[KnownOperation, OperationHandler]:
    """Ikat ``checkpoint.run`` ke RegistryCheckpointRunner.

    Envelope membawa blok ``checkpoint`` yang disusun Control API dari LabSpec
    package: ``{handler, artifact, timeoutSeconds, params, files}``. Berkas
    pemeriksa ditulis ke direktori sementara (akar package untuk handler) dan
    dihapus sesudahnya; akar workspace dari ``payload.workspace`` (ADR-050 §4).
    Agent tidak pernah menerima string perintah atau path absolut.

    Hasil ``LEWAT`` dan ``GAGAL`` dilaporkan sebagai ``ok`` dengan status di
    dalam payload agar UI dapat membedakannya dari kegagalan transport;
    ``KESALAHAN`` (pemeriksa tidak dapat dijalankan) sebagai ``failed``.
    """

    def _run(payload: Mapping[str, Any]) -> HandlerResult:
        import tempfile
        from pathlib import Path

        spec = payload.get("checkpoint")
        if not isinstance(spec, Mapping):
            raise OperationRejectedError(
                "Spesifikasi checkpoint tidak disertakan server. Muat ulang halaman Workbench.")
        handler = spec.get("handler")
        artifact = spec.get("artifact")
        if not isinstance(handler, str) or not handler:
            raise OperationRejectedError("handler wajib berupa teks allowlist.")
        if not isinstance(artifact, str) or not artifact:
            raise OperationRejectedError("artifact wajib berupa path relatif.")
        timeout = spec.get("timeoutSeconds", 180)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise OperationRejectedError("timeoutSeconds harus bilangan positif.")
        params = spec.get("params") or {}
        if not isinstance(params, Mapping):
            raise OperationRejectedError("params harus berupa objek.")
        module_id = payload.get("moduleId") or ""
        if not isinstance(module_id, str):
            raise OperationRejectedError("moduleId harus berupa teks.")
        berkas = _bundel_pemeriksa(spec)
        if artifact not in {rel for rel, _ in berkas}:
            raise OperationRejectedError("Artefak pemeriksa tidak ada di bundel server.")
        if roots is None:
            raise OperationRejectedError("Workspace belum terikat di Local Runner ini.")
        try:
            akar = roots.for_payload(payload).resolver.root
        except (InvalidPathError, PathEscapeError) as exc:
            raise OperationRejectedError("Akar workspace tidak diizinkan.", detail=str(exc)) from exc

        with tempfile.TemporaryDirectory(prefix="workbench-checkpoint-") as tmp:
            paket = Path(tmp)
            for rel, isi in berkas:
                tujuan = paket / rel
                tujuan.parent.mkdir(parents=True, exist_ok=True)
                tujuan.write_bytes(isi)
            request = CheckpointRequest(
                handler=handler,
                artifact=artifact,
                timeout_seconds=int(min(timeout, _CEK_MAKS_DETIK)),
                params=dict(params),
                workspace_root=str(akar),
                package_root=str(paket),
                module_id=module_id,
            )
            result = runner.run(request)
        status = "failed" if result.status is CheckpointStatus.ERROR else "ok"
        return HandlerResult(status=status, payload={"result": result.to_public()},
                             detail=result.summary)

    return {KnownOperation.CHECKPOINT_RUN: _run}


class LayananAgent:
    """Pengikat ``service.*`` ke :mod:`pgservice` (ADR-053).

    Definisi layanan dari ``requirements/services.json`` di folder agent;
    klaster dari blok ``services`` yang disisipkan Control API. Tidak ada
    unduhan di sini -- biner dipasang lewat ``ensure-env`` (ADR-041 §5).
    """

    def __init__(self, *, akar_agent: Any, state_dir: Any, akar_workspace: Any,
                 data_dir: Any = None):
        from pathlib import Path

        from . import pgservice

        self._pg = pgservice
        self.akar_agent = Path(akar_agent)
        # Klaster, biner, dan berkas sandi berada di bawah state dir; di Windows
        # nama pengguna non-ASCII membuat initdb gagal → pakai nama pendek 8.3.
        self.state_dir = pgservice.jalur_aman_windows(Path(state_dir))
        # Klaster (data) per akun Workbench; biner tetap dipakai bersama dari
        # ``state_dir``. Tanpa ``data_dir``: satu tempat seperti sebelumnya.
        self.data_dir = (pgservice.jalur_aman_windows(Path(data_dir))
                         if data_dir is not None else self.state_dir)
        self.akar_workspace = Path(akar_workspace)
        self.definisi = pgservice.muat_layanan(self.akar_agent)

    def tersedia(self) -> bool:
        from .env_setup import kunci_platform

        k = kunci_platform()
        return any(d.id.startswith("postgres-") and k in d.platform for d in self.definisi.values())

    def _definisi(self, payload: Mapping[str, Any]) -> Any:
        blok = payload.get("services")
        pg = blok.get("postgres") if isinstance(blok, Mapping) else None
        versi = str(pg.get("version", "")) if isinstance(pg, Mapping) else ""
        defn = self.definisi.get(f"postgres-{versi.split('.', 1)[0]}")
        if defn is None:
            raise OperationRejectedError(
                f"Agent ini tidak menyediakan PostgreSQL {versi or '?'}; unduh ulang agent terbaru.")
        return defn

    def _venv_bin(self, defn: Any) -> Any:
        from .env_setup import _jalur_python_di, jalur_venv_profil, muat_profil

        for p in muat_profil(self.akar_agent).values():
            if defn.id in p.layanan:
                py = _jalur_python_di(jalur_venv_profil(self.akar_agent, p.id))
                return py.parent if py.is_file() else None
        return None

    def kelola(self) -> Any:
        from .env_setup import kunci_platform

        kunci = kunci_platform()
        return self._pg.KelolaPostgres(
            state_dir=self.data_dir, akar_workspace=self.akar_workspace,
            lokasi_bin=lambda d: self._pg.lokasi_biner(d, self.state_dir, kunci),
            venv_bin=self._venv_bin)

    def hentikan_semua(self) -> int:
        """Klaster yang menyala dihentikan saat agent keluar (ADR-053 §6)."""
        from .env_setup import kunci_platform

        kunci = kunci_platform()
        dirs = [b for d in self.definisi.values()
                if (b := self._pg.lokasi_biner(d, self.state_dir, kunci)) is not None]
        return self._pg.hentikan_semua(self.data_dir, dirs)

    def _spesifikasi(self, payload: Mapping[str, Any]) -> tuple[Any, Any]:
        defn = self._definisi(payload)
        spec = self._pg.spesifikasi_dari(payload, defn)
        if course_root_of(payload) != spec.course_id:
            # Berkas mahasiswa (.env, peluncur, data/raw) ada di akar mata kuliah.
            raise OperationRejectedError(
                "Layanan basis data membutuhkan tata letak workspace per mata kuliah.")
        # Port pengganti laptop ini (bila port bawaan package terpakai) berlaku di semua operasi.
        return defn, self.kelola().terapkan_port(spec)

    def kerja_muat(self, payload: Mapping[str, Any], files: Any) -> Callable[[Any], HandlerResult]:
        """Job ``service.postgres.load`` (ADR-055 §2): verifikasi lalu muat."""
        import json as _json

        from . import datasets_op

        defn, spec = self._spesifikasi(payload)
        _ref, manifest = datasets_op._manifest_dari(payload)
        rencana_rel = [f.get("path") for f in manifest.get("files") or []
                       if isinstance(f, Mapping) and str(f.get("path", "")).endswith("/load.json")]
        if len(rencana_rel) != 1:
            raise OperationRejectedError("Dataset ini tidak membawa rencana muat basis data.")

        def kerja(ctx: Any) -> HandlerResult:
            lapor = lambda teks: ctx.emit("stdout", teks + "\n")  # noqa: E731
            lapor("Memeriksa berkas dataset (SHA-256) …")
            cocok, total = datasets_op.salinan_cocok(files, manifest, deep=True)
            if cocok != total:
                return HandlerResult(
                    status="failed", payload={"code": "dataset_missing", "present": cocok,
                                              "total": total},
                    detail="Dataset belum lengkap atau berubah di laptop. Siapkan dataset "
                           "di laptop dulu, lalu muat lagi.")
            rencana_path = files.resolver.resolve(f"{datasets_op.MOUNT}/{rencana_rel[0]}").absolute
            rencana = _json.loads(rencana_path.read_text(encoding="utf-8"))
            try:
                hasil = self.kelola().muat(defn, spec, rencana_path.parent, rencana, lapor=lapor,
                                           batal=lambda: ctx.cancel_requested)
            except self._pg.LayananGagal as exc:
                if exc.code == "cancelled":
                    return HandlerResult(status="cancelled", payload={}, detail=exc.pesan)
                return HandlerResult(status="failed", payload=exc.payload(), detail=exc.pesan)
            return HandlerResult(status="ok", payload=hasil)

        return kerja

    # -- SQL (ADR-054 §2, PGD-04) ------------------------------------------

    def _sasaran(self, payload: Mapping[str, Any], alias: Any = None,
                 database: Any = None) -> dict[str, Any]:
        """Klaster + basis data tujuan yang dideklarasikan package, sudah menyala.

        Mengembalikan argv dasar dan lingkungan ``psql`` sebagai ``praktikum``
        (``PGPASSFILE`` agent; tidak ada sandi di argv maupun payload).
        """
        defn, spec = self._spesifikasi(payload)
        alias = payload.get("alias") if alias is None else alias
        database = payload.get("database") if database is None else database
        if not isinstance(alias, str) or not alias:
            raise OperationRejectedError("alias klaster wajib diisi.")
        k = spec.pilih(alias)[0]
        if not isinstance(database, str) or database not in k.databases:
            raise OperationRejectedError(
                f"Basis data '{str(database)[:63]}' tidak dideklarasikan untuk klaster {alias}.")
        kelola = self.kelola()
        bin_dir = kelola._bin(defn)
        if not kelola._berjalan(defn, spec, k):
            raise self._pg.LayananGagal(
                "service_stopped",
                f"Basis data {k.alias} (localhost:{k.port}) belum menyala. Nyalakan dulu di "
                "panel Basis data praktikum.", alias=k.alias, port=k.port)
        pgpass = kelola._dasar(spec) / "pgpass"
        if not pgpass.is_file():
            raise self._pg.LayananGagal(
                "service_not_ready", "Kredensial lokal belum dibuat. Nyalakan basis data sekali.")
        return {"psql": str(bin_dir / self._pg._exe("psql")), "port": k.port, "alias": k.alias,
                "database": database, "pgpass": str(pgpass),
                "env": kelola._env_proses(bin_dir), "course": spec.course_id}

    @staticmethod
    def _batas_detik(payload: Mapping[str, Any]) -> int:
        t = payload.get("timeoutSeconds")
        if isinstance(t, bool) or not isinstance(t, (int, float)) or t <= 0:
            t = 600
        return int(min(t, 3600))

    def kerja_sql(self, op: KnownOperation, payload: Mapping[str, Any],
                  files: Any) -> tuple[Callable[[Any], HandlerResult], int]:
        """Susun fungsi kerja job ``sql.execute`` / ``sql.run_file`` / ``sql.check``.

        Validasi yang dapat dilakukan tanpa menjalankan apa pun (ukuran, path,
        meta-perintah) terjadi di sini sehingga penolakan langsung terlihat.
        """
        from . import sqlops

        batas = self._batas_detik(payload)
        variabel: dict[str, str] = {}
        if op is KnownOperation.SQL_EXECUTE:
            teks = payload.get("sql")
            if not isinstance(teks, str) or not teks.strip():
                raise OperationRejectedError("SQL kosong.")
            if len(teks.encode("utf-8")) > sqlops.BATAS_SQL:
                raise OperationRejectedError("SQL melebihi 256 KiB; simpan sebagai berkas lalu "
                                             "jalankan berkasnya.")
            mode, sumber = "editor", teks
        elif op is KnownOperation.SQL_RUN_FILE:
            rel = payload.get("path")
            if not isinstance(rel, str) or not rel.lower().endswith(".sql"):
                raise OperationRejectedError("Hanya berkas .sql yang dapat dijalankan.")
            try:
                path = files.resolver.resolve(rel).absolute
            except Exception as exc:  # PathResolver: traversal / symlink keluar akar
                raise OperationRejectedError(f"Path berkas tidak sah: {exc}") from None
            if not path.is_file():
                raise OperationRejectedError(f"Berkas tidak ditemukan: {rel}")
            if path.stat().st_size > sqlops.BATAS_BERKAS:
                raise OperationRejectedError("Berkas SQL melebihi 1 MiB.")
            mode, sumber = "berkas", path.read_text(encoding="utf-8", errors="replace")
            try:
                variabel = sqlops.variabel_sah(payload.get("variables"))
            except sqlops.SqlDitolak as exc:
                raise OperationRejectedError(exc.pesan) from None
        else:
            cek = payload.get("check")
            if not isinstance(cek, Mapping) or not isinstance(cek.get("script"), str):
                raise OperationRejectedError("Spesifikasi pemeriksa tidak disertakan server.")
            mode, sumber = "berkas", cek["script"]
            batas = int(min(max(int(cek.get("timeoutSeconds") or 120), 10), 600))
        try:
            metas = sqlops.periksa_meta(sumber, mode=mode)
        except sqlops.SqlDitolak as exc:
            ditolak = exc

            def tolak(_ctx: Any) -> HandlerResult:
                return HandlerResult(status="failed", payload=ditolak.payload(), detail=ditolak.pesan)

            return tolak, batas
        if op is KnownOperation.SQL_CHECK:
            cek = payload["check"]
            alias, database = cek.get("alias"), cek.get("database")
        else:
            alias, database = payload.get("alias"), payload.get("database")

        def kerja(ctx: Any) -> HandlerResult:
            try:
                s = self._sasaran(payload, alias, database)
            except self._pg.LayananGagal as exc:
                return HandlerResult(status="failed", payload=exc.payload(), detail=exc.pesan)
            env = sqlops.lingkungan(s["env"], pgpass=s["pgpass"], batas_detik=batas)
            tabel = op is KnownOperation.SQL_EXECUTE
            pakai = dict(variabel)
            sandi = None
            if op is KnownOperation.SQL_RUN_FILE:
                # Skrip penyambung FDW memakai :'sandi'. Di terminal mahasiswa mengambilnya
                # sendiri dari PGPASSFILE; di sini agent yang mengisinya (kata sandi peran
                # praktikum miliknya sendiri, sudah ada di laptop ini).
                sandi = sqlops.sandi_pgpass(s["pgpass"])
                if sandi:
                    pakai[sqlops.VARIABEL_SANDI] = sandi
            argv = sqlops.argv_psql(s["psql"], port=s["port"], database=s["database"],
                                    pengguna=self._pg.PERAN_MAHASISWA, tabel=tabel, variabel=pakai)
            batal = lambda: ctx.cancel_requested  # noqa: E731
            if tabel:
                pengurai = sqlops.PenguraiHasil(max_rows=_baris_maks(payload))
                h = sqlops.jalankan(argv, masukan=sqlops.siapkan_editor(sumber, metas).encode("utf-8"),
                                    env=env, batas_waktu=batas + 10, batal=batal,
                                    konsumen=pengurai.umpan)
                return _hasil_editor(sqlops, h, pengurai, s, batas)
            keluaran = bytearray()

            def tampung(b: bytes) -> None:
                if len(keluaran) < sqlops.BATAS_KELUARAN + 1:
                    keluaran.extend(b[: sqlops.BATAS_KELUARAN + 1 - len(keluaran)])

            h = sqlops.jalankan(argv, masukan=sumber.encode("utf-8"), env=env,
                                batas_waktu=batas + 10, batal=batal, konsumen=tampung,
                                gabung_stderr=True)
            teks = sqlops.rapikan_teks(keluaran[: sqlops.BATAS_KELUARAN].decode("utf-8", "replace"))
            if sandi:
                # Keluaran dikirim ke web: kata sandi tidak boleh ikut (mis. \echo :sandi,
                # atau pesan galat yang mengutip pernyataan CREATE USER MAPPING).
                teks = teks.replace(sandi, "[sandi]")
            dasar = {"output": teks, "outputTruncated": len(keluaran) > sqlops.BATAS_KELUARAN,
                     "exitCode": h.exit_code, "seconds": h.seconds, "alias": s["alias"],
                     "database": s["database"]}
            if h.cancelled:
                return HandlerResult(status="cancelled", payload=dasar, detail="Eksekusi dihentikan.")
            if h.timed_out:
                return HandlerResult(status="timeout", payload={**dasar, "code": "sql_timeout",
                                                                "limitSeconds": batas},
                                     detail=f"Eksekusi melewati batas {batas} detik.")
            if op is KnownOperation.SQL_CHECK:
                return HandlerResult(status="ok", payload=_hasil_cek(dasar, h, payload["check"]))
            return HandlerResult(status="ok", payload=dasar)

        return kerja, batas

    def katalog(self, payload: Mapping[str, Any]) -> HandlerResult:
        """``db.catalog``: schema/tabel/kolom/PK/FK dari ``pg_catalog`` (baca)."""
        from . import sqlops

        try:
            s = self._sasaran(payload)
        except self._pg.LayananGagal as exc:
            return HandlerResult(status="failed", payload=exc.payload(), detail=exc.pesan)
        env = sqlops.lingkungan(s["env"], pgpass=s["pgpass"], batas_detik=30)
        argv = [s["psql"], "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-h", "localhost",
                "-p", str(s["port"]), "-U", self._pg.PERAN_MAHASISWA, "-d", s["database"],
                "-f", "-"]
        keluar = bytearray()
        h = sqlops.jalankan(argv, masukan=sqlops.sql_katalog().encode(), env=env,
                            batas_waktu=40, batal=lambda: False, konsumen=keluar.extend)
        if h.exit_code != 0:
            _n, galat, lain = sqlops.urai_stderr(h.stderr)
            pesan = (galat or {}).get("message") or " ".join(lain)[:300] or "katalog gagal dibaca"
            return HandlerResult(status="failed", payload={"code": "catalog_failed"},
                                 detail=f"Katalog basis data tidak terbaca: {pesan}")
        try:
            data = sqlops.urai_katalog(keluar.decode("utf-8", "replace"))
        except ValueError as exc:
            return HandlerResult(status="failed", payload={"code": "catalog_failed"},
                                 detail=f"Katalog basis data tidak terbaca: {exc}")
        return HandlerResult(status="ok", payload={
            "alias": s["alias"], "database": s["database"], "port": s["port"], **data})

    def handler(self, op: KnownOperation) -> OperationHandler:
        def _jalankan(payload: Mapping[str, Any]) -> HandlerResult:
            defn, spec = self._spesifikasi(payload)
            k = self.kelola()
            try:
                if op is KnownOperation.SERVICE_STATUS:
                    hasil = k.status(defn, spec)
                elif op is KnownOperation.SERVICE_START:
                    hasil = k.start(defn, spec, payload.get("alias"))
                else:
                    hasil = k.stop(defn, spec, payload.get("alias"))
            except self._pg.LayananGagal as exc:
                return HandlerResult(status="failed", payload=exc.payload(), detail=exc.pesan)
            return HandlerResult(status="ok", payload=hasil)

        return _jalankan


def _baris_maks(payload: Mapping[str, Any]) -> int:
    n = payload.get("maxRows")
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        return 200
    return min(n, 1000)


def _hasil_editor(sqlops: Any, h: Any, pengurai: Any, s: Mapping[str, Any],
                  batas: int) -> HandlerResult:
    """Jawaban ``sql.execute``: hasil berurutan + NOTICE + galat pertama.

    Galat SQL bukan kegagalan transport: status ``ok`` dengan ``error`` agar
    hasil pernyataan sebelum galat tetap tampil (perilaku psql, autocommit).
    """
    items = pengurai.selesai()
    notices, galat, lain = sqlops.urai_stderr(h.stderr)
    if galat is not None and h.exit_code == 0:
        notices.append({**galat, "severity": "INFO" if galat.get("client") else galat["severity"]})
        galat = None
    if galat is None and h.exit_code not in (0, None) and not (h.cancelled or h.timed_out):
        galat = {"severity": "ERROR", "message": " ".join(lain)[:1000] or
                 f"psql berhenti dengan kode {h.exit_code}.", "line": None, "sqlstate": None}
    if galat is not None and galat.get("sqlstate") == "57014" and "statement timeout" in \
            str(galat.get("message", "")):
        galat = {**galat, "code": "sql_timeout", "limitSeconds": batas}
    payload = {
        "items": items, "itemsOmitted": pengurai.items_dilewati,
        "budgetExhausted": pengurai.anggaran_habis, "notices": notices, "error": galat,
        "exitCode": h.exit_code, "seconds": h.seconds, "alias": s["alias"],
        "database": s["database"], "outputCapped": h.output_capped,
    }
    if h.cancelled:
        return HandlerResult(status="cancelled", payload=payload, detail="Kueri dihentikan.")
    if h.timed_out:
        return HandlerResult(status="timeout", payload={**payload, "code": "sql_timeout",
                                                        "limitSeconds": batas},
                             detail=f"Kueri melewati batas {batas} detik.")
    return HandlerResult(status="ok", payload=payload)


def _hasil_cek(dasar: Mapping[str, Any], h: Any, cek: Mapping[str, Any]) -> dict[str, Any]:
    """Urai keluaran ``check.sql`` (``\\pset border 2``) menjadi butir LULUS/GAGAL."""
    try:
        from workbench_checkpoint.models import CheckpointFinding, CheckpointStatus
        from workbench_checkpoint.parse import (aggregate_status, parse_sql_check_output,
                                                summarise)
    except ImportError:  # pragma: no cover - ZIP agent selalu membawa paket checkpoint
        return {**dasar, "result": {"status": "KESALAHAN", "summary": "Pengurai checkpoint "
                                    "tidak tersedia di agent ini.", "findings": []}}
    temuan = parse_sql_check_output(dasar["output"])
    if temuan:
        status = aggregate_status(temuan)
        ringkas = summarise(status, temuan)
    else:
        status = CheckpointStatus.ERROR
        ringkas = ("psql gagal dan tidak menghasilkan butir pemeriksaan." if h.exit_code
                   else "Keluaran SQL tidak memuat butir status.")
        temuan = (CheckpointFinding(status=status, title="psql",
                                    detail=dasar["output"][-400:] or None),)
    return {**dasar, "artifact": cek.get("artifact"), "result": {
        "status": status.value, "summary": ringkas,
        "passed": status is CheckpointStatus.PASSED, "durationSeconds": h.seconds,
        "findings": [{"status": f.status.value, "title": f.title, "detail": f.detail}
                     for f in temuan],
    }}


def _job_handlers(jobs: JobRunner) -> dict[KnownOperation, OperationHandler]:
    """``job.cancel`` / ``job.status`` (kontrak relay-long-operations §4)."""

    def _job_id(payload: Mapping[str, Any]) -> str:
        jid = payload.get("jobId")
        if not isinstance(jid, str) or not jid or len(jid) > 128:
            raise OperationRejectedError("jobId wajib diisi.")
        return jid

    def _cancel(payload: Mapping[str, Any]) -> HandlerResult:
        jid = _job_id(payload)
        job = jobs.cancel(jid)
        if job is None:
            return HandlerResult(status="failed", payload={"code": "job_unknown", "jobId": jid},
                                 detail="Job tidak dikenal agent (mungkin sudah lama selesai).")
        if job.terminal and not job.cancel_requested:
            return HandlerResult(status="failed",
                                 payload={"code": "job_finished", "jobId": jid, "state": job.state},
                                 detail="Job sudah selesai.")
        return HandlerResult(status="ok", payload={
            "jobId": jid, "state": job.state if job.terminal else "cancelling"})

    def _status(payload: Mapping[str, Any]) -> HandlerResult:
        jid = _job_id(payload)
        since = payload.get("since", 0)
        snap = jobs.snapshot(jid, since=since if isinstance(since, int) and since >= 0 else 0)
        if snap is None:
            return HandlerResult(status="failed", payload={"code": "job_unknown", "jobId": jid},
                                 detail="Job tidak dikenal agent.")
        return HandlerResult(status="ok", payload={"job": snap})

    return {KnownOperation.JOB_CANCEL: _cancel, KnownOperation.JOB_STATUS: _status}


def _git_handlers(roots: "WorkspaceRoots") -> dict[KnownOperation, OperationHandler]:
    """Operasi ``git.*`` di akar mata kuliah (ADR-051; kontrak §3.1).

    Hanya tata letak per-course: repo = satu folder mata kuliah. Path dari
    peramban tetap lewat PathResolver akar itu. Satu kunci untuk semua
    operasi git: index dulwich tidak aman ditulis bersamaan.
    """
    from . import gitlocal

    def _bungkus(kerja: Any) -> OperationHandler:
        def _handler(payload: Mapping[str, Any]) -> HandlerResult:
            if course_root_of(payload) is None:
                return HandlerResult(
                    status="failed", payload={"code": "per_course_required"},
                    detail="Git hanya tersedia untuk mata kuliah bertata letak per-course.")
            try:
                files = roots.for_payload(payload)
            except (InvalidPathError, PathEscapeError) as exc:
                raise OperationRejectedError("Path tidak diizinkan.", detail=str(exc)) from exc
            opsi = gitlocal.OpsiGit.dari_payload(payload)
            if not _KUNCI_GIT.acquire(timeout=_TUNGGU_KUNCI_PENDEK):
                return HandlerResult(status="failed", payload={"code": "git_busy"},
                                     detail="Operasi git lain (push/tarik) masih berjalan. Coba lagi sebentar.")
            try:
                hasil = kerja(files.resolver.root, files.resolver, payload, opsi)
            except gitlocal.GitError as exc:
                return HandlerResult(status="failed", payload=exc.to_payload(), detail=exc.detail)
            finally:
                _KUNCI_GIT.release()
            return HandlerResult(status="ok", payload=hasil)

        return _handler

    return {
        KnownOperation.GIT_STATUS: _bungkus(lambda akar, r, p, o: gitlocal.status(akar)),
        KnownOperation.GIT_INIT: _bungkus(lambda akar, r, p, o: gitlocal.init(akar, o)),
        KnownOperation.GIT_STAGE: _bungkus(
            lambda akar, r, p, o: gitlocal.stage(akar, r, p.get("paths"), o)),
        KnownOperation.GIT_UNSTAGE: _bungkus(
            lambda akar, r, p, o: gitlocal.unstage(akar, r, p.get("paths"))),
        KnownOperation.GIT_COMMIT: _bungkus(
            lambda akar, r, p, o: gitlocal.commit(akar, p.get("message"), o)),
        KnownOperation.GIT_LOG: _bungkus(lambda akar, r, p, o: gitlocal.log(akar, p.get("limit"))),
        KnownOperation.GIT_DIFF_SUMMARY: _bungkus(
            lambda akar, r, p, o: gitlocal.diff_summary(akar, r, p.get("path"))),
    }


class OperationAllowlist:
    """Pemeta operation ID → handler."""

    def __init__(
        self,
        handlers: Mapping[KnownOperation, OperationHandler] | None = None,
        *,
        jupyter: JupyterBackend | None = None,
        workspace: WorkspaceFiles | None = None,
        checkpoint: Any | None = None,
        jobs: JobRunner | None = None,
    ):
        terdaftar: dict[KnownOperation, OperationHandler] = {
            op: _belum_siap(op) for op in KnownOperation
        }
        terdaftar[KnownOperation.AGENT_HEALTH] = _health
        terdaftar[KnownOperation.AGENT_RESOURCES] = _resources
        self._jupyter = jupyter
        self._workspace = workspace
        self._checkpoint = checkpoint
        #: Pekerja operasi *long* (ADR-049). Runner menetapkan ``on_update``.
        self.jobs = jobs or JobRunner()
        terdaftar.update(_job_handlers(self.jobs))
        #: Diisi runner: store Dataset Manager dan pengambil artefak (DL-04).
        self.dataset_store: Any = None
        self.package_fetcher: Any = None
        from .client import download_url

        self.url_fetcher: Any = download_url
        #: Diisi runner: :class:`githubauth.GitHubAuth` di direktori state (GH-02).
        self.github_auth: Any = None
        #: Diganti test: host git palsu (GH-03). Produksi selalu github.com.
        self.git_remote_factory: Any = None
        for op, kerja in ((KnownOperation.GITHUB_AUTH_START, lambda g, p: g.start(p)),
                          (KnownOperation.GITHUB_AUTH_POLL, lambda g, p: g.poll()),
                          (KnownOperation.GITHUB_AUTH_STATUS, lambda g, p: g.status()),
                          (KnownOperation.GITHUB_AUTH_REVOKE, lambda g, p: g.revoke()),
                          (KnownOperation.GITHUB_REPOS,
                           lambda g, p: self._remote().repos(p.get("query"))),
                          (KnownOperation.GITHUB_REPO_CREATE,
                           lambda g, p: self._remote().repo_create(
                               p.get("name"), p.get("private", True), p.get("description")))):
            terdaftar[op] = self._github_handler(op, kerja)
        self._roots = WorkspaceRoots(workspace) if workspace is not None else None
        if workspace is not None:
            terdaftar.update(_workspace_handlers(workspace, roots=self._roots))
            terdaftar[KnownOperation.DATASET_VERIFY] = self._dataset_verify
            if _git_tersedia():
                terdaftar.update(_git_handlers(self._roots))
                terdaftar[KnownOperation.GIT_REMOTE_BIND] = self._git_remote_bind
        if jupyter is not None:
            terdaftar.update(_jupyter_handlers(
                jupyter, self.jobs,
                workspace.resolver.root if workspace is not None else None))
        if self._checkpoint is not None:
            terdaftar.update(_checkpoint_handlers(self._checkpoint, self._roots))
        if handlers:
            terdaftar.update(handlers)
        self._handlers = terdaftar
        #: Diikat runner lewat :meth:`ikat_layanan` (butuh direktori state).
        self.layanan: LayananAgent | None = None
        #: Amplop sesi jalur pipa IDE (ADR-072 §6): ditulis relay, dibaca pipa.
        self.amplop = PenyimpanAmplop()
        #: ``True`` selama agent melayani pipa IDE (``run --stdio``).
        self.pipa_aktif = False
        self._handlers[KnownOperation.SESSION_ENVELOPE] = self._session_envelope

    def _session_envelope(self, payload: Mapping[str, Any]) -> HandlerResult:
        """``session.envelope``: simpan amplop sesi kiriman server (relay saja).

        Pipa tidak pernah sampai ke sini: operasi ini bukan kelas A dan ditolak
        lapisan pipa sebelum ``dispatch`` dipanggil.
        """
        if asal_operasi() != "relay":  # pragma: no cover - sabuk pengaman
            raise OperationRejectedError("Amplop sesi hanya diterima dari server.")
        if not self.pipa_aktif:
            return HandlerResult(
                status="failed", payload={"code": KODE_PIPA_TIDAK_AKTIF},
                detail="Agent ini tidak dijalankan oleh aplikasi DSWorkbench (IDE), "
                       "jadi amplop sesi tidak dipakai.")
        try:
            amplop = self.amplop.simpan(payload)
        except AmplopTidakSah as exc:
            return HandlerResult(status="failed", payload={"code": exc.code}, detail=str(exc))
        return HandlerResult(status="ok", payload=amplop.publik())

    def ikat_layanan(self, *, akar_agent: Any, state_dir: Any, data_dir: Any = None) -> None:
        """Ikat ``service.*`` bila agent membawa definisi layanan untuk platform ini."""
        if self._workspace is None:
            return
        layanan = LayananAgent(akar_agent=akar_agent, state_dir=state_dir, data_dir=data_dir,
                               akar_workspace=self._workspace.resolver.root)
        if not layanan.tersedia():
            return
        self.layanan = layanan
        for op in _SERVICE_OPS:
            self._handlers[op] = layanan.handler(op)
        self._handlers[KnownOperation.DB_CATALOG] = layanan.katalog

    def _github_handler(self, op: KnownOperation, kerja: Any) -> OperationHandler:
        belum = _belum_siap(op)

        def _handler(payload: Mapping[str, Any]) -> HandlerResult:
            if self.github_auth is None:
                return belum(payload)
            from .githubauth import GitHubAuthError
            from .gitlocal import GitError

            try:
                return HandlerResult(status="ok", payload=kerja(self.github_auth, payload))
            except (GitHubAuthError, GitError) as exc:
                return HandlerResult(status="failed", payload=exc.to_payload(), detail=exc.detail)

        return _handler

    def _remote(self) -> Any:
        from .gitlocal import GitError
        from .gitremote import GitHubRemote

        if self.github_auth is None:
            raise GitError("auth_missing", "Akun GitHub belum ditautkan di laptop ini.")
        if self.git_remote_factory is not None:
            return self.git_remote_factory(self.github_auth)
        return GitHubRemote(self.github_auth)

    def _akar_git(self, payload: Mapping[str, Any]) -> Any:
        """Akar mata kuliah per-course untuk operasi git remote."""
        from .gitlocal import GitError

        if course_root_of(payload) is None:
            raise GitError("per_course_required",
                           "Git hanya tersedia untuk mata kuliah bertata letak per-course.")
        assert self._roots is not None
        try:
            return self._roots.for_payload(payload).resolver.root
        except (InvalidPathError, PathEscapeError) as exc:
            raise OperationRejectedError("Path tidak diizinkan.", detail=str(exc)) from exc

    def _git_remote_bind(self, payload: Mapping[str, Any]) -> HandlerResult:
        from .gitlocal import GitError

        try:
            akar = self._akar_git(payload)
            remote = self._remote()
            if not _KUNCI_GIT.acquire(timeout=_TUNGGU_KUNCI_PENDEK):
                raise GitError("git_busy", "Operasi git lain masih berjalan. Coba lagi sebentar.")
            try:
                hasil = remote.remote_bind(akar, payload.get("fullName"))
            finally:
                _KUNCI_GIT.release()
        except GitError as exc:
            return HandlerResult(status="failed", payload=exc.to_payload(), detail=exc.detail)
        return HandlerResult(status="ok", payload=hasil)

    def _git_job_spec(self, op: KnownOperation, data: Mapping[str, Any]) -> JobSpec:
        """``git.push``/``git.pull``/``git.merge``/``git.clone`` sebagai job di lane ``network`` (ADR-049)."""
        from .gitlocal import GitError, OpsiGit
        from .gitremote import Dibatalkan

        if data.get("job") is not True:
            raise OperationRejectedError("Push/tarik GitHub memerlukan Workbench versi baru (mode job).")
        opsi = OpsiGit.dari_payload(data)
        policy = data.get("policy") if isinstance(data.get("policy"), Mapping) else None

        def work(ctx: Any) -> HandlerResult:
            try:
                akar = self._akar_git(data)
                remote = self._remote()
                with _KUNCI_GIT:
                    if op is KnownOperation.GIT_PUSH:
                        hasil = remote.push(akar, opsi, policy, ctx)
                    elif op is KnownOperation.GIT_PULL:
                        hasil = remote.pull(akar, ctx)
                    elif op is KnownOperation.GIT_MERGE:
                        hasil = remote.merge(akar, opsi, ctx)
                    else:
                        hasil = remote.clone(akar, data.get("fullName"), opsi, ctx)
            except Dibatalkan:
                return HandlerResult(status="cancelled", payload={"code": "cancelled"},
                                     detail="Dibatalkan.")
            except GitError as exc:
                return HandlerResult(status="failed", payload=exc.to_payload(), detail=exc.detail)
            return HandlerResult(status="ok", payload=hasil)

        timeout = data.get("timeoutSeconds")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            timeout = DEFAULT_NETWORK_TIMEOUT_SECONDS
        return JobSpec(operation=op.value, lane="network", work=work, job_mode=True,
                       timeout=float(min(timeout, HARD_TIMEOUT_SECONDS)), queue=True)

    def dispatch(self, operation: Any, payload: Mapping[str, Any] | None = None) -> HandlerResult:
        op = KnownOperation.parse(operation)
        if not isinstance(payload, Mapping) and payload is not None:
            raise OperationRejectedError(
                "payload harus berupa objek.",
                detail="tipe tidak sah",
            )
        handler = self._handlers[op]
        return handler(dict(payload or {}))

    def _dataset_verify(self, payload: Mapping[str, Any]) -> HandlerResult:
        from . import datasets_op

        assert self._roots is not None
        return datasets_op.verify(payload, self._roots.for_payload(payload))

    def _dataset_spec(self, op: KnownOperation, data: Mapping[str, Any]) -> JobSpec:
        from pathlib import Path

        from . import datasets_op

        if data.get("job") is not True:
            raise OperationRejectedError(
                "Penyiapan dataset memerlukan Workbench versi baru (mode job).")
        assert self._roots is not None
        files = self._roots.for_payload(data)
        course_id = course_root_of(data) or str(data.get("courseId") or "")
        store = Path(self.dataset_store) if self.dataset_store else (
            Path.home() / ".workbench-agent" / "datasets")
        ref, work = datasets_op.materialize_work(
            data, files, store_dir=store, fetch_url=self.url_fetcher,
            fetch_package=self.package_fetcher, course_id=course_id)
        timeout = data.get("timeoutSeconds")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            timeout = DEFAULT_NETWORK_TIMEOUT_SECONDS
        return JobSpec(operation=op.value, lane="network", work=work, job_mode=True,
                       timeout=float(min(timeout, HARD_TIMEOUT_SECONDS)), queue=True)

    def long_spec(self, operation: Any, payload: Mapping[str, Any] | None) -> JobSpec | None:
        """Susun JobSpec bila operasi ini *long*; ``None`` = operasi biasa.

        Satu-satunya tempat yang menentukan operasi mana yang tidak boleh
        berjalan di loop transport (ADR-049 §1).
        """
        op = KnownOperation.parse(operation)
        data = dict(payload or {})
        if op is KnownOperation.DATASET_MATERIALIZE and self._roots is not None:
            return self._dataset_spec(op, data)
        if (op in (KnownOperation.GIT_PUSH, KnownOperation.GIT_PULL, KnownOperation.GIT_MERGE,
                   KnownOperation.GIT_CLONE)
                and self._roots is not None and _git_tersedia()):
            return self._git_job_spec(op, data)
        if (op is KnownOperation.SERVICE_POSTGRES_LOAD and self.layanan is not None
                and self._roots is not None):
            if data.get("job") is not True:
                raise OperationRejectedError(
                    "Pemuatan data memerlukan Workbench versi baru (mode job).")
            work = self.layanan.kerja_muat(data, self._roots.for_payload(data))
            timeout = data.get("timeoutSeconds")
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
                timeout = DEFAULT_JOB_TIMEOUT_SECONDS
            return JobSpec(operation=op.value, lane=f"service:{course_root_of(data)}", work=work,
                           job_mode=True, timeout=float(min(timeout, HARD_TIMEOUT_SECONDS)))
        if op in _SQL_JOB_OPS and self.layanan is not None and self._roots is not None:
            if data.get("job") is not True:
                raise OperationRejectedError("SQL dari web memerlukan Workbench versi baru (mode job).")
            work, batas = self.layanan.kerja_sql(op, data, self._roots.for_payload(data))
            return JobSpec(operation=op.value, lane=f"sql:{course_root_of(data)}", work=work,
                           job_mode=True, timeout=float(batas + 30))
        if op is KnownOperation.JUPYTER_EXECUTE and self._jupyter is not None:
            backend = self._jupyter
            kid = _kernel_id(data)
            code = data.get("code")
            if not isinstance(code, str):
                raise OperationRejectedError("code wajib berupa teks.")
            job_mode = data.get("job") is True
            timeout = _timeout_payload(data, job_mode=job_mode)
            return JobSpec(
                operation=op.value,
                lane=_lane_kernel(kid),
                work=lambda ctx: _jalankan_sel(backend, kid, code, timeout, ctx.emit),
                job_mode=job_mode,
                timeout=timeout,
                cancel=lambda: backend.interrupt_kernel(kid),
            )
        return None

    def submit_long(self, message_id: str, operation: Any,
                    payload: Mapping[str, Any] | None) -> tuple[JobSpec, HandlerResult | None] | None:
        """Jalankan operasi *long* di pekerja.

        Mengembalikan ``(spec, jawaban)``: ``jawaban`` dikirim segera sebagai
        hasil operasi -- ``{jobId, state}`` untuk mode job, penolakan
        ``kernel_busy``, atau ``None`` untuk jalur sinkron lama (hasilnya dikirim
        saat job selesai). ``None`` = bukan operasi *long*.
        """
        spec = self.long_spec(operation, payload)
        if spec is None:
            return None
        try:
            job = self.jobs.submit(message_id, spec)
        except LaneBusy as exc:
            if exc.lane.startswith("sql:"):
                return spec, HandlerResult(
                    status="failed",
                    payload={"code": "sql_busy", "activeJobId": exc.active_job_id},
                    detail="Kueri lain masih berjalan untuk mata kuliah ini. Tunggu atau hentikan dulu.",
                )
            if exc.lane.startswith("service:"):
                return spec, HandlerResult(
                    status="failed",
                    payload={"code": "service_busy", "activeJobId": exc.active_job_id},
                    detail="Pemuatan data lain masih berjalan. Tunggu atau hentikan dulu.",
                )
            return spec, HandlerResult(
                status="failed",
                payload={"code": "kernel_busy", "activeJobId": exc.active_job_id},
                detail="Sel lain masih berjalan di kernel ini. Tunggu atau hentikan dulu.",
            )
        if not spec.job_mode:
            return spec, None
        return spec, HandlerResult(status="ok", payload={
            "jobId": job.job_id, "state": job.state, "lane": job.lane,
        })

    def implements(self, operation: KnownOperation) -> bool:
        dasar = {
            KnownOperation.AGENT_HEALTH,
            KnownOperation.AGENT_RESOURCES,
            KnownOperation.JOB_CANCEL,
            KnownOperation.JOB_STATUS,
        }
        if self._workspace is not None:
            dasar = dasar | _WORKSPACE_FILE_OPS | {KnownOperation.DATASET_MATERIALIZE,
                                                   KnownOperation.DATASET_VERIFY}
            if _git_tersedia():
                dasar = dasar | _GIT_OPS
        if self._jupyter is not None:
            dasar = dasar | _JUPYTER_OPS
        if self._checkpoint is not None:
            dasar = dasar | _CHECKPOINT_OPS
        if self.github_auth is not None:
            dasar = dasar | _GITHUB_AUTH_OPS
            if self._workspace is not None and _git_tersedia():
                dasar = dasar | _GIT_REMOTE_OPS
        if self.layanan is not None:
            dasar = dasar | _SERVICE_OPS | _SQL_JOB_OPS | {KnownOperation.SERVICE_POSTGRES_LOAD,
                                                           KnownOperation.DB_CATALOG}
        if self.pipa_aktif:
            dasar = dasar | {KnownOperation.SESSION_ENVELOPE}
        return operation in dasar

    def capabilities(self) -> list[str]:
        """Kelompok operasi yang benar-benar terikat pada agent ini.

        Dilaporkan ke Control API setiap connect. Versi agent saja tidak cukup:
        ia menyatakan build mana yang berjalan, bukan apa yang dapat
        dikerjakannya -- paket pendamping bisa saja gagal diimpor pada
        komputer tertentu, dan hasilnya sama persis bagi mahasiswa.
        """
        punya = ["agent", CAPABILITY_RELAY_JOBS]
        if self._jupyter is not None:
            punya.append("jupyter")
        if self._checkpoint is not None:
            punya.append("checkpoint")
            if self._workspace is not None:
                punya.append(CAPABILITY_CHECKPOINT_PACKAGE)
        if self._workspace is not None:
            punya.append("workspace")
            punya.append(CAPABILITY_WORKSPACE_FILES_V2)
            punya.append(CAPABILITY_WORKSPACE_ACCOUNT)
            if _datasets_tersedia():
                punya.append(CAPABILITY_DATASETS)
            if _git_tersedia():
                punya.append(CAPABILITY_GIT)
        if self.github_auth is not None:
            punya.append(CAPABILITY_GITHUB_AUTH)
            if self._workspace is not None and _git_tersedia():
                punya.append(CAPABILITY_GIT_MERGE)
        if self.layanan is not None:
            punya.append(CAPABILITY_SERVICES_POSTGRES)
            punya.append(CAPABILITY_SQL)
            punya.append(CAPABILITY_SQL_VARS)
        if self.pipa_aktif:
            # ADR-072: agent sedang melayani pipa IDE dan menerima amplop sesi.
            punya.append(CAPABILITY_IDE_STDIO)
        return punya

    def register(self, operation: KnownOperation, handler: OperationHandler) -> None:
        self._handlers[operation] = handler
