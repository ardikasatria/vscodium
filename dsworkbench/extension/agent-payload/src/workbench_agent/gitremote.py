"""Remote GitHub untuk repo mata kuliah (GH-03; ADR-051 §6-§9; kontrak §3.3).

- Remote **hanya** ``https://github.com/<owner>/<repo>.git`` (§6). Host lain
  hanya dapat disuntikkan lewat konstruktor, yaitu di test. Browser tidak
  pernah mengirim URL.
- Tidak ada force push, penulisan ulang riwayat, atau merge otomatis (§7):
  - push hanya bila remote leluhur HEAD;
  - pull hanya fast-forward;
  - divergen → berhenti. Pemilik dapat meminta ``git.merge`` (amandemen §7,
    0.4.7): merge commit dibuat hanya bila tidak ada berkas yang diubah di
    kedua sisi; selain itu berhenti dan menyebut berkasnya.
- Token hanya diteruskan ke klien HTTP dulwich di memori, tidak ditulis ke
  ``.git/config``. Klien memakai ``urllib3.PoolManager`` dengan bundel certifi
  (ADR-051 §5, TLS).
- Semua akses repo lewat :func:`gitlocal.buka` (``RepoAman``: tanpa hooks,
  tanpa filter, ``core.symlinks=false``), termasuk checkout hasil clone/pull.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from . import gitlocal
from .githubauth import GitHubAuth, GitHubAuthError
from .gitlocal import GitError, OpsiGit

GIT_BASE = "https://github.com"
CABANG = "main"
PESAN_GABUNG = b"Gabungkan perubahan dari GitHub\n"
#: Berkas bentrok yang disebut ke UI paling banyak sekian.
_MAX_BENTROK = 20
_PESAN_DIVERGEN = ("Riwayat di laptop dan di GitHub berbeda arah. "
                   "Workbench tidak menggabungkan otomatis.")
REF_MAIN = b"refs/heads/main"
REF_REMOTE = b"refs/remotes/origin/main"
_POLA_FULL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
_POLA_NAMA_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
#: Kebijakan visibilitas (ADR-051 §11) yang mewajibkan repo private.
_WAJIB_PRIVATE = ("private", "private-until-deadline")


class Dibatalkan(Exception):
    pass


def periksa_full_name(full: Any) -> str:
    if not isinstance(full, str) or not _POLA_FULL.fullmatch(full):
        raise GitError("invalid_repo", "Nama repository harus berbentuk pemilik/nama.")
    nama = full.split("/", 1)[1]
    if nama in (".", "..") or nama.lower().endswith(".git"):
        raise GitError("invalid_repo", "Nama repository tidak sah.")
    return full


def _pool_manager(base: str) -> Any:
    import urllib3

    timeout = urllib3.Timeout(connect=15.0, read=120.0)
    if base.startswith("https:"):
        import certifi

        return urllib3.PoolManager(ca_certs=certifi.where(), cert_reqs="CERT_REQUIRED",
                                   timeout=timeout)
    return urllib3.PoolManager(timeout=timeout)


class GitHubRemote:
    def __init__(self, auth: GitHubAuth, *, git_base: str = GIT_BASE,
                 pool_manager: Callable[[str], Any] = _pool_manager):
        self.auth = auth
        self._git_base = git_base.rstrip("/")
        self._pool = pool_manager

    # -- REST ----------------------------------------------------------------

    def _api(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        try:
            return self.auth.api(method, path, body)
        except GitHubAuthError as exc:
            raise GitError(exc.code, exc.detail) from exc

    def repos(self, query: Any = None) -> dict[str, Any]:
        st, isi = self._api("GET", "/user/repos?per_page=100&sort=updated"
                                   "&affiliation=owner,collaborator,organization_member")
        if st != 200 or not isinstance(isi, list):
            raise GitError("github_error", f"Daftar repository tidak dapat dibaca (HTTP {st}).")
        q = query.strip().lower() if isinstance(query, str) else ""
        hasil = []
        for r in isi:
            if not isinstance(r, Mapping) or not isinstance(r.get("full_name"), str):
                continue
            if q and q not in r["full_name"].lower():
                continue
            hasil.append(_repo_publik(r))
        return {"repos": hasil[:100]}

    def repo_create(self, name: Any, private: Any = True, description: Any = None) -> dict[str, Any]:
        if not isinstance(name, str) or not _POLA_NAMA_REPO.fullmatch(name) or name in (".", ".."):
            raise GitError("invalid_repo", "Nama repository hanya huruf, angka, titik, - dan _.")
        body: dict[str, Any] = {"name": name, "private": private is not False, "auto_init": False}
        if isinstance(description, str) and description.strip():
            body["description"] = description.strip()[:350]
        st, isi = self._api("POST", "/user/repos", body)
        if st == 422:
            raise GitError("repo_exists", "Repository dengan nama itu sudah ada di akun Anda.")
        if st in (403, 404):
            raise GitError("repo_create_forbidden",
                           "Aplikasi GitHub tidak diizinkan membuat repository. Buat di github.com, lalu pilih di sini.")
        if st != 201 or not isinstance(isi, Mapping):
            raise GitError("github_error", f"Repository tidak dapat dibuat (HTTP {st}).")
        return _repo_publik(isi)

    def repo_info(self, full: str) -> dict[str, Any]:
        st, isi = self._api("GET", f"/repos/{full}")
        if st == 404:
            raise GitError("repo_not_found",
                           "Repository tidak ditemukan atau aplikasi GitHub belum dipasang di repository itu.")
        if st != 200 or not isinstance(isi, Mapping):
            raise GitError("github_error", f"Repository tidak dapat dibaca (HTTP {st}).")
        return _repo_publik(isi)

    # -- git -----------------------------------------------------------------

    def _klien(self) -> tuple[Any, Any]:
        from dulwich.client import Urllib3HttpGitClient

        try:
            user, sandi = self.auth.git_credentials()
        except GitHubAuthError as exc:
            raise GitError(exc.code, exc.detail) from exc
        klien = Urllib3HttpGitClient(self._git_base, pool_manager=self._pool(self._git_base),
                                     username=user, password=sandi, quiet=True)
        return klien, sandi

    def _path(self, full: str) -> bytes:
        return f"/{full}.git".encode()

    def _jalankan(self, kerja: Callable[[], Any], sandi: str) -> Any:
        """Petakan galat dulwich/urllib3 ke kode stabil; token tidak ikut pesan."""
        from dulwich.client import HTTPUnauthorized
        from dulwich.errors import GitProtocolError, HangupException, NotGitRepository

        try:
            return kerja()
        except Dibatalkan:
            raise
        except HTTPUnauthorized as exc:
            raise GitError("auth_expired", "GitHub menolak kredensial. Tautkan ulang akun GitHub.") from exc
        except NotGitRepository as exc:
            raise GitError("repo_not_found", "Repository tidak ditemukan di GitHub.") from exc
        except (GitProtocolError, HangupException) as exc:
            pesan = str(exc).replace(sandi, "***") if sandi else str(exc)
            if "403" in pesan or "denied" in pesan.lower():
                raise GitError("no_push_access", "Akun GitHub Anda tidak dapat menulis ke repository ini.") from exc
            raise GitError("network", f"Pertukaran git dengan GitHub gagal ({pesan[:160]}).") from exc
        except OSError as exc:
            raise GitError("network", "GitHub tidak dapat dihubungi dari laptop ini.") from exc
        except Exception as exc:  # urllib3 & lainnya
            if type(exc).__module__.startswith("urllib3"):
                raise GitError("network", "GitHub tidak dapat dihubungi dari laptop ini.") from exc
            raise

    def _progres(self, ctx: Any) -> Callable[[bytes], None]:
        def _p(data: bytes) -> None:
            if ctx is not None and getattr(ctx, "cancel_requested", False):
                raise Dibatalkan()
            if ctx is not None and data:
                teks = data.decode("utf-8", "replace").strip()
                if teks:
                    ctx.emit("stdout", teks.splitlines()[-1][:200] + "\n")
        return _p

    def _fetch(self, repo: Any, full: str, ctx: Any) -> bytes | None:
        """Ambil ``main`` remote ke ``refs/remotes/origin/main``; ``None`` = repo kosong."""
        klien, sandi = self._klien()
        tampung: dict[str, bytes | None] = {"sha": None}

        def mau(refs: Mapping[bytes, bytes], depth: Any = None, **_kw: Any) -> list[bytes]:
            sha = refs.get(REF_MAIN)
            tampung["sha"] = sha
            if not sha or sha in repo.object_store:
                return []
            return [sha]

        self._jalankan(lambda: klien.fetch(self._path(full), repo, determine_wants=mau,
                                           progress=self._progres(ctx)), sandi)
        sha = tampung["sha"]
        if sha:
            repo.refs[REF_REMOTE] = sha
        return sha

    # -- operasi --------------------------------------------------------------

    def remote_bind(self, akar: Path, full: Any) -> dict[str, Any]:
        full = periksa_full_name(full)
        info = self.repo_info(full)
        if not info["permissions"]["push"]:
            raise GitError("no_push_access", "Akun GitHub Anda tidak dapat menulis ke repository ini.")
        repo = gitlocal.buka(akar)
        try:
            _set_origin(repo, info["fullName"])
        finally:
            repo.close()
        return info

    def push(self, akar: Path, opsi: OpsiGit, policy: Mapping[str, Any] | None,
             ctx: Any = None) -> dict[str, Any]:
        repo = gitlocal.buka(akar)
        try:
            full = _origin(repo)
            if gitlocal._cabang(repo) != CABANG:
                raise GitError("branch_not_main", "Hanya cabang main yang dapat di-push.")
            head = gitlocal._head(repo)
            if head is None:
                raise GitError("nothing_to_push", "Belum ada commit untuk di-push.")
            info = self.repo_info(full)
            visibilitas = info["visibility"]
            aturan = (policy or {}).get("visibility") or "private"
            if aturan in _WAJIB_PRIVATE and info["private"] is not True:
                raise GitError("visibility_forbidden",
                               "Kebijakan Kelas melarang push ke repository publik. Ubah repository menjadi private di GitHub.",
                               visibility=visibilitas)
            if not info["permissions"]["push"]:
                raise GitError("no_push_access", "Akun GitHub Anda tidak dapat menulis ke repository ini.")
            jauh = self._fetch(repo, full, ctx)
            if jauh == head:
                return {"sha": _teks(head), "shortSha": _teks(head)[:7], "branch": CABANG,
                        "files": 0, "bytes": 0, "visibility": visibilitas, "upToDate": True,
                        "fullName": full, "htmlUrl": info["htmlUrl"]}
            if jauh is not None and not _leluhur(repo, jauh, head):
                if _leluhur(repo, head, jauh):
                    raise GitError("remote_ahead", "Ada commit di GitHub yang belum ada di laptop. Tarik dulu, lalu push lagi.")
                raise GitError("diverged", _PESAN_DIVERGEN)
            ukuran, berkas = _ukuran_kiriman(repo, jauh, head)
            batas = _batas_push(opsi)
            if ukuran > batas:
                raise GitError("too_large", "Total kiriman melebihi batas push.", size=ukuran, limitBytes=batas)
            klien, sandi = self._klien()

            def refs_baru(refs: dict[bytes, bytes]) -> dict[bytes, bytes]:
                # Server boleh maju sejak fetch: tolak di sini juga (tanpa force).
                sekarang = refs.get(REF_MAIN)
                if sekarang not in (None, jauh):
                    raise GitError("remote_ahead", "GitHub berubah selama push. Tarik dulu, lalu push lagi.")
                return {**refs, REF_MAIN: head}

            hasil = self._jalankan(lambda: klien.send_pack(
                self._path(full), refs_baru, repo.generate_pack_data,
                progress=self._progres(ctx)), sandi)
            status = (getattr(hasil, "ref_status", None) or {}).get(REF_MAIN)
            if status:
                raise GitError("push_rejected", f"GitHub menolak push: {str(status)[:160]}")
            repo.refs[REF_REMOTE] = head
            return {"sha": _teks(head), "shortSha": _teks(head)[:7], "branch": CABANG,
                    "files": berkas, "bytes": ukuran, "visibility": visibilitas,
                    "upToDate": False, "fullName": full, "htmlUrl": info["htmlUrl"]}
        finally:
            repo.close()

    def pull(self, akar: Path, ctx: Any = None) -> dict[str, Any]:
        repo = gitlocal.buka(akar)
        try:
            full = _origin(repo)
            if gitlocal._cabang(repo) != CABANG:
                raise GitError("branch_not_main", "Hanya cabang main yang dapat ditarik.")
            jauh = self._fetch(repo, full, ctx)
            head = gitlocal._head(repo)
            if jauh is None or jauh == head:
                return {"sha": _teks(head) if head else None, "updated": False, "files": 0}
            if head is not None and _leluhur(repo, jauh, head):
                return {"sha": _teks(head), "updated": False, "files": 0, "localAhead": True}
            if head is not None and not _leluhur(repo, head, jauh):
                raise GitError("diverged", _PESAN_DIVERGEN)
            n = _majukan(repo, akar, head, jauh)
            return {"sha": _teks(jauh), "shortSha": _teks(jauh)[:7], "updated": True, "files": n}
        finally:
            repo.close()

    def merge(self, akar: Path, opsi: OpsiGit, ctx: Any = None) -> dict[str, Any]:
        """Gabungkan ``main`` GitHub ke ``main`` laptop atas permintaan pemilik.

        Tidak mengirim apa pun ke GitHub; pemilik menekan Push sesudahnya.
        """
        repo = gitlocal.buka(akar)
        try:
            full = _origin(repo)
            if gitlocal._cabang(repo) != CABANG:
                raise GitError("branch_not_main", "Hanya cabang main yang dapat digabungkan.")
            if opsi.identity is None:
                raise GitError("identity_missing",
                               "Identitas commit tidak dikirim Workbench. Muat ulang halaman lalu coba lagi.")
            jauh = self._fetch(repo, full, ctx)
            head = gitlocal._head(repo)
            if jauh is None or (head is not None and _leluhur(repo, jauh, head)):
                return {"sha": _teks(head) if head else None, "merged": False, "updated": False,
                        "files": 0}
            if head is None or _leluhur(repo, head, jauh):
                n = _majukan(repo, akar, head, jauh)
                return {"sha": _teks(jauh), "shortSha": _teks(jauh)[:7], "merged": False,
                        "updated": True, "files": n}
            sha, n = _gabungkan(repo, akar, head, jauh, opsi.identity.as_bytes())
            return {"sha": _teks(sha), "shortSha": _teks(sha)[:7], "merged": True,
                    "updated": True, "files": n}
        finally:
            repo.close()

    def clone(self, akar: Path, full: Any, opsi: OpsiGit, ctx: Any = None) -> dict[str, Any]:
        full = periksa_full_name(full)
        akar.mkdir(parents=True, exist_ok=True)
        isi = [p.name for p in akar.iterdir() if p.name != ".workbench"]
        if isi:
            raise GitError("not_empty", "Clone hanya ke folder mata kuliah yang masih kosong.")
        info = self.repo_info(full)
        gitlocal.init(akar, opsi)            # RepoAman + core.symlinks=false + .gitignore
        (akar / ".gitignore").unlink(missing_ok=True)   # pakai .gitignore milik repo
        repo = gitlocal.buka(akar)
        try:
            _set_origin(repo, info["fullName"])
            jauh = self._fetch(repo, info["fullName"], ctx)
            n = _majukan(repo, akar, None, jauh) if jauh else 0
            if not (akar / ".gitignore").exists():
                (akar / ".gitignore").write_text(gitlocal.isi_gitignore(opsi), encoding="utf-8",
                                                 newline="\n")
            return {**info, "sha": _teks(jauh) if jauh else None, "files": n}
        finally:
            repo.close()


# ---------------------------------------------------------------------------


def _teks(sha: bytes | str) -> str:
    return sha.decode("ascii") if isinstance(sha, bytes) else sha


def _repo_publik(r: Mapping[str, Any]) -> dict[str, Any]:
    perm = r.get("permissions") if isinstance(r.get("permissions"), Mapping) else {}
    private = r.get("private") is True
    vis = r.get("visibility") if r.get("visibility") in ("public", "private", "internal") else (
        "private" if private else "public")
    html = r.get("html_url")
    return {"fullName": r.get("full_name"), "private": private, "visibility": vis,
            "htmlUrl": html if isinstance(html, str) and html.startswith("https://") else None,
            "defaultBranch": r.get("default_branch") if isinstance(r.get("default_branch"), str) else None,
            "permissions": {"push": perm.get("push") is True}}


def _set_origin(repo: Any, full: str) -> None:
    cfg = repo.get_config()
    cfg.set((b"remote", b"origin"), b"url", f"https://github.com/{full}.git".encode())
    cfg.set((b"remote", b"origin"), b"fetch", b"+refs/heads/*:refs/remotes/origin/*")
    cfg.write_to_path()


def _origin(repo: Any) -> str:
    remote = gitlocal._remote(repo)
    if not remote:
        raise GitError("not_bound", "Folder mata kuliah ini belum terhubung ke repository GitHub.")
    return periksa_full_name(remote["fullName"])


def _leluhur(repo: Any, a: bytes, b: bytes) -> bool:
    """``a`` leluhur (atau sama dengan) ``b``."""
    from dulwich.graph import can_fast_forward

    if a == b:
        return True
    if a not in repo.object_store or b not in repo.object_store:
        return False
    return can_fast_forward(repo, a, b)


def _berubah(store: Any, lama: bytes | None, baru: bytes) -> dict[bytes, tuple[int, bytes] | None]:
    """Path yang berubah dari tree ``lama`` ke ``baru`` → ``(mode, sha)`` baru; ``None`` = dihapus."""
    from dulwich.diff_tree import tree_changes

    hasil: dict[bytes, tuple[int, bytes] | None] = {}
    for ch in tree_changes(store, lama, baru):
        if ch.old is not None and ch.old.path:
            hasil.setdefault(ch.old.path, None)
        if ch.new is not None and ch.new.path:
            hasil[ch.new.path] = (ch.new.mode, ch.new.sha)
    return hasil


def _gabungkan(repo: Any, akar: Path, head: bytes, jauh: bytes, ident: bytes) -> tuple[bytes, int]:
    """Merge commit ``head`` + ``jauh`` tanpa menggabungkan isi berkas.

    Berkas yang diubah di kedua sisi dengan hasil berbeda adalah bentrok,
    termasuk bila barisnya tidak tumpang-tindih: penggabungan per baris dapat
    merusak notebook (JSON) tanpa terlihat. Tidak ada yang ditulis bila bentrok.
    """
    from dulwich.graph import find_merge_base
    from dulwich.merge import Merger
    from dulwich.objects import Commit

    store = repo.object_store
    basis = find_merge_base(repo, [head, jauh])
    pohon_basis = store[store[basis[0]].tree] if basis else None   # None: riwayat tak berkaitan
    kami, mereka = store[store[head].tree], store[store[jauh].tree]
    id_basis = pohon_basis.id if pohon_basis is not None else None
    ubah_kami = _berubah(store, id_basis, kami.id)
    ubah_mereka = _berubah(store, id_basis, mereka.id)
    bentrok = sorted(p for p in ubah_kami.keys() & ubah_mereka.keys()
                     if ubah_kami[p] != ubah_mereka[p])
    if not bentrok:
        # Tanpa gitattributes/config: tidak ada merge driver yang dijalankan.
        pohon, bentrok = Merger(store).merge_trees(pohon_basis, kami, mereka)
        bentrok = sorted(bentrok)
    if bentrok:
        jalur = [p.decode("utf-8", "replace")[:200] for p in bentrok]
        raise GitError("merge_conflict",
                       "Berkas yang sama diubah di laptop dan di GitHub; Workbench tidak menggabungkan isinya.",
                       paths=jalur[:_MAX_BENTROK], total=len(jalur))
    store.add_object(pohon)
    c = Commit()
    c.tree = pohon.id
    c.parents = [head, jauh]
    c.author = c.committer = ident
    c.author_time = c.commit_time = int(time.time())
    c.author_timezone = c.commit_timezone = time.localtime().tm_gmtoff or 0
    c.encoding = b"UTF-8"
    c.message = PESAN_GABUNG
    store.add_object(c)
    return c.id, _majukan(repo, akar, head, c.id)


def _batas_push(opsi: OpsiGit) -> int:
    return getattr(opsi, "max_push_bytes", None) or 200 * 1024 * 1024


def _ukuran_kiriman(repo: Any, jauh: bytes | None, head: bytes) -> tuple[int, int]:
    """``(byte objek baru, jumlah berkas berubah)`` antara remote dan HEAD."""
    from dulwich.diff_tree import tree_changes
    from dulwich.object_store import MissingObjectFinder

    store = repo.object_store
    haves = [jauh] if jauh else []
    total = 0
    for sha, _hint in MissingObjectFinder(store, haves, [head]):
        total += len(store[sha].as_raw_string())
    lama = store[jauh].tree if jauh else None
    berkas = sum(1 for _ in tree_changes(store, lama, store[head].tree))
    return total, berkas


def _majukan(repo: Any, akar: Path, head: bytes | None, target: bytes) -> int:
    """Majukan ``main`` ke ``target`` (keturunan ``head``) dan tulis berkas kerja; tolak bila kotor."""
    from dulwich import porcelain
    from dulwich.diff_tree import tree_changes

    store = repo.object_store
    lama = store[head].tree if head else None
    perubahan = list(tree_changes(store, lama, store[target].tree))
    if head is not None:
        st = porcelain.status(repo, untracked_files="no")
        if any(st.staged.values()) or st.unstaged:
            raise GitError("dirty_worktree", "Ada perubahan yang belum di-commit. Commit atau batalkan dulu, lalu tarik lagi.")
    for ch in perubahan:
        jalur = ch.new.path if ch.new and ch.new.path else None
        if not jalur:
            continue
        rel = jalur.decode("utf-8", "replace")
        bagian = rel.split("/")
        if ".git" in (b.lower() for b in bagian) or ".." in bagian or rel.startswith("/"):
            raise GitError("unsafe_path", f"Repository memuat path yang tidak diizinkan: {rel[:120]}")
        tujuan = akar / Path(*bagian)
        # Berkas belum terlacak yang akan tertimpa isi dari GitHub.
        if (ch.old is None or ch.old.path is None) and os.path.lexists(tujuan):
            raise GitError("dirty_worktree", f"{rel[:120]} sudah ada di laptop dan belum di-commit.")
    repo.refs[REF_MAIN] = target
    porcelain.reset(repo, "hard", target)
    # core.symlinks=false (lapis paksa RepoAman): symlink dari repo menjadi berkas
    # biasa. Pastikan tidak satu pun symlink tertulis.
    for ch in perubahan:
        if ch.new and ch.new.path:
            p = akar / Path(*ch.new.path.decode("utf-8", "replace").split("/"))
            if p.is_symlink():
                p.unlink()
                raise GitError("unsafe_path", "Repository memuat symlink; dibatalkan.")
    return len(perubahan)
