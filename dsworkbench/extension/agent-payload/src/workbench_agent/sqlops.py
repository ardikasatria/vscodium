"""SQL dari web lewat ``psql`` milik bundel (ADR-054 §2, PGD-04).

Agent berjalan di interpreter tanpa psycopg; ``psql`` bundel ADR-053 sudah ada
dan memahami meta-perintah yang dipakai naskah. Modul ini tidak tahu apa pun
tentang relay: ia menerima argv/lingkungan dari :mod:`operations` dan
mengembalikan struktur data.

Tiga bagian:

* :func:`periksa_meta` -- **daftar izin** meta-perintah ``psql``. Daftar tolak
  tidak cukup: nilai variabel yang diinterpolasi (``:v``) dipindai ulang
  sebagai meta-perintah, sehingga ``SELECT '\\! …' AS v \\gset`` lalu ``:v``
  menjalankan shell; backtick di argumen meta juga menjalankan shell. Yang
  diizinkan hanya perintah baca/tampilan; ``\\gset``, ``\\setenv``, I/O berkas,
  koneksi ulang, dan ``\\set`` untuk variabel khusus psql (huruf besar, mis.
  ``ON_ERROR_STOP``) ditolak.
* :class:`PenguraiHasil` -- keluaran ``psql -A -F US -0``: rekaman dipisah NUL,
  kolom dipisah US (0x1F); tag perintah (``INSERT 0 1``), ``\\echo`` dan
  ``\\timing`` tetap diakhiri baris baru. NUL tidak mungkin ada di teks
  PostgreSQL, jadi nilai multi-baris tidak mengacaukan batas baris hasil.
* :func:`jalankan` -- proses anak tanpa shell, batal (SIGINT → psql mengirim
  permintaan pembatalan ke server) lalu terminate, batas waktu, batas keluaran.
"""

from __future__ import annotations

import json
import queue
import re
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

#: Batas teks SQL dari editor (kontrak §3.3).
BATAS_SQL = 256 * 1024
#: Batas berkas ``.sql`` untuk ``sql.run_file``.
BATAS_BERKAS = 1024 * 1024
#: Baris per hasil yang dikirim ke web (bawaan / maksimum).
BARIS_BAWAAN = 200
BARIS_MAKS = 1000
#: Satu sel dipotong di sini (karakter).
BATAS_SEL = 4000
#: Anggaran teks sel yang dikirim (≈ byte). Badan permintaan API ≤ 1 MiB.
ANGGARAN_HASIL = 512 * 1024
#: Item (hasil + pesan) maksimum dalam satu jawaban.
ITEM_MAKS = 50
#: Keluaran teks ``sql.run_file`` / ``sql.check`` yang dikirim.
BATAS_KELUARAN = 256 * 1024
#: Keluaran mentah psql yang dibaca sebelum proses dihentikan paksa.
BATAS_MENTAH = 256 * 1024 * 1024
#: stderr yang disimpan.
BATAS_STDERR = 256 * 1024

PEMISAH_KOLOM = "\x1f"
#: Penanda NULL. ESC tidak lazim ada di data praktikum; nilai ini diubah
#: menjadi ``None`` sebelum dikirim.
PENANDA_NULL = "\x1bNULL\x1b"

_FOOTER = re.compile(r"^\((\d+) rows?\)$")


class SqlDitolak(Exception):
    """SQL/berkas ditolak sebelum dieksekusi (kode kontrak §4)."""

    def __init__(self, code: str, pesan: str, **tambahan: Any):
        super().__init__(pesan)
        self.code = code
        self.pesan = pesan
        self.tambahan = tambahan

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, **self.tambahan}


# ---------------------------------------------------------------------------
# Meta-perintah
# ---------------------------------------------------------------------------

