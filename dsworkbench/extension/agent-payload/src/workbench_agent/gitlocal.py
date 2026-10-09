"""Git lokal di akar mata kuliah (GH-01; ADR-051 §5, §9, §10) -- tanpa jaringan.

Mesin git = dulwich murni-Python yang di-vendor (``vendor/`` di samping
``src/``). Biner ``git`` yang terpasang tidak pernah dipakai.

**Isolasi dari kernel DW.** Kernel notebook berjalan di proses yang sama.
Karena itu ``vendor/`` tidak ada di ``PYTHONPATH``, dan modul ini:

- memuat paket ``dulwich`` langsung dari berkasnya, tanpa menyentuh urutan
  ``sys.path``;
- menambahkan ``vendor/`` ke **akhir** ``sys.path`` untuk ``urllib3`` dan
  ``typing_extensions``. Pustaka yang sudah dipasang mahasiswa selalu menang;
- tidak mengubah ``os.environ``. Isolasi config dilakukan lewat
  :class:`RepoAman`, bukan lewat ``GIT_CONFIG_*``.

**Pengerasan (terbukti perlu di GH-00):**

- hooks dikosongkan;
- ``.gitattributes`` diabaikan, sehingga filter driver, LFS, dan eol tidak
  berjalan;
- config hanya dari ``.git/config`` repo itu, dengan lapis paksa
  ``core.symlinks=false`` dan ``core.excludesFile`` kosong;
- repo yang config-nya memuat ``include*``, ``filter.*``, ``core.hooksPath``,
  ``core.fsmonitor``, atau ``core.worktree`` ditolak.
"""

from __future__ import annotations

import difflib
import importlib.util
import json
import os
import re
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

#: ``<paket agent>/vendor``: di repo ``agent/local-runner/vendor``, di ZIP
#: ``<folder>/vendor``, di payload desktop ``payload/vendor``.
VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor"

#: Versi dulwich yang diuji GH-00 dan di-vendor (scripts/vendor-git-engine.py).
DULWICH_VERSION = (1, 2, 15)

MAX_PATHS = 500
MAX_STATUS_FILES = 2000
MAX_EXPANDED_FILES = 5000
MAX_LOG = 50
MAX_MESSAGE = 2000
MAX_SCAN_BYTES = 10 * 1024 * 1024
MAX_DIFF_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_PUSH_BYTES = 200 * 1024 * 1024
HARD_MAX_PUSH_BYTES = 2000 * 1024 * 1024
#: GitHub memblokir berkas > 100 MB; setting server dipangkas ke sini.
HARD_MAX_FILE_BYTES = 100 * 1024 * 1024

#: Tambahan platform untuk ``.gitignore`` bawaan (ADR-051 §9).
PLATFORM_IGNORE = (
    "data/", "*.pt", "*.pth", "checkpoints/", ".venv*", "__pycache__/",
    ".ipynb_checkpoints/", ".workbench/", ".env", ".env.*", ".DS_Store", "Thumbs.db",
)

#: Selalu diabaikan walau ``.gitignore`` disunting: metadata Workspace Manager.
_SELALU_DIABAIKAN = (".workbench",)

_POLA_IGNORE = re.compile(r"^[A-Za-z0-9_.*?/\[\]-][A-Za-z0-9_.*?/\[\] -]{0,199}$")
_POLA_EMAIL = re.compile(r"^[A-Za-z0-9._+-]{1,120}@users\.noreply\.[A-Za-z0-9.-]{1,200}$")
_POLA_REPO = re.compile(
    r"^https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}?)(?:\.git)?$")

#: Kunci config yang membuat repo ditolak (G7): (seksi, kunci | None = semua).
_CONFIG_TERLARANG = (
    (b"include", None), (b"includeif", None), (b"filter", None),
    (b"core", b"hookspath"), (b"core", b"fsmonitor"), (b"core", b"worktree"),
    (b"extensions", b"worktreeconfig"),
)

