"""Mesin DuckDB untuk DSWorkbench Viz mandiri (dasbor dari berkas data, tanpa basis data praktikum).

Dijalankan ekstensi sebagai proses anak dari lingkungan Python khusus Viz. Protokol: satu
objek JSON per baris di stdin, satu jawaban JSON per baris di stdout.

    {"id": 1, "op": "buka", "berkas": ["/jalur/penjualan.parquet"]}
    {"id": 1, "op": "intip", "berkas": [...]}                      (kolom tiap berkas, tanpa memuat)
    {"id": 1, "op": "buka", "tabel": [{"nama": "penjualan", "berkas": [...]}, {"nama": "produk", "berkas": [...]}],
     "gabung": [{"kiri": "penjualan", "kolomKiri": "produk_id", "kanan": "produk", "kolomKanan": "produk_id", "jenis": "left"}]}
    (kunci gabungan: tambah "tambahan": [{"kiri": "kolom_kiri", "kanan": "kolom_kanan"}, ...] pada hubungan itu)
    {"id": 2, "op": "kueri", "sql": "SELECT ... FROM sumber ...", "maks": 1000}

Berkas pilihan pengguna dibuka sebagai tabel/view bernama ``sumber``. Sesudah itu akses DuckDB
ke luar dikunci: hanya berkas itu yang terbaca, tidak ada jaringan, tidak ada ekstensi yang
dipasang, dan konfigurasinya tidak dapat diubah lagi. Hanya SATU pernyataan SELECT per
permintaan. Skrip ini hanya memakai pustaka standar dan ``duckdb``.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import json
import math
import os
import re
import shutil
import sys
import tempfile

import duckdb

AKHIRAN_CSV = (".csv", ".tsv", ".txt")
AKHIRAN_PARQUET = (".parquet", ".pq")
BERKAS_MAKS = 64
BARIS_MAKS = 1000
SEL_MAKS = 4000

_sementara = tempfile.mkdtemp(prefix="dsw-viz-")
_con: duckdb.DuckDBPyConnection | None = None


def _teks(v):
    """Nilai sel sebagai teks (seperti hasil psql), ``None`` untuk NULL."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return repr(v)
    if isinstance(v, decimal.Decimal):
        return format(v, "f")
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat(sep=" ") if isinstance(v, _dt.datetime) else v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "(biner)"
    s = str(v)
    return s if len(s) <= SEL_MAKS else s[:SEL_MAKS] + "…"


def _tutup():
    global _con
    if _con is not None:
        try:
            _con.close()
        except Exception:
            pass
        _con = None


NAMA_TABEL = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")
TABEL_MAKS = 12
KUNCI_MAKS = 6
JENIS_GABUNG = {"left": "LEFT JOIN", "inner": "INNER JOIN"}


def _qi(nama):
    return '"' + str(nama).replace('"', '""') + '"'


def _jalur_sah(berkas):
    """Jalur berkas data yang ada dan berjenis dikenal; mengembalikan (jalur nyata, parquet?)."""
    if not isinstance(berkas, list) or not berkas or len(berkas) > BERKAS_MAKS:
        raise ValueError("Pilih satu sampai %d berkas data." % BERKAS_MAKS)
    jalur = []
    for b in berkas:
        if not isinstance(b, str) or "\x00" in b or not os.path.isabs(b) or not os.path.isfile(b):
            raise ValueError("Berkas data tidak ditemukan: %s" % (b if isinstance(b, str) else "?"))
        jalur.append(os.path.realpath(b))
    akhiran = {os.path.splitext(j)[1].lower() for j in jalur}
    parquet = akhiran <= set(AKHIRAN_PARQUET)
    if not parquet and not akhiran <= set(AKHIRAN_CSV):
        raise ValueError("Jenis berkas belum didukung. Satu tabel berisi berkas CSV/TSV saja, atau Parquet saja.")
    return jalur, parquet


def _pembaca(jalur, parquet):
    return ("read_parquet(%s, union_by_name = true)" if parquet else "read_csv(%s, union_by_name = true, sample_size = 50000)") % _daftar(jalur)


def intip(berkas):
    """Kolom tiap berkas TANPA memuat isinya (untuk mengelompokkan berkas dan menyusun JOIN)."""
    if not isinstance(berkas, list) or not berkas or len(berkas) > BERKAS_MAKS:
        raise ValueError("Pilih satu sampai %d berkas data." % BERKAS_MAKS)
    hasil = []
    con = duckdb.connect(":memory:")
    try:
        con.execute("SET autoinstall_known_extensions = false")
        con.execute("SET autoload_known_extensions = false")
        for b in berkas:
            jalur, parquet = _jalur_sah([b])
            kolom = [{"nama": r[0], "tipe": r[1]} for r in con.execute("DESCRIBE SELECT * FROM " + _pembaca(jalur, parquet)).fetchall()]
            hasil.append({"berkas": b, "kolom": kolom})
    finally:
        con.close()
    return {"berkas": hasil}


