"""Tautan akun GitHub di laptop (GH-02; ADR-051 §2, §4; kontrak §3.2).

Token GitHub **hanya** ada di sini:

- tidak pernah dikirim ke Control API;
- tidak masuk hasil relay maupun log;
- ``device_code`` tetap di memori proses agent.

Penyimpanan ``<state>/github.json``:

- izin berkas ``0600``;
- di Windows setiap rahasia dibungkus DPAPI (``CryptProtectData``, entropy =
  id perangkat), sehingga salinan berkas ke akun atau komputer lain tidak
  terbaca (terbukti GH-00);
- di macOS/Linux ``0600`` saja (ADR-051 §2).

Device flow sama untuk GitHub App dan OAuth App. Bedanya hanya ``scope=repo``
untuk OAuth App. Host GitHub adalah konstanta; test menyuntikkan server palsu
lewat konstruktor, bukan lewat payload.
"""

from __future__ import annotations

import base64
import json
import os
import re
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

WEB_BASE = "https://github.com"
API_BASE = "https://api.github.com"
USER_AGENT = "workbench-agent (GH-02)"
TIMEOUT_SECONDS = 10.0
FILE_NAME = "github.json"

#: Client ID publik GitHub (``Iv1.``/``Iv23…`` GitHub App, ``Ov23…`` OAuth App).
_POLA_CLIENT_ID = re.compile(r"^[A-Za-z0-9.]{10,64}$")
_JENIS = ("github-app", "oauth-app")
#: Sisa waktu minimum sebelum token dianggap kedaluwarsa (refresh lebih dulu).
_JEDA_REFRESH = 300


class GitHubAuthError(Exception):
    def __init__(self, code: str, detail: str, **extra: Any):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.extra = extra

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, **self.extra}


# ---------------------------------------------------------------------------
# Rahasia saat istirahat
# ---------------------------------------------------------------------------


def _dpapi(data: bytes, entropy: bytes, *, protect: bool) -> bytes:
    """CryptProtectData/CryptUnprotectData lewat ctypes (tanpa dependensi)."""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes) -> tuple[BLOB, Any]:
        buf = ctypes.create_string_buffer(b, len(b))
        return BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    masuk, _b1 = blob(data)
    ent, _b2 = blob(entropy)
    keluar = BLOB()
    fungsi = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    ok = fungsi(ctypes.byref(masuk), None, ctypes.byref(ent), None, None,
                CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(keluar))
    if not ok:
        raise OSError(ctypes.get_last_error() or "DPAPI gagal")
    try:
        return ctypes.string_at(keluar.pbData, keluar.cbData)
    finally:
        kernel32.LocalFree(keluar.pbData)


class PenyimpanRahasia:
    """``dpapi:<b64>`` di Windows; ``plain:<teks>`` di tempat lain (berkas 0600)."""

    def __init__(self, entropy: str, *, windows: bool | None = None):
        self._entropy = entropy.encode("utf-8")
        self._windows = (os.name == "nt") if windows is None else windows

    def bungkus(self, rahasia: str) -> str:
        if self._windows:
            b = _dpapi(rahasia.encode("utf-8"), self._entropy, protect=True)
            return "dpapi:" + base64.b64encode(b).decode("ascii")
        return "plain:" + rahasia

    def buka(self, nilai: str) -> str:
        if nilai.startswith("dpapi:"):
            if os.name != "nt":
                raise GitHubAuthError("auth_unreadable", "Tautan GitHub dibuat di Windows lain.")
            return _dpapi(base64.b64decode(nilai[6:]), self._entropy, protect=False).decode("utf-8")
        if nilai.startswith("plain:"):
            return nilai[6:]
        raise GitHubAuthError("auth_unreadable", "Berkas tautan GitHub tidak dikenal.")


# ---------------------------------------------------------------------------
# HTTP ke GitHub
# ---------------------------------------------------------------------------


def _konteks_tls() -> Any:
    from .client import konteks_tls

    return konteks_tls()


Opener = Callable[[urllib.request.Request, float], tuple[int, bytes, Mapping[str, str]]]


def _opener_bawaan(req: urllib.request.Request, timeout: float) -> tuple[int, bytes, Mapping[str, str]]:
    konteks = _konteks_tls() if req.full_url.startswith("https:") else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=konteks) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b"", dict(exc.headers or {})


