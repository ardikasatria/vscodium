"""Pekerja operasi berdurasi panjang (ADR-049).

Loop transport agent (connect, heartbeat, poll) tidak pernah menunggu sel
notebook, push git, atau unduhan dataset. Operasi *long* dijalankan di sini,
di utas pekerja per *lane*:

- ``kernel:<id>`` -- satu eksekusi aktif per kernel; yang kedua ditolak
  (``kernel_busy``), tidak diantrekan (ADR-049 §4);
- ``network`` -- satu aktif, antre maks :data:`NETWORK_QUEUE_MAX`.

Modul ini tidak melakukan HTTP. Setiap perubahan dilaporkan lewat
``on_update``; runner mengantrekannya dan loop transport yang mengirim, agar
klien HTTP tetap satu utas dan server yang lambat tidak menahan pekerja.

State job: ``queued → running → succeeded | failed | cancelled | timed_out``
(kontrak ``docs/relay-long-operations-contract.md`` §2).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

#: State terminal: tidak ada transisi keluar.
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timed_out"})

#: Job terminal disimpan sekian detik untuk ``job.status``/dorongan ulang.
RETENTION_SECONDS = 15 * 60

#: Tenggang setelah pembatalan sebelum job dinyatakan ``cancel_failed``.
#: Lebih panjang dari tenggang kernel proses anak (5 s) agar restart paksa di
#: sana sempat melapor lebih dulu.
CANCEL_GRACE_SECONDS = 8.0

#: Antrean lane ``network``.
NETWORK_QUEUE_MAX = 4

#: Batas keluaran bertahap per job (karakter), sama dengan batas keluaran sel.
MAX_OUTPUT_CHARS = 100_000

#: Selama ``running``: dorong keluaran baru paling cepat tiap sekian detik, dan
#: tanda hidup paling lambat tiap :data:`PROGRESS_KEEPALIVE_SECONDS`.
PROGRESS_MIN_SECONDS = 2.0
PROGRESS_KEEPALIVE_SECONDS = 30.0


class LaneBusy(Exception):
    """Lane sibuk dan tidak mengantre (kernel) atau antreannya penuh."""

    def __init__(self, lane: str, active_job_id: str | None):
        self.lane = lane
        self.active_job_id = active_job_id
        super().__init__(f"lane {lane} sibuk")


@dataclass
class JobContext:
    """Yang diterima fungsi kerja: tenggat dan penyalur keluaran bertahap."""

    job: "Job"
    timeout: float | None
    _emit: Callable[[str, str], None]

    def emit(self, name: str, text: str) -> None:
        self._emit(name, text)

    @property
    def cancel_requested(self) -> bool:
        return self.job.cancel_requested


@dataclass
class JobSpec:
    """Cara menjalankan satu operasi *long* (disusun allowlist)."""

    operation: str
    lane: str
    work: Callable[[JobContext], Any]
    #: ``True``: jawab segera dengan ``jobId``; ``False``: jalur sinkron lama --
    #: hasil akhirnya dikirim sebagai hasil operasi biasa.
    job_mode: bool
    timeout: float | None
    #: Dipanggil (dari utas transport) saat pembatalan diminta.
    cancel: Callable[[], None] | None = None
    queue: bool = False


@dataclass
class Job:
    job_id: str
    operation: str
    lane: str
    job_mode: bool
    state: str = "queued"
    created_at: str = ""
    started_at: str | None = None
    updated_at: str = ""
    finished_at: str | None = None
    deadline_at: str | None = None
    reason: str | None = None
    detail: str | None = None
    result: Any = None
    cancel_requested: bool = False
    output: list[tuple[int, str, str]] = field(default_factory=list)
    output_chars: int = 0
    output_truncated: bool = False
    #: internal
    _t_start: float | None = None
    _t_cancel: float | None = None
    _t_end: float | None = None
    _t_deadline: float | None = None
    _pushed_output: int = 0
    _t_pushed: float = 0.0
    _thread_alive: bool = False

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def elapsed_ms(self, now: float) -> int:
        if self._t_start is None:
            return 0
        akhir = self._t_end if self._t_end is not None else now
        return int((akhir - self._t_start) * 1000)

    def snapshot(self, now: float, *, since: int = 0) -> dict[str, Any]:
        """Bentuk publik (kontrak §5), keluaran sejak ``since``."""
        keluar = []
        for offset, name, text in self.output:
            if offset + len(text) <= since:
                continue
            potong = text[max(0, since - offset):]
            keluar.append({"type": "stream", "name": name, "text": potong})
        return {
            "jobId": self.job_id,
            "operation": self.operation,
            "state": self.state,
            "lane": self.lane,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "updatedAt": self.updated_at,
            "finishedAt": self.finished_at,
            "deadlineAt": self.deadline_at,
            "elapsedMs": self.elapsed_ms(now),
            "output": keluar,
            "outputOffset": since,
            "nextSince": self.output_chars,
            "outputTruncated": self.output_truncated,
            "result": self.result,
            "reason": self.reason,
            "detail": self.detail,
        }


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


class JobRunner:
    """Pemilik seluruh job agent. Aman dipanggil dari beberapa utas."""

    def __init__(
        self,
        *,
        on_update: Callable[[Job, str], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wallclock: Callable[[], float] = time.time,
        start_monitor: bool = True,
    ):
        #: ``on_update(job, kind)`` -- ``kind``: ``state`` (transisi) atau
        #: ``progress`` (keluaran baru / tanda hidup). Ditetapkan runner.
        self.on_update = on_update
        self._mono = monotonic
        self._wall = wallclock
        self._jobs: dict[str, Job] = {}
        self._specs: dict[str, JobSpec] = {}
        self._lane_active: dict[str, str] = {}
        self._lane_queue: dict[str, deque[str]] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        #: Pengawas dimulai saat job pertama, bukan saat dibuat: allowlist yang
        #: tidak pernah menjalankan job tidak menyimpan utas menganggur.
        self._monitor_wanted = start_monitor

    # ------------------------------------------------------------------
    # API

    def submit(self, job_id: str, spec: JobSpec) -> Job:
        with self._lock:
            if self._monitor_wanted and self._monitor is None:
                self._monitor = threading.Thread(target=self._monitor_loop,
                                                 name="job-monitor", daemon=True)
                self._monitor.start()
            if job_id in self._jobs:
                return self._jobs[job_id]
            aktif = self._lane_active.get(spec.lane)
            antre = self._lane_queue.setdefault(spec.lane, deque())
            if aktif is not None:
                if not spec.queue or len(antre) >= NETWORK_QUEUE_MAX:
                    raise LaneBusy(spec.lane, aktif)
            now_w = self._wall()
            job = Job(job_id=job_id, operation=spec.operation, lane=spec.lane,
                      job_mode=spec.job_mode, created_at=_iso(now_w), updated_at=_iso(now_w))
            self._jobs[job_id] = job
            self._specs[job_id] = spec
            if aktif is None:
                self._start(job)
            else:
                antre.append(job_id)
                self._notify(job, "state")
            return job

    def cancel(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.terminal:
                return job
            if job.state == "queued":
                self._lane_queue.get(job.lane, deque()).remove(job_id)
                job.cancel_requested = True
                self._finish(job, "cancelled", reason="cancelled_by_user")
                return job
            if job.cancel_requested:
                return job
            job.cancel_requested = True
            job._t_cancel = self._mono()
            spec = self._specs.get(job_id)
        if spec is not None and spec.cancel is not None:
            try:
                spec.cancel()
            except Exception:  # noqa: BLE001 - pengawas akan menyatakan cancel_failed
                pass
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def lane_active(self, lane: str) -> Job | None:
        with self._lock:
            jid = self._lane_active.get(lane)
            return self._jobs.get(jid) if jid else None

    def active(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if not j.terminal]

    def any_running(self) -> bool:
        with self._lock:
            return any(j.state == "running" for j in self._jobs.values())

    def snapshot(self, job_id: str, *, since: int = 0) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot(self._mono(), since=since) if job else None

    def mark_pushed(self, job_id: str, output_chars: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job._pushed_output = max(job._pushed_output, output_chars)
                job._t_pushed = self._mono()

    def close(self) -> None:
        self._stop.set()

    def tick(self) -> None:
        """Satu putaran pengawas (dipanggil utas monitor; test memanggil langsung)."""
        now = self._mono()
        progress: list[Job] = []
        with self._lock:
            for job in list(self._jobs.values()):
                if job.terminal:
                    if job._t_end is not None and now - job._t_end > RETENTION_SECONDS \
                            and not job._thread_alive:
                        self._jobs.pop(job.job_id, None)
                        self._specs.pop(job.job_id, None)
                    continue
                if job.state != "running":
                    continue
                if job._t_deadline is not None and now > job._t_deadline + CANCEL_GRACE_SECONDS \
                        and not job.cancel_requested:
                    # Fungsi kerja tidak menegakkan tenggatnya sendiri.
                    job.reason = "timeout"
                    threading.Thread(target=self.cancel, args=(job.job_id,), daemon=True).start()
                if job.cancel_requested and job._t_cancel is not None \
                        and now > job._t_cancel + CANCEL_GRACE_SECONDS:
                    # Sel in-process di dalam panggilan C yang panjang: utasnya
                    # masih berjalan, lane tetap sibuk sampai ia kembali.
                    self._finish(job, "timed_out" if job.reason == "timeout" else "failed",
                                 reason="timeout" if job.reason == "timeout" else "cancel_failed",
                                 detail=("Sel tidak dapat dihentikan (sedang di dalam pustaka "
                                         "C). Tekan Restart bila tidak segera berhenti."),
                                 release_lane=False)
                    continue
                if job.output_chars > job._pushed_output and now - job._t_pushed >= PROGRESS_MIN_SECONDS:
                    progress.append(job)
                elif now - job._t_pushed >= PROGRESS_KEEPALIVE_SECONDS:
                    progress.append(job)
        for job in progress:
            with self._lock:
                job._t_pushed = now
            self._notify(job, "progress")

    # ------------------------------------------------------------------
    # internal

    def _monitor_loop(self) -> None:
        while not self._stop.wait(0.5):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - pengawas tidak boleh mati
                pass

    def _start(self, job: Job) -> None:
        spec = self._specs[job.job_id]
        now = self._mono()
        job.state = "running"
        job._t_start = now
        job._t_pushed = now
        job.started_at = _iso(self._wall())
        job.updated_at = job.started_at
        if spec.timeout is not None and spec.timeout > 0:
            job._t_deadline = now + spec.timeout
            job.deadline_at = _iso(self._wall() + spec.timeout)
        self._lane_active[job.lane] = job.job_id
        job._thread_alive = True
        threading.Thread(target=self._run, args=(job, spec), name=f"job-{job.lane}",
                         daemon=True).start()
        self._notify(job, "state")

    def _emit(self, job: Job, name: str, text: str) -> None:
        with self._lock:
            if job.output_truncated or not text:
                return
            sisa = MAX_OUTPUT_CHARS - job.output_chars
            if len(text) > sisa:
                text = text[:max(0, sisa)]
                job.output_truncated = True
            if text:
                job.output.append((job.output_chars, name, text))
                job.output_chars += len(text)

    def _run(self, job: Job, spec: JobSpec) -> None:
        ctx = JobContext(job=job, timeout=spec.timeout,
                         _emit=lambda n, t: self._emit(job, n, t))
        state, reason, detail, result = "failed", "error", None, None
        try:
            hasil = spec.work(ctx)
            state, reason, detail, result = _classify(job, hasil)
        except KeyboardInterrupt:
            state = "cancelled" if job.cancel_requested else "failed"
            reason = "cancelled_by_user" if job.cancel_requested else "error"
        except Exception as exc:  # noqa: BLE001
            detail = str(exc) or type(exc).__name__
        with self._lock:
            job._thread_alive = False
            if job.terminal:
                # Sudah dinyatakan cancel_failed oleh pengawas; lepaskan lane.
                job.result = job.result if job.result is not None else result
                self._release_lane(job)
                return
            self._finish(job, state, reason=reason, detail=detail, result=result)

    def _finish(self, job: Job, state: str, *, reason: str | None = None,
                detail: str | None = None, result: Any = None,
                release_lane: bool = True) -> None:
        now = self._mono()
        job.state = state
        job.reason = None if state == "succeeded" else reason
        job.detail = detail
        job.result = result
        job._t_end = now
        job.finished_at = _iso(self._wall())
        job.updated_at = job.finished_at
        if release_lane:
            self._release_lane(job)
        self._notify(job, "state")

    def _release_lane(self, job: Job) -> None:
        if self._lane_active.get(job.lane) != job.job_id:
            return
        del self._lane_active[job.lane]
        antre = self._lane_queue.get(job.lane)
        while antre:
            berikut = self._jobs.get(antre.popleft())
            if berikut is not None and berikut.state == "queued":
                self._start(berikut)
                break

    def _notify(self, job: Job, kind: str) -> None:
        if self.on_update is None:
            return
        try:
            self.on_update(job, kind)
        except Exception:  # noqa: BLE001
            pass


def _classify(job: Job, hasil: Any) -> tuple[str, str | None, str | None, Any]:
    """Petakan ``HandlerResult`` operasi menjadi state job."""
    status = getattr(hasil, "status", "failed")
    payload: Mapping[str, Any] = getattr(hasil, "payload", {}) or {}
    detail = getattr(hasil, "detail", None)
    result = dict(payload)
    if job.cancel_requested:
        if payload.get("code") == "kernel_restarted" or _ename_terakhir(payload) == "KernelRestarted":
            return "failed", "cancel_failed", detail, result
        if job.reason == "timeout":
            return "timed_out", "timeout", detail, result
        return "cancelled", "cancelled_by_user", detail, result
    if status == "ok":
        return "succeeded", None, detail, result
    if status == "timeout":
        return "timed_out", "timeout", detail, result
    if status == "cancelled":
        return "cancelled", "cancelled_by_user", detail, result
    return "failed", "error", detail, result


def _ename_terakhir(payload: Mapping[str, Any]) -> str | None:
    hasil = payload.get("result")
    if not isinstance(hasil, Mapping):
        return None
    keluaran = hasil.get("outputs")
    if not isinstance(keluaran, list) or not keluaran:
        return None
    akhir = keluaran[-1]
    return akhir.get("ename") if isinstance(akhir, Mapping) else None


__all__ = [
    "CANCEL_GRACE_SECONDS", "Job", "JobContext", "JobRunner", "JobSpec", "LaneBusy",
    "MAX_OUTPUT_CHARS", "NETWORK_QUEUE_MAX", "RETENTION_SECONDS", "TERMINAL",
]