def buka(berkas, tabel=None, gabung=None):
    """Muat berkas data sebagai ``sumber`` lalu kunci akses ke luar.

    Bentuk sederhana: ``berkas`` = daftar berkas berskema sama (ditumpuk). Bentuk bertabel:
    ``tabel`` = [{"nama", "berkas": [...]}] (tabel pertama = dasar) dan ``gabung`` =
    [{"kiri", "kolomKiri", "kanan", "kolomKanan", "jenis"}]; hasil JOIN-nya menjadi ``sumber``.
    """
    global _con
    if tabel is None:
        tabel = [{"nama": "t0", "berkas": berkas}]
    if not isinstance(tabel, list) or not tabel or len(tabel) > TABEL_MAKS:
        raise ValueError("Pilih satu sampai %d tabel." % TABEL_MAKS)
    daftar = []
    for t in tabel:
        if not isinstance(t, dict) or not isinstance(t.get("nama"), str) or not NAMA_TABEL.match(t["nama"]) or t["nama"] == "sumber":
            raise ValueError("Nama tabel tidak sah.")
        if t["nama"] in {d[0] for d in daftar}:
            raise ValueError("Nama tabel kembar: %s" % t["nama"])
        jalur, parquet = _jalur_sah(t.get("berkas"))
        daftar.append((t["nama"], jalur, parquet))
    if sum(len(d[1]) for d in daftar) > BERKAS_MAKS:
        raise ValueError("Terlalu banyak berkas (paling banyak %d)." % BERKAS_MAKS)
    _tutup()
    for sisa in os.listdir(_sementara):
        try:
            os.remove(os.path.join(_sementara, sisa))
        except OSError:
            shutil.rmtree(os.path.join(_sementara, sisa), ignore_errors=True)
    # Basis data sementara di disk: berkas CSV besar tidak harus muat di memori.
    con = duckdb.connect(os.path.join(_sementara, "viz.duckdb"))
    con.execute("SET autoinstall_known_extensions = false")
    con.execute("SET autoload_known_extensions = false")
    con.execute("SET temp_directory = ?", [os.path.join(_sementara, "tmp")])
    try:
        kolom_tabel = {}
        for nama, jalur, parquet in daftar:
            # Parquet dibaca di tempat (view); CSV disalin sekali ke tabel kolumnar.
            con.execute("CREATE %s %s AS SELECT * FROM %s" % ("VIEW" if parquet else "TABLE", _qi(nama), _pembaca(jalur, parquet)))
            kolom_tabel[nama] = [{"nama": r[0], "tipe": r[1]} for r in con.execute("DESCRIBE " + _qi(nama)).fetchall()]
        con.execute("CREATE VIEW sumber AS " + _sql_gabung(daftar, kolom_tabel, gabung or []))
        parket = [j for _, jalur, parquet in daftar if parquet for j in jalur]
        if parket:
            con.execute("SET allowed_paths = %s" % _daftar(parket))
        con.execute("SET allowed_directories = %s" % _daftar([_sementara]))
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        kolom = [{"nama": r[0], "tipe": r[1]} for r in con.execute("DESCRIBE sumber").fetchall()]
        # JOIN dapat menggandakan baris (kunci kanan tidak unik): cacahnya dilaporkan agar pengguna diberi tahu.
        baris = None
        if len(daftar) > 1:
            baris = {"dasar": con.execute("SELECT count(*) FROM " + _qi(daftar[0][0])).fetchone()[0], "sumber": con.execute("SELECT count(*) FROM sumber").fetchone()[0]}
    except Exception:
        con.close()
        raise
    _con = con
    return {"kolom": kolom, "baris": baris, "tabel": [{"nama": n, "kolom": kolom_tabel[n]} for n, _, _ in daftar], "disalin": not all(p for _, _, p in daftar)}