#: Pemindai rahasia (ADR-051 §9). Nilai rahasianya tidak pernah dilaporkan.
_POLA_RAHASIA = (
    ("github_token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b")),
    ("aws_access_key", re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("gcp_service_account", re.compile(rb"\"private_key\"\s*:\s*\"-----BEGIN")),
    ("private_key", re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")),
)
_MAX_TEMUAN = 20


class GitError(Exception):
    """Kegagalan terstruktur: ``code`` stabil untuk UI, ``detail`` untuk manusia."""

    def __init__(self, code: str, detail: str, **extra: Any):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.extra = extra

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, **self.extra}


# ---------------------------------------------------------------------------
# Memuat mesin
# ---------------------------------------------------------------------------

_kunci_muat = threading.Lock()
_mesin: dict[str, Any] = {}


def tersedia() -> bool:
    """Mesin git ada di salinan agent ini (tanpa mengimpornya)."""
    if "dulwich" in sys.modules:
        return _versi_cocok(sys.modules["dulwich"])
    return (VENDOR_DIR / "dulwich" / "__init__.py").is_file()


def _versi_cocok(mod: Any) -> bool:
    return tuple(getattr(mod, "__version__", ())[:3]) == DULWICH_VERSION


def muat() -> Any:
    """Paket ``dulwich`` dari vendor; :class:`GitError` bila tidak dapat dipakai."""
    with _kunci_muat:
        if "repo_cls" in _mesin:
            return _mesin["dulwich"]
        mod = sys.modules.get("dulwich")
        if mod is None:
            init = VENDOR_DIR / "dulwich" / "__init__.py"
            if not init.is_file():
                raise GitError("engine_unavailable",
                               "Mesin git tidak ada di salinan agent ini. Perbarui Local Runner.")
            spec = importlib.util.spec_from_file_location(
                "dulwich", init, submodule_search_locations=[str(init.parent)])
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            sys.modules["dulwich"] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception:
                sys.modules.pop("dulwich", None)
                raise
        if not _versi_cocok(mod):
            raise GitError("engine_unavailable",
                           "Versi dulwich lain sudah dimuat di proses ini; mesin git agent tidak dipakai.")
        # Hanya untuk urllib3/typing_extensions yang belum dipasang: di akhir.
        if str(VENDOR_DIR) not in sys.path:
            sys.path.append(str(VENDOR_DIR))
        _mesin["dulwich"] = mod
        _mesin["repo_cls"] = _buat_repo_aman()
        return mod


def _buat_repo_aman() -> type:
    from dulwich.attrs import GitAttributes
    from dulwich.config import ConfigDict, StackedConfig
    from dulwich.repo import Repo

    class RepoAman(Repo):
        """Repo tanpa hooks, tanpa atribut, dan hanya dengan config lokalnya."""

        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            self.hooks.clear()

        def get_gitattributes(self, tree: bytes | None = None) -> Any:
            return GitAttributes()

        def get_config_stack(self) -> Any:
            paksa = ConfigDict()
            paksa.set((b"core",), b"symlinks", False)
            paksa.set((b"core",), b"excludesFile", os.devnull.encode())
            lokal = self.get_config()
            return StackedConfig([paksa, lokal], writable=lokal)

    return RepoAman


def _repo_cls() -> type:
    muat()
    return _mesin["repo_cls"]


# ---------------------------------------------------------------------------
# Opsi dari API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Identitas:
    name: str
    email: str

    def as_bytes(self) -> bytes:
        return f"{self.name} <{self.email}>".encode("utf-8")


