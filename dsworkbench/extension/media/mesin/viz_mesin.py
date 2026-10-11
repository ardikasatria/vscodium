"""Mesin DuckDB untuk DSWorkbench Viz mandiri (dasbor dari berkas data, tanpa basis data praktikum).

Dijalankan ekstensi sebagai proses anak dari lingkungan Python khusus Viz. Protokol: satu
objek JSON per baris di stdin, satu jawaban JSON per baris di stdout.

    {"id": 1, "op": "buka", "berkas": ["/jalur/penjualan.parquet"]}
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


def buka(berkas):
    """Muat berkas data sebagai ``sumber`` lalu kunci akses ke luar."""
    global _con
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
        raise ValueError("Jenis berkas belum didukung. Pilih berkas CSV/TSV saja, atau Parquet saja.")
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
    if parquet:
        # Parquet dibaca di tempat (view): cepat dan tidak digandakan.
        con.execute("CREATE VIEW sumber AS SELECT * FROM read_parquet(%s, union_by_name = true)" % _daftar(jalur))
        con.execute("SET allowed_paths = %s" % _daftar(jalur))
    else:
        # CSV disalin sekali ke tabel kolumnar; kueri berikutnya tidak membaca ulang teksnya.
        con.execute("CREATE TABLE sumber AS SELECT * FROM read_csv(%s, union_by_name = true, sample_size = 50000)" % _daftar(jalur))
    con.execute("SET allowed_directories = %s" % _daftar([_sementara]))
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    _con = con
    kolom = [{"nama": r[0], "tipe": r[1]} for r in con.execute("DESCRIBE sumber").fetchall()]
    return {"kolom": kolom, "disalin": not parquet}


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
                    jawab = buka(p.get("berkas"))
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
