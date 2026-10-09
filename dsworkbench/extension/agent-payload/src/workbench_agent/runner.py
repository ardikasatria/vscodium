"""Loop utama agent: connect, heartbeat, poll, kerjakan, reconnect."""

from __future__ import annotations

import contextlib
import platform
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from .backoff import Backoff
from .client import ControlPlaneClient, HttpError
from .config import AGENT_VERSION, AgentConfig
from .errors import (
    AgentError,
    AuthenticationFailedError,
    NotPairedError,
    OperationRejectedError,
    TransportError,
)
from .jobs import Job
from .local_ops import AWALAN_JOB_PIPA
from .operations import OperationAllowlist
from .akun import state_akun
from .state import DeviceState, StateStore

SleepFn = Callable[[float], None]
ClockFn = Callable[[], float]


def default_allowlist(*, default_kernel: bool = True) -> OperationAllowlist:
    """Allowlist produksi: ikat capability yang paketnya terpasang.

    ``default_kernel=False``: komputer ini tidak memasang profil bawaan (mis.
    mahasiswa yang hanya mengambil Deep Learning). Agent tetap berjalan -- di
    interpreter profil lain -- tetapi kernel profil bawaan ditolak dengan
    ``profile_not_ready``, bukan dieksekusi diam-diam di lingkungan yang salah.
    """
    kwargs: dict = {}
    try:
        from workbench_checkpoint import RegistryCheckpointRunner
    except ImportError:  # pragma: no cover
        RegistryCheckpointRunner = None  # type: ignore[misc, assignment]
    if RegistryCheckpointRunner is not None:
        kwargs["checkpoint"] = RegistryCheckpointRunner()

    jupyter = _coba_jupyter_backend(default_kernel=default_kernel)
    if jupyter is not None:
        kwargs["jupyter"] = jupyter

    workspace = _coba_workspace()
    if workspace is not None:
        kwargs["workspace"] = workspace

    return OperationAllowlist(**kwargs)


def _coba_workspace():
    """Ikat operasi berkas pada satu akar workspace di komputer mahasiswa.

    Tanpa ini seluruh operasi ``workspace.*`` ada di allowlist tetapi tidak
    pernah terikat, dan mahasiswa menerima "handlernya belum diikat" ketika
    membuka tab Files -- pesan yang tidak memberi tahu apa pun yang dapat ia
    lakukan.

    Akarnya di folder home, bukan di folder agent: agent boleh diunduh ulang
    dan diganti kapan saja, sedangkan pekerjaan mahasiswa tidak boleh ikut
    terhapus bersamanya.
    """
    try:
        from workbench_workspace import PathResolver, WorkspaceFiles
    except ImportError:  # pragma: no cover
        return None

    akar = akar_workspace()
    if akar is None:
        return None
    return WorkspaceFiles(PathResolver(akar))


#: Akun Workbench pemilik pairing yang sedang berjalan (lihat ``tetapkan_akun``).
_AKUN_AKTIF: str | None = None


def tetapkan_akun(akun: str | None) -> None:
    """Tetapkan akun aktif sebelum allowlist dibangun.

    Folder kerja, kernel, token GitHub, dan klaster basis data mengikuti akun
    ini (``akun.py``). ``None`` = agent belum tahu akunnya: perilaku lama.
    """
    global _AKUN_AKTIF
    _AKUN_AKTIF = akun or None


def akar_workspace(akun: str | None = None):
    """Akar workspace mahasiswa di komputer ini, dibuat bila belum ada.

    Satu sumber untuk operasi berkas workspace dan direktori kerja kernel:
    berkas yang ditulis sel (``metrics.csv``) harus muncul di tab Workspace.
    Akun pertama di komputer memakai folder dasar; akun lain mendapat folder
    sendiri di ``<dasar>-akun/<akun>/``.
    """
    import os
    from pathlib import Path

    from .akun import akar_workspace_akun

    dasar = Path(
        (os.environ.get("AGENT_WORKSPACE_ROOT") or "").strip()
        or (Path.home() / "workbench-workspace")
    ).expanduser()
    akar = akar_workspace_akun(dasar, akun or _AKUN_AKTIF)
    try:
        akar.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return akar