@dataclass(frozen=True)
class OpsiGit:
    """``payload.git`` yang disisipkan Control API (browser tidak dapat mengisinya)."""

    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    ignore: tuple[str, ...] = ()
    strip_outputs: bool = False
    identity: Identitas | None = None
    #: Batas total objek baru per push (GH-03), dari setting server.
    max_push_bytes: int = DEFAULT_MAX_PUSH_BYTES

    @classmethod
    def dari_payload(cls, payload: Mapping[str, Any]) -> "OpsiGit":
        g = payload.get("git")
        if not isinstance(g, Mapping):
            return cls()
        batas = g.get("maxFileBytes")
        if isinstance(batas, bool) or not isinstance(batas, int) or batas <= 0:
            batas = DEFAULT_MAX_FILE_BYTES
        ignore = tuple(p for p in (g.get("ignore") or ())[:100]
                       if isinstance(p, str) and _POLA_IGNORE.fullmatch(p))
        ident = None
        i = g.get("identity")
        if isinstance(i, Mapping):
            nama = i.get("name")
            email = i.get("email")
            if isinstance(nama, str) and isinstance(email, str) and _POLA_EMAIL.fullmatch(email):
                nama = re.sub(r"[<>\x00-\x1f\x7f]", "", nama).strip()[:100]
                if nama:
                    ident = Identitas(nama, email)
        push = g.get("maxPushBytes")
        if isinstance(push, bool) or not isinstance(push, int) or push <= 0:
            push = DEFAULT_MAX_PUSH_BYTES
        return cls(max_file_bytes=min(batas, HARD_MAX_FILE_BYTES), ignore=ignore,
                   strip_outputs=g.get("stripOutputs") is True, identity=ident,
                   max_push_bytes=min(push, HARD_MAX_PUSH_BYTES))


# ---------------------------------------------------------------------------
# Membuka repo
# ---------------------------------------------------------------------------


def _git_dir(akar: Path) -> Path:
    return akar / ".git"


def _ada_repo(akar: Path) -> bool:
    return os.path.lexists(_git_dir(akar))


def periksa_repo(akar: Path) -> None:
    """Tolak ``.git`` yang bukan direktori biasa atau config yang berbahaya (G7)."""
    g = _git_dir(akar)
    if g.is_symlink() or not g.is_dir():
        raise GitError("repo_unsafe", "Folder .git di mata kuliah ini bukan repo biasa "
                       "(berkas, symlink, atau worktree lain); Workbench tidak membukanya.")
    for rel in ("commondir", os.path.join("objects", "info", "alternates")):
        if os.path.lexists(g / rel):
            raise GitError("repo_unsafe", "Repo ini merujuk objek di luar folder mata kuliah; "
                           "Workbench tidak membukanya.")
    muat()
    from dulwich.config import ConfigFile

    try:
        cfg = ConfigFile.from_path(str(g / "config"))
    except FileNotFoundError:
        return
    except Exception as exc:  # noqa: BLE001 -- config rusak = tolak
        raise GitError("repo_unsafe", "Config repo tidak dapat dibaca.") from exc
    for seksi in cfg.sections():
        nama = seksi[0].lower()
        for s_larang, k_larang in _CONFIG_TERLARANG:
            if nama != s_larang:
                continue
            if k_larang is None:
                raise GitError("repo_unsafe", _pesan_config(seksi[0].decode(errors="replace")))
            for kunci, _nilai in cfg.items(seksi):
                if kunci.lower() == k_larang:
                    raise GitError("repo_unsafe", _pesan_config(
                        f"{nama.decode()}.{kunci.decode(errors='replace')}"))


def _pesan_config(kunci: str) -> str:
    return (f"Config repo memuat '{kunci}', yang dapat menjalankan program lain. "
            "Workbench tidak membuka repo ini; hapus pengaturan itu dari .git/config.")


def buka(akar: Path) -> Any:
    if not _ada_repo(akar):
        raise GitError("not_initialized", "Folder mata kuliah ini belum menjadi repo git.")
    periksa_repo(akar)
    return _repo_cls()(str(akar))


def _head(repo: Any) -> bytes | None:
    try:
        return repo.head()
    except KeyError:
        return None


def _cabang(repo: Any) -> str | None:
    try:
        isi = repo.refs.read_ref(b"HEAD")
    except KeyError:
        return None
    if isi and isi.startswith(b"ref: refs/heads/"):
        return isi[len(b"ref: refs/heads/"):].decode("utf-8", "replace")
    return None