#: Boleh di editor dan di berkas.
META_UMUM = frozenset({
    "echo", "qecho", "warn", "timing", "set", "unset", "if", "elif", "else", "endif",
    "g", "gx", "gdesc", "crosstabview", "errverbose", "conninfo", "encoding",
    "h", "help", "?", "copyright", "bind", "r", "reset", "p", "print", "q", "quit",
    "l", "list", "z", "sf", "sv", ";", ":",
})
#: Mengubah format keluaran: hanya untuk berkas (keluaran teks apa adanya).
#: Editor selalu menampilkan tabel, jadi di sana ditolak dengan pesan jelas.
META_TAMPILAN = frozenset({"pset", "a", "t", "x", "f", "H", "C", "T"})
#: Meta yang keluarannya dokumen (judul, footer, bantuan), bukan satu tabel
#: ber-``(N rows)``: di editor dibungkus sebagai teks rata (lihat :func:`siapkan_editor`).
META_TEKS = frozenset({"l", "list", "z", "sf", "sv", "h", "help", "?", "copyright", "conninfo"})
#: ``\g``/``\gx`` hanya tanpa argumen (argumen = berkas atau ``|perintah``).
_TANPA_ARGUMEN = frozenset({"g", "gx"})
_VAR_PENGGUNA = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class Meta:
    nama: str
    argumen: str
    baris: int
    #: Rentang ``teks[mulai:akhir]`` = backslash s.d. akhir argumen.
    mulai: int = 0
    akhir: int = 0