@dataclass
class _Tertunda:
    """Device flow yang sedang berjalan; ``device_code`` tidak pernah keluar."""

    device_code: str
    client_id: str
    app_type: str
    interval: int
    expires_at: float
    next_poll_at: float


class GitHubAuth:
    def __init__(self, state_dir: Path, device_id: str, *, web_base: str = WEB_BASE,
                 api_base: str = API_BASE, opener: Opener | None = None,
                 clock: Callable[[], float] = time.time, windows: bool | None = None):
        self.path = Path(state_dir) / FILE_NAME
        self._web = web_base.rstrip("/")
        self._api = api_base.rstrip("/")
        self._open = opener or _opener_bawaan
        self._now = clock
        self._rahasia = PenyimpanRahasia(device_id, windows=windows)
        self._tertunda: _Tertunda | None = None
        self._kunci = threading.Lock()

    # -- HTTP ---------------------------------------------------------------

    def _form(self, path: str, data: Mapping[str, str]) -> dict[str, Any]:
        req = urllib.request.Request(
            self._web + path, data=urllib.parse.urlencode(data).encode(), method="POST",
            headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        return self._json(req)

    def _api_req(self, method: str, path: str, token: str | None = None,
                 body: Any = None) -> tuple[int, Any]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT,
                   "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self._api + path, data=data, method=method, headers=headers)
        try:
            st, raw, _h = self._open(req, TIMEOUT_SECONDS)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GitHubAuthError("network", "GitHub tidak dapat dihubungi dari laptop ini.") from exc
        try:
            return st, (json.loads(raw) if raw else None)
        except ValueError:
            return st, None

    def _json(self, req: urllib.request.Request) -> dict[str, Any]:
        try:
            st, raw, _h = self._open(req, TIMEOUT_SECONDS)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GitHubAuthError("network", "GitHub tidak dapat dihubungi dari laptop ini.") from exc
        try:
            isi = json.loads(raw) if raw else {}
        except ValueError:
            isi = {}
        if st >= 400 and not isi.get("error"):
            raise GitHubAuthError("github_error", f"GitHub menjawab HTTP {st}.", http=st)
        return isi if isinstance(isi, dict) else {}

    # -- berkas -------------------------------------------------------------

    def _baca(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _tulis(self, data: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, sementara = tempfile.mkstemp(prefix=".github-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.chmod(sementara, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(sementara, self.path)
        except Exception:
            try:
                os.unlink(sementara)
            except OSError:
                pass
            raise

    def _hapus(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _simpan_token(self, lama: Mapping[str, Any] | None, t: Mapping[str, Any],
                      client_id: str, app_type: str, account: Mapping[str, Any]) -> dict[str, Any]:
        sekarang = self._now()
        data: dict[str, Any] = {
            "version": 1,
            "clientId": client_id,
            "appType": app_type,
            "account": dict(account),
            "scope": t.get("scope") or None,
            "linkedAt": (lama or {}).get("linkedAt") or _iso(sekarang),
            "accessToken": self._rahasia.bungkus(str(t["access_token"])),
            "expiresAt": _iso(sekarang + int(t["expires_in"])) if t.get("expires_in") else None,
            "refreshToken": (self._rahasia.bungkus(str(t["refresh_token"]))
                             if t.get("refresh_token") else None),
            "refreshExpiresAt": (_iso(sekarang + int(t["refresh_token_expires_in"]))
                                 if t.get("refresh_token_expires_in") else None),
        }
        self._tulis(data)
        return data

    # -- operasi ------------------------------------------------------------

    def start(self, cfg: Mapping[str, Any]) -> dict[str, Any]:
        client_id, app_type, app_name = _konfigurasi(cfg)
        with self._kunci:
            t = self._form("/login/device/code", {"client_id": client_id,
                                                  **({"scope": "repo"} if app_type == "oauth-app" else {})})
            if t.get("error"):
                raise _galat_device(str(t["error"]))
            try:
                kode, user_code = str(t["device_code"]), str(t["user_code"])
                uri = str(t.get("verification_uri") or "https://github.com/login/device")
                expires, interval = int(t.get("expires_in") or 900), int(t.get("interval") or 5)
            except (KeyError, TypeError, ValueError) as exc:
                raise GitHubAuthError("github_error", "Jawaban GitHub tidak lengkap.") from exc
            if not uri.startswith("https://github.com/"):
                raise GitHubAuthError("github_error", "Alamat verifikasi GitHub tidak dikenal.")
            sekarang = self._now()
            self._tertunda = _Tertunda(kode, client_id, app_type, interval,
                                       sekarang + expires, sekarang + interval)
        return {"userCode": user_code, "verificationUri": uri, "expiresIn": expires,
                "interval": interval, "appName": app_name, "appType": app_type}

    def poll(self) -> dict[str, Any]:
        with self._kunci:
            p = self._tertunda
            sekarang = self._now()
            if p is None:
                data = self._baca()
                if data and isinstance(data.get("account"), dict):
                    return {"state": "linked", "account": _akun_publik(data["account"])}
                return {"state": "expired"}
            if sekarang >= p.expires_at:
                self._tertunda = None
                return {"state": "expired"}
            if sekarang < p.next_poll_at:
                return {"state": "pending", "interval": p.interval}
            t = self._form("/login/oauth/access_token", {
                "client_id": p.client_id, "device_code": p.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            err = t.get("error")
            if err == "authorization_pending":
                p.next_poll_at = sekarang + p.interval
                return {"state": "pending", "interval": p.interval}
            if err == "slow_down":
                p.interval = int(t.get("interval") or p.interval + 5)
                p.next_poll_at = sekarang + p.interval
                return {"state": "slow_down", "interval": p.interval}
            if err == "expired_token":
                self._tertunda = None
                return {"state": "expired"}
            if err == "access_denied":
                self._tertunda = None
                return {"state": "denied"}
            if err:
                self._tertunda = None
                raise _galat_device(str(err))
            if not t.get("access_token"):
                raise GitHubAuthError("github_error", "Jawaban token GitHub tidak lengkap.")
            akun = self._akun(str(t["access_token"]))
            self._simpan_token(None, t, p.client_id, p.app_type, akun)
            self._tertunda = None
            return {"state": "linked", "account": _akun_publik(akun)}

    def _akun(self, token: str) -> dict[str, Any]:
        st, u = self._api_req("GET", "/user", token)
        if st != 200 or not isinstance(u, dict) or not isinstance(u.get("id"), int):
            raise GitHubAuthError("github_error", "Profil GitHub tidak dapat dibaca.", http=st)
        login = str(u.get("login") or "")
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", login):
            raise GitHubAuthError("github_error", "Login GitHub tidak sah.")
        avatar = u.get("avatar_url")
        return {"githubUserId": u["id"], "login": login,
                "avatarUrl": avatar if isinstance(avatar, str) and avatar.startswith("https://") else None}

    def status(self) -> dict[str, Any]:
        data = self._baca()
        if not data or not isinstance(data.get("account"), dict):
            return {"linked": False, "pending": self._tertunda is not None}
        return {"linked": True, "account": _akun_publik(data["account"]),
                "appType": data.get("appType"), "permissions": data.get("scope"),
                "linkedAt": data.get("linkedAt"), "expiresAt": data.get("expiresAt"),
                "refreshExpiresAt": data.get("refreshExpiresAt"),
                "pending": self._tertunda is not None}

    def access_token(self) -> str:
        """Token akses yang masih berlaku (refresh bila perlu). Hanya untuk agent (GH-03)."""
        with self._kunci:
            data = self._baca()
            if not data or not data.get("accessToken"):
                raise GitHubAuthError("auth_missing", "Akun GitHub belum ditautkan di laptop ini.")
            kedaluwarsa = _epoch(data.get("expiresAt"))
            if kedaluwarsa is None or kedaluwarsa - _JEDA_REFRESH > self._now():
                return self._rahasia.buka(data["accessToken"])
            refresh = data.get("refreshToken")
            batas_refresh = _epoch(data.get("refreshExpiresAt"))
            if not refresh or (batas_refresh is not None and batas_refresh <= self._now()):
                raise GitHubAuthError("auth_expired", "Tautan GitHub kedaluwarsa. Tautkan ulang.")
            t = self._form("/login/oauth/access_token", {
                "client_id": str(data.get("clientId")), "grant_type": "refresh_token",
                "refresh_token": self._rahasia.buka(refresh)})
            if t.get("error") or not t.get("access_token"):
                raise GitHubAuthError("auth_expired", "Tautan GitHub kedaluwarsa. Tautkan ulang.")
            self._simpan_token(data, t, str(data.get("clientId")), str(data.get("appType")),
                               data["account"])
            return str(t["access_token"])

    def app_type(self) -> str:
        data = self._baca() or {}
        return str(data.get("appType") or "github-app")

    def git_credentials(self) -> tuple[str, str]:
        """``(username, password)`` HTTPS git untuk token yang berlaku (GH-03).

        OAuth App: token sebagai username (terbukti GH-00 tahap 2). GitHub App:
        ``x-access-token`` + token (bentuk dokumentasi; dibuktikan di uji GitHub
        App GH-00).
        """
        token = self.access_token()
        if self.app_type() == "oauth-app":
            return token, "x-oauth-basic"
        return "x-access-token", token

    def api(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """Panggilan REST GitHub dengan token pengguna (tidak pernah dikembalikan)."""
        st, isi = self._api_req(method, path, self.access_token(), body)
        if st == 401:
            raise GitHubAuthError("auth_expired", "Tautan GitHub kedaluwarsa. Tautkan ulang di Profil.")
        return st, isi

    def revoke(self) -> dict[str, Any]:
        with self._kunci:
            self._tertunda = None
            data = self._baca()
            if data is None:
                self._hapus()
                return {"revokedLocal": True, "revokedRemote": False}
            kredensial = []
            for k in ("accessToken", "refreshToken"):
                if data.get(k):
                    try:
                        kredensial.append(self._rahasia.buka(data[k]))
                    except (GitHubAuthError, OSError, ValueError):
                        pass
            jauh = False
            if kredensial:
                try:
                    st, _ = self._api_req("POST", "/credentials/revoke",
                                          body={"credentials": kredensial})
                    jauh = st in (200, 202, 204)
                except GitHubAuthError:
                    jauh = False
            self._hapus()
            return {"revokedLocal": True, "revokedRemote": jauh}


def _konfigurasi(cfg: Mapping[str, Any]) -> tuple[str, str, str]:
    """``payload.github`` dari Control API (setting), bukan dari peramban."""
    g = cfg.get("github") if isinstance(cfg, Mapping) else None
    if not isinstance(g, Mapping):
        raise GitHubAuthError("not_configured", "Integrasi GitHub belum dikonfigurasi admin.")
    client_id, app_type = g.get("clientId"), g.get("appType")
    if not isinstance(client_id, str) or not _POLA_CLIENT_ID.fullmatch(client_id):
        raise GitHubAuthError("not_configured", "Client ID GitHub belum diisi admin.")
    if app_type not in _JENIS:
        raise GitHubAuthError("not_configured", "Jenis aplikasi GitHub tidak dikenal.")
    nama = g.get("appName")
    nama = re.sub(r"[\x00-\x1f\x7f]", "", nama).strip()[:80] if isinstance(nama, str) else ""
    return client_id, str(app_type), nama or "Data Science Workbench"


def _galat_device(err: str) -> GitHubAuthError:
    if err == "device_flow_disabled":
        return GitHubAuthError("device_flow_disabled",
                               "Device flow belum diaktifkan pada aplikasi GitHub institusi.")
    if err in ("incorrect_client_credentials", "unauthorized_client"):
        return GitHubAuthError("not_configured", "Client ID GitHub tidak dikenali GitHub.")
    return GitHubAuthError("github_error", f"GitHub menolak permintaan ({err[:40]}).")


def _akun_publik(a: Mapping[str, Any]) -> dict[str, Any]:
    return {"githubUserId": a.get("githubUserId"), "login": a.get("login"),
            "avatarUrl": a.get("avatarUrl")}


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).replace(microsecond=0).isoformat()


def _epoch(v: Any) -> float | None:
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v).timestamp()
    except ValueError:
        return None


__all__ = ["GitHubAuth", "GitHubAuthError", "PenyimpanRahasia"]