def _remote(repo: Any) -> dict[str, str] | None:
    try:
        url = repo.get_config().get((b"remote", b"origin"), b"url").decode("utf-8", "replace")
    except KeyError:
        return None
    m = _POLA_REPO.fullmatch(url)
    return {"fullName": m.group(1)} if m else None


def _teks(p: bytes | str) -> str:
    return p.decode("utf-8", "replace") if isinstance(p, bytes) else p


def _rel_status(p: bytes | str) -> str:
    """Path dari ``porcelain.status``: dulwich mengembalikannya dalam bentuk
    filesystem (``d\\b.py`` di Windows); kontrak memakai ``/`` di semua OS."""
    t = _teks(p)
    return t.replace(os.sep, "/") if os.sep != "/" else t


def _tree_path(rel: str) -> bytes:
    return rel.encode("utf-8")


# ---------------------------------------------------------------------------
# .gitignore
# ---------------------------------------------------------------------------


def isi_gitignore(opsi: OpsiGit) -> str:
    baris = ["# Dibuat Data Science Workbench (ADR-051). Boleh ditambah; berkas yang",
             "# cocok di sini tidak ikut commit maupun push.", *PLATFORM_IGNORE]
    tambahan = [p for p in opsi.ignore if p not in PLATFORM_IGNORE]
    if tambahan:
        baris += ["", "# dari package mata kuliah", *tambahan]
    return "\n".join(baris) + "\n"


def _pengabai(repo: Any) -> Any:
    from dulwich.ignore import IgnoreFilterManager

    return IgnoreFilterManager.from_repo(repo, config=repo.get_config_stack())


def _diabaikan(pengabai: Any, rel: str, is_dir: bool = False) -> bool:
    bagian = rel.split("/")
    if bagian[0] in _SELALU_DIABAIKAN or ".git" in bagian:
        return True
    return bool(pengabai.is_ignored(rel + ("/" if is_dir else "")))


# ---------------------------------------------------------------------------
# Operasi
# ---------------------------------------------------------------------------


def init(akar: Path, opsi: OpsiGit) -> dict[str, Any]:
    """Idempoten. Cabang ``main``; ``.gitignore`` bawaan bila belum ada."""
    muat()   # mesin vendor dimuat sebelum impor dulwich apa pun
    akar.mkdir(parents=True, exist_ok=True)
    dibuat = False
    if _ada_repo(akar):
        periksa_repo(akar)
    else:
        repo = _repo_cls().init(str(akar), default_branch=b"main")
        cfg = repo.get_config()
        cfg.set((b"core",), b"symlinks", False)
        cfg.write_to_path()
        repo.close()
        dibuat = True
    gi = akar / ".gitignore"
    tulis_gi = not os.path.lexists(gi)
    if tulis_gi:
        gi.write_text(isi_gitignore(opsi), encoding="utf-8", newline="\n")
    return {"initialized": True, "created": dibuat, "gitignoreWritten": tulis_gi,
            "branch": "main" if dibuat else _cabang(buka(akar))}


