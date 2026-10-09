"""Layanan PostgreSQL yang dikelola agent (ADR-053).

Dua lapis:

* **Biner** (`requirements/services.json`): bundel per platform yang diunduh,
  diverifikasi `sha256`, dan diekstrak sekali ke direktori state agent -- atau
  PostgreSQL sistem (Linux). Hanya lewat tindakan lokal (``ensure-env``), tidak
  pernah dari peramban (ADR-041 §5).
* **Klaster per mata kuliah**: dideklarasikan package (`course.yaml
  spec.services.postgres`), disisipkan Control API ke payload. Dibuat
  (``initdb``) dan dinyalakan saat ``service.start``; konfigurasi keamanan
  (loopback, scram, tanpa socket Unix) ditulis agent dan tidak dapat diubah
  package.

Tidak ada id mata kuliah, nama basis data, atau port di modul ini: semuanya
data. Sandi dibangkitkan per laptop dan tidak pernah keluar dari laptop --
bukan di hasil operasi, bukan di log agent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from .errors import OperationRejectedError

BERKAS_LAYANAN = "services.json"
BERKAS_PORT = "ports.json"
ADMIN = "workbench_admin"
PERAN_MAHASISWA = "praktikum"

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_NILAI_ENV = re.compile(r"^[A-Za-z0-9_.:/@-]{0,200}$")
_COMPOSE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
AKSES = ("read", "owner")

#: Batas waktu subprocess (detik).
WAKTU_INITDB = 180
WAKTU_PG_CTL = 90
WAKTU_PSQL = 120
#: Satu tabel/skrip pada pemuatan data (dataset sedang: jutaan baris).
WAKTU_MUAT = 3600

#: Port pengganti bila port bawaan klaster terpakai: bawaan + LANGKAH_PORT * i.
LANGKAH_PORT = 10
JUMLAH_PORT_PENGGANTI = 20


class LayananGagal(Exception):
    """Kegagalan terstruktur: ``code`` dikirim di payload, ``pesan`` berbahasa Indonesia."""

    def __init__(self, code: str, pesan: str, **tambahan: Any):
        super().__init__(pesan)
        self.code = code
        self.pesan = pesan
        self.tambahan = tambahan

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, **self.tambahan}


# --------------------------------------------------------------------------
# Definisi layanan (services.json)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SumberPlatform:
    source: str  # bundle | system
    url: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    major: int | None = None


@dataclass(frozen=True)
class DefinisiLayanan:
    id: str
    nama: str
    versi: str
    biner_wajib: tuple[str, ...]
    ekstensi_wajib: tuple[str, ...]
    platform: Mapping[str, SumberPlatform]

    @property
    def mayor(self) -> int:
        return int(self.versi.split(".", 1)[0])


def _baca_sumber(kunci: str, isi: Mapping[str, Any]) -> SumberPlatform:
    src = isi.get("source")
    if src == "bundle":
        url, sha, uk = isi.get("url"), isi.get("sha256"), isi.get("sizeBytes")
        if not (isinstance(url, str) and url.startswith("https://")):
            raise ValueError(f"{kunci}: url bundel wajib https")
        if not (isinstance(sha, str) and _SHA.fullmatch(sha)):
            raise ValueError(f"{kunci}: sha256 bundel tidak sah")
        if not (isinstance(uk, int) and uk > 0):
            raise ValueError(f"{kunci}: sizeBytes bundel tidak sah")
        return SumberPlatform("bundle", url=url, sha256=sha, size_bytes=uk)
    if src == "system":
        mayor = isi.get("major")
        if not (isinstance(mayor, int) and mayor > 0):
            raise ValueError(f"{kunci}: major wajib untuk sumber sistem")
        return SumberPlatform("system", major=mayor)
    raise ValueError(f"{kunci}: source harus 'bundle' atau 'system'")


def muat_layanan(akar_agent: Path) -> dict[str, DefinisiLayanan]:
    """Baca ``requirements/services.json``; berkas tidak ada = tidak ada layanan."""
    path = akar_agent / "requirements" / BERKAS_LAYANAN
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    hasil: dict[str, DefinisiLayanan] = {}
    for sid, isi in (data.get("services") or {}).items():
        if not _SLUG.fullmatch(sid):
            raise ValueError(f"id layanan tidak sah: {sid!r}")
        hasil[sid] = DefinisiLayanan(
            id=sid,
            nama=str(isi["name"]),
            versi=str(isi["version"]),
            biner_wajib=tuple(isi["requiredBinaries"]),
            ekstensi_wajib=tuple(isi["requiredExtensions"]),
            platform={k: _baca_sumber(k, v) for k, v in (isi.get("platforms") or {}).items()},
        )
    return hasil


def _exe(nama: str) -> str:
    return nama + ".exe" if sys.platform == "win32" else nama


def _dir_ekstensi(akar_bin: Path) -> list[Path]:
    """Lokasi `*.control` relatif terhadap `bin/` (bundel EDB vs paket sistem)."""
    induk = akar_bin.parent
    return [induk / "share" / "extension", induk / "share" / "postgresql" / "extension",
            Path(f"/usr/share/postgresql/{induk.name}/extension")]


def periksa_biner(defn: DefinisiLayanan, akar_bin: Path) -> None:
    hilang = [b for b in defn.biner_wajib if not (akar_bin / _exe(b)).is_file()]
    if hilang:
        raise LayananGagal("service_not_ready",
                           f"Berkas {defn.nama} tidak lengkap: {', '.join(hilang)} tidak ada.",
                           missing=hilang)
    dirs = _dir_ekstensi(akar_bin)
    kurang = [e for e in defn.ekstensi_wajib
              if not any((d / f"{e}.control").is_file() for d in dirs)]
    if kurang:
        raise LayananGagal("service_not_ready",
                           f"Ekstensi {defn.nama} tidak lengkap: {', '.join(kurang)}.",
                           missingExtensions=kurang)


def periksa_vc_runtime(akar_bin: Path) -> None:
    """Windows: biner EDB butuh VCRUNTIME140 dan MSVCP140 (spike PGD-00)."""
    if sys.platform != "win32":
        return
    sistem = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    kurang = [d for d in ("vcruntime140.dll", "msvcp140.dll")
              if not (akar_bin / d).is_file() and not (sistem / d).is_file()]
    if kurang:
        raise LayananGagal(
            "vc_runtime_missing",
            "Komputer ini belum memiliki Microsoft Visual C++ Redistributable "
            f"({', '.join(kurang)} tidak ditemukan). Pasang \"Visual C++ "
            "Redistributable for Visual Studio 2015-2022 (x64)\" dari situs resmi "
            "Microsoft, lalu ulangi.",
            hint="https://aka.ms/vs/17/release/vc_redist.x64.exe", missing=kurang)


def _dir_sistem(mayor: int) -> list[Path]:
    return [Path(f"/usr/lib/postgresql/{mayor}/bin"), Path(f"/usr/pgsql-{mayor}/bin")]


def lokasi_biner(defn: DefinisiLayanan, state_dir: Path, kunci_platform: str) -> Path | None:
    """Direktori `bin/` yang siap dipakai, tanpa mengunduh apa pun; None = belum."""
    src = defn.platform.get(kunci_platform)
    if src is None:
        return None
    if src.source == "system":
        for d in _dir_sistem(src.major or defn.mayor):
            try:
                periksa_biner(defn, d)
                return d
            except LayananGagal:
                continue
        return None
    tujuan = _dir_terpasang(defn, state_dir)
    penanda = tujuan / ".workbench-installed"
    if not penanda.is_file() or penanda.read_text().strip() != src.sha256:
        return None
    return tujuan / "bin"


def _dir_terpasang(defn: DefinisiLayanan, state_dir: Path) -> Path:
    return state_dir / "services" / "bin" / f"{defn.id}-{defn.versi}"


def _ekstrak_aman(arsip: Path, tujuan: Path) -> None:
    """Ekstrak bundel: tolak path absolut, `..`, backslash, dan symlink di ZIP."""
    with zipfile.ZipFile(arsip) as z:
        for info in z.infolist():
            nama = info.filename
            bagian = PurePosixPath(nama).parts
            if (not nama or nama.startswith("/") or "\\" in nama or ":" in bagian[0]
                    or any(b in ("..", "") for b in bagian)):
                raise LayananGagal("checksum_mismatch", f"Bundel memuat path tidak aman: {nama!r}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode and mode not in (0o100000, 0o040000):
                raise LayananGagal("checksum_mismatch", f"Bundel memuat entri khusus: {nama!r}")
            if info.is_dir():
                (tujuan / nama).mkdir(parents=True, exist_ok=True)
                continue
            target = tujuan / nama
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as sumber, open(target, "wb") as keluar:
                shutil.copyfileobj(sumber, keluar, 1024 * 1024)
            if os.name == "posix":
                os.chmod(target, 0o755 if (info.external_attr >> 16) & 0o111 else 0o644)
    meta_path = tujuan / "bundle.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    for rel, sasaran in (meta.get("symlinks") or {}).items():
        bagian = PurePosixPath(rel).parts
        if (not isinstance(sasaran, str) or "/" in sasaran or "\\" in sasaran
                or sasaran in ("", ".", "..") or any(b in ("..", "") for b in bagian)
                or rel.startswith("/")):
            raise LayananGagal("checksum_mismatch", f"Tautan bundel tidak aman: {rel!r}")
        link = tujuan / rel
        asli = link.parent / sasaran
        if not asli.is_file():
            raise LayananGagal("checksum_mismatch", f"Tautan bundel menunjuk berkas tak ada: {rel!r}")
        if link.exists() or link.is_symlink():
            link.unlink()
        if os.name == "posix":
            os.symlink(sasaran, link)
        else:  # pragma: no cover - bundel Windows tidak punya tautan
            shutil.copy2(asli, link)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for potong in iter(lambda: f.read(1024 * 1024), b""):
            h.update(potong)
    return h.hexdigest()


def pasang(defn: DefinisiLayanan, state_dir: Path, kunci_platform: str,
           unduh: Callable[[str, Path], None], *, lapor: Callable[[str], None] = lambda s: None,
           ) -> Path:
    """Pastikan biner layanan siap; unduh bundel bila perlu. Idempoten."""
    src = defn.platform.get(kunci_platform)
    if src is None:
        raise LayananGagal("service_unsupported",
                           f"{defn.nama} belum tersedia untuk platform {kunci_platform}.",
                           platform=kunci_platform)
    ada = lokasi_biner(defn, state_dir, kunci_platform)
    if ada is not None:
        periksa_biner(defn, ada)
        periksa_vc_runtime(ada)
        return ada
    if src.source == "system":
        raise LayananGagal(
            "system_postgres_missing",
            f"{defn.nama} belum terpasang di sistem. Pasang paket PostgreSQL {src.major} "
            "dari repositori resmi PostgreSQL (PGDG), misalnya "
            f"`sudo apt install postgresql-{src.major} postgresql-client-{src.major}`, "
            "lalu ulangi. Agent tidak memasang paket sistem.",
            hint=f"postgresql-{src.major}")
    tujuan = _dir_terpasang(defn, state_dir)
    kerja = state_dir / "services" / "tmp"
    kerja.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=kerja) as tmp:
        arsip = Path(tmp) / "bundle.zip"
        lapor(f"Mengunduh {defn.nama} ({(src.size_bytes or 0) / 1e6:.0f} MB)...")
        unduh(str(src.url), arsip)
        if arsip.stat().st_size != src.size_bytes or _sha256(arsip) != src.sha256:
            raise LayananGagal("checksum_mismatch",
                               f"Unduhan {defn.nama} tidak cocok dengan checksum yang dipatok; "
                               "tidak ada berkas yang dipasang.")
        lapor("Mengekstrak...")
        ekstrak = Path(tmp) / "x"
        ekstrak.mkdir()
        _ekstrak_aman(arsip, ekstrak)
        periksa_biner(defn, ekstrak / "bin")
        (ekstrak / ".workbench-installed").write_text(str(src.sha256) + "\n")
        if tujuan.exists():
            shutil.rmtree(tujuan)
        tujuan.parent.mkdir(parents=True, exist_ok=True)
        os.replace(ekstrak, tujuan)
    periksa_vc_runtime(tujuan / "bin")
    return tujuan / "bin"


# --------------------------------------------------------------------------
# Spesifikasi klaster (payload dari Control API)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Klaster:
    alias: str
    port: int
    databases: tuple[str, ...]
    akses: str
    ekstensi: tuple[str, ...] = ()
    env_prefix: str | None = None
    env_databases: Mapping[str, str] = field(default_factory=dict)
    #: Port yang dideklarasikan package; ``port`` bisa berupa port pengganti.
    port_bawaan: int | None = None
    #: Nama layanan docker-compose naskah yang diarahkan ke klaster ini.
    compose_service: str | None = None

    @property
    def bawaan(self) -> int:
        return self.port if self.port_bawaan is None else self.port_bawaan

    def info_port(self) -> dict[str, int]:
        return {"port": self.port, **({"defaultPort": self.bawaan} if self.bawaan != self.port else {})}


@dataclass(frozen=True)
class SpesifikasiLayanan:
    course_id: str
    service_id: str
    klaster: tuple[Klaster, ...]
    env: Mapping[str, str] = field(default_factory=dict)

    def pilih(self, alias: Any) -> tuple[Klaster, ...]:
        if alias in (None, ""):
            return self.klaster
        hasil = tuple(k for k in self.klaster if k.alias == alias)
        if not hasil:
            raise OperationRejectedError(f"Klaster '{str(alias)[:40]}' tidak dikenal.")
        return hasil


def spesifikasi_dari(payload: Mapping[str, Any], defn: DefinisiLayanan) -> SpesifikasiLayanan:
    """Validasi blok ``services`` yang disisipkan Control API (kontrak §1.2)."""
    blok = payload.get("services")
    cid = payload.get("courseId")
    if not isinstance(cid, str) or not _SLUG.fullmatch(cid):
        raise OperationRejectedError("courseId wajib berupa slug mata kuliah.")
    if not isinstance(blok, Mapping) or not isinstance(blok.get("postgres"), Mapping):
        raise OperationRejectedError("Mata kuliah ini tidak mendeklarasikan layanan PostgreSQL.")
    pg = blok["postgres"]
    if str(pg.get("version", "")).split(".", 1)[0] != str(defn.mayor):
        raise OperationRejectedError(
            f"Mata kuliah meminta PostgreSQL {pg.get('version')}, agent menyediakan {defn.versi}.")
    daftar, port_dipakai, alias_dipakai, compose_dipakai = [], set(), set(), {None}
    for k in pg.get("clusters") or []:
        if not isinstance(k, Mapping):
            raise OperationRejectedError("Deklarasi klaster tidak sah.")
        alias, port = k.get("alias"), k.get("port")
        if not isinstance(alias, str) or not _SLUG.fullmatch(alias) or alias in alias_dipakai:
            raise OperationRejectedError("alias klaster tidak sah atau ganda.")
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535 \
                or port in port_dipakai:
            raise OperationRejectedError("port klaster harus 1024–65535 dan unik.")
        dbs = k.get("databases") or []
        if not dbs or not all(isinstance(d, str) and _IDENT.fullmatch(d) for d in dbs) \
                or len(set(dbs)) != len(dbs) or any(d in ("postgres", "template0", "template1")
                                                     for d in dbs):
            raise OperationRejectedError("nama basis data klaster tidak sah.")
        akses = k.get("studentAccess")
        if akses not in AKSES:
            raise OperationRejectedError("studentAccess harus 'read' atau 'owner'.")
        eks = tuple(k.get("extensions") or ())
        if any(e not in defn.ekstensi_wajib for e in eks):
            raise OperationRejectedError("ekstensi di luar yang disediakan layanan.")
        prefix = k.get("envPrefix")
        if prefix is not None and (not isinstance(prefix, str) or not _ENV.fullmatch(prefix)):
            raise OperationRejectedError("envPrefix tidak sah.")
        env_db = k.get("envDatabases") or {}
        if not isinstance(env_db, Mapping) or not all(
                isinstance(a, str) and _ENV.fullmatch(a) and b in dbs for a, b in env_db.items()):
            raise OperationRejectedError("envDatabases tidak sah.")
        compose = k.get("composeService")
        if compose is not None and (not isinstance(compose, str) or not _COMPOSE.fullmatch(compose)
                                    or compose in compose_dipakai):
            raise OperationRejectedError("composeService tidak sah atau ganda.")
        port_dipakai.add(port)
        alias_dipakai.add(alias)
        compose_dipakai.add(compose)
        daftar.append(Klaster(alias, port, tuple(dbs), akses, eks, prefix, dict(env_db),
                              compose_service=compose))
    if not daftar:
        raise OperationRejectedError("Tidak ada klaster yang dideklarasikan.")
    env = pg.get("env") or {}
    if not isinstance(env, Mapping) or not all(
            isinstance(a, str) and _ENV.fullmatch(a) and isinstance(b, str)
            and _NILAI_ENV.fullmatch(b) for a, b in env.items()):
        raise OperationRejectedError("env layanan tidak sah.")
    return SpesifikasiLayanan(cid, defn.id, tuple(daftar), dict(env))


# --------------------------------------------------------------------------
# Klaster
# --------------------------------------------------------------------------

def _tulis_rahasia(path: Path, isi: str) -> None:
    """Tulis atomik dengan izin 0600 (Windows: izin bawaan profil pengguna)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sementara = path.with_name(path.name + ".tmp")
    fd = os.open(sementara, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(isi)
    os.replace(sementara, path)
    if os.name == "posix":
        os.chmod(path, 0o600)


def _sql_literal(teks: str) -> str:
    return "'" + teks.replace("'", "''") + "'"


def _port_bebas(port: int) -> bool:
    for keluarga, alamat in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        s = socket.socket(keluarga, socket.SOCK_STREAM)
        try:
            if keluarga == socket.AF_INET6 and not socket.has_ipv6:
                continue
            s.bind((alamat, port))
        except OSError:
            if keluarga == socket.AF_INET6:
                continue  # IPv6 loopback mungkin mati; IPv4 yang menentukan
            return False
        finally:
            s.close()
    return True


def jalur_aman_windows(p: Path) -> Path:
    """Windows: path berhuruf non-ASCII → nama pendek 8.3 bila tersedia.

    PostgreSQL di Windows memperlakukan path sebagai teks kode halaman ANSI;
    ``initdb`` menulis path direktori data ke katalog UTF-8 dan gagal
    (``invalid byte sequence for encoding "UTF8"``) bila path memuat mis. "ñ"
    dari nama pengguna Windows. Nama pendek menunjuk folder yang sama.
    Di luar Windows, atau bila path sudah ASCII, path dikembalikan apa adanya.
    """
    if sys.platform != "win32" or str(p).isascii():
        return p
    try:
        import ctypes
        from ctypes import wintypes

        fungsi = ctypes.windll.kernel32.GetShortPathNameW  # type: ignore[attr-defined]
        fungsi.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        fungsi.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(32768)
        n = fungsi(str(p), buf, len(buf))
        if 0 < n < len(buf) and buf.value.isascii():
            return Path(buf.value)
    except (OSError, AttributeError, ValueError):
        pass
    return p


class KelolaPostgres:
    """Klaster PostgreSQL per mata kuliah di direktori state agent."""

    def __init__(self, *, state_dir: Path, akar_workspace: Path,
                 lokasi_bin: Callable[[DefinisiLayanan], Path | None],
                 venv_bin: Callable[[DefinisiLayanan], Path | None] = lambda d: None):
        self.state_dir = state_dir
        self.akar_workspace = akar_workspace
        self._lokasi_bin = lokasi_bin
        self._venv_bin = venv_bin

    # -- path ---------------------------------------------------------------

    def _dasar(self, spec: SpesifikasiLayanan) -> Path:
        return self.state_dir / "services" / spec.course_id

    def _pgdata(self, spec: SpesifikasiLayanan, k: Klaster) -> Path:
        return self._dasar(spec) / k.alias

    def _bin(self, defn: DefinisiLayanan) -> Path:
        b = self._lokasi_bin(defn)
        if b is None:
            raise LayananGagal(
                "service_not_ready",
                f"{defn.nama} belum dipasang di komputer ini. Buka DSWorkbench dan pilih "
                "\"Siapkan lingkungan\" untuk mata kuliah ini (atau jalankan "
                "`workbench-agent ensure-env --profile <profil mata kuliah>`).",
                hint="ensure-env")
        return b

    # -- rahasia -----------------------------------------------------------

    def _rahasia(self, spec: SpesifikasiLayanan) -> dict[str, str]:
        path = self._dasar(spec) / "secrets.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("admin"), str) and isinstance(data.get("student"), str):
                return data
        data = {"admin": secrets.token_urlsafe(24), "student": secrets.token_urlsafe(18),
                "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _tulis_rahasia(path, json.dumps(data, indent=1) + "\n")
        return data

    # -- port pengganti -------------------------------------------------------

    def _baca_port(self, dasar: Path) -> dict[str, dict[str, int]]:
        """``ports.json``: ``{alias: {"default": bawaan, "port": pengganti}}``."""
        try:
            data = json.loads((dasar / BERKAS_PORT).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {a: e for a, e in data.items()
                if isinstance(e, dict) and all(
                    isinstance(e.get(x), int) and not isinstance(e.get(x), bool)
                    and 1024 <= e[x] <= 65535 for x in ("default", "port"))}

    def terapkan_port(self, spec: SpesifikasiLayanan) -> SpesifikasiLayanan:
        """Spesifikasi dengan port efektif laptop ini (port pengganti yang tersimpan).

        Entri tersimpan hanya berlaku selama port bawaan package tidak berubah
        dan tidak bentrok dengan port klaster lain mata kuliah yang sama.
        Idempoten: memanggil ulang pada hasilnya memberi hasil yang sama.
        """
        simpan = self._baca_port(self._dasar(spec))
        bawaan = {k.alias: k.bawaan for k in spec.klaster}
        dipakai = set(bawaan.values())
        port = dict(bawaan)
        for alias, e in simpan.items():
            if bawaan.get(alias) == e["default"] and e["port"] not in dipakai:
                port[alias] = e["port"]
                dipakai.add(e["port"])
        return replace(spec, klaster=tuple(
            replace(k, port=port[k.alias], port_bawaan=k.bawaan) for k in spec.klaster))

    def _simpan_port(self, spec: SpesifikasiLayanan) -> None:
        pindah = {k.alias: {"default": k.bawaan, "port": k.port}
                  for k in spec.klaster if k.port != k.bawaan}
        path = self._dasar(spec) / BERKAS_PORT
        if not pindah:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        sementara = path.with_name(path.name + ".tmp")
        sementara.write_text(json.dumps(pindah, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(sementara, path)

    def _port_mata_kuliah_lain(self, spec: SpesifikasiLayanan) -> set[int]:
        dipakai: set[int] = set()
        for path in (self.state_dir / "services").glob(f"*/{BERKAS_PORT}"):
            if path.parent.name != spec.course_id:
                dipakai |= {e["port"] for e in self._baca_port(path.parent).values()}
        return dipakai

    @staticmethod
    def _port_berjalan(pgdata: Path) -> int | None:
        """Port postmaster yang sedang menyala (baris ke-4 ``postmaster.pid``)."""
        try:
            baris = (pgdata / "postmaster.pid").read_text(encoding="utf-8").splitlines()
            return int(baris[3])
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def _pilih_port(k: Klaster, dipakai: set[int]) -> int:
        calon = [k.port, k.bawaan] + [k.bawaan + LANGKAH_PORT * i
                                      for i in range(1, JUMLAH_PORT_PENGGANTI + 1)]
        dicoba = []
        for p in dict.fromkeys(calon):
            if not 1024 <= p <= 65535 or p in dipakai:
                continue
            dicoba.append(p)
            if _port_bebas(p):
                return p
        raise LayananGagal(
            "port_in_use",
            f"Port {k.bawaan} untuk klaster '{k.alias}' sedang dipakai program lain, begitu "
            f"pula port penggantinya (sampai {max(dicoba, default=k.bawaan)}). Hentikan program "
            "yang memakainya -- misalnya lingkungan Docker praktikum (`docker compose down`) "
            "atau PostgreSQL yang terpasang di laptop -- lalu nyalakan lagi dari Workbench.",
            alias=k.alias, port=k.bawaan, likelyCause="unknown", tried=dicoba)

    def _tetapkan_port(self, defn: DefinisiLayanan, spec: SpesifikasiLayanan,
                       sasaran: set[str]) -> SpesifikasiLayanan:
        """Pilih port final klaster ``sasaran`` yang belum menyala, lalu simpan.

        Urutan: port efektif sekarang (tersimpan), port bawaan package, lalu
        port pengganti. Klaster yang menyala di port lain dari port efektifnya
        dihentikan agar dinyalakan ulang di port yang tercatat.
        """
        dipakai = self._port_mata_kuliah_lain(spec)
        tetap: dict[str, int] = {}
        for k in spec.klaster:
            if k.alias not in sasaran:
                tetap[k.alias] = k.port
            elif self._berjalan(defn, spec, k):
                if self._port_berjalan(self._pgdata(spec, k)) in (None, k.port):
                    tetap[k.alias] = k.port
                else:
                    self._hentikan(defn, spec, k)
        dipakai |= set(tetap.values())
        port = dict(tetap)
        for k in spec.klaster:
            if k.alias not in port:
                # Port milik klaster lain yang belum ditetapkan tidak boleh direbut.
                dipesan = {p for o in spec.klaster if o.alias not in port and o.alias != k.alias
                           for p in (o.port, o.bawaan)}
                port[k.alias] = self._pilih_port(k, dipakai | dipesan)
                dipakai.add(port[k.alias])
        spec = replace(spec, klaster=tuple(replace(k, port=port[k.alias]) for k in spec.klaster))
        self._simpan_port(spec)
        return spec

    def _pgpass(self, spec: SpesifikasiLayanan, peran: str, sandi: str, nama: str) -> Path:
        baris = []
        for k in spec.klaster:
            for host in ("localhost", "127.0.0.1", "::1"):
                h = host.replace(":", "\\:")
                baris.append(f"{h}:{k.port}:*:{peran}:{sandi.replace(':', chr(92) + ':')}")
        path = self._dasar(spec) / nama
        _tulis_rahasia(path, "\n".join(baris) + "\n")
        return path

    # -- proses ------------------------------------------------------------

    def _env_proses(self, bin_dir: Path, pgpass: Path | None = None) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        # Locale proses = locale klaster (initdb --locale=C). Di macOS locale
        # yang tak valid/kosong membuat postmaster gagal ("became multithreaded
        # during startup"); pesan server juga konsisten berbahasa Inggris.
        for kunci in ("LANG", "LC_CTYPE", "LC_MESSAGES"):
            env.pop(kunci, None)
        env["LC_ALL"] = "C"
        if pgpass is not None:
            env["PGPASSFILE"] = str(pgpass)
        return env

    @staticmethod
    def _jalankan(argv: list[str], *, env: Mapping[str, str], waktu: float,
                  masukan: str | None = None, kode_gagal: str = "service_failed") -> str:
        bendera = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            hasil = subprocess.run(argv, input=masukan, capture_output=True, text=True,
                                   env=dict(env), timeout=waktu, creationflags=bendera)
        except subprocess.TimeoutExpired:
            raise LayananGagal(kode_gagal, f"{Path(argv[0]).name} melewati batas waktu {waktu:.0f} s.")
        except OSError as exc:
            raise LayananGagal(kode_gagal, f"{Path(argv[0]).name} tidak dapat dijalankan: {exc}")
        if hasil.returncode != 0:
            ekor = (hasil.stderr or hasil.stdout or "").strip().splitlines()[-6:]
            raise LayananGagal(kode_gagal, f"{Path(argv[0]).name} gagal: " + " | ".join(ekor))
        return hasil.stdout

    @staticmethod
    def _jalankan_pg_ctl_start(argv: list[str], *, env: Mapping[str, str], waktu: float) -> None:
        """``pg_ctl start``: keluaran ke berkas sementara, bukan pipa.

        Di Windows pg_ctl meluncurkan postmaster dengan pewarisan handle,
        sehingga postmaster ikut memegang ujung tulis pipa stdout/stderr.
        ``capture_output`` lalu menunggu EOF yang tak pernah datang selama
        klaster hidup -- juga sesudah timeout (bukti CI windows-2022).
        """
        bendera = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        with tempfile.TemporaryFile() as keluaran:
            try:
                proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=keluaran,
                                      stderr=subprocess.STDOUT, env=dict(env), timeout=waktu,
                                      creationflags=bendera)
            except subprocess.TimeoutExpired:
                raise LayananGagal("service_failed",
                                   f"{Path(argv[0]).name} melewati batas waktu {waktu:.0f} s.")
            except OSError as exc:
                raise LayananGagal("service_failed",
                                   f"{Path(argv[0]).name} tidak dapat dijalankan: {exc}")
            if proc.returncode != 0:
                keluaran.seek(0)
                teks = keluaran.read().decode("utf-8", "replace")
                ekor = teks.strip().splitlines()[-6:]
                raise LayananGagal("service_failed",
                                   f"{Path(argv[0]).name} gagal: " + " | ".join(ekor))

    def _psql_admin(self, defn: DefinisiLayanan, spec: SpesifikasiLayanan, k: Klaster,
                    skrip: str, *, db: str = "postgres") -> str:
        bin_dir = self._bin(defn)
        pgpass = self._dasar(spec) / "pgpass-admin"
        return self._jalankan(
            [str(bin_dir / _exe("psql")), "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
             "-h", "localhost", "-p", str(k.port), "-U", ADMIN, "-d", db, "-f", "-"],
            env=self._env_proses(bin_dir, pgpass), waktu=WAKTU_PSQL, masukan=skrip)

    # -- initdb & konfigurasi ------------------------------------------------

    def _initdb(self, defn: DefinisiLayanan, spec: SpesifikasiLayanan, k: Klaster,
                sandi_admin: str) -> bool:
        pgdata = self._pgdata(spec, k)
        if (pgdata / "PG_VERSION").is_file():
            versi = (pgdata / "PG_VERSION").read_text().strip()
            if versi != str(defn.mayor):
                raise LayananGagal("service_failed",
                                   f"Klaster '{k.alias}' dibuat dengan PostgreSQL {versi}, "
                                   f"agent memakai {defn.versi}.")
            return False
        if pgdata.exists() and any(pgdata.iterdir()):
            raise LayananGagal("service_failed",
                               f"Direktori klaster '{k.alias}' berisi berkas lain; tidak ditimpa.")
        bin_dir = self._bin(defn)
        if sys.platform == "win32" and not str(pgdata).isascii():
            raise LayananGagal(
                "service_failed",
                f"Folder data basis data praktikum ({pgdata}) memuat huruf non-ASCII, dan "
                "Windows tidak menyediakan nama pendek untuknya. PostgreSQL tidak dapat "
                "membuat basis data di folder seperti itu. Pakai akun Windows yang namanya "
                "hanya huruf/angka Latin, atau kerjakan dengan lingkungan Docker naskah.")
        pgdata.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(pgdata, 0o700)
        with tempfile.TemporaryDirectory(dir=self._dasar(spec)) as tmp:
            pw = Path(tmp) / "pw"
            _tulis_rahasia(pw, sandi_admin + "\n")
            self._jalankan(
                [str(bin_dir / _exe("initdb")), "-D", str(pgdata), "-U", ADMIN,
                 f"--pwfile={pw}", "-A", "scram-sha-256", "-E", "UTF8", "--locale=C",
                 "--no-instructions"],
                env=self._env_proses(bin_dir), waktu=WAKTU_INITDB)
        return True

    @staticmethod
    def _tulis_konfigurasi(pgdata: Path, k: Klaster) -> None:
        """Konfigurasi keamanan: selalu dari agent, ditulis ulang setiap start."""
        baris = [
            "# Dikelola Workbench agent (ADR-053). Jangan disunting; ditulis ulang saat start.",
            "listen_addresses = 'localhost'",
            f"port = {k.port}",
            "unix_socket_directories = ''",
            "password_encryption = 'scram-sha-256'",
            "logging_collector = off",
        ]
        if "pg_stat_statements" in k.ekstensi:
            baris.append("shared_preload_libraries = 'pg_stat_statements'")
        (pgdata / "workbench.conf").write_text("\n".join(baris) + "\n", encoding="utf-8")
        conf = pgdata / "postgresql.conf"
        isi = conf.read_text(encoding="utf-8")
        if "include = 'workbench.conf'" not in isi:
            conf.write_text(isi.rstrip("\n") + "\n\ninclude = 'workbench.conf'\n", encoding="utf-8")
        (pgdata / "pg_hba.conf").write_text(
            "# Dikelola Workbench agent (ADR-053): hanya loopback, scram-sha-256.\n"
            "host all all 127.0.0.1/32 scram-sha-256\n"
            "host all all ::1/128 scram-sha-256\n", encoding="utf-8")

    def _siapkan_peran(self, defn: DefinisiLayanan, spec: SpesifikasiLayanan, k: Klaster,
                       sandi_mhs: str) -> None:
        skrip = [
            "DO $$ BEGIN",
            f"  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{PERAN_MAHASISWA}') THEN",
            f"    CREATE ROLE {PERAN_MAHASISWA} LOGIN;",
            "  END IF;",
            "END $$;",
            f"ALTER ROLE {PERAN_MAHASISWA} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            f"NOREPLICATION NOBYPASSRLS PASSWORD {_sql_literal(sandi_mhs)};",
        ]
        pemilik = PERAN_MAHASISWA if k.akses == "owner" else ADMIN
        for db in k.databases:
            skrip.append(
                f"SELECT format('CREATE DATABASE %I OWNER %I', '{db}', '{pemilik}') "
                f"WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '{db}') \\gexec")
        for db in k.databases:
            skrip.append(f"\\connect {db}")
            if k.akses == "read":
                skrip += [
                    f"REVOKE CREATE ON SCHEMA public FROM {PERAN_MAHASISWA};",
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {ADMIN} GRANT USAGE ON SCHEMAS TO {PERAN_MAHASISWA};",
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {ADMIN} GRANT SELECT ON TABLES TO {PERAN_MAHASISWA};",
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {ADMIN} GRANT SELECT ON SEQUENCES TO {PERAN_MAHASISWA};",
                ]
            for e in k.ekstensi:
                skrip.append(f"CREATE EXTENSION IF NOT EXISTS {e};")
            if "postgres_fdw" in k.ekstensi:
                skrip.append(f"GRANT USAGE ON FOREIGN DATA WRAPPER postgres_fdw TO {PERAN_MAHASISWA};")
        self._psql_admin(defn, spec, k, "\n".join(skrip) + "\n")

    # -- berkas untuk mahasiswa ---------------------------------------------

    def _akar_mk(self, spec: SpesifikasiLayanan) -> Path:
        return self.akar_workspace / spec.course_id

    def _tulis_env(self, spec: SpesifikasiLayanan) -> str:
        """`.env` akar mata kuliah. Tidak menimpa `.env` buatan mahasiswa.

        **Tanpa sandi**: ``*_PASSWORD`` sengaja kosong. libpq (dan psycopg2)
        menganggap sandi kosong sebagai tidak diisi lalu membaca ``PGPASSFILE``
        yang diatur Terminal mata kuliah. Dengan begitu tidak ada rahasia di
        workspace -- berkas workspace dapat dibaca lewat relay (``read_file``).
        """
        awal, akhir = "# >>> dikelola Workbench agent", "# <<< dikelola Workbench agent"
        baris = [awal,
                 "# Dibangkitkan saat layanan dinyalakan. *_PASSWORD kosong: sandi dibaca",
                 "# dari PGPASSFILE (buka lewat .workbench/shell/terminal.*)."]
        for k in spec.klaster:
            if k.env_prefix:
                p = k.env_prefix
                baris += [f"{p}_HOST=localhost", f"{p}_PORT={k.port}",
                          f"{p}_USER={PERAN_MAHASISWA}", f"{p}_PASSWORD="]
            baris += [f"{a}={b}" for a, b in sorted(k.env_databases.items())]
        # Nilai env yang sama dengan port bawaan klaster (mis. FDW_PORT) ikut port penggantinya.
        pindah = {str(k.bawaan): str(k.port) for k in spec.klaster if k.port != k.bawaan}
        baris += [f"{a}={pindah.get(b, b)}" for a, b in sorted(spec.env.items())]
        baris.append(akhir)
        blok = "\n".join(baris) + "\n"
        path = self._akar_mk(spec) / ".env"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(blok, encoding="utf-8")
            return "written"
        isi = path.read_text(encoding="utf-8")
        if awal in isi and akhir in isi:
            depan, sisa = isi.split(awal, 1)
            _, belakang = sisa.split(akhir, 1)
            path.write_text(depan + blok.rstrip("\n") + belakang, encoding="utf-8")
            return "updated"
        return "kept"

    @staticmethod
    def _tulis_shim(dir_shell: Path, venv: Path | None, spec: SpesifikasiLayanan) -> tuple[Path | None, str | None]:
        """`docker` pengganti di ``shell/bin`` (lihat ``_docker_naskah``).

        Mengembalikan (direktori shim, kalimat banner). ``docker.json`` ditulis
        ulang setiap start sehingga port-nya selalu sama dengan `.env`.
        """
        dir_bin = dir_shell / "bin"
        peta = {k.compose_service: {"port": k.port, "alias": k.alias}
                for k in spec.klaster if k.compose_service}
        if not peta:
            shutil.rmtree(dir_bin, ignore_errors=True)
            return None, None
        python = venv / ("python.exe" if os.name == "nt" else "python") if venv else None
        if python is None or not python.exists():
            shutil.rmtree(dir_bin, ignore_errors=True)
            return None, ("Perintah docker naskah belum diterjemahkan: jalankan Siapkan lingkungan "
                          "di DSWorkbench, lalu nyalakan ulang basis data.")
        dir_bin.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).with_name("_docker_naskah.py"), dir_bin / "docker_shim.py")
        (dir_bin / "docker.json").write_text(json.dumps(peta, indent=2, sort_keys=True) + "\n",
                                             encoding="utf-8")
        (dir_bin / "docker").write_text(
            "#!/bin/sh\n"
            f"exec {shlex.quote(str(python))} {shlex.quote(str(dir_bin / 'docker_shim.py'))} \"$@\"\n",
            encoding="utf-8", newline="")
        (dir_bin / "docker.cmd").write_text(
            f"@\"{python}\" \"%~dp0docker_shim.py\" %*\r\n@exit /b %ERRORLEVEL%\r\n",
            encoding="utf-8", newline="")
        if os.name == "posix":
            os.chmod(dir_bin / "docker", 0o755)
        layanan = ", ".join(sorted(peta))
        return dir_bin, (f"Perintah naskah docker compose exec LAYANAN psql ... diterjemahkan ke "
                         f"PostgreSQL di laptop (layanan: {layanan}).")

    def _tulis_peluncur(self, defn: DefinisiLayanan, spec: SpesifikasiLayanan) -> None:
        bin_dir = self._bin(defn)
        venv = self._venv_bin(defn)
        pgpass = self._dasar(spec) / "pgpass"
        akar = self._akar_mk(spec)
        dir_shell = akar / ".workbench" / "shell"
        dir_shell.mkdir(parents=True, exist_ok=True)
        dir_shim, info_shim = self._tulis_shim(dir_shell, venv, spec)
        jalur = ([str(dir_shim)] if dir_shim else []) + [str(bin_dir)] + ([str(venv)] if venv else [])
        port = ", ".join(f"{k.alias}={k.port}" for k in spec.klaster)
        q = shlex.quote
        ps = lambda t: "'" + str(t).replace("'", "''") + "'"  # noqa: E731
        dbt_dir = akar / ".workbench" / "dbt"
        sh = [
            "#!/bin/sh",
            "# Terminal mata kuliah (Workbench agent, ADR-053 §7). Jalankan: sh .workbench/shell/terminal.sh",
            f"PATH={q(os.pathsep.join(jalur))}:\"$PATH\"; export PATH",
            f"export PGUSER={PERAN_MAHASISWA} PGHOST=localhost PGPASSFILE={q(str(pgpass))}",
            f"export DBT_PROFILES_DIR={q(str(dbt_dir))} DO_NOT_TRACK=1",
            f"cd {q(str(akar))} || exit 1",
            f"echo 'PostgreSQL: {port} (pengguna {PERAN_MAHASISWA}). Contoh: psql -p {spec.klaster[0].port} -d {spec.klaster[0].databases[0]}'",
            *([f"echo '{info_shim}'"] if info_shim else []),
            'exec "${SHELL:-/bin/sh}" -i',
        ]
        ps1 = [
            "# Terminal mata kuliah (Workbench agent, ADR-053 §7).",
            f"$env:Path = {ps(os.pathsep.join(jalur) + os.pathsep)} + $env:Path",
            f"$env:PGUSER = '{PERAN_MAHASISWA}'; $env:PGHOST = 'localhost'; $env:PGPASSFILE = {ps(pgpass)}",
            f"$env:DBT_PROFILES_DIR = {ps(dbt_dir)}; $env:DO_NOT_TRACK = '1'",
            f"Set-Location {ps(akar)}",
            f"Write-Host 'PostgreSQL: {port} (pengguna {PERAN_MAHASISWA}).'",
            *([f"Write-Host {ps(info_shim)}"] if info_shim else []),
        ]
        cmd = [
            "@echo off",
            "rem Terminal mata kuliah (Workbench agent, ADR-053 §7).",
            f"set \"PATH={os.pathsep.join(jalur)};%PATH%\"",
            f"set \"PGUSER={PERAN_MAHASISWA}\"", "set \"PGHOST=localhost\"",
            f"set \"PGPASSFILE={pgpass}\"",
            f"set \"DBT_PROFILES_DIR={dbt_dir}\"", "set \"DO_NOT_TRACK=1\"",
            f"cd /d \"{akar}\"",
            f"echo PostgreSQL: {port} (pengguna {PERAN_MAHASISWA}).",
            *([f"echo {info_shim}"] if info_shim else []),
            "cmd /k",
        ]
        # newline="": akhir baris persis seperti ditulis (LF untuk sh, CRLF untuk Windows) di semua OS.
        (dir_shell / "terminal.sh").write_text("\n".join(sh) + "\n", encoding="utf-8", newline="")
        (dir_shell / "terminal.ps1").write_text("\r\n".join(ps1) + "\r\n", encoding="utf-8", newline="")
        (dir_shell / "terminal.cmd").write_text("\r\n".join(cmd) + "\r\n", encoding="utf-8", newline="")
        if os.name == "posix":
            os.chmod(dir_shell / "terminal.sh", 0o755)

    # -- status / start / stop ------------------------------------------------

    def _berjalan(self, defn: DefinisiLayanan, spec: SpesifikasiLayanan, k: Klaster) -> bool:
        pgdata = self._pgdata(spec, k)
        if not (pgdata / "postmaster.pid").is_file():
            return False
        bin_dir = self._lokasi_bin(defn)
        if bin_dir is None:
            return False
        try:
            self._jalankan([str(bin_dir / _exe("pg_ctl")), "status", "-D", str(pgdata)],
                           env=self._env_proses(bin_dir), waktu=30)
            return True
        except LayananGagal:
            return False

    def status(self, defn: DefinisiLayanan, spec: SpesifikasiLayanan) -> dict[str, Any]:
        spec = self.terapkan_port(spec)
        klaster = []
        for k in spec.klaster:
            pgdata = self._pgdata(spec, k)
            dibuat = (pgdata / "PG_VERSION").is_file()
            state = "running" if dibuat and self._berjalan(defn, spec, k) else "stopped"
            termuat = self._termuat(spec, k)
            klaster.append({"alias": k.alias, **k.info_port(), "initialized": dibuat,
                            "state": state, "access": k.akses,
                            "databases": [{"name": d, **({"loaded": termuat[d]} if d in termuat else {})}
                                          for d in k.databases]})
        dasar_ada = self._dasar(spec)
        while not dasar_ada.exists() and dasar_ada != dasar_ada.parent:
            dasar_ada = dasar_ada.parent
        return {
            "service": defn.id, "version": defn.versi,
            "installed": self._lokasi_bin(defn) is not None,
            "clusters": klaster,
            "diskFreeBytes": shutil.disk_usage(dasar_ada).free,
        }

    def start(self, defn: DefinisiLayanan, spec: SpesifikasiLayanan,
              alias: Any = None) -> dict[str, Any]:
        bin_dir = self._bin(defn)
        periksa_vc_runtime(bin_dir)
        spec = self.terapkan_port(spec)
        spec = self._tetapkan_port(defn, spec, {k.alias for k in spec.pilih(alias)})
        rahasia = self._rahasia(spec)
        self._pgpass(spec, ADMIN, rahasia["admin"], "pgpass-admin")
        self._pgpass(spec, PERAN_MAHASISWA, rahasia["student"], "pgpass")
        hasil = []
        for k in spec.pilih(alias):
            baru = self._initdb(defn, spec, k, rahasia["admin"])
            pgdata = self._pgdata(spec, k)
            if not self._berjalan(defn, spec, k):
                self._tulis_konfigurasi(pgdata, k)
                self._jalankan_pg_ctl_start(
                    [str(bin_dir / _exe("pg_ctl")), "start", "-D", str(pgdata), "-w",
                     "-t", str(WAKTU_PG_CTL - 10), "-l", str(self._dasar(spec) / f"{k.alias}.log")],
                    env=self._env_proses(bin_dir), waktu=WAKTU_PG_CTL)
            self._siapkan_peran(defn, spec, k, rahasia["student"])
            hasil.append({"alias": k.alias, **k.info_port(), "state": "running",
                          "initialized": baru})
        env_file = self._tulis_env(spec)
        self._tulis_peluncur(defn, spec)
        return {"clusters": hasil, "envFile": env_file,
                "terminal": ".workbench/shell/terminal." + ("cmd" if sys.platform == "win32" else "sh")}

    # -- data kanonik (ADR-055 §2, PGD-03) -------------------------------------

    def _berkas_termuat(self, spec: SpesifikasiLayanan, k: Klaster) -> Path:
        return self._dasar(spec) / f"{k.alias}.datasets.json"

    def _termuat(self, spec: SpesifikasiLayanan, k: Klaster) -> dict[str, Any]:
        path = self._berkas_termuat(spec, k)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def muat(self, defn: DefinisiLayanan, spec: SpesifikasiLayanan, akar_data: Path,
             rencana: Mapping[str, Any], *, lapor: Callable[[str], None] = lambda s: None,
             batal: Callable[[], bool] = lambda: False) -> dict[str, Any]:
        """Muat satu rilis kanonik ke klaster sumber.

        ``akar_data`` = folder rilis di ``data/raw`` (berkas sudah diverifikasi
        SHA-256 terhadap manifest oleh pemanggil); ``rencana`` = isi
        ``load.json``. Klaster ``owner`` (gudang mahasiswa) ditolak.

        Rencana berisi satu basis data, atau ``databases``: daftar entri
        berbentuk sama yang masing-masing membawa ``dir`` (subfolder di samping
        ``load.json``). Entri biasa membuat ulang basis datanya (idempoten);
        entri ``"mode": "append"`` menambahkan baris ke basis data yang sudah
        dimuat, dalam satu transaksi, dan hanya sekali.
        """
        if "databases" in rencana:
            daftar = rencana.get("databases")
            if not isinstance(daftar, list) or not daftar:
                raise LayananGagal("dataset_invalid", "Rencana muat dataset tidak sah.")
            bagian = []
            for e in daftar:
                d = e.get("dir") if isinstance(e, Mapping) else None
                if not (isinstance(d, str) and _IDENT.fullmatch(d)):
                    raise LayananGagal("dataset_invalid", "Rencana muat dataset tidak sah.")
                umum = {a: rencana.get(a) for a in ("dataset", "version", "profile")}
                bagian.append((akar_data / d, {**umum, **e}))
        else:
            bagian = [(akar_data, rencana)]
        siap = [(self._periksa_rencana(spec, akar, r), akar, r) for akar, r in bagian]
        hasil = [self._muat_satu(defn, spec, k, akar, r, lapor=lapor, batal=batal)
                 for k, akar, r in siap]
        if "databases" not in rencana:
            return hasil[0]
        return {"alias": hasil[0]["alias"], "databases": hasil,
                "tables": sum(h["tables"] for h in hasil),
                "rows": sum(h.get("rows", 0) for h in hasil),
                "dataset": rencana.get("dataset"), "version": rencana.get("version"),
                "profile": rencana.get("profile")}

    def _periksa_rencana(self, spec: SpesifikasiLayanan, akar_data: Path,
                         rencana: Mapping[str, Any]) -> Klaster:
        db = rencana.get("database")
        skema = rencana.get("schema")
        if not (isinstance(db, str) and _IDENT.fullmatch(db)
                and isinstance(skema, str) and _IDENT.fullmatch(skema)):
            raise LayananGagal("dataset_invalid", "Rencana muat dataset tidak sah.")
        k = next((c for c in spec.klaster if db in c.databases), None)
        if k is None:
            raise LayananGagal("dataset_invalid",
                               f"Basis data '{db}' tidak dideklarasikan mata kuliah ini.")
        if k.akses != "read":
            raise LayananGagal("owner_cluster_protected",
                               "Data kanonik hanya dimuat ke klaster sumber; gudang Anda tidak disentuh.",
                               alias=k.alias)
        tambah = rencana.get("mode") == "append"
        if rencana.get("mode") not in (None, "append"):
            raise LayananGagal("dataset_invalid", "Rencana muat dataset tidak sah.")
        tabel = rencana.get("tables") or []
        berkas = [t.get("file") for t in tabel]
        if not tambah:
            berkas += [rencana.get("preData"), rencana.get("postData")]
        for b in berkas:
            if not isinstance(b, str) or "/" in b or "\\" in b or b.startswith(".") \
                    or not (akar_data / b).is_file():
                raise LayananGagal("dataset_missing",
                                   "Berkas dataset belum lengkap di laptop. Siapkan dataset dulu.")
        for t in tabel:
            if not (isinstance(t.get("table"), str) and _IDENT.fullmatch(t["table"])
                    and isinstance(t.get("rows"), int)):
                raise LayananGagal("dataset_invalid", "Daftar tabel rencana muat tidak sah.")
        if tambah and not tabel:
            raise LayananGagal("dataset_invalid", "Daftar tabel rencana muat tidak sah.")
        return k

    def _muat_satu(self, defn: DefinisiLayanan, spec: SpesifikasiLayanan, k: Klaster,
                   akar_data: Path, rencana: Mapping[str, Any], *,
                   lapor: Callable[[str], None], batal: Callable[[], bool]) -> dict[str, Any]:
        db, skema = rencana["database"], rencana["schema"]
        tabel = rencana.get("tables") or []
        tambah = rencana.get("mode") == "append"
        termuat = self._termuat(spec, k)
        if tambah:
            dasar = termuat.get(db)
            syarat = rencana.get("requires") if isinstance(rencana.get("requires"), Mapping) else {}
            if not isinstance(dasar, dict) or any(dasar.get(a) != b for a, b in syarat.items()):
                label = " ".join(str(syarat[a]) for a in ("dataset", "profile") if a in syarat)
                raise LayananGagal(
                    "dataset_base_missing",
                    f"Muat dataset {label or 'dasarnya'} ke {db} dulu; data ini ditambahkan "
                    "di atasnya.")
            if any(isinstance(x, dict) and (x.get("dataset"), x.get("version"))
                   == (rencana.get("dataset"), rencana.get("version"))
                   for x in dasar.get("appended") or []):
                lapor(f"{rencana.get('dataset')} sudah ditambahkan ke {db}; dilewati.")
                return {"alias": k.alias, "database": db, "tables": len(tabel), "rows": 0,
                        "alreadyLoaded": True}

        self.start(defn, spec, k.alias)
        spec = self.terapkan_port(spec)
        k = spec.pilih(k.alias)[0]
        rahasia = self._rahasia(spec)
        bin_dir = self._bin(defn)
        pgpass = self._dasar(spec) / "pgpass-admin"
        env = self._env_proses(bin_dir, pgpass)
        psql = [str(bin_dir / _exe("psql")), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", "localhost",
                "-p", str(k.port), "-U", ADMIN]

        def cek() -> None:
            if batal():
                raise _Batal()

        def hitung() -> dict[str, int]:
            keluar = self._jalankan(
                psql + ["-d", db, "-A", "-t", "-c", " UNION ALL ".join(
                    f"SELECT '{t['table']}', count(*) FROM {skema}.{t['table']}" for t in tabel)],
                env=env, waktu=WAKTU_MUAT)
            return {a: int(b) for a, b in (baris.split("|") for baris in keluar.split())}

        try:
            if tambah:
                sebelum = hitung()
                lapor(f"Menambahkan {len(tabel)} tabel ke {db} (satu transaksi) …")
                # Satu skrip, satu transaksi: COPY ... FROM STDIN membaca datanya
                # dari aliran skrip yang sama, jadi gagal di tengah tidak
                # meninggalkan separuh batch.
                with tempfile.TemporaryFile() as skrip:
                    skrip.write(b"BEGIN;\n")
                    for t in tabel:
                        skrip.write(f"COPY {skema}.{t['table']} FROM STDIN WITH "
                                    "(FORMAT csv, HEADER);\n".encode())
                        with open(akar_data / t["file"], "rb") as masukan:
                            shutil.copyfileobj(masukan, skrip)
                        skrip.write(b"\\.\n")
                    skrip.write(b"COMMIT;\n")
                    skrip.seek(0)
                    cek()
                    self._jalankan_berkas(psql + ["-d", db, "-f", "-"], env=env, masukan=skrip)
                self._jalankan(psql + ["-d", db, "-c", "ANALYZE " + ", ".join(
                    f"{skema}.{t['table']}" for t in tabel)], env=env, waktu=WAKTU_MUAT)
                sesudah = hitung()
                nyata = {t["table"]: sesudah.get(t["table"], 0) - sebelum.get(t["table"], 0)
                         for t in tabel}
            else:
                lapor(f"Membuat ulang basis data {db} …")
                self._jalankan(psql + ["-d", "postgres", "-c",
                                       f"DROP DATABASE IF EXISTS {db} WITH (FORCE)"],
                               env=env, waktu=WAKTU_PSQL)
                termuat.pop(db, None)
                self._simpan_termuat(spec, k, termuat)
                self._siapkan_peran(defn, spec, k, rahasia["student"])
                # Lewat stdin, bukan ``-f <path>``: psql Windows membuka path dengan
                # kode halaman ANSI, sedangkan akar workspace bisa berhuruf non-ASCII.
                with open(akar_data / rencana["preData"], "rb") as masukan:
                    self._jalankan_berkas(psql + ["-d", db, "-f", "-"], env=env, masukan=masukan,
                                          label="Skema")
                for t in tabel:
                    cek()
                    lapor(f"COPY {skema}.{t['table']} ({t['rows']:,} baris) …")
                    with open(akar_data / t["file"], "rb") as masukan:
                        self._jalankan_berkas(
                            psql + ["-d", db, "-c",
                                    f"COPY {skema}.{t['table']} FROM STDIN WITH (FORMAT csv, HEADER)"],
                            env=env, masukan=masukan)
                cek()
                lapor("Membuat indeks dan constraint …")
                with open(akar_data / rencana["postData"], "rb") as masukan:
                    self._jalankan_berkas(psql + ["-d", db, "-f", "-"], env=env, masukan=masukan,
                                          label="Indeks/constraint")
                self._jalankan(psql + ["-d", db, "-c", "ANALYZE"], env=env, waktu=WAKTU_MUAT)
                nyata = hitung()
        except _Batal:
            raise LayananGagal("cancelled", "Pemuatan dihentikan; basis data sumber belum lengkap.")
        beda = [t["table"] for t in tabel if nyata.get(t["table"]) != t["rows"]]
        if beda:
            raise LayananGagal("dataset_invalid",
                               f"Jumlah baris tidak cocok dengan rencana: {', '.join(beda)}.")
        catatan = {"dataset": rencana.get("dataset"), "version": rencana.get("version"),
                   "profile": rencana.get("profile"),
                   "loadedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "rows": sum(nyata.values())}
        termuat = self._termuat(spec, k)
        if tambah:
            termuat[db].setdefault("appended", []).append(catatan)
        else:
            termuat[db] = catatan
        self._simpan_termuat(spec, k, termuat)
        lapor(f"Selesai: {db}, {len(tabel)} tabel, {catatan['rows']:,} baris.")
        return {"alias": k.alias, "database": db, "tables": len(tabel), **catatan}

    def _simpan_termuat(self, spec: SpesifikasiLayanan, k: Klaster,
                        termuat: Mapping[str, Any]) -> None:
        self._berkas_termuat(spec, k).write_text(json.dumps(termuat, indent=1) + "\n",
                                                 encoding="utf-8")

    @staticmethod
    def _jalankan_berkas(argv: list[str], *, env: Mapping[str, str], masukan: Any,
                         label: str = "COPY") -> None:
        """Seperti :meth:`_jalankan`, tetapi stdin dialirkan dari berkas (COPY besar)."""
        bendera = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            hasil = subprocess.run(argv, stdin=masukan, capture_output=True, env=dict(env),
                                   timeout=WAKTU_MUAT, creationflags=bendera)
        except subprocess.TimeoutExpired:
            raise LayananGagal("service_failed", f"{label} melewati batas waktu.")
        if hasil.returncode != 0:
            ekor = (hasil.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-4:]
            raise LayananGagal("service_failed", f"{label} gagal: " + " | ".join(ekor))

    def _hentikan(self, defn: DefinisiLayanan, spec: SpesifikasiLayanan, k: Klaster) -> None:
        bin_dir = self._bin(defn)
        self._jalankan([str(bin_dir / _exe("pg_ctl")), "stop", "-D",
                        str(self._pgdata(spec, k)), "-m", "fast", "-w"],
                       env=self._env_proses(bin_dir), waktu=WAKTU_PG_CTL)

    def stop(self, defn: DefinisiLayanan, spec: SpesifikasiLayanan,
             alias: Any = None) -> dict[str, Any]:
        spec = self.terapkan_port(spec)
        hasil = []
        for k in spec.pilih(alias):
            if self._berjalan(defn, spec, k):
                self._hentikan(defn, spec, k)
            hasil.append({"alias": k.alias, **k.info_port(), "state": "stopped"})
        return {"clusters": hasil}


class _Batal(Exception):
    pass


def hentikan_semua(state_dir: Path, dir_bin: list[Path]) -> int:
    """Hentikan setiap klaster yang menyala di state agent (saat agent keluar).

    Tidak butuh spesifikasi mata kuliah: klaster dikenali dari
    ``services/<courseId>/<alias>/postmaster.pid``. Kegagalan satu klaster
    tidak menghentikan yang lain; jumlah yang dihentikan dikembalikan.
    """
    pg_ctl = next((d / _exe("pg_ctl") for d in dir_bin if (d / _exe("pg_ctl")).is_file()), None)
    if pg_ctl is None:
        return 0
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env["LC_ALL"] = "C"
    n = 0
    for pid in sorted((state_dir / "services").glob("*/*/postmaster.pid")):
        try:
            KelolaPostgres._jalankan([str(pg_ctl), "stop", "-D", str(pid.parent), "-m", "fast",
                                      "-w", "-t", "30"], env=env, waktu=45)
            n += 1
        except LayananGagal:
            continue
    return n
