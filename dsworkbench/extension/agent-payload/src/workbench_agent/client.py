"""Klien HTTP ke Control API.

Hanya memakai ``urllib`` pustaka standar. Setiap metode agent memakai kredensial
perangkat; tidak ada cookie, tidak ada CSRF -- itu jalur browser.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote, urljoin, urlsplit

from .errors import AuthenticationFailedError, TransportError

#: Konteks TLS dibangun sekali; membangunnya berarti membaca bundel dari disk.
_KONTEKS: ssl.SSLContext | None = None
_KONTEKS_DICARI = False


def konteks_tls() -> ssl.SSLContext | None:
    """Konteks TLS yang benar-benar memegang sertifikat akar.

    Python yang dipasang dari python.org di macOS tidak memakai Keychain dan
    datang **tanpa satu pun sertifikat akar** sampai ``Install
    Certificates.command`` dijalankan. Akibatnya setiap HTTPS gagal dengan
    ``CERTIFICATE_VERIFY_FAILED`` -- termasuk pairing, yang justru hal pertama
    yang dikerjakan mahasiswa, sehingga aplikasi tampak rusak sejak langkah
    pertama.

    ``certifi`` sudah ikut terpasang di lingkungan praktikum karena ``requests``
    memerlukannya. Bundel Mozilla itu **selalu** ditambahkan di atas penyimpanan
    bawaan, bukan hanya saat penyimpanan kosong. Windows memasang akar
    Microsoft Trusted Root secara bertahap, hanya ketika CryptoAPI memintanya,
    dan OpenSSL tidak pernah memicu unduhan itu. Akibatnya penyimpanan Windows
    tidak kosong, tetapi bisa tidak memuat ISRG Root X1/X2 yang dibutuhkan
    rantai Let's Encrypt terbaru. Akar dari penyimpanan bawaan (termasuk akar
    proxy kampus) tetap dipercaya; certifi hanya menambah.

    Verifikasi **tidak pernah** dimatikan. Kode pairing dan kredensial perangkat
    melewati koneksi ini; mematikan verifikasi akan menyerahkan keduanya kepada
    siapa pun yang berada di jaringan yang sama, dan itu harga yang tidak
    sebanding dengan kenyamanan apa pun.
    """
    global _KONTEKS, _KONTEKS_DICARI
    if _KONTEKS_DICARI:
        return _KONTEKS
    _KONTEKS_DICARI = True
    try:
        konteks = ssl.create_default_context()
    except Exception:  # noqa: BLE001 - biarkan urllib memakai bawaannya
        return None
    try:
        import certifi

        konteks.load_verify_locations(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - pesan kesalahan yang menjelaskan
        pass                                        # sudah disiapkan di _minta()
    _KONTEKS = konteks
    return konteks


def _stream(request: urllib.request.Request, dest: Path,
            progress: Callable[[int], None] | None, *, timeout: float) -> None:
    """Tulis jawaban HTTP ke ``dest`` sepotong demi sepotong."""
    konteks = konteks_tls() if urlsplit(request.full_url).scheme == "https" else None
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=konteks) as response, \
                open(dest, "wb") as keluar:
            for potong in iter(lambda: response.read(256 * 1024), b""):
                keluar.write(potong)
                if progress is not None:
                    progress(len(potong))
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise TransportError(
                f"Sertifikat {urlsplit(request.full_url).hostname} tidak dapat diverifikasi.") from exc
        raise TransportError(f"Unduhan gagal: {exc.reason}") from exc
    except OSError as exc:
        raise TransportError(f"Unduhan terputus: {exc}") from exc


def download_url(url: str, dest: Path, progress: Callable[[int], None] | None = None,
                 *, timeout: float = 60.0) -> None:
    """Unduh artefak dataset dari URL HTTPS (sumber tercantum di manifest server)."""
    if not url.startswith("https://"):
        raise TransportError("Unduhan dataset hanya lewat HTTPS.")
    request = urllib.request.Request(url, headers={"User-Agent": "workbench-agent/0.1"})
    try:
        _stream(request, dest, progress, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise TransportError(f"Sumber dataset menjawab {exc.code} untuk {url}") from None


def _pesan_sertifikat(base_url: str) -> str:
    """Kesalahan sertifikat butuh jalan keluar, bukan sekadar nama galat."""
    return (
        f"Tidak dapat memverifikasi sertifikat {base_url}.\n"
        "  Python di komputer ini belum memiliki daftar sertifikat akar.\n"
        "  Perbaikan: buka DSWorkbench lalu pilih menu 6 (Pasang ulang).\n"
        "  Bila masih gagal, jalankan sekali:\n"
        '    open "/Applications/Python 3.12/Install Certificates.command"\n'
        "  (sesuaikan angka versinya dengan Python yang terpasang)."
    )


class HttpError(TransportError):
    """Jawaban HTTP yang bukan sukses."""

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ):
        super().__init__(message, detail=code)
        self.status = status
        self.error_code = code
        self.payload = dict(payload or {})


class ControlPlaneClient:
    """Pembungkus endpoint agent di Control API."""

    def __init__(self, base_url: str, *, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout

    def pair(self, *, code: str, info: Mapping[str, str]) -> dict[str, Any]:
        return self._request("POST", "api/devices/pair", body={
            "code": code,
            "name": info.get("name", "Komputer mahasiswa"),
            "os": info.get("os", "tidak diketahui"),
            "arch": info.get("arch", "tidak diketahui"),
            "agentVersion": info.get("agentVersion", "tidak diketahui"),
        })

    def heartbeat(
        self, *, device_id: str, credential: str, busy: bool = False,
        active_jobs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"deviceId": device_id, "credential": credential, "busy": busy}
        if active_jobs is not None:
            body["activeJobs"] = active_jobs
        return self._request("POST", "api/agent/heartbeat", body=body)

    def connect(
        self,
        *,
        device_id: str,
        credential: str,
        agent_version: str | None = None,
        capabilities: list[str] | None = None,
        active_jobs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Daftarkan saluran relay, sekaligus laporkan apa yang dapat dikerjakan.

        Versi dan kemampuan dikirim di sini, bukan hanya saat pairing: agent
        boleh diunduh ulang tanpa memasangkan diri lagi, dan catatan perangkat
        harus mengikuti yang sedang berjalan.
        """
        body: dict[str, Any] = {"deviceId": device_id, "credential": credential}
        if agent_version:
            body["agentVersion"] = agent_version
        if capabilities is not None:
            body["capabilities"] = capabilities
        if active_jobs is not None:
            # Server menandai job perangkat ini yang tidak lagi ada (agent
            # dimulai ulang) sebagai gagal (ADR-049 §8).
            body["activeJobs"] = active_jobs
        return self._request("POST", "api/agent/connect", body=body)

    def disconnect(self, *, device_id: str, credential: str) -> None:
        self._request(
            "POST", "api/agent/disconnect",
            body={"deviceId": device_id, "credential": credential},
            expect_body=False,
        )

    def poll(
        self, *, device_id: str, credential: str, busy: bool = False, wait: float = 0.0
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "deviceId": device_id, "credential": credential, "busy": busy,
        }
        if wait > 0:
            # Batas waktu HTTP harus melampaui lama server menahan poll.
            body["waitSeconds"] = min(wait, max(0.0, self.timeout - 5.0))
        return self._request("POST", "api/agent/poll", body=body)

    def job_update(
        self, *, device_id: str, credential: str, job_id: str, update: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Dorong keadaan satu job ke Control API (ADR-049 §2, kontrak §7.2)."""
        body = {"deviceId": device_id, "credential": credential, **dict(update)}
        return self._request("POST", f"api/agent/jobs/{quote(job_id, safe='')}", body=body)

    def result(
        self,
        *,
        device_id: str,
        credential: str,
        message_id: str,
        status: str,
        payload: Mapping[str, Any] | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        badan: dict[str, Any] = {
            "deviceId": device_id,
            "credential": credential,
            "messageId": message_id,
            "status": status,
            "payload": dict(payload or {}),
        }
        if detail is not None:
            badan["detail"] = detail
        return self._request("POST", "api/agent/result", body=badan)

    def download_package_file(
        self, *, device_id: str, credential: str, course_id: str, path: str,
        dest: Path, progress: Callable[[int], None] | None = None,
    ) -> None:
        """Unduh artefak dataset dari course package (DL-04).

        Control API hanya melayani path yang tercantum sebagai sumber
        ``package`` di manifest dataset mata kuliah itu, untuk pemilik perangkat
        yang terdaftar di mata kuliah tersebut.
        """
        body = json.dumps({"deviceId": device_id, "credential": credential,
                           "courseId": course_id, "path": path}).encode("utf-8")
        request = urllib.request.Request(
            urljoin(self.base_url, "api/agent/package-file"), data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8",
                     "User-Agent": "workbench-agent/0.1"})
        try:
            _stream(request, dest, progress, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise AuthenticationFailedError() from None
            payload = _baca_error(exc)
            raise HttpError(str(payload.get("message") or f"Control API menjawab {exc.code}."),
                            status=exc.code, payload=payload) from None

    def health(self) -> dict[str, Any]:
        return self._request("GET", "api/health")

    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        expect_body: bool = True,
    ) -> dict[str, Any]:
        url = urljoin(self.base_url, path)
        data = None
        headers = {"Accept": "application/json", "User-Agent": "workbench-agent/0.1"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            konteks = (
                konteks_tls()
                if urlsplit(url).scheme == "https"
                else None
            )
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=konteks
            ) as response:
                mentah = response.read()
                if not expect_body or response.status == 204 or not mentah:
                    return {}
                try:
                    hasil = json.loads(mentah.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise TransportError(
                        "Control API mengembalikan jawaban yang bukan JSON."
                    ) from exc
                if not isinstance(hasil, dict):
                    raise TransportError(
                        "Control API mengembalikan JSON yang bukan objek."
                    )
                return hasil
        except urllib.error.HTTPError as exc:
            payload = _baca_error(exc)
            code = payload.get("error") if isinstance(payload.get("error"), str) else None
            message = payload.get("message") if isinstance(payload.get("message"), str) else (
                f"Control API menjawab {exc.code}."
            )
            if exc.code == 401:
                raise AuthenticationFailedError() from None
            raise HttpError(message, status=exc.code, code=code, payload=payload) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                raise TransportError(_pesan_sertifikat(self.base_url)) from exc
            raise TransportError(
                f"Tidak dapat menghubungi Control API di {self.base_url}: {exc.reason}"
            ) from exc


def _baca_error(exc: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        mentah = exc.read()
        data = json.loads(mentah.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