def status(akar: Path) -> dict[str, Any]:
    muat()   # mesin vendor dimuat sebelum impor dulwich apa pun
    if not _ada_repo(akar):
        return {"initialized": False, "branch": None, "clean": True, "ahead": 0,
                "behind": 0, "files": [], "truncated": False, "remote": None}
    from dulwich import porcelain

    repo = buka(akar)
    try:
        st = porcelain.status(repo, untracked_files="all")
        staged: dict[str, str] = {}
        for jenis, daftar in st.staged.items():
            for p in daftar:
                staged[_rel_status(p)] = jenis
        unstaged = {_rel_status(p) for p in st.unstaged}
        unstaged |= _racy_berubah(repo, akar, unstaged)
        entri: dict[str, dict[str, Any]] = {}
        for p in sorted(set(staged) | unstaged | {_rel_status(p) for p in st.untracked}):
            ada = os.path.lexists(akar / p)
            if p in staged and p in unstaged:
                state, is_staged = ("deleted", False) if not ada else ("staged_modified", True)
            elif p in staged:
                state, is_staged = ("deleted" if staged[p] == "delete" else "staged"), True
            elif p in unstaged:
                state, is_staged = ("modified" if ada else "deleted"), False
            else:
                state, is_staged = "untracked", False
            try:
                ukuran = (akar / p).stat().st_size if ada else None
            except OSError:
                ukuran = None
            entri[p] = {"path": p, "state": state, "staged": is_staged, "size": ukuran}
        files = list(entri.values())
        ahead, behind = _ahead_behind(repo)
        return {"initialized": True, "branch": _cabang(repo), "clean": not files,
                "ahead": ahead, "behind": behind, "files": files[:MAX_STATUS_FILES],
                "truncated": len(files) > MAX_STATUS_FILES, "remote": _remote(repo)}
    finally:
        repo.close()


#: Jendela "racily clean" (git): entri yang mtime-nya sedekat ini dengan index
#: tidak dipercaya dari stat saja. dulwich 1.2.15 mempercayai stat yang cocok;
#: di Windows (ctime = waktu dibuat) suntingan berukuran sama tepat setelah
#: stage bisa tak terlihat (ditemukan di CI Windows GH-04).
_JENDELA_RACY_NS = 2_000_000_000


def _racy_berubah(repo: Any, akar: Path, sudah: set[str]) -> set[str]:
    """Path terlacak yang stat-nya cocok tetapi isinya berbeda dari index."""
    from dulwich.index import blob_from_path_and_stat

    try:
        index_ns = os.stat(repo.index_path()).st_mtime_ns
    except OSError:
        return set()
    hasil: set[str] = set()
    for tp, entry in repo.open_index().items():
        rel = _teks(tp)
        if rel in sudah or not hasattr(entry, "mtime"):
            continue
        m = entry.mtime
        entri_ns = (m[0] * 1_000_000_000 + m[1]) if isinstance(m, tuple) else int(m) * 1_000_000_000
        if entri_ns < index_ns - _JENDELA_RACY_NS:
            continue
        jalur = akar / Path(*rel.split("/"))
        try:
            st = os.lstat(jalur)
        except OSError:
            continue
        if not os.path.isfile(jalur) or os.path.islink(jalur):
            continue
        if blob_from_path_and_stat(os.fsencode(jalur), st).id != entry.sha:
            hasil.add(rel)
    return hasil


def _ahead_behind(repo: Any) -> tuple[int, int]:
    """Terhadap ``refs/remotes/origin/<cabang>`` (diisi GH-03); 0/0 bila belum ada."""
    cabang = _cabang(repo)
    head = _head(repo)
    if not cabang or head is None:
        return 0, 0
    try:
        jauh = repo.refs[f"refs/remotes/origin/{cabang}".encode()]
    except KeyError:
        return 0, 0

    def leluhur(sha: bytes) -> set[bytes]:
        return {e.commit.id for e in repo.get_walker(include=[sha], max_entries=5000)}

    a, b = leluhur(head), leluhur(jauh)
    return len(a - b), len(b - a)


def _periksa_path(resolver: Any, p: Any) -> str:
    """Path relatif akar mata kuliah (POSIX) atau GitError('outside_root')."""
    if not isinstance(p, str) or not p:
        raise GitError("outside_root", "path tidak sah")
    try:
        r = resolver.resolve(p)
    except Exception as exc:  # noqa: BLE001 -- InvalidPath/PathEscape dari PathResolver
        raise GitError("outside_root", str(exc)) from exc
    rel = str(r.relative)
    if rel in ("", ".") or ".git" in PurePosixPath(rel).parts:
        raise GitError("outside_root", "path tidak sah")
    if r.real_relative is not None and r.real_relative != r.relative:
        raise GitError("symlink", "path melewati symlink")
    return rel