def _sql_gabung(daftar, kolom_tabel, gabung):
    """SELECT yang menjadi ``sumber``: tabel dasar, lalu tiap tabel lain dihubungkan dengan JOIN.

    Nama kolom yang sudah ada diberi akhiran ``_<tabel>``; kolom kunci di sisi kanan yang
    namanya sama dengan kunci kirinya tidak diulang.
    """
    dasar = daftar[0][0]
    if len(daftar) == 1:
        return "SELECT * FROM " + _qi(dasar)
    if not isinstance(gabung, list) or len(gabung) != len(daftar) - 1:
        raise ValueError("Setiap tabel tambahan harus dihubungkan tepat satu kali.")
    ada = {dasar}
    pakai = set()
    pilih = []
    for k in kolom_tabel[dasar]:
        pilih.append("%s.%s AS %s" % (_qi(dasar), _qi(k["nama"]), _qi(k["nama"])))
        pakai.add(k["nama"].lower())
    klausa = []
    for g in gabung:
        if not isinstance(g, dict):
            raise ValueError("Hubungan tabel tidak sah.")
        kiri, kanan, kk, kn = g.get("kiri"), g.get("kanan"), g.get("kolomKiri"), g.get("kolomKanan")
        jenis = JENIS_GABUNG.get(g.get("jenis", "left"))
        if kiri not in ada or kanan not in kolom_tabel or kanan in ada or jenis is None:
            raise ValueError("Hubungan tabel tidak sah: tabel kiri harus sudah terhubung dan tabel kanan belum.")
        # Kunci gabungan: pasangan pertama + "tambahan"; semua pasangan harus cocok (AND).
        tambahan = g.get("tambahan") or []
        if not isinstance(tambahan, list) or len(tambahan) > KUNCI_MAKS - 1 or not all(isinstance(t, dict) for t in tambahan):
            raise ValueError("Kolom penghubung tambahan tidak sah.")
        pasangan = [(kk, kn)] + [(t.get("kiri"), t.get("kanan")) for t in tambahan]
        nama_kiri = {c["nama"] for c in kolom_tabel[kiri]}
        nama_kanan = {c["nama"] for c in kolom_tabel[kanan]}
        if any(a not in nama_kiri or b not in nama_kanan for a, b in pasangan) or len(set(pasangan)) != len(pasangan):
            raise ValueError("Kolom penghubung tidak ada di tabelnya.")
        syarat = " AND ".join("%s.%s = %s.%s" % (_qi(kiri), _qi(a), _qi(kanan), _qi(b)) for a, b in pasangan)
        klausa.append("%s %s ON %s" % (jenis, _qi(kanan), syarat))
        # Kolom kunci kanan yang namanya sama dengan kunci kirinya tidak diulang di hasil.
        kembar = {b for a, b in pasangan if a.lower() == b.lower()}
        for k in kolom_tabel[kanan]:
            if k["nama"] in kembar:
                continue
            alias = k["nama"] if k["nama"].lower() not in pakai else "%s_%s" % (k["nama"], kanan)
            while alias.lower() in pakai:
                alias += "_"
            pakai.add(alias.lower())
            pilih.append("%s.%s AS %s" % (_qi(kanan), _qi(k["nama"]), _qi(alias)))
        ada.add(kanan)
    return "SELECT " + ", ".join(pilih) + " FROM " + _qi(dasar) + " " + " ".join(klausa)


def _daftar(jalur):
    return "[" + ", ".join("'" + j.replace("'", "''") + "'" for j in jalur) + "]"


def kueri(sql, maks):
    if _con is None:
        raise ValueError("Belum ada berkas data yang dibuka.")
    if not isinstance(sql, str) or len(sql) > 200_000:
        raise ValueError("Kueri tidak sah.")
    pernyataan = duckdb.extract_statements(sql)
    if len(pernyataan) != 1 or pernyataan[0].type != duckdb.StatementType.SELECT:
        raise ValueError("Hanya satu pernyataan SELECT yang dijalankan.")
    batas = max(1, min(int(maks) if isinstance(maks, int) else BARIS_MAKS, BARIS_MAKS))
    hasil = _con.execute(sql)
    kolom = [d[0] for d in hasil.description]
    baris = hasil.fetchmany(batas + 1)
    return {"kolom": kolom, "baris": [[_teks(v) for v in r] for r in baris[:batas]], "terpotong": len(baris) > batas}


def main() -> int:
    print(json.dumps({"siap": True, "duckdb": duckdb.__version__}), flush=True)
    try:
        for baris in sys.stdin:
            baris = baris.strip()
            if not baris:
                continue
            id_ = None
            try:
                p = json.loads(baris)
                id_ = p.get("id")
                op = p.get("op")
                if op == "buka":
                    jawab = buka(p.get("berkas"), p.get("tabel"), p.get("gabung"))
                elif op == "intip":
                    jawab = intip(p.get("berkas"))
                elif op == "kueri":
                    jawab = kueri(p.get("sql"), p.get("maks"))
                elif op == "tutup":
                    break
                else:
                    raise ValueError("Operasi tidak dikenal.")
                print(json.dumps({"id": id_, "ok": True, **jawab}, ensure_ascii=False), flush=True)
            except Exception as e:  # jawaban galat, proses tetap hidup
                print(json.dumps({"id": id_, "ok": False, "pesan": str(e)[:600]}, ensure_ascii=False), flush=True)
    finally:
        _tutup()
        shutil.rmtree(_sementara, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
