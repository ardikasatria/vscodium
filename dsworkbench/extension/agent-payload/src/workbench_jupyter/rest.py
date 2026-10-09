"""Klien REST Jupyter Server (pustaka standar)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Mapping
from urllib.parse import urljoin

from .errors import JupyterError, JupyterProtocolError, KernelNotFoundError
from .models import ConnectionInfo, KernelInfo, KernelState


class RestClient:
    """Pembungkus endpoint ``/api/kernels`` dan ``/api/status``."""

    def __init__(self, connection: ConnectionInfo, *, timeout: float = 30.0):
        self.connection = connection
        self.timeout = timeout

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/api/status")

    def list_kernels(self) -> list[KernelInfo]:
        data = self._request("GET", "/api/kernels")
        if not isinstance(data, list):
            raise JupyterProtocolError("Jawaban /api/kernels bukan daftar.")
        return [self._kernel(item) for item in data if isinstance(item, dict)]

    def start_kernel(self, *, name: str | None = None) -> KernelInfo:
        body = {"name": name or self.connection.kernel_name}
        data = self._request("POST", "/api/kernels", body=body)
        if not isinstance(data, dict):
            raise JupyterProtocolError("Jawaban start kernel bukan objek.")
        return self._kernel(data)

    def get_kernel(self, kernel_id: str) -> KernelInfo:
        try:
            data = self._request("GET", f"/api/kernels/{kernel_id}")
        except JupyterError as exc:
            if "404" in str(exc.detail or ""):
                raise KernelNotFoundError(kernel_id) from exc
            raise
        if not isinstance(data, dict):
            raise JupyterProtocolError("Jawaban get kernel bukan objek.")
        return self._kernel(data)

    def delete_kernel(self, kernel_id: str) -> None:
        try:
            self._request("DELETE", f"/api/kernels/{kernel_id}", expect_body=False)
        except JupyterError as exc:
            if "404" in str(exc.detail or ""):
                raise KernelNotFoundError(kernel_id) from exc
            raise

    def interrupt_kernel(self, kernel_id: str) -> None:
        try:
            self._request(
                "POST", f"/api/kernels/{kernel_id}/interrupt", expect_body=False
            )
        except JupyterError as exc:
            if "404" in str(exc.detail or ""):
                raise KernelNotFoundError(kernel_id) from exc
            raise

    def restart_kernel(self, kernel_id: str) -> KernelInfo:
        try:
            data = self._request("POST", f"/api/kernels/{kernel_id}/restart")
        except JupyterError as exc:
            if "404" in str(exc.detail or ""):
                raise KernelNotFoundError(kernel_id) from exc
            raise
        if isinstance(data, dict) and data:
            return self._kernel(data)
        return self.get_kernel(kernel_id)

    # ------------------------------------------------------------------

    def _kernel(self, data: Mapping[str, Any]) -> KernelInfo:
        kid = data.get("id")
        if not isinstance(kid, str) or not kid:
            raise JupyterProtocolError("kernel tanpa id")
        name = data.get("name") if isinstance(data.get("name"), str) else "python3"
        state = KernelState.parse(data.get("execution_state") or data.get("state"))
        return KernelInfo(id=kid, name=name, state=state, execution_state=state)

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        expect_body: bool = True,
    ) -> Any:
        url = urljoin(self.connection.base_url + "/", path.lstrip("/"))
        headers = {
            "Accept": "application/json",
            "Authorization": f"token {self.connection.token}",
            "User-Agent": "workbench-jupyter/0.1",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                mentah = response.read()
                if not expect_body or response.status == 204 or not mentah:
                    return {}
                try:
                    return json.loads(mentah.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise JupyterProtocolError(
                        "Jupyter Server mengembalikan jawaban yang bukan JSON."
                    ) from exc
        except urllib.error.HTTPError as exc:
            detail = f"HTTP {exc.code}"
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                if isinstance(payload, dict) and "message" in payload:
                    detail = f"HTTP {exc.code}: {payload.get('message')}"
            except Exception:
                pass
            raise JupyterError(
                f"Permintaan Jupyter gagal ({method} {path}).",
                detail=detail,
            ) from None
        except urllib.error.URLError as exc:
            raise JupyterError(
                f"Tidak dapat menghubungi Jupyter Server di {self.connection.base_url}.",
                detail=str(exc.reason),
            ) from exc