def _daftar_path(paths: Any) -> list[Any]:
    if not isinstance(paths, list) or not paths:
        raise GitError("invalid_paths", "paths wajib berupa daftar tidak kosong.")
    if len(paths) > MAX_PATHS:
        raise GitError("invalid_paths", f"Paling banyak {MAX_PATHS} path per permintaan.")
    return paths


def stage(akar: Path, resolver: Any, paths: Any, opsi: OpsiGit) -> dict[str, Any]:
    muat()   # mesin vendor dimuat sebelum impor dulwich apa pun
    from dulwich import porcelain

    repo = buka(akar)
    try:
        index = repo.open_index()
        terlacak = {_teks(p) for p in index}
        pengabai = _pengabai(repo)
        ditolak: list[dict[str, Any]] = []
        calon: list[str] = []

        def tolak(path: str, alasan: str, **ekstra: Any) -> None:
            ditolak.append({"path": path, "reason": alasan, **ekstra})

        def periksa_berkas(rel: str, *, eksplisit: bool) -> None:
            abs_ = akar / rel
            if abs_.is_symlink():
                tolak(rel, "symlink")
                return
            if rel not in terlacak and _diabaikan(pengabai, rel):
                if eksplisit:
                    tolak(rel, "ignored")
                return
            if not abs_.exists():
                if rel in terlacak:
                    calon.append(rel)      # hapus dari index
                elif eksplisit:
                    tolak(rel, "not_found")
                return
            ukuran = abs_.stat().st_size
            if ukuran > opsi.max_file_bytes:
                tolak(rel, "too_large", size=ukuran, limitBytes=opsi.max_file_bytes)
                return
            calon.append(rel)

        for p in _daftar_path(paths):
            try:
                rel = _periksa_path(resolver, p)
            except GitError as exc:
                tolak(p if isinstance(p, str) else "", exc.code)
                continue
            abs_ = akar / rel
            if abs_.is_dir() and not abs_.is_symlink():
                if _diabaikan(pengabai, rel, is_dir=True):
                    tolak(rel, "ignored")
                    continue
                for dasar, dirs, nama_berkas in os.walk(abs_):
                    rel_dasar = Path(dasar).relative_to(akar).as_posix()
                    simpan = []
                    for d in sorted(dirs):
                        rd = f"{rel_dasar}/{d}"
                        if (Path(dasar) / d).is_symlink():
                            tolak(rd, "symlink")
                        elif not _diabaikan(pengabai, rd, is_dir=True):
                            simpan.append(d)
                    dirs[:] = simpan
                    for n in sorted(nama_berkas):
                        periksa_berkas(f"{rel_dasar}/{n}", eksplisit=False)
                    if len(calon) > MAX_EXPANDED_FILES:
                        raise GitError("too_many", f"Lebih dari {MAX_EXPANDED_FILES} berkas; "
                                       "pilih folder yang lebih kecil.")
                # berkas terlacak yang sudah dihapus di dalam folder ini
                awal = rel + "/"
                for t in terlacak:
                    if t.startswith(awal) and not os.path.lexists(akar / t):
                        calon.append(t)
            else:
                periksa_berkas(rel, eksplisit=True)
        unik = sorted(set(calon))
        if unik:
            porcelain.add(repo, paths=[str(akar / Path(*r.split("/"))) for r in unik])
        return {"staged": unik, "rejected": ditolak}
    finally:
        repo.close()


def unstage(akar: Path, resolver: Any, paths: Any) -> dict[str, Any]:
    muat()   # mesin vendor dimuat sebelum impor dulwich apa pun
    from dulwich import porcelain

    repo = buka(akar)
    try:
        st = porcelain.status(repo, untracked_files="no")
        staged = {_rel_status(p) for daftar in st.staged.values() for p in daftar}
        pilih: set[str] = set()
        for p in _daftar_path(paths):
            try:
                rel = _periksa_path(resolver, p)
            except GitError:
                continue
            awal = rel + "/"
            pilih |= {s for s in staged if s == rel or s.startswith(awal)}
        hasil = sorted(pilih)
        if hasil:
            repo.get_worktree().unstage([os.path.join(*r.split("/")) for r in hasil])
        return {"unstaged": hasil}
    finally:
        repo.close()


