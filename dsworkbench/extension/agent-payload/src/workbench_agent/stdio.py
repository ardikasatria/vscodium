"""Jalur pipa IDE: ``workbench-agent run --stdio`` (ADR-072 §3.5, §6).

Ekstensi IDE menyalakan agent sebagai proses anak dan bertukar satu objek JSON
per baris lewat stdin/stdout -- tanpa port jaringan. Spesifikasi lengkapnya:
``docs/ide-stdio-protocol.md``.

Tiga aturan yang ditegakkan di sini, bukan di handler:

1. **Satu pintu.** Operasi dari pipa memanggil ``OperationAllowlist.dispatch``
   dan ``submit_long`` yang sama dengan relay. Modul ini tidak punya handler.
2. **Kelas A saja.** Operasi di luar :data:`local_ops.LOCAL_OPS` ditolak
   ``bukan_kelas_lokal`` sebelum allowlist disentuh.
3. **Akar kerja dari server.** ``workspace`` dan ``services`` kiriman klien
   dibuang; isinya diganti dari amplop sesi yang dikirim server lewat relay.

Kanal protokol adalah salinan fd 1 yang diambil sebelum apa pun berjalan; fd 1
lalu dialihkan ke stderr (pola ``workbench_jupyter.worker``), sehingga
``print``, pustaka C, dan proses anak tidak dapat mengotori kanal.
"""

from __future__ import annotations

import copy
import json
import math
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, BinaryIO, Mapping

from .config import AGENT_VERSION
from .errors import OperationRejectedError
from .jobs import TERMINAL
from .local_ops import (
    AWALAN_JOB_PIPA,
    KODE_AMPLOP_DIPERLUKAN,
    KODE_BUKAN_KELAS_LOKAL,
    KODE_KERNEL_BUKAN_PIPA,
    KODE_LAYANAN_TIDAK_ADA,
    OPS_JOB,
    OPS_KERNEL,
    OPS_KERNEL_PERLU_AMPLOP,
    OPS_LAYANAN,
    OPS_PERLU_AMPLOP,
    PROTOKOL_STDIO,
    dari_pipa,
    kelas_lokal,
)

#: Batas satu baris protokol (tanpa akhir baris), sama dengan frame relay.
BATAS_BARIS = 1024 * 1024

#: Panjang ``id`` permintaan dari klien.
BATAS_ID = 128

KODE_JSON_TIDAK_SAH = "json_tidak_sah"
KODE_BARIS_TERLALU_PANJANG = "baris_terlalu_panjang"
KODE_PERMINTAAN_TIDAK_SAH = "permintaan_tidak_sah"
KODE_ID_TERPAKAI = "id_terpakai"
KODE_HASIL_TERLALU_BESAR = "hasil_terlalu_besar"
KODE_BELUM_DIPASANGKAN = "belum_dipasangkan"
KODE_GALAT_INTERNAL = "galat_internal"
KODE_OPERASI_DITOLAK = "operasi_ditolak"
KODE_AGENT_SUDAH_BERJALAN = "agent_sudah_berjalan"