def pindai_meta(teks: str) -> list[Meta]:
    """Semua meta-perintah ``psql`` di luar literal/komentar SQL.

    Konservatif: di argumen meta, setiap backslash dianggap awal meta-perintah
    berikutnya (aturan psql untuk backslash tak berkutip), kecuali ``\\\\`` yang
    mengakhiri argumen dan kembali ke SQL.
    """
    hasil: list[Meta] = []
    i, n, baris = 0, len(teks), 1
    kedalaman_komentar = 0
    while i < n:
        c = teks[i]
        if c == "\n":
            baris += 1
            i += 1
            continue
        if kedalaman_komentar:
            if teks.startswith("*/", i):
                kedalaman_komentar -= 1
                i += 2
            elif teks.startswith("/*", i):
                kedalaman_komentar += 1
                i += 2
            else:
                i += 1
            continue
        if teks.startswith("/*", i):
            kedalaman_komentar = 1
            i += 2
            continue
        if teks.startswith("--", i):
            j = teks.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "'":
            escape = i > 0 and teks[i - 1] in "eE" and (i < 2 or not (teks[i - 2].isalnum() or teks[i - 2] == "_"))
            i += 1
            while i < n:
                if teks[i] == "\n":
                    baris += 1
                if escape and teks[i] == "\\":
                    i += 2
                    continue
                if teks[i] == "'":
                    if i + 1 < n and teks[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
            i += 1
            continue
        if c == '"':
            j = i + 1
            while j < n:
                if teks[j] == '"':
                    if j + 1 < n and teks[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            baris += teks.count("\n", i, j)
            i = j + 1
            continue
        if c == "$":
            m = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$", teks[i:])
            if m and not (i > 0 and (teks[i - 1].isalnum() or teks[i - 1] == "_")):
                tag = m.group(0)
                j = teks.find(tag, i + len(tag))
                j = n if j < 0 else j + len(tag)
                baris += teks.count("\n", i, j)
                i = j
                continue
            i += 1
            continue
        if c == "\\":
            i, baris = _baca_meta(teks, i, baris, hasil)
            continue
        i += 1
    return hasil


def _baca_meta(teks: str, i: int, baris: int, hasil: list[Meta]) -> tuple[int, int]:
    """Baca meta-perintah mulai ``teks[i] == '\\'`` sesuai aturan argumen psql.

    Argumen berakhir di akhir baris. Di dalamnya: ``'…'`` (backslash meng-escape
    karakter berikut, ``''`` = kutip), ``"…"``, dan backtick dikenali; backslash
    **tak berkutip** memulai meta berikutnya, kecuali ``\\\\`` yang mengakhiri
    argumen dan mengembalikan sisa baris ke SQL. Kembalikan posisi lanjutan.
    """
    n = len(teks)
    akhir_baris = teks.find("\n", i)
    akhir_baris = n if akhir_baris < 0 else akhir_baris
    pos = i
    while pos < akhir_baris and teks[pos] == "\\":
        m = re.match(r"\\([A-Za-z][A-Za-z0-9_+]*|.?)", teks[pos:akhir_baris])
        nama = m.group(1) if m else ""
        if nama == "\\":
            return pos + 2, baris
        mulai_arg = j = pos + (m.end() if m else 1)
        while j < akhir_baris:
            c = teks[j]
            if c == "'":
                j += 1
                while j < akhir_baris:
                    if teks[j] == "\\":
                        j += 2
                        continue
                    if teks[j] == "'":
                        if j + 1 < akhir_baris and teks[j + 1] == "'":
                            j += 2
                            continue
                        break
                    j += 1
                j += 1
                continue
            if c in ('"', "`"):
                tutup = teks.find(c, j + 1, akhir_baris)
                j = akhir_baris if tutup < 0 else tutup + 1
                continue
            if c == "\\":
                break
            j += 1
        j = min(j, akhir_baris)
        hasil.append(Meta(nama=nama, argumen=teks[mulai_arg:j].strip(), baris=baris,
                          mulai=pos, akhir=j))
        pos = j
    return akhir_baris, baris


def _nama_dasar(nama: str) -> str:
    return nama.rstrip("+") if nama not in ("?",) else nama


def _meta_teks(nama: str) -> bool:
    nama = _nama_dasar(nama)
    return nama in META_TEKS or re.fullmatch(r"d[A-Za-z]*", nama) is not None


#: Penanda blok teks di stdout (``\echo`` dengan escape oktal psql).
_MULAI_TEKS, _AKHIR_TEKS = b"\x02", b"\x03"
_PRA = "\\set QUIET on \\pset format aligned \\echo '\\002' \\set QUIET off "
_PASCA = " \\set QUIET on \\echo '\\003' \\pset format unaligned \\set QUIET off"


def siapkan_editor(teks: str, metas: Sequence[Meta]) -> str:
    """Bungkus meta deskripsi agar keluarannya teks rata di antara STX/ETX.

    Sisipan ditaruh di baris yang sama (meta dipisah backslash), jadi nomor
    baris galat psql tetap sama dengan teks mahasiswa. Dipanggil sesudah
    :func:`periksa_meta`; sisipan sendiri tidak dipindai.
    """
    hasil = teks
    for m in sorted(metas, key=lambda x: x.mulai, reverse=True):
        if not _meta_teks(m.nama):
            continue
        hasil = hasil[:m.mulai] + _PRA + hasil[m.mulai:m.akhir] + _PASCA + hasil[m.akhir:]
    return hasil


def periksa_meta(teks: str, *, mode: str) -> list[Meta]:
    """Tolak meta-perintah di luar daftar izin. ``mode``: ``editor`` / ``berkas``."""
    metas = pindai_meta(teks)
    for m in metas:
        nama = _nama_dasar(m.nama)
        tampil = "\\" + m.nama
        if "`" in m.argumen:
            raise SqlDitolak("forbidden_meta_command",
                             f"Baris {m.baris}: backtick di argumen {tampil} tidak diizinkan "
                             "(menjalankan perintah shell). Pakai Terminal mata kuliah.",
                             command=tampil, line=m.baris)
        # \d… = perintah deskripsi katalog (\d, \dt, \dn, \dx, \dS+, …): baca saja.
        diizinkan = nama in META_UMUM or re.fullmatch(r"d[A-Za-z]*", nama) is not None
        if not diizinkan and nama in META_TAMPILAN:
            if mode == "berkas":
                diizinkan = True
            else:
                raise SqlDitolak(
                    "forbidden_meta_command",
                    f"Baris {m.baris}: {tampil} mengubah tampilan psql; editor selalu "
                    "menampilkan hasil sebagai tabel. Hapus perintah ini, atau simpan "
                    "sebagai berkas lalu pilih Jalankan berkas.",
                    command=tampil, line=m.baris)
        if not diizinkan:
            raise SqlDitolak(
                "forbidden_meta_command",
                f"Baris {m.baris}: meta-perintah {tampil} tidak diizinkan dari web "
                "(I/O berkas, shell, atau koneksi lain). Pakai Terminal mata kuliah "
                "bila memang dibutuhkan.",
                command=tampil, line=m.baris)
        if nama in _TANPA_ARGUMEN and m.argumen:
            raise SqlDitolak("forbidden_meta_command",
                             f"Baris {m.baris}: {tampil} dengan argumen menulis ke berkas atau "
                             "perintah; pakai tanpa argumen.", command=tampil, line=m.baris)
        if nama in ("set", "unset") and m.argumen:
            var = m.argumen.split(None, 1)[0]
            if not _VAR_PENGGUNA.fullmatch(var):
                raise SqlDitolak(
                    "forbidden_meta_command",
                    f"Baris {m.baris}: variabel khusus psql ({var}) tidak dapat diubah dari "
                    "web; pakai nama variabel huruf kecil.", command=tampil, line=m.baris)
            if nama == "set" and "\\" in m.argumen:
                raise SqlDitolak("forbidden_meta_command",
                                 f"Baris {m.baris}: nilai \\set tidak boleh memuat backslash.",
                                 command=tampil, line=m.baris)
    return metas


# ---------------------------------------------------------------------------
# Keluaran psql
# ---------------------------------------------------------------------------


@dataclass
class _Hasil:
    columns: list[str]
    rows: list[list[str | None]] = field(default_factory=list)
    count: int = 0
    clipped_cells: bool = False


class PenguraiHasil:
    """Urai keluaran ``psql -A -F US -0`` secara bertahap (hemat memori)."""

    def __init__(self, *, max_rows: int = BARIS_BAWAAN, anggaran: int = ANGGARAN_HASIL):
        self.max_rows = max(1, min(int(max_rows), BARIS_MAKS))
        self.anggaran = anggaran
        self.items: list[dict[str, Any]] = []
        self.items_dilewati = 0
        self._buf = b""
        self._hasil: _Hasil | None = None
        self._terpakai = 0
        self.anggaran_habis = False

    # -- umpan --------------------------------------------------------------

    def umpan(self, data: bytes) -> None:
        self._buf += data
        self._proses(akhir=False)

    def selesai(self) -> list[dict[str, Any]]:
        self._proses(akhir=True)
        if self._hasil is not None:
            # psql berhenti di tengah hasil (galat/batal): tampilkan yang ada.
            self._tutup(lengkap=False)
        if self._buf.strip():
            self._status(self._buf.decode("utf-8", "replace").rstrip("\n"))
        self._buf = b""
        return self.items

    # -- internal -----------------------------------------------------------

    def _proses(self, *, akhir: bool) -> None:
        while self._buf:
            if self._hasil is None:
                if self._buf.startswith(_MULAI_TEKS):
                    j = self._buf.find(_AKHIR_TEKS + b"\n")
                    if j < 0:
                        if not akhir:
                            return
                        j = len(self._buf)
                    teks = self._buf[1:j].decode("utf-8", "replace").strip("\n")
                    self._buf = self._buf[j + 2:]
                    if teks:
                        self._tambah({"kind": "text", "text": teks[:BATAS_KELUARAN]})
                    continue
                nl = self._buf.find(b"\n")
                nul = self._buf.find(b"\0")
                if nul >= 0 and (nl < 0 or nul < nl):
                    kepala = self._buf[:nul].decode("utf-8", "replace")
                    self._buf = self._buf[nul + 1:]
                    self._hasil = _Hasil(columns=kepala.split(PEMISAH_KOLOM))
                    continue
                if nl >= 0:
                    self._status(self._buf[:nl].decode("utf-8", "replace"))
                    self._buf = self._buf[nl + 1:]
                    continue
                return  # tunggu data berikut (atau selesai() menangani sisa)
            nul = self._buf.find(b"\0")
            if nul < 0:
                return
            rekaman = self._buf[:nul].decode("utf-8", "replace")
            self._buf = self._buf[nul + 1:]
            m = _FOOTER.match(rekaman)
            if m and int(m.group(1)) == self._hasil.count:
                self._tutup(lengkap=True)
                continue
            self._baris(rekaman)

    def _baris(self, rekaman: str) -> None:
        h = self._hasil
        assert h is not None
        h.count += 1
        if len(h.rows) >= self.max_rows or self.anggaran_habis:
            return
        sel: list[str | None] = []
        for nilai in rekaman.split(PEMISAH_KOLOM):
            if nilai == PENANDA_NULL:
                sel.append(None)
                continue
            if len(nilai) > BATAS_SEL:
                nilai = nilai[:BATAS_SEL] + "…"
                h.clipped_cells = True
            self._terpakai += len(nilai) + 3
            sel.append(nilai)
        if self._terpakai > self.anggaran:
            self.anggaran_habis = True
            return
        h.rows.append(sel)

    def _tutup(self, *, lengkap: bool) -> None:
        h = self._hasil
        assert h is not None
        self._hasil = None
        self._tambah({
            "kind": "result",
            "columns": h.columns,
            "rows": h.rows,
            "rowCount": h.count,
            "truncated": len(h.rows) < h.count,
            "cellsClipped": h.clipped_cells,
            "complete": lengkap,
        })

    def _status(self, teks: str) -> None:
        if teks:
            self._tambah({"kind": "status", "text": teks[:2000]})

    def _tambah(self, item: dict[str, Any]) -> None:
        if len(self.items) >= ITEM_MAKS:
            self.items_dilewati += 1
            return
        self.items.append(item)


_AWAL_PESAN = re.compile(
    r"^psql:[^:\n]*:(\d+): (ERROR|FATAL|PANIC|WARNING|NOTICE|INFO|LOG|DEBUG):  "
    r"(?:([0-9A-Z]{5}): )?(.*)$")
#: Galat sisi klien psql (meta-perintah), mis. ``psql:<stdin>:6: \\crosstabview: …``.
_PSQL_KLIEN = re.compile(r"^psql:[^:\n]*:(\d+): (.*)$")
_LANJUTAN = re.compile(r"^(DETAIL|HINT|CONTEXT|QUERY|LOCATION|STATEMENT):  (.*)$")


def urai_stderr(teks: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[str]]:
    """Pesan server dari stderr psql (``VERBOSITY=verbose``).

    Hasil: ``(notices, error, lain)`` -- ``error`` = ERROR/FATAL pertama;
    ``lain`` = baris yang bukan pesan server (mis. galat koneksi psql).
    """
    pesan: list[dict[str, Any]] = []
    lain: list[str] = []
    kini: dict[str, Any] | None = None
    for baris in teks.splitlines():
        m = _AWAL_PESAN.match(baris)
        if m:
            kini = {"severity": m.group(2), "line": int(m.group(1)),
                    "sqlstate": m.group(3), "message": m.group(4)}
            pesan.append(kini)
            continue
        mk = _PSQL_KLIEN.match(baris)
        if mk:
            kini = {"severity": "ERROR", "line": int(mk.group(1)), "sqlstate": None,
                    "message": re.sub(r"^error: ", "", mk.group(2)), "client": True}
            pesan.append(kini)
            continue
        lj = _LANJUTAN.match(baris)
        if lj and kini is not None:
            kunci = lj.group(1).lower()
            if kunci != "location":  # path sumber C server: tidak berguna bagi mahasiswa
                kini[kunci] = (kini.get(kunci, "") + "\n" + lj.group(2)).strip()
            continue
        if kini is not None and (baris.startswith("LINE ") or baris.startswith(" ")):
            kini["pointer"] = (kini.get("pointer", "") + "\n" + baris).strip("\n")
            continue
        if baris.strip():
            lain.append(baris)
            kini = None
    # ON_ERROR_STOP berhenti di galat yang *terakhir*; pesan "error:" klien
    # sebelumnya (mis. "Did not find any relations.") tidak menghentikan skrip.
    galat = next((p for p in reversed(pesan) if p["severity"] in ("ERROR", "FATAL", "PANIC")), None)
    notices = [p for p in pesan if p is not galat][:100]
    return notices, galat, lain[:20]


def rapikan_teks(teks: str) -> str:
    """Awalan ``psql:<stdin>:N:`` → ``baris N:`` agar keluaran berkas mudah dibaca."""
    return re.sub(r"(?m)^psql:<stdin>:(\d+): ", r"baris \1: ", teks)


# ---------------------------------------------------------------------------
# Proses
# ---------------------------------------------------------------------------


@dataclass
class HasilProses:
    exit_code: int
    stderr: str
    seconds: float
    timed_out: bool = False
    cancelled: bool = False
    output_capped: bool = False


def balik_crlf(sisa: bytes, potong: bytes) -> tuple[bytes, bytes]:
    """Balikkan terjemahan ``\\n`` → ``\\r\\n`` stdout mode teks psql Windows.

    psql Windows menulis stdout dalam mode teks: setiap ``\\n`` -- termasuk di
    dalam nilai kolom -- menjadi ``\\r\\n``, sedangkan ``\\r\\n`` asli menjadi
    ``\\r\\r\\n``. Mengganti ``\\r\\n`` → ``\\n`` adalah kebalikan persisnya.
    ``\\r`` di ujung potongan ditahan (``sisa``) sampai potongan berikutnya.
    Mengembalikan ``(keluaran, sisa_baru)``.
    """
    data = sisa + potong
    sisa_baru = b""
    if data.endswith(b"\r"):
        data, sisa_baru = data[:-1], b"\r"
    return data.replace(b"\r\n", b"\n"), sisa_baru


#: Variabel psql yang dipakai agent sendiri; tidak boleh ditimpa dari web.
VARIABEL_AGENT = frozenset({"ON_ERROR_STOP", "VERBOSITY", "SHOW_CONTEXT"})
#: Variabel yang diisi agent pada ``sql.run_file``: kata sandi peran praktikum, yang
#: dibutuhkan skrip penyambung ``postgres_fdw`` (``password :'sandi'``).
VARIABEL_SANDI = "sandi"
BATAS_VARIABEL = 16
_NAMA_VARIABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")
_NILAI_TERLARANG = re.compile(r"[\x00-\x1f\x7f]")


def variabel_sah(mentah: Any) -> dict[str, str]:
    """Variabel psql (``-v nama=nilai``) kiriman web untuk ``sql.run_file``.

    Padanan ``psql -v batch=1 -f berkas.sql`` di terminal. Nama terbatas pada
    pengenal biasa; nilai satu baris tanpa karakter kendali. Nilainya menjadi satu
    argumen argv (tanpa shell), jadi tanda kutip dan spasi aman apa adanya.
    """
    if mentah is None:
        return {}
    if not isinstance(mentah, Mapping) or len(mentah) > BATAS_VARIABEL:
        raise SqlDitolak("sql_variable_invalid", f"Variabel tidak sah (paling banyak {BATAS_VARIABEL}).")
    keluar: dict[str, str] = {}
    for nama, nilai in mentah.items():
        if not isinstance(nama, str) or not _NAMA_VARIABEL.fullmatch(nama):
            raise SqlDitolak("sql_variable_invalid", f"Nama variabel tidak sah: {str(nama)[:40]!r}.")
        if nama.upper() in VARIABEL_AGENT or nama == VARIABEL_SANDI:
            raise SqlDitolak("sql_variable_invalid", f"Variabel {nama} diisi Local Runner dan tidak dapat diubah.")
        if isinstance(nilai, bool) or not isinstance(nilai, (str, int, float)):
            raise SqlDitolak("sql_variable_invalid", f"Nilai variabel {nama} harus teks atau angka.")
        teks = str(nilai)
        if len(teks) > 400 or _NILAI_TERLARANG.search(teks):
            raise SqlDitolak("sql_variable_invalid", f"Nilai variabel {nama} terlalu panjang atau "
                                                     "memuat karakter yang tidak diizinkan.")
        keluar[nama] = teks
    return keluar


def sandi_pgpass(pgpass: str | Path) -> str | None:
    """Kata sandi di baris pertama berkas pgpass (kolom ke-5), tanpa escape-nya."""
    try:
        baris = next((b for b in Path(pgpass).read_text(encoding="utf-8").splitlines()
                      if b.strip() and not b.lstrip().startswith("#")), "")
    except OSError:
        return None
    kolom = re.split(r"(?<!\\):", baris)
    if len(kolom) < 5:
        return None
    return re.sub(r"\\(.)", r"\1", ":".join(kolom[4:])) or None


def argv_psql(psql: str, *, port: int, database: str, pengguna: str,
              tabel: bool, variabel: Mapping[str, str] | None = None) -> list[str]:
    """argv ``psql`` tanpa shell; SQL selalu lewat stdin (``-f -``)."""
    argv = [psql, "-X", "-h", "localhost", "-p", str(port), "-U", pengguna, "-d", database,
            "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose", "-v", "SHOW_CONTEXT=errors"]
    for nama, nilai in (variabel or {}).items():
        argv += ["-v", f"{nama}={nilai}"]
    if tabel:
        argv += ["-A", "-F", PEMISAH_KOLOM, "-0", "-P", "null=" + PENANDA_NULL,
                 "-P", "footer=on", "-P", "pager=off"]
    else:
        argv += ["-P", "pager=off"]
    return argv + ["-f", "-"]


def lingkungan(env_dasar: Mapping[str, str], *, pgpass: str, batas_detik: int) -> dict[str, str]:
    env = {k: v for k, v in env_dasar.items() if not k.startswith("PG")}
    env["PGPASSFILE"] = pgpass
    env["PGCLIENTENCODING"] = "UTF8"
    env["PGAPPNAME"] = "workbench-sql"
    env["PGCONNECT_TIMEOUT"] = "10"
    # Batas di sisi server: tetap berlaku walau proses psql dimatikan.
    env["PGOPTIONS"] = f"-c statement_timeout={int(batas_detik) * 1000}"
    return env


def jalankan(argv: Sequence[str], *, masukan: bytes, env: Mapping[str, str],
             batas_waktu: float, batal: Callable[[], bool],
             konsumen: Callable[[bytes], None], gabung_stderr: bool = False,
             batas_mentah: int = BATAS_MENTAH) -> HasilProses:
    """Jalankan psql; stdout diumpankan bertahap ke ``konsumen``.

    Batal/batas waktu: SIGINT (psql meneruskannya sebagai permintaan
    pembatalan ke server) → tunggu 3 s → terminate → kill. Windows: grup proses
    baru + CTRL_BREAK (ditangani psql dengan cara yang sama).
    """
    t0 = time.monotonic()
    bendera = 0
    if sys.platform == "win32":
        bendera = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv list, tanpa shell
            list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if gabung_stderr else subprocess.PIPE,
            env=dict(env), creationflags=bendera,
            start_new_session=sys.platform != "win32")
    except OSError as exc:
        raise SqlDitolak("service_not_ready", f"psql tidak dapat dijalankan: {exc}") from exc

    antre: "queue.Queue[bytes | None]" = queue.Queue(maxsize=64)
    galat: list[bytes] = []

    def tulis() -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write(masukan)
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                proc.stdin.close()  # type: ignore[union-attr]
            except OSError:
                pass

    def baca_stdout() -> None:
        assert proc.stdout is not None
        sisa = b""
        while True:
            potong = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
            if not potong:
                break
            if sys.platform == "win32":
                potong, sisa = balik_crlf(sisa, potong)
                if not potong:
                    continue
            antre.put(potong)
        if sisa:
            antre.put(sisa)
        antre.put(None)

    def baca_stderr() -> None:
        assert proc.stderr is not None
        total = 0
        for potong in iter(lambda: proc.stderr.read(8192), b""):  # type: ignore[union-attr]
            if total < BATAS_STDERR:
                galat.append(potong[: BATAS_STDERR - total])
            total += len(potong)

    utas = [threading.Thread(target=tulis, daemon=True),
            threading.Thread(target=baca_stdout, daemon=True)]
    if not gabung_stderr:
        utas.append(threading.Thread(target=baca_stderr, daemon=True))
    for u in utas:
        u.start()

    tenggat = t0 + batas_waktu
    hasil = HasilProses(exit_code=-1, stderr="", seconds=0.0)
    dibaca = 0
    dihentikan = False
    while True:
        try:
            potong = antre.get(timeout=0.2)
        except queue.Empty:
            potong = b""
        if potong is None:
            break
        if potong:
            dibaca += len(potong)
            if dibaca <= batas_mentah:
                konsumen(potong)
            elif not dihentikan:
                hasil.output_capped = True
                _hentikan(proc)
                dihentikan = True
        if not dihentikan:
            if batal():
                hasil.cancelled = True
                _hentikan(proc)
                dihentikan = True
            elif time.monotonic() > tenggat:
                hasil.timed_out = True
                _hentikan(proc)
                dihentikan = True
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    for u in utas[2:]:
        u.join(timeout=5)
    for pipa in (proc.stdout, proc.stderr):
        if pipa is not None:
            try:
                pipa.close()
            except OSError:
                pass
    hasil.exit_code = proc.returncode
    hasil.stderr = b"".join(galat).decode("utf-8", "replace")
    hasil.seconds = round(time.monotonic() - t0, 3)
    return hasil


def _hentikan(proc: subprocess.Popen) -> None:
    """Batalkan kueri dengan sopan, lalu paksa bila psql tidak berhenti."""
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            proc.send_signal(signal.SIGINT)
    except (OSError, ValueError):
        pass

    def paksa() -> None:
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    threading.Thread(target=paksa, daemon=True).start()


# ---------------------------------------------------------------------------
# Katalog
# ---------------------------------------------------------------------------

#: Relasi maksimum di katalog (partisi dihitung di induknya, tidak didaftar).
RELASI_MAKS = 400

SQL_KATALOG = r"""
WITH ns AS (
  SELECT n.oid, n.nspname
  FROM pg_namespace n
  WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
    AND n.nspname NOT LIKE 'pg\_temp\_%' AND n.nspname NOT LIKE 'pg\_toast\_temp\_%'
), rel AS (
  SELECT c.oid, ns.nspname, c.relname, c.relkind,
         CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END AS est,
         obj_description(c.oid, 'pg_class') AS komentar,
         (SELECT count(*) FROM pg_inherits i WHERE i.inhparent = c.oid) AS partisi
  FROM pg_class c JOIN ns ON ns.oid = c.relnamespace
  WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition
    -- objek milik ekstensi (mis. view pg_stat_statements) bukan pekerjaan mahasiswa
    AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass
                    AND d.objid = c.oid AND d.deptype = 'e')
  ORDER BY ns.nspname, c.relname
  LIMIT %(batas)s
)
SELECT json_build_object(
  'schemas', COALESCE((SELECT json_agg(json_build_object(
      'name', ns.nspname,
      'tables', COALESCE((SELECT json_agg(json_build_object(
          'name', r.relname,
          'kind', CASE r.relkind WHEN 'r' THEN 'table' WHEN 'p' THEN 'partitioned'
                  WHEN 'v' THEN 'view' WHEN 'm' THEN 'matview' ELSE 'foreign' END,
          'rowsEstimate', r.est,
          'partitions', r.partisi,
          'comment', r.komentar,
          'columns', COALESCE((SELECT json_agg(json_build_object(
              'name', a.attname,
              'type', format_type(a.atttypid, a.atttypmod),
              'nullable', NOT a.attnotnull,
              'comment', col_description(r.oid, a.attnum)) ORDER BY a.attnum)
            FROM pg_attribute a
            WHERE a.attrelid = r.oid AND a.attnum > 0 AND NOT a.attisdropped), '[]'::json),
          'primaryKey', (SELECT json_agg(a.attname ORDER BY k.ord)
            FROM pg_constraint co
            CROSS JOIN LATERAL unnest(co.conkey) WITH ORDINALITY k(attnum, ord)
            JOIN pg_attribute a ON a.attrelid = co.conrelid AND a.attnum = k.attnum
            WHERE co.conrelid = r.oid AND co.contype = 'p'),
          'foreignKeys', COALESCE((SELECT json_agg(json_build_object(
              'name', co.conname,
              'columns', (SELECT json_agg(a.attname ORDER BY k.ord)
                 FROM unnest(co.conkey) WITH ORDINALITY k(attnum, ord)
                 JOIN pg_attribute a ON a.attrelid = co.conrelid AND a.attnum = k.attnum),
              'refSchema', rn.nspname,
              'refTable', rc.relname,
              'refColumns', (SELECT json_agg(a.attname ORDER BY k.ord)
                 FROM unnest(co.confkey) WITH ORDINALITY k(attnum, ord)
                 JOIN pg_attribute a ON a.attrelid = co.confrelid AND a.attnum = k.attnum))
              ORDER BY co.conname)
            FROM pg_constraint co
            JOIN pg_class rc ON rc.oid = co.confrelid
            JOIN pg_namespace rn ON rn.oid = rc.relnamespace
            WHERE co.conrelid = r.oid AND co.contype = 'f'), '[]'::json))
        ORDER BY r.relname)
        FROM rel r WHERE r.nspname = ns.nspname), '[]'::json))
      ORDER BY ns.nspname)
    FROM ns), '[]'::json),
  'truncated', (SELECT count(*) FROM pg_class c JOIN ns ON ns.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition)
               > %(batas)s,
  'serverVersion', current_setting('server_version'),
  'sizeBytes', pg_database_size(current_database())
);
"""


def sql_katalog() -> str:
    return SQL_KATALOG.replace("%(batas)s", str(RELASI_MAKS))


def urai_katalog(stdout: str) -> dict[str, Any]:
    teks = stdout.strip()
    if not teks:
        raise ValueError("katalog kosong")
    data = json.loads(teks)
    if not isinstance(data, dict) or not isinstance(data.get("schemas"), list):
        raise ValueError("bentuk katalog tidak dikenal")
    return data


__all__ = [
    "ANGGARAN_HASIL", "BARIS_BAWAAN", "BARIS_MAKS", "BATAS_BERKAS", "BATAS_KELUARAN",
    "BATAS_SQL", "HasilProses", "Meta", "PenguraiHasil", "SqlDitolak", "argv_psql",
    "jalankan", "lingkungan", "periksa_meta", "pindai_meta", "rapikan_teks",
    "sql_katalog", "urai_katalog", "urai_stderr",
]