def pindai_rahasia(path: str, isi: bytes) -> list[dict[str, Any]]:
    """Temuan ``{path, line, kind}``; nilai yang cocok tidak pernah dikembalikan."""
    nama = path.rsplit("/", 1)[-1]
    temuan: list[dict[str, Any]] = []
    if nama == ".env" or (nama.startswith(".env.") and nama not in (".env.example", ".env.sample")):
        temuan.append({"path": path, "line": 1, "kind": "env_file"})
    potong = isi[:MAX_SCAN_BYTES]
    if b"\x00" in potong[:8000]:
        return temuan
    for no, baris in enumerate(potong.split(b"\n"), start=1):
        for jenis, pola in _POLA_RAHASIA:
            if pola.search(baris):
                temuan.append({"path": path, "line": no, "kind": jenis})
                break
        if len(temuan) >= _MAX_TEMUAN:
            break
    return temuan


def commit(akar: Path, message: Any, opsi: OpsiGit) -> dict[str, Any]:
    muat()   # mesin vendor dimuat sebelum impor dulwich apa pun
    from dulwich import porcelain

    if not isinstance(message, str) or not message.strip():
        raise GitError("empty_message", "Pesan commit wajib diisi.")
    pesan = message.strip().replace("\r\n", "\n")
    if len(pesan) > MAX_MESSAGE:
        raise GitError("message_too_long", f"Pesan commit paling panjang {MAX_MESSAGE} karakter.")
    if opsi.identity is None:
        raise GitError("identity_missing",
                       "Identitas commit tidak dikirim Workbench. Muat ulang halaman lalu coba lagi.")
    repo = buka(akar)
    try:
        st = porcelain.status(repo, untracked_files="no")
        if not any(st.staged.values()):
            raise GitError("nothing_staged", "Belum ada perubahan yang dipilih untuk commit.")
        index = repo.open_index()
        temuan: list[dict[str, Any]] = []
        for p in sorted(_rel_status(x) for x in st.staged["add"] + st.staged["modify"]):
            sha = index[_tree_path(p)].sha
            blob = repo.object_store[sha]
            data = blob.as_raw_string()
            if len(data) > opsi.max_file_bytes:
                raise GitError("too_large", f"{p} melebihi batas ukuran.", path=p,
                               size=len(data), limitBytes=opsi.max_file_bytes)
            temuan += pindai_rahasia(p, data)
        if temuan:
            raise GitError("secret_detected", "Commit ditolak: ada berkas yang tampak berisi rahasia.",
                           findings=temuan[:_MAX_TEMUAN])
        ident = opsi.identity.as_bytes()
        sha = porcelain.commit(repo, message=pesan.encode("utf-8"), author=ident,
                               committer=ident, no_verify=True)
        teks = _teks(sha)
        return {"sha": teks, "shortSha": teks[:7]}
    finally:
        repo.close()


def log(akar: Path, limit: Any) -> dict[str, Any]:
    muat()   # mesin vendor dimuat sebelum impor dulwich apa pun
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        limit = 20
    limit = min(limit, MAX_LOG)
    repo = buka(akar)
    try:
        head = _head(repo)
        if head is None:
            return {"commits": []}
        hasil = []
        for e in repo.get_walker(include=[head], max_entries=limit):
            c = e.commit
            nama = _teks(c.author).split(" <", 1)[0]
            waktu = datetime.fromtimestamp(
                c.author_time, tz=timezone(timedelta(seconds=c.author_timezone)))
            sha = _teks(c.id)
            hasil.append({"sha": sha, "shortSha": sha[:7], "message": _teks(c.message).strip(),
                          "authoredAt": waktu.isoformat(), "authorName": nama})
        return {"commits": hasil}
    finally:
        repo.close()