class _TanpaProfilBawaan:
    """Pengganti backend profil bawaan ketika profil itu tidak dipasang.

    Router meneruskan kernel profil lain ke proses anaknya masing-masing;
    hanya permintaan kernel profil bawaan yang sampai ke sini, dan jawabannya
    selalu "belum siap" -- dengan kode yang sudah dikenal halaman notebook.
    """

    #: Dibaca router: kernel profil bawaan ditolak juga untuk pipa IDE.
    tanpa_profil_bawaan = True

    def ping(self) -> bool:
        return True

    def start_kernel(self, *, name: str | None = None, **_kwargs):
        from .env_setup import PROFIL_BAWAAN, ProfilBelumSiap

        raise ProfilBelumSiap(
            PROFIL_BAWAAN,
            "Lingkungan praktikum bawaan (Data Science) tidak dipasang di komputer "
            "ini. Pilih praktikumnya di aplikasi DSWorkbench, lalu pasang lingkungannya.",
        )

    def _tidak_ada(self, kernel_id: str, *args, **kwargs):
        from workbench_jupyter import KernelNotFoundError

        raise KernelNotFoundError(kernel_id)

    stop_kernel = interrupt_kernel = restart_kernel = kernel_status = _tidak_ada

    def execute(self, kernel_id: str, code: str, *, timeout: float = 60.0):
        return self._tidak_ada(kernel_id)


def _coba_jupyter_backend(*, default_kernel: bool = True):
    """Utamakan Jupyter Server loopback; jatuh ke LocalPythonBackend.

    LocalPythonBackend menjalankan ``exec`` di proses agent — kernel milik
    mahasiswa di laptopnya, bukan di VM. Cukup untuk Run cell di browser lewat
    relay sebelum runtime Docker Jupyter disiapkan.

    Backend itu melayani profil bawaan. Router di atasnya menjalankan profil
    lain (mis. Deep Learning) sebagai proses anak dengan interpreter profilnya
    (ADR-041 §4). Keduanya memakai akar workspace sebagai direktori kerja.
    """
    try:
        from workbench_jupyter import (
            ConnectionInfo,
            HttpJupyterBackend,
            LocalPythonBackend,
        )
    except ImportError:  # pragma: no cover
        return None

    import os

    from .kernels import ProfileKernelRouter

    workspace = akar_workspace()
    url = (os.environ.get("WORKBENCH_JUPYTER_URL") or "http://127.0.0.1:8888").strip()
    token = (os.environ.get("WORKBENCH_JUPYTER_TOKEN") or "").strip()
    bawaan = None if default_kernel else _TanpaProfilBawaan()
    if token and bawaan is None:
        try:
            backend = HttpJupyterBackend(ConnectionInfo(base_url=url, token=token))
            if backend.ping():
                bawaan = backend
        except ValueError:
            pass
    if bawaan is None:
        bawaan = LocalPythonBackend(cwd=workspace)
    return ProfileKernelRouter(bawaan, workspace_root=workspace)


