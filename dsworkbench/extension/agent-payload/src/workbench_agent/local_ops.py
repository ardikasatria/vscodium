"""Kelas operasi jalur lokal (pipa IDE) dan amplop sesi -- ADR-072 §3.5, §6.

Ekstensi IDE menyalakan agent sebagai proses anak (``run --stdio``) dan
memanggil ``OperationAllowlist`` yang sama dengan relay. Dua hal yang
membedakan pipa dari relay ada di sini, sebagai DATA di satu tempat:

1. **Kelas A** (:data:`LOCAL_OPS`): operasi yang boleh datang dari pipa.
   Selebihnya (kelas B) ditolak ``bukan_kelas_lokal`` sebelum handler mana pun
   dipanggil. Salinannya untuk server dan ekstensi: ``docs/ide-local-ops.json``
   (ada uji yang membandingkan keduanya).
2. **Amplop sesi**: akar kerja dan blok ``services`` tidak pernah diambil dari
   klien pipa. Server mengirim amplop per mata kuliah lewat relay
   (``session.envelope``); agent menyimpannya di memori dan menyisipkan isinya
   ke setiap operasi pipa.

Modul ini tidak mengimpor ``operations`` (dipakai olehnya).
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping

#: Capability yang dilaporkan saat connect bila agent berjalan dengan ``--stdio``.
CAPABILITY_IDE_STDIO = "ide.stdio.v1"

#: Versi protokol pipa (pesan ``hello``).
PROTOKOL_STDIO = 1

#: Nama operasi relay pembawa amplop. Hanya diterima dari relay.
OP_AMPLOP = "session.envelope"

#: Kelas A: operasi yang boleh lewat pipa. Terurut; sama dengan
#: ``docs/ide-local-ops.json``.
LOCAL_OPS: tuple[str, ...] = (
    "db.catalog",
    "job.cancel",
    "job.status",
    "jupyter.execute",
    "jupyter.interrupt",
    "jupyter.kernel_start",
    "jupyter.kernel_status",
    "jupyter.kernel_stop",
    "jupyter.restart",
    "sql.execute",
    "sql.run_file",
    "workspace.ensure",
    "workspace.info",
    "workspace.list",
    "workspace.preview",
    "workspace.read_file",
    "workspace.tree",
    "workspace.write_file",
)

_LOCAL = frozenset(LOCAL_OPS)

#: Butuh amplop sah untuk ``courseId`` kiriman klien; ``workspace`` disisipkan.
OPS_PERLU_AMPLOP = frozenset({
    "workspace.ensure", "workspace.info", "workspace.list", "workspace.preview",
    "workspace.read_file", "workspace.tree", "workspace.write_file",
    "jupyter.kernel_start", "sql.execute", "sql.run_file", "db.catalog",
})

#: Selain ``workspace``, blok ``services`` amplop ikut disisipkan.
OPS_LAYANAN = frozenset({"sql.execute", "sql.run_file", "db.catalog"})

#: Menunjuk kernel lewat ``kernelId``: hanya kernel yang dimulai dari pipa.
OPS_KERNEL = frozenset({
    "jupyter.kernel_stop", "jupyter.kernel_status", "jupyter.interrupt",
    "jupyter.restart", "jupyter.execute",
})

#: Operasi kernel yang menjalankan kode: amplop mata kuliah kernel itu harus
#: masih sah. Hentikan/interupsi/status tidak, agar sel selalu dapat dihentikan.
OPS_KERNEL_PERLU_AMPLOP = frozenset({"jupyter.execute", "jupyter.restart"})

#: Menunjuk job lewat ``jobId``: hanya job yang dimulai dari pipa.
OPS_JOB = frozenset({"job.status", "job.cancel"})

#: Ruang nama id job milik pipa. Id pesan relay dibuat server dan tidak pernah
#: berawalan ini, sehingga id klien pipa tidak dapat bertabrakan dengannya.
AWALAN_JOB_PIPA = "ide:"

# -- kode galat stabil ------------------------------------------------------

KODE_BUKAN_KELAS_LOKAL = "bukan_kelas_lokal"
KODE_AMPLOP_DIPERLUKAN = "amplop_diperlukan"
KODE_AMPLOP_TIDAK_SAH = "amplop_tidak_sah"
KODE_AMPLOP_KEDALUWARSA = "amplop_kedaluwarsa"
KODE_LAYANAN_TIDAK_ADA = "layanan_tidak_ada_di_amplop"
KODE_KERNEL_BUKAN_PIPA = "kernel_bukan_milik_pipa"
KODE_PIPA_TIDAK_AKTIF = "pipa_tidak_aktif"

#: Toleransi selisih jam agent terhadap server (detik). Jam laptop sering
#: meleset beberapa menit; amplop baru dianggap kedaluwarsa setelah
#: ``expiresAt`` + toleransi menurut jam agent. Lihat :class:`PenyimpanAmplop`.
TOLERANSI_JAM_DETIK = 120.0

#: Umur amplop terpanjang yang diterima (detik); di atasnya dianggap salah bentuk.
UMUR_AMPLOP_MAKS_DETIK = 24 * 3600.0

_POLA_COURSE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_TATA_LETAK = ("per-course", "flat")


def kelas_lokal(operasi: Any) -> bool:
    """``True`` bila operasi termasuk kelas A (boleh lewat pipa)."""
    return isinstance(operasi, str) and operasi in _LOCAL


# -- asal operasi -----------------------------------------------------------

#: Asal operasi yang sedang dikerjakan utas ini: ``relay`` (bawaan) atau ``pipa``.
#: Ditetapkan lapisan pipa, tidak pernah dari payload -- jadi tidak dapat dipalsu
#: peramban maupun ekstensi.
_ASAL: contextvars.ContextVar[str] = contextvars.ContextVar("asal_operasi", default="relay")


def asal_operasi() -> str:
    return _ASAL.get()


@contextlib.contextmanager
def dari_pipa() -> Iterator[None]:
    """Tandai operasi di dalam blok ini sebagai berasal dari pipa IDE."""
    token = _ASAL.set("pipa")
    try:
        yield
    finally:
        _ASAL.reset(token)


# -- amplop sesi ------------------------------------------------------------


class AmplopTidakSah(Exception):
    """Amplop dari server salah bentuk atau sudah kedaluwarsa."""

    def __init__(self, pesan: str, *, code: str = KODE_AMPLOP_TIDAK_SAH):
        self.code = code
        super().__init__(pesan)


def _waktu_iso(nilai: Any, nama: str) -> float:
    """ISO 8601 → detik epoch. Tanpa zona waktu dianggap UTC."""
    if not isinstance(nilai, str) or not nilai or len(nilai) > 64:
        raise AmplopTidakSah(f"{nama} amplop harus waktu ISO 8601.")
    teks = nilai.strip()
    if teks[-1:] in ("Z", "z"):
        teks = teks[:-1] + "+00:00"
    try:
        t = datetime.fromisoformat(teks)
    except ValueError:
        raise AmplopTidakSah(f"{nama} amplop harus waktu ISO 8601.") from None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp()


@dataclass(frozen=True)
class Amplop:
    """Satu amplop sesi yang sudah divalidasi."""

    envelope_id: str
    course_id: str
    workspace: Mapping[str, Any]
    services: Mapping[str, Any] | None
    ops: frozenset[str]
    issued_at: float
    expires_at: float
    expires_at_teks: str
    #: ``monotonic()`` saat diterima: batas umur tidak bergantung jam dinding.
    diterima_mono: float

    def publik(self) -> dict[str, Any]:
        return {"envelopeId": self.envelope_id, "courseId": self.course_id,
                "expiresAt": self.expires_at_teks, "ops": sorted(self.ops)}


class PenyimpanAmplop:
    """Amplop sesi per mata kuliah, di memori. Aman dipakai lintas utas.

    Ditulis utas transport relay, dibaca utas pipa. Amplop baru untuk
    ``courseId`` yang sama menggantikan yang lama; tidak ada yang ditulis ke
    disk, sehingga agent yang dimulai ulang selalu meminta amplop baru.

    **Jam agent boleh meleset.** Amplop berlaku selama keduanya benar:

    - jam dinding agent ≤ ``expiresAt`` + :data:`TOLERANSI_JAM_DETIK`;
    - waktu monoton sejak amplop diterima ≤ (``expiresAt`` − ``issuedAt``) +
      toleransi. Jadi jam laptop yang tertinggal jauh (atau dimundurkan) tidak
      memperpanjang amplop melebihi umur yang ditetapkan server.

    Jam laptop yang terlalu cepat lebih dari toleransi membuat amplop ditolak
    saat diterima (``amplop_kedaluwarsa``); perbaikannya menyetel jam.
    """

    def __init__(self, *, wallclock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 toleransi: float = TOLERANSI_JAM_DETIK):
        self._wall = wallclock
        self._mono = monotonic
        self._toleransi = float(toleransi)
        self._kunci = threading.Lock()
        self._per_course: dict[str, Amplop] = {}

    def _kedaluwarsa(self, a: Amplop) -> bool:
        if self._wall() > a.expires_at + self._toleransi:
            return True
        umur = max(0.0, a.expires_at - a.issued_at) + self._toleransi
        return self._mono() - a.diterima_mono > umur

    def simpan(self, muatan: Any) -> Amplop:
        """Validasi lalu simpan amplop dari relay. ``AmplopTidakSah`` bila ditolak."""
        if not isinstance(muatan, Mapping):
            raise AmplopTidakSah("Amplop sesi harus berupa objek.")
        eid = muatan.get("envelopeId")
        if not isinstance(eid, str) or not eid or len(eid) > 128:
            raise AmplopTidakSah("envelopeId amplop wajib berupa teks.")
        cid = muatan.get("courseId")
        if not isinstance(cid, str) or not _POLA_COURSE_ID.fullmatch(cid):
            raise AmplopTidakSah("courseId amplop harus slug mata kuliah.")
        ws = muatan.get("workspace")
        if not isinstance(ws, Mapping) or ws.get("layout") not in _TATA_LETAK:
            raise AmplopTidakSah("workspace amplop harus {layout, courseId}.")
        if ws.get("courseId") != cid:
            raise AmplopTidakSah("workspace.courseId amplop tidak sama dengan courseId.")
        services = muatan.get("services")
        if services is not None and not isinstance(services, Mapping):
            raise AmplopTidakSah("services amplop harus berupa objek.")
        ops = muatan.get("ops")
        if not isinstance(ops, list) or len(ops) > 256 \
                or not all(isinstance(o, str) for o in ops):
            raise AmplopTidakSah("ops amplop harus daftar nama operasi.")
        terbit = _waktu_iso(muatan.get("issuedAt"), "issuedAt")
        habis = _waktu_iso(muatan.get("expiresAt"), "expiresAt")
        if habis <= terbit or habis - terbit > UMUR_AMPLOP_MAKS_DETIK:
            raise AmplopTidakSah("Masa berlaku amplop tidak sah.")
        amplop = Amplop(
            envelope_id=eid, course_id=cid,
            workspace={"layout": ws["layout"], "courseId": cid},
            services=copy.deepcopy(dict(services)) if services is not None else None,
            # Kelas B di dalam ``ops`` diabaikan: amplop tidak dapat memperluas kelas A.
            ops=frozenset(o for o in ops if o in _LOCAL),
            issued_at=terbit, expires_at=habis,
            expires_at_teks=str(muatan.get("expiresAt")),
            diterima_mono=self._mono(),
        )
        if self._kedaluwarsa(amplop):
            raise AmplopTidakSah(
                "Amplop sesi sudah kedaluwarsa menurut jam komputer ini. Periksa "
                "tanggal dan jam komputer, lalu buka ulang mata kuliahnya.",
                code=KODE_AMPLOP_KEDALUWARSA)
        with self._kunci:
            self._per_course[cid] = amplop
        return amplop

    def ambil(self, course_id: Any) -> Amplop | None:
        """Amplop sah untuk mata kuliah itu; yang kedaluwarsa dibuang."""
        if not isinstance(course_id, str):
            return None
        with self._kunci:
            a = self._per_course.get(course_id)
            if a is None:
                return None
            if self._kedaluwarsa(a):
                del self._per_course[course_id]
                return None
            return a

    def hapus_semua(self) -> None:
        with self._kunci:
            self._per_course.clear()


__all__ = [
    "AWALAN_JOB_PIPA", "Amplop", "AmplopTidakSah", "CAPABILITY_IDE_STDIO",
    "KODE_AMPLOP_DIPERLUKAN", "KODE_AMPLOP_KEDALUWARSA", "KODE_AMPLOP_TIDAK_SAH",
    "KODE_BUKAN_KELAS_LOKAL", "KODE_KERNEL_BUKAN_PIPA", "KODE_LAYANAN_TIDAK_ADA",
    "KODE_PIPA_TIDAK_AKTIF", "LOCAL_OPS", "OPS_JOB", "OPS_KERNEL",
    "OPS_KERNEL_PERLU_AMPLOP", "OPS_LAYANAN", "OPS_PERLU_AMPLOP", "OP_AMPLOP",
    "PROTOKOL_STDIO", "PenyimpanAmplop", "TOLERANSI_JAM_DETIK", "asal_operasi",
    "dari_pipa", "kelas_lokal",
]