def _blob_head(repo: Any, rel: str) -> bytes | None:
    from dulwich.object_store import tree_lookup_path

    head = _head(repo)
    if head is None:
        return None
    try:
        _mode, sha = tree_lookup_path(repo.__getitem__, repo[head].tree, _tree_path(rel))
    except KeyError:
        return None
    obj = repo.object_store[sha]
    return obj.as_raw_string() if obj.type_name == b"blob" else None


def diff_summary(akar: Path, resolver: Any, path: Any) -> dict[str, Any]:
    """Ringkasan perubahan berkas kerja terhadap HEAD, tanpa isi mentah."""
    muat()   # mesin vendor dimuat sebelum impor dulwich apa pun
    rel = _periksa_path(resolver, path)
    repo = buka(akar)
    try:
        lama = _blob_head(repo, rel)
    finally:
        repo.close()
    abs_ = akar / rel
    baru = None
    if abs_.is_file() and not abs_.is_symlink():
        if abs_.stat().st_size > MAX_DIFF_BYTES:
            return {"path": rel, "kind": "too_large", "status": "modified" if lama is not None else "added"}
        baru = abs_.read_bytes()
    if lama is None and baru is None:
        raise GitError("not_found", "Berkas tidak ada di laptop maupun di commit terakhir.")
    keadaan = ("added" if lama is None else "deleted" if baru is None
               else "unchanged" if lama == baru else "modified")
    hasil: dict[str, Any] = {"path": rel, "status": keadaan}
    if any(len(x) > MAX_DIFF_BYTES for x in (lama, baru) if x is not None):
        return {**hasil, "kind": "too_large"}
    if rel.endswith(".ipynb"):
        ringkas = _diff_notebook(lama, baru)
        if ringkas is not None:
            return {**hasil, "kind": "notebook", **ringkas}
    if any(b"\x00" in x[:8000] for x in (lama, baru) if x is not None):
        return {**hasil, "kind": "binary"}
    a = (lama or b"").decode("utf-8", "replace").splitlines()
    b = (baru or b"").decode("utf-8", "replace").splitlines()
    tambah = kurang = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            kurang += i2 - i1
        if op in ("replace", "insert"):
            tambah += j2 - j1
    return {**hasil, "kind": "text", "added": tambah, "removed": kurang}


def _sel(isi: bytes | None) -> list[dict[str, Any]] | None:
    if isi is None:
        return []
    try:
        nb = json.loads(isi.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    cells = nb.get("cells") if isinstance(nb, dict) else None
    return [c for c in cells if isinstance(c, dict)] if isinstance(cells, list) else None


def _teks_sel(c: Mapping[str, Any], kunci: str) -> str:
    v = c.get(kunci, "")
    if isinstance(v, list):
        return "".join(str(x) for x in v)
    return json.dumps(v, sort_keys=True) if not isinstance(v, str) else v


def _diff_notebook(lama: bytes | None, baru: bytes | None) -> dict[str, int] | None:
    a, b = _sel(lama), _sel(baru)
    if a is None or b is None:
        return None
    pakai_id = all(isinstance(c.get("id"), str) for c in a + b)
    kunci_a = {(c["id"] if pakai_id else i): c for i, c in enumerate(a)}
    kunci_b = {(c["id"] if pakai_id else i): c for i, c in enumerate(b)}
    sama = kunci_a.keys() & kunci_b.keys()
    diubah = sum(1 for k in sama if _teks_sel(kunci_a[k], "source") != _teks_sel(kunci_b[k], "source"))
    keluaran = sum(1 for k in sama if json.dumps(kunci_a[k].get("outputs"), sort_keys=True)
                   != json.dumps(kunci_b[k].get("outputs"), sort_keys=True))
    return {"cellsAdded": len(kunci_b.keys() - sama), "cellsRemoved": len(kunci_a.keys() - sama),
            "cellsModified": diubah, "outputsChanged": keluaran}