class Agent:
    """Local Runner yang terhubung ke satu Control API."""

    #: Penghitung operasi pendek yang sedang dikerjakan. Sejak ADR-072 penulisnya
    #: dua: utas transport relay dan utas pipa IDE. Kunci dan nilai awalnya di
    #: kelas agar tetap ada pada instance yang dirakit tanpa ``__init__`` (uji).
    _kunci_sibuk = threading.Lock()
    _n_sibuk = 0
    #: Penerima perubahan job milik pipa IDE (``PipaIde``); ``None`` = tanpa pipa.
    _penerima_pipa: Callable[[Job, str], None] | None = None
    #: Dipanggil saat keadaan sambungan server berubah: ``connected``/``disconnected``.
    _on_relay: Callable[[str], None] | None = None
    _keadaan_relay: str | None = None

    @property
    def _busy(self) -> bool:
        with self._kunci_sibuk:
            return self._n_sibuk > 0

    @_busy.setter
    def _busy(self, nilai: bool) -> None:
        with self._kunci_sibuk:
            self._n_sibuk = 1 if nilai else 0

    @contextlib.contextmanager
    def sedang_sibuk(self) -> Iterator[None]:
        """Tandai satu operasi pendek sedang dikerjakan (aman lintas utas)."""
        with self._kunci_sibuk:
            self._n_sibuk += 1
        try:
            yield
        finally:
            with self._kunci_sibuk:
                self._n_sibuk = max(0, self._n_sibuk - 1)

    def __init__(
        self,
        config: AgentConfig,
        state: DeviceState,
        *,
        client: ControlPlaneClient | None = None,
        allowlist: OperationAllowlist | None = None,
        sleep: SleepFn = time.sleep,
        monotonic: ClockFn = time.monotonic,
        announce: Callable[[str], None] | None = None,
        on_relay: Callable[[str], None] | None = None,
    ):
        self.config = config
        self._on_relay = on_relay
        #: Laporan keadaan koneksi untuk manusia (CLI mencetaknya). Tanpa ini
        #: agent diam, seperti sebelumnya.
        self._announce = announce or (lambda _pesan: None)
        self.state = state
        self.client = client or ControlPlaneClient(
            config.control_plane_url, timeout=config.request_timeout_seconds
        )
        # Sebelum allowlist bawaan dibangun: akar workspace mengikuti akun pairing.
        if allowlist is None:
            tetapkan_akun(state.account)
        self.allowlist = allowlist or default_allowlist()
        self._sleep = sleep
        self._monotonic = monotonic
        self._stop = False
        #: Poll terakhir benar-benar ditahan server (long-poll); lewati jeda.
        self._long_poll_dilayani = False
        self._connected = False
        self._heartbeat_every = config.heartbeat_interval_seconds
        self._last_heartbeat = 0.0
        #: Kiriman dari pekerja job ke loop transport (ADR-049 §1): hanya utas
        #: transport yang melakukan HTTP. Isi: ``("job", id)`` = dorong keadaan
        #: job; ``("result", id)`` = hasil akhir jalur sinkron lama.
        self._outbox: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.allowlist.jobs.on_update = self._job_berubah
        # DL-04: store dataset di direktori state agent; artefak package diambil
        # dari Control API dengan kredensial perangkat ini.
        self.allowlist.dataset_store = config.resolved_state_dir() / "datasets"
        self.allowlist.package_fetcher = self._ambil_berkas_package
        # GH-02: token GitHub di direktori state yang sama (github.json 0600,
        # DPAPI di Windows dengan entropy id perangkat).
        if getattr(self.allowlist, "github_auth", None) is None:
            from .githubauth import GitHubAuth

            # Token GitHub per akun Workbench: akun lain di komputer yang sama
            # tidak boleh memakai token akun sebelumnya.
            self.allowlist.github_auth = GitHubAuth(
                state_akun(config.resolved_state_dir(), state.account), state.device_id)
        # ADR-053: layanan dikelola agent (PostgreSQL); klaster di direktori state.
        try:
            from .env_setup import akar_agent_dari_modul

            self.allowlist.ikat_layanan(
                akar_agent=akar_agent_dari_modul(), state_dir=config.resolved_state_dir(),
                # Biner layanan dipakai bersama; klaster basis data per akun.
                data_dir=state_akun(config.resolved_state_dir(), state.account))
        except (OSError, ValueError, KeyError) as exc:  # pragma: no cover - services.json rusak
            # Agent tetap berjalan; operasi service.* dijawab "belum siap".
            self.layanan_galat: str | None = f"services.json tidak dapat dibaca: {exc}"
        self.backoff = Backoff(
            initial=config.backoff_initial_seconds,
            maximum=config.backoff_max_seconds,
            multiplier=config.backoff_multiplier,
        )

    def stop(self) -> None:
        self._stop = True

    def run_forever(self) -> None:
        """Hubungkan, kerjakan operasi, dan sambung ulang bila putus."""
        while not self._stop:
            try:
                self._session()
            except AuthenticationFailedError:
                # Kredensial dicabut atau dirotasi: backoff tidak membantu.
                raise
            except (TransportError, HttpError, AgentError) as exc:
                self._connected = False
                self._lapor_relay("disconnected")
                if self._stop:
                    return
                jeda = self.backoff.next()
                self._announce(
                    f"koneksi ke {self.config.control_plane_url} terputus: {exc} "
                    f"-- mencoba lagi dalam {jeda:.0f} detik")
                self._sleep(jeda)

    def run_once(self) -> int:
        """Satu putaran: pastikan terhubung, satu poll, kerjakan.

        Dipakai test dan ``workbench-agent once``. Mengembalikan jumlah operasi
        yang dikerjakan.
        """
        if not self._connected:
            self._connect()
        jumlah = self._poll_and_handle()
        # "Satu putaran" mencakup job yang dimulainya: tunggu selesai lalu kirim,
        # agar `workbench-agent once` tidak keluar sebelum hasilnya terkirim.
        while self.allowlist.jobs.active() and not self._stop:
            self.flush_outbox()
            time.sleep(0.05)
        self.flush_outbox()
        return jumlah

    # ------------------------------------------------------------------

    def _session(self) -> None:
        self._connect()
        self.backoff.reset()
        while not self._stop:
            sekarang = self._monotonic()
            if sekarang - self._last_heartbeat >= self._heartbeat_every:
                self._heartbeat()
            # Long-poll hanya saat menganggur: outbox baru dikirim sesudah poll
            # kembali, jadi job yang berjalan tetap memakai poll singkat.
            tunggu = 0.0
            if not self._sibuk() and self._outbox.empty():
                tunggu = self.config.long_poll_seconds
            dikerjakan = self._poll_and_handle(wait=tunggu)
            self.flush_outbox()
            if dikerjakan == 0 and not self._long_poll_dilayani:
                self._sleep(self.config.poll_interval_seconds)

    def _lapor_relay(self, keadaan: str) -> None:
        """Beri tahu pendengar (pipa IDE) hanya bila keadaan sambungan berubah."""
        if keadaan == self._keadaan_relay:
            return
        self._keadaan_relay = keadaan
        if self._on_relay is not None:
            try:
                self._on_relay(keadaan)
            except Exception:  # noqa: BLE001 - pendengar rusak tidak memutus relay
                pass

    def _job_aktif(self) -> list[dict[str, Any]]:
        # Job milik pipa IDE tidak dikenal server dan tidak dilaporkan kepadanya.
        return [{"jobId": j.job_id, "state": j.state}
                for j in self.allowlist.jobs.active()
                if j.job_mode and not j.job_id.startswith(AWALAN_JOB_PIPA)][:16]

    def _catat_akun(self, akun: Any) -> None:
        """Simpan akun pemilik pairing dari Control API bila agent belum tahu.

        Pairing lama (sebelum 0.4.9) tidak menyimpan akunnya. Akun yang baru
        diketahui di sini menjadi pemilik folder yang sedang dipakai; folder
        tidak berpindah di tengah sesi.
        """
        nama = akun.get("username") if isinstance(akun, dict) else None
        if not isinstance(nama, str) or not nama or nama == self.state.account:
            return
        from .akun import akar_workspace_akun, state_akun as _state_akun

        self.state.account = nama
        try:
            StateStore(self.config.resolved_state_dir()).save(self.state)
        except OSError:
            return
        import os
        from pathlib import Path

        dasar = Path((os.environ.get("AGENT_WORKSPACE_ROOT") or "").strip()
                     or (Path.home() / "workbench-workspace")).expanduser()
        akar_workspace_akun(dasar, nama)          # klaim bila belum ada pemilik
        _state_akun(self.config.resolved_state_dir(), nama)

    def _sibuk(self) -> bool:
        return self._busy or self.allowlist.jobs.any_running()

    def _connect(self) -> None:
        jawaban = self.client.connect(
            device_id=self.state.device_id,
            credential=self.state.credential,
            agent_version=AGENT_VERSION,
            capabilities=self.allowlist.capabilities(),
            active_jobs=self._job_aktif(),
        )
        # Control API mungkin baru dimulai ulang dan tidak mengenal job yang
        # masih berjalan: dorong ulang semuanya (ADR-049 §2).
        for item in self._job_aktif():
            self._outbox.put(("job", item["jobId"]))
        self._catat_akun(jawaban.get("account"))
        interval = jawaban.get("heartbeatIntervalSeconds")
        if isinstance(interval, (int, float)) and interval > 0:
            self._heartbeat_every = float(interval)
        self._last_heartbeat = self._monotonic()
        self._connected = True
        self._announce(f"agent {AGENT_VERSION} terhubung ke {self.config.control_plane_url}")
        self._lapor_relay("connected")

    def _heartbeat(self) -> None:
        self.client.heartbeat(
            device_id=self.state.device_id,
            credential=self.state.credential,
            busy=self._sibuk(),
            active_jobs=self._job_aktif(),
        )
        self._last_heartbeat = self._monotonic()

    def _ambil_berkas_package(self, course_id: str, path: str, dest, progress) -> None:
        self.client.download_package_file(
            device_id=self.state.device_id, credential=self.state.credential,
            course_id=course_id, path=path, dest=dest, progress=progress)

    def _job_berubah(self, job: Job, kind: str) -> None:
        """Dipanggil dari utas pekerja/pengawas: antrekan, jangan kirim di sini.

        Rute per asal (ADR-072 §6): job yang dimulai dari pipa IDE dikirim ke
        pipa dan tidak pernah masuk outbox server.
        """
        if job.job_id.startswith(AWALAN_JOB_PIPA):
            penerima = self._penerima_pipa
            if penerima is not None:
                penerima(job, kind)
            return
        if job.job_mode:
            self._outbox.put(("job", job.job_id))
        elif kind == "state" and job.terminal:
            self._outbox.put(("result", job.job_id))

    def flush_outbox(self) -> int:
        """Kirim keadaan job dan hasil sinkron lama yang tertunda (utas transport)."""
        tertunda: list[tuple[str, str]] = []
        while True:
            try:
                item = self._outbox.get_nowait()
            except queue.Empty:
                break
            if item not in tertunda:
                tertunda.append(item)
        terkirim = 0
        for i, (jenis, job_id) in enumerate(tertunda):
            try:
                if jenis == "job":
                    self._dorong_job(job_id)
                else:
                    self._kirim_hasil_sinkron(job_id)
                terkirim += 1
            except (TransportError, HttpError) as exc:
                if isinstance(exc, HttpError) and 400 <= exc.status < 500 and exc.status != 401:
                    continue  # ditolak server (mis. job tak dikenal) -- jangan ulangi
                for sisa in tertunda[i:]:
                    self._outbox.put(sisa)
                raise
        return terkirim

    def _dorong_job(self, job_id: str) -> None:
        jobs = self.allowlist.jobs
        job = jobs.get(job_id)
        if job is None:
            return
        snap = jobs.snapshot(job_id, since=job._pushed_output)
        if snap is None:
            return
        self.client.job_update(
            device_id=self.state.device_id,
            credential=self.state.credential,
            job_id=job_id,
            update={
                "operation": snap["operation"],
                "state": snap["state"],
                "elapsedMs": snap["elapsedMs"],
                "deadlineAt": snap["deadlineAt"],
                "output": snap["output"],
                "outputOffset": snap["outputOffset"],
                "outputTruncated": snap["outputTruncated"],
                "result": snap["result"] if job.terminal else None,
                "reason": snap["reason"],
                "detail": snap["detail"],
            },
        )
        jobs.mark_pushed(job_id, snap["nextSince"])

    def _kirim_hasil_sinkron(self, job_id: str) -> None:
        job = self.allowlist.jobs.get(job_id)
        if job is None or not job.terminal:
            return
        status = {"succeeded": "ok", "timed_out": "timeout"}.get(job.state, "failed")
        self.client.result(
            device_id=self.state.device_id,
            credential=self.state.credential,
            message_id=job_id,
            status=status,
            payload=dict(job.result or {}),
            detail=job.detail,
        )

    def _poll_and_handle(self, *, wait: float = 0.0) -> int:
        jawaban = self.client.poll(
            device_id=self.state.device_id,
            credential=self.state.credential,
            busy=self._sibuk(),
            wait=wait,
        )
        # Server lama tidak mengenal long-poll dan menjawab seketika: tanpa
        # tanda ini agent akan poll tanpa jeda.
        dilayani = jawaban.get("longPollSeconds")
        self._long_poll_dilayani = (
            wait > 0 and isinstance(dilayani, (int, float)) and dilayani > 0
        )
        self._last_heartbeat = self._monotonic()
        operasi = jawaban.get("operations") or []
        if not isinstance(operasi, list):
            raise TransportError("jawaban poll tidak memuat daftar operations")

        for item in operasi:
            if self._stop:
                break
            if not isinstance(item, dict):
                continue
            self._handle_one(item)
        return len(operasi)

    def _gagal(self, message_id: str, operation: Any, detail: str) -> None:
        self.client.result(
            device_id=self.state.device_id,
            credential=self.state.credential,
            message_id=message_id,
            status="failed",
            payload={"operation": operation},
            detail=detail,
        )

    def _handle_one(self, envelope: dict) -> None:
        message_id = envelope.get("messageId")
        operation = envelope.get("operation")
        payload = envelope.get("payload") or {}
        if not isinstance(message_id, str):
            return

        with self.sedang_sibuk():
            try:
                # Operasi long: pekerja job; loop transport tidak menunggu.
                panjang = self.allowlist.submit_long(message_id, operation, payload)
                hasil = None if panjang is not None else self.allowlist.dispatch(operation, payload)
            except OperationRejectedError as exc:
                self._gagal(message_id, operation, str(exc))
                return
            except Exception as exc:  # noqa: BLE001 - galat handler tidak boleh mematikan agent
                # Satu operasi yang rusak tidak boleh memutus mahasiswa dari
                # seluruh Workbench; laporkan sebagai kegagalan operasi itu saja.
                self._announce(f"galat internal pada {operation}: {type(exc).__name__}: {exc}")
                self._gagal(message_id, operation,
                            f"Galat internal Local Runner saat menjalankan {operation}. "
                            "Agent tetap berjalan; laporkan ke asisten bila berulang.")
                return

            if panjang is not None:
                _spec, jawaban_awal = panjang
                if jawaban_awal is not None:
                    self.client.result(
                        device_id=self.state.device_id,
                        credential=self.state.credential,
                        message_id=message_id,
                        status=jawaban_awal.status,
                        payload=dict(jawaban_awal.payload),
                        detail=jawaban_awal.detail,
                    )
                return

            self.client.result(
                device_id=self.state.device_id,
                credential=self.state.credential,
                message_id=message_id,
                status=hasil.status,
                payload=dict(hasil.payload),
                detail=hasil.detail,
            )