class _BarisRusak:
    """Baris masuk yang tidak dapat dipakai (bukan EOF)."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail


def _tanpa_nan(nilai: Any) -> Any:
    """Ganti NaN/±Inf dengan ``None``: ``JSON.parse`` di ekstensi menolaknya."""
    if isinstance(nilai, float):
        return nilai if math.isfinite(nilai) else None
    if isinstance(nilai, Mapping):
        return {str(k): _tanpa_nan(v) for k, v in nilai.items()}
    if isinstance(nilai, (list, tuple)):
        return [_tanpa_nan(v) for v in nilai]
    return nilai


def _serial(pesan: Mapping[str, Any]) -> bytes:
    # ensure_ascii: keluaran sel boleh memuat surrogate tunggal atau U+2028;
    # dengan escape \\uXXXX barisnya selalu UTF-8 sah dan tetap satu baris.
    try:
        teks = json.dumps(pesan, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except ValueError:
        teks = json.dumps(_tanpa_nan(pesan), ensure_ascii=True, allow_nan=False,
                          separators=(",", ":"))
    return teks.encode("ascii")


class KanalStdio:
    """Kanal JSON per baris. Penulisan diserialkan satu kunci (banyak utas menulis)."""

    def __init__(self, masuk: BinaryIO, keluar: BinaryIO, *,
                 fd_masuk: int | None = None, fd_keluar: int | None = None):
        self._masuk = masuk
        self._keluar = keluar
        #: fd asli klien, untuk diteruskan bila agent meluncurkan ulang dirinya.
        self.fd_masuk = fd_masuk
        self.fd_keluar = fd_keluar
        self._kunci_tulis = threading.Lock()
        self.putus = False

    def kirim(self, pesan: Mapping[str, Any]) -> bool:
        """Tulis satu pesan. ``False`` bila klien sudah pergi."""
        try:
            data = _serial(pesan)
        except (TypeError, ValueError) as exc:
            data = _serial(_pengganti(pesan, KODE_GALAT_INTERNAL,
                                      f"Hasil tidak dapat dijadikan JSON: {exc}"))
        if len(data) > BATAS_BARIS:
            data = _serial(_pengganti(
                pesan, KODE_HASIL_TERLALU_BESAR,
                f"Hasil berukuran {len(data)} byte melebihi batas {BATAS_BARIS} byte "
                "per pesan. Kurangi keluaran sel atau baca berkas per potongan."))
        with self._kunci_tulis:
            if self.putus:
                return False
            try:
                self._keluar.write(data + b"\n")
                self._keluar.flush()
            except (OSError, ValueError):
                self.putus = True
                return False
        return True

    def baca(self) -> "dict[str, Any] | _BarisRusak | None":
        """Pesan berikutnya; ``None`` saat EOF (klien pergi)."""
        while True:
            try:
                baris = self._masuk.readline(BATAS_BARIS + 2)
            except (OSError, ValueError):
                return None
            if not baris:
                return None
            if len(baris.rstrip(b"\r\n")) > BATAS_BARIS:
                # Buang sisa baris raksasa itu, jangan menafsirkannya sepotong-sepotong.
                while not baris.endswith(b"\n"):
                    try:
                        baris = self._masuk.readline(64 * 1024)
                    except (OSError, ValueError):
                        return None
                    if not baris:
                        break
                return _BarisRusak(KODE_BARIS_TERLALU_PANJANG,
                                   f"Satu baris protokol paling besar {BATAS_BARIS} byte.")
            if not baris.strip():
                continue
            try:
                pesan = json.loads(baris.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return _BarisRusak(KODE_JSON_TIDAK_SAH, "Baris protokol bukan JSON UTF-8 yang sah.")
            if not isinstance(pesan, dict):
                return _BarisRusak(KODE_PERMINTAAN_TIDAK_SAH, "Pesan harus berupa objek JSON.")
            return pesan


def _pengganti(pesan: Mapping[str, Any], code: str, detail: str) -> dict[str, Any]:
    """Pesan kecil pengganti pesan yang tidak dapat dikirim, dengan ``id`` yang sama."""
    jenis = pesan.get("type")
    if jenis == "job":
        return {"type": "job", "id": pesan.get("id"), "state": pesan.get("state"),
                "offset": pesan.get("offset", 0), "output": [],
                "result": {"code": code}, "reason": "error", "detail": detail}
    if jenis == "result":
        return {"type": "result", "id": pesan.get("id"), "status": "failed",
                "payload": {"code": code}, "detail": detail}
    return {"type": "error", "code": code, "detail": detail}


def amankan_kanal() -> KanalStdio:
    """Ambil fd 0/1 asli sebagai kanal protokol, lalu jauhkan dari siapa pun.

    Harus dipanggil sebelum apa pun sempat menulis. Sesudahnya:

    - fd 1 dan ``sys.stdout`` menuju stderr: ``print``, pustaka C, dan proses
      anak (kernel, psql, pg_ctl) tidak dapat menulis ke kanal;
    - fd 0 dan ``sys.stdin`` menuju perangkat kosong: proses anak dan
      ``input()`` di sel tidak dapat mencuri baris protokol.

    Salinan fd-nya tidak diwariskan ke proses anak (``os.dup`` membuatnya
    non-inheritable).
    """
    try:
        sys.stdout.flush()
    except Exception:  # noqa: BLE001 - stdout sudah tertutup; lanjut saja
        pass
    fd_keluar = os.dup(1)
    fd_masuk = os.dup(0)
    try:
        os.dup2(2, 1)
    except OSError:  # pragma: no cover - fd 2 tertutup; tetap berjalan
        pass
    try:
        nol = os.open(os.devnull, os.O_RDONLY)
        os.dup2(nol, 0)
        os.close(nol)
        sys.stdin = open(os.devnull, "r", encoding="utf-8")  # noqa: SIM115
    except OSError:  # pragma: no cover
        pass
    sys.stdout = sys.stderr
    return KanalStdio(os.fdopen(fd_masuk, "rb"), os.fdopen(fd_keluar, "wb", buffering=0),
                      fd_masuk=fd_masuk, fd_keluar=fd_keluar)


def pesan_halo(*, capabilities: list[str], paired: bool) -> dict[str, Any]:
    return {"type": "hello", "protocol": PROTOKOL_STDIO, "agentVersion": AGENT_VERSION,
            "capabilities": list(capabilities), "paired": bool(paired)}


def layani_tanpa_pairing(kanal: KanalStdio) -> int:
    """Perangkat belum dipasangkan: laporkan lewat ``hello``, tolak semua operasi.

    Tanpa pairing tidak ada relay, jadi tidak ada amplop dan tidak ada operasi
    yang dapat dikerjakan. Agent tetap hidup sampai klien menutup pipa agar
    ekstensi tidak menganggapnya mati mendadak.
    """
    kanal.kirim(pesan_halo(capabilities=[], paired=False))
    while True:
        pesan = kanal.baca()
        if pesan is None:
            return 0
        if isinstance(pesan, _BarisRusak):
            kanal.kirim({"type": "error", "code": pesan.code, "detail": pesan.detail})
            continue
        rid = pesan.get("id")
        if isinstance(rid, str) and 0 < len(rid) <= BATAS_ID:
            kanal.kirim({"type": "result", "id": rid, "status": "rejected",
                         "payload": {"code": KODE_BELUM_DIPASANGKAN},
                         "detail": "Komputer ini belum dipasangkan dengan akun Workbench."})


class _Tolak(Exception):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass
class _Lacak:
    """Job milik pipa yang hasilnya masih harus diantar ke klien."""

    id_klien: str
    #: ``job``: potongan + pesan akhir ``job``; ``sinkron``: satu ``result`` di akhir.
    mode: str
    terkirim: int = 0
    keadaan: str | None = None


class PipaIde:
    """Pelayan pipa IDE di atas satu :class:`~workbench_agent.runner.Agent`."""

    def __init__(self, agent: Any, kanal: KanalStdio, *, pekerja: int = 4,
                 jeda_dorong: float = 0.1):
        self.agent = agent
        self.allowlist = agent.allowlist
        self.kanal = kanal
        self._jeda = jeda_dorong
        self._kunci = threading.Lock()
        #: kernelId → courseId; hanya kernel yang dimulai lewat pipa ini.
        self._kernel: dict[str, str] = {}
        self._lacak: dict[str, _Lacak] = {}
        self._kunci_dorong = threading.Lock()
        self._bangun = threading.Event()
        self._tutup = threading.Event()
        self._antrean: "queue.Queue[dict[str, Any] | None]" = queue.Queue()
        self._utas: list[threading.Thread] = []
        self._jumlah_pekerja = max(1, pekerja)
        self.allowlist.pipa_aktif = True
        agent._penerima_pipa = self._job_berubah

    # ------------------------------------------------------------------
    # daur hidup

    def mulai(self) -> None:
        """Nyalakan utas pekerja operasi pendek dan pengantar keluaran job."""
        if self._utas:
            return
        for i in range(self._jumlah_pekerja):
            t = threading.Thread(target=self._loop_pekerja, name=f"pipa-ide-{i}", daemon=True)
            t.start()
            self._utas.append(t)
        t = threading.Thread(target=self._loop_dorong, name="pipa-ide-job", daemon=True)
        t.start()
        self._utas.append(t)

    def halo(self, *, paired: bool = True) -> None:
        self.kanal.kirim(pesan_halo(capabilities=self.allowlist.capabilities(), paired=paired))

    def lapor_relay(self, keadaan: str, *, alasan: str | None = None) -> None:
        pesan: dict[str, Any] = {"type": "relay", "state": keadaan}
        if alasan:
            pesan["reason"] = alasan
        self.kanal.kirim(pesan)

    def layani(self) -> None:
        """Baca permintaan sampai EOF (klien pergi). Dipanggil di utas utama."""
        self.mulai()
        while not self._tutup.is_set():
            pesan = self.kanal.baca()
            if pesan is None:
                return
            if isinstance(pesan, _BarisRusak):
                self.kanal.kirim({"type": "error", "code": pesan.code, "detail": pesan.detail})
                continue
            self._antrean.put(pesan)

    def tutup(self, *, tenggang: float = 10.0) -> None:
        """Klien pergi: hentikan job dan kernel milik pipa, lalu lepaskan pipa."""
        if self._tutup.is_set():
            return
        self._tutup.set()
        self._bangun.set()
        for _ in self._utas:
            self._antrean.put(None)
        jobs = self.allowlist.jobs
        for job in jobs.active():
            if job.job_id.startswith(AWALAN_JOB_PIPA):
                try:
                    jobs.cancel(job.job_id)
                except Exception:  # noqa: BLE001
                    pass
        with self._kunci:
            kernel = list(self._kernel)
            self._kernel.clear()

        def _hentikan(kid: str) -> None:
            try:
                self.allowlist.dispatch("jupyter.kernel_stop", {"kernelId": kid})
            except Exception:  # noqa: BLE001 - penutupan tidak boleh tersangkut
                pass

        penghenti = [threading.Thread(target=_hentikan, args=(kid,), daemon=True)
                     for kid in kernel]
        for t in penghenti:
            t.start()
        batas = time.monotonic() + tenggang
        for t in penghenti:
            t.join(max(0.0, batas - time.monotonic()))
        while time.monotonic() < batas and any(
                j.job_id.startswith(AWALAN_JOB_PIPA) for j in jobs.active()):
            time.sleep(0.05)
        self.allowlist.pipa_aktif = False
        self.allowlist.amplop.hapus_semua()
        self.agent._penerima_pipa = None

    def kernel_pipa(self) -> list[str]:
        with self._kunci:
            return list(self._kernel)

    # ------------------------------------------------------------------
    # permintaan

    def _loop_pekerja(self) -> None:
        while True:
            pesan = self._antrean.get()
            if pesan is None or self._tutup.is_set():
                return
            try:
                self.tangani(pesan)
            except Exception as exc:  # noqa: BLE001 - satu permintaan rusak tidak mematikan pipa
                print(f"pipa IDE: galat internal: {type(exc).__name__}: {exc}",
                      file=sys.stderr, flush=True)

    def _hasil(self, rid: str, status: str, payload: Mapping[str, Any],
               detail: str | None = None) -> None:
        self.kanal.kirim({"type": "result", "id": rid, "status": status,
                          "payload": dict(payload), "detail": detail})

    def tangani(self, pesan: Mapping[str, Any]) -> None:
        """Kerjakan satu permintaan ``{id, op, payload}`` dan kirim jawabannya."""
        rid = pesan.get("id")
        if not isinstance(rid, str) or not rid or len(rid) > BATAS_ID:
            self.kanal.kirim({"type": "error", "code": KODE_PERMINTAAN_TIDAK_SAH,
                              "detail": f"id wajib berupa teks 1–{BATAS_ID} karakter."})
            return
        op = pesan.get("op")
        payload = pesan.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            self._hasil(rid, "rejected", {"code": KODE_PERMINTAAN_TIDAK_SAH},
                        "payload harus berupa objek.")
            return
        # Kelas B (dan nama tak dikenal) berhenti di sini: allowlist tidak disentuh.
        if not kelas_lokal(op):
            self._hasil(rid, "rejected",
                        {"code": KODE_BUKAN_KELAS_LOKAL, "operation": str(op)[:64]},
                        "Operasi ini tidak tersedia lewat jalur lokal aplikasi; "
                        "jalankan lewat server Workbench (relay).")
            return
        id_job = AWALAN_JOB_PIPA + rid
        if self.allowlist.jobs.get(id_job) is not None:
            self._hasil(rid, "rejected", {"code": KODE_ID_TERPAKAI, "operation": op},
                        "id ini masih dipakai job lain; gunakan id baru.")
            return
        try:
            data = self._siapkan(op, payload)
        except _Tolak as exc:
            self._hasil(rid, "rejected", {"code": exc.code, "operation": op}, exc.detail)
            return

        with dari_pipa(), self.agent.sedang_sibuk():
            try:
                panjang = self.allowlist.submit_long(id_job, op, data)
                hasil = None if panjang is not None else self.allowlist.dispatch(op, data)
            except OperationRejectedError as exc:
                self._hasil(rid, "rejected", {"code": KODE_OPERASI_DITOLAK, "operation": op},
                            str(exc))
                return
            except Exception as exc:  # noqa: BLE001 - galat handler tidak mematikan pipa
                print(f"galat internal pada {op} (pipa): {type(exc).__name__}: {exc}",
                      file=sys.stderr, flush=True)
                self._hasil(rid, "failed", {"code": KODE_GALAT_INTERNAL, "operation": op},
                            f"Galat internal Local Runner saat menjalankan {op}.")
                return

        if panjang is not None:
            spec, awal = panjang
            if awal is None:
                # Jalur sinkron (tanpa ``job: true``): satu ``result`` saat selesai.
                self._lacak_job(id_job, rid, "sinkron")
                return
            self._hasil(rid, awal.status, self._id_publik(dict(awal.payload)), awal.detail)
            if awal.status == "ok" and spec.job_mode:
                self._lacak_job(id_job, rid, "job")
            return

        muatan = dict(hasil.payload)
        if hasil.status == "ok":
            if op == "jupyter.kernel_start":
                kernel = muatan.get("kernel")
                kid = kernel.get("id") if isinstance(kernel, Mapping) else None
                if isinstance(kid, str):
                    with self._kunci:
                        self._kernel[kid] = data["courseId"]
            elif op == "jupyter.kernel_stop":
                with self._kunci:
                    self._kernel.pop(data.get("kernelId"), None)
        self._hasil(rid, hasil.status, self._id_publik(muatan), hasil.detail)

    def _amplop(self, course_id: Any, op: str) -> Any:
        amplop = self.allowlist.amplop.ambil(course_id)
        if amplop is None:
            raise _Tolak(KODE_AMPLOP_DIPERLUKAN,
                         "Belum ada amplop sesi yang berlaku untuk mata kuliah ini. "
                         "Minta amplop baru ke server Workbench, lalu ulangi.")
        if op not in amplop.ops:
            raise _Tolak(KODE_AMPLOP_DIPERLUKAN,
                         f"Amplop sesi mata kuliah ini tidak mengizinkan operasi {op}.")
        return amplop

    def _siapkan(self, op: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Susun payload yang diteruskan ke allowlist. ``_Tolak`` bila tidak boleh."""
        data = dict(payload)
        # Tidak pernah dari klien: akar kerja dan klaster ditetapkan server.
        data.pop("workspace", None)
        data.pop("services", None)

        if op in OPS_JOB:
            data.pop("courseId", None)
            jid = data.get("jobId")
            if isinstance(jid, str) and jid:
                data["jobId"] = AWALAN_JOB_PIPA + jid
            return data

        if op in OPS_KERNEL:
            kid = data.get("kernelId")
            with self._kunci:
                course = self._kernel.get(kid) if isinstance(kid, str) else None
            if course is None:
                raise _Tolak(KODE_KERNEL_BUKAN_PIPA,
                             "Kernel ini tidak dimulai dari aplikasi DSWorkbench "
                             "(atau sudah dihentikan).")
            # Mata kuliah kernel = yang tercatat saat dimulai, bukan kiriman klien.
            data["courseId"] = course
            if op in OPS_KERNEL_PERLU_AMPLOP:
                data["workspace"] = dict(self._amplop(course, op).workspace)
            return data

        assert op in OPS_PERLU_AMPLOP, op
        cid = data.get("courseId")
        if not isinstance(cid, str) or not cid:
            raise _Tolak(KODE_AMPLOP_DIPERLUKAN, "courseId wajib diisi untuk operasi ini.")
        amplop = self._amplop(cid, op)
        data["workspace"] = dict(amplop.workspace)
        if op in OPS_LAYANAN:
            if amplop.services is None:
                raise _Tolak(KODE_LAYANAN_TIDAK_ADA,
                             "Amplop sesi mata kuliah ini tidak memuat layanan basis data "
                             "(mata kuliah tanpa basis data praktikum, atau fiturnya "
                             "belum dinyalakan di server).")
            data["services"] = copy.deepcopy(dict(amplop.services))
        return data

    @staticmethod
    def _id_publik(muatan: dict[str, Any]) -> dict[str, Any]:
        """Id job di pipa = ``id`` permintaan klien; awalan internal tidak keluar."""
        n = len(AWALAN_JOB_PIPA)
        jid = muatan.get("jobId")
        if isinstance(jid, str) and jid.startswith(AWALAN_JOB_PIPA):
            muatan["jobId"] = jid[n:]
        if "activeJobId" in muatan:
            aktif = muatan.get("activeJobId")
            if isinstance(aktif, str) and aktif.startswith(AWALAN_JOB_PIPA):
                muatan["activeJobId"] = aktif[n:]
                muatan["activeOrigin"] = "pipa"
            else:
                # Job milik relay (web) tidak dapat dituju dari pipa.
                muatan["activeJobId"] = None
                muatan["activeOrigin"] = "relay"
        job = muatan.get("job")
        if isinstance(job, Mapping) and isinstance(job.get("jobId"), str) \
                and job["jobId"].startswith(AWALAN_JOB_PIPA):
            muatan["job"] = {**job, "jobId": job["jobId"][n:]}
        return muatan

    # ------------------------------------------------------------------
    # job: keluaran bertahap dan hasil akhir ke pipa (bukan ke server)

    def _lacak_job(self, id_job: str, rid: str, mode: str) -> None:
        with self._kunci:
            self._lacak[id_job] = _Lacak(id_klien=rid, mode=mode)
        self._bangun.set()

    def _job_berubah(self, _job: Any, _kind: str) -> None:
        """Dari utas pekerja/pengawas job: bangunkan pengantar, jangan menulis di sini."""
        self._bangun.set()

    def _loop_dorong(self) -> None:
        while not self._tutup.is_set():
            with self._kunci:
                ada = bool(self._lacak)
            self._bangun.wait(self._jeda if ada else 1.0)
            self._bangun.clear()
            try:
                self.dorong()
            except Exception as exc:  # noqa: BLE001 - pengantar tidak boleh mati
                print(f"pipa IDE: galat pengantar job: {type(exc).__name__}: {exc}",
                      file=sys.stderr, flush=True)

    def dorong(self) -> None:
        """Antar keluaran baru dan hasil akhir setiap job pipa yang dilacak."""
        jobs = self.allowlist.jobs
        with self._kunci_dorong:
            with self._kunci:
                daftar = list(self._lacak.items())
            for id_job, l in daftar:
                snap = jobs.snapshot(id_job, since=l.terkirim)
                if snap is None:
                    with self._kunci:
                        self._lacak.pop(id_job, None)
                    continue
                selesai = snap["state"] in TERMINAL
                if l.mode == "sinkron":
                    if not selesai:
                        continue
                    status = {"succeeded": "ok", "timed_out": "timeout",
                              "cancelled": "cancelled"}.get(snap["state"], "failed")
                    self._hasil(l.id_klien, status, snap["result"] or {}, snap["detail"])
                elif snap["output"] or snap["state"] != l.keadaan or selesai:
                    pesan: dict[str, Any] = {
                        "type": "job", "id": l.id_klien, "state": snap["state"],
                        "offset": snap["outputOffset"], "output": snap["output"],
                    }
                    if selesai:
                        pesan.update(result=snap["result"], reason=snap["reason"],
                                     detail=snap["detail"], elapsedMs=snap["elapsedMs"],
                                     outputTruncated=snap["outputTruncated"])
                    self.kanal.kirim(pesan)
                    l.terkirim = snap["nextSince"]
                    l.keadaan = snap["state"]
                    jobs.mark_pushed(id_job, l.terkirim)
                if selesai:
                    with self._kunci:
                        self._lacak.pop(id_job, None)


__all__ = [
    "BATAS_BARIS", "KanalStdio", "PipaIde", "amankan_kanal", "layani_tanpa_pairing",
    "pesan_halo",
]