def pair_device(
    config: AgentConfig,
    code: str,
    *,
    name: str | None = None,
    client: ControlPlaneClient | None = None,
    store: StateStore | None = None,
) -> DeviceState:
    """Tukar kode pairing menjadi kredensial, lalu simpan lokal."""
    klien = client or ControlPlaneClient(
        config.control_plane_url, timeout=config.request_timeout_seconds
    )
    info = {
        "name": name or config.default_device_name,
        "os": platform.system() or "tidak diketahui",
        "arch": platform.machine() or "tidak diketahui",
        "agentVersion": AGENT_VERSION,
    }
    jawaban = klien.pair(code=code, info=info)
    device = jawaban.get("device") or {}
    credential = jawaban.get("credential")
    device_id = device.get("id") if isinstance(device, dict) else None
    if not isinstance(device_id, str) or not isinstance(credential, str):
        raise TransportError("jawaban pairing tidak memuat device.id atau credential")

    akun = jawaban.get("account")
    nama_akun = akun.get("username") if isinstance(akun, dict) else None
    state = DeviceState(
        device_id=device_id,
        credential=credential,
        control_plane_url=config.control_plane_url,
        name=str(device.get("name") or info["name"]),
        os=info["os"],
        arch=info["arch"],
        agent_version=AGENT_VERSION,
        paired_at=datetime.now(timezone.utc).isoformat(),
        account=nama_akun if isinstance(nama_akun, str) and nama_akun else None,
    )
    penyimpan = store or StateStore(config.resolved_state_dir())
    penyimpan.save(state)
    return state


def load_state(config: AgentConfig, store: StateStore | None = None) -> DeviceState:
    penyimpan = store or StateStore(config.resolved_state_dir())
    if not penyimpan.exists():
        raise NotPairedError()
    return penyimpan.load()
