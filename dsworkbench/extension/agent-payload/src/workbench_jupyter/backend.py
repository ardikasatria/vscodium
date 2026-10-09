"""Backend Jupyter: protokol, HTTP sungguhan, dan tiruan untuk test."""

from __future__ import annotations

import time
import uuid
from typing import Protocol

from .errors import JupyterProtocolError, JupyterTimeoutError, KernelNotFoundError
from .models import (
    DEFAULT_EXECUTE_TIMEOUT_SECONDS,
    MAX_OUTPUT_CHARS,
    ConnectionInfo,
    ErrorOutput,
    ExecuteResultOutput,
    ExecutionResult,
    KernelInfo,
    KernelState,
    Output,
    StreamOutput,
)
from .rest import RestClient
from .ws import KernelChannel


class JupyterBackend(Protocol):
    """Kontrak backend yang dipakai agent dan test."""

    def ping(self) -> bool: ...

    def start_kernel(self, *, name: str | None = None) -> KernelInfo: ...

    def stop_kernel(self, kernel_id: str) -> None: ...

    def interrupt_kernel(self, kernel_id: str) -> None: ...

    def restart_kernel(self, kernel_id: str) -> KernelInfo: ...

    def kernel_status(self, kernel_id: str) -> KernelInfo: ...

    def execute(
        self,
        kernel_id: str,
        code: str,
        *,
        timeout: float = DEFAULT_EXECUTE_TIMEOUT_SECONDS,
    ) -> ExecutionResult: ...


def _potong(teks: str) -> str:
    if len(teks) <= MAX_OUTPUT_CHARS:
        return teks
    return teks[: MAX_OUTPUT_CHARS - 20] + "\n…[dipotong]"


def _message(
    *,
    msg_type: str,
    session: str,
    content: dict,
    channel: str = "shell",
    parent: dict | None = None,
) -> dict:
    header = {
        "msg_id": str(uuid.uuid4()),
        "username": "workbench",
        "session": session,
        "msg_type": msg_type,
        "version": "5.3",
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return {
        "header": header,
        "parent_header": parent or {},
        "metadata": {},
        "content": content,
        "channel": channel,
        "buffers": [],
    }


def execute_on_channel(
    channel: KernelChannel,
    code: str,
    *,
    timeout: float,
) -> ExecutionResult:
    """Kirim execute_request dan kumpulkan keluaran sampai idle/error."""
    if not isinstance(code, str):
        raise JupyterProtocolError("kode harus berupa teks")
    # Batas kasar mencegah payload raksasa lewat relay.
    if len(code) > 200_000:
        raise JupyterProtocolError("kode melebihi 200 KB")

    session = channel.session_id
    permintaan = _message(
        msg_type="execute_request",
        session=session,
        content={
            "code": code,
            "silent": False,
            "store_history": True,
            "user_expressions": {},
            "allow_stdin": False,
            "stop_on_error": True,
        },
    )
    msg_id = permintaan["header"]["msg_id"]
    channel.send_json(permintaan)

    outputs: list[Output] = []
    status = "ok"
    execution_count: int | None = None
    deadline = time.monotonic() + timeout
    selesai = False

    while not selesai:
        sisa = deadline - time.monotonic()
        if sisa <= 0:
            raise JupyterTimeoutError(
                f"Eksekusi melebihi batas waktu {timeout:g} detik."
            )
        try:
            pesan = channel.recv_json(timeout=min(sisa, 5.0))
        except JupyterTimeoutError:
            continue

        header = pesan.get("header") or {}
        parent = pesan.get("parent_header") or {}
        if parent.get("msg_id") != msg_id:
            continue
        msg_type = header.get("msg_type")
        content = pesan.get("content") or {}

        if msg_type == "stream":
            outputs.append(StreamOutput(
                name=str(content.get("name") or "stdout"),
                text=_potong(str(content.get("text") or "")),
            ))
        elif msg_type == "error":
            status = "error"
            tb = content.get("traceback") or []
            outputs.append(ErrorOutput(
                ename=str(content.get("ename") or "Error"),
                evalue=str(content.get("evalue") or ""),
                traceback=tuple(str(x) for x in tb) if isinstance(tb, list) else (),
            ))
        elif msg_type in {"execute_result", "display_data"}:
            data = content.get("data") or {}
            if not isinstance(data, dict):
                data = {}
            # Simpan MIME teks + gambar (base64). Batasi ukuran tiap nilai.
            aman: dict = {}
            for k, v in data.items():
                if not isinstance(k, str):
                    continue
                if k.startswith("text/") or k in {"application/json"}:
                    if isinstance(v, str):
                        aman[k] = _potong(v)
                    elif isinstance(v, list):
                        aman[k] = _potong("".join(str(x) for x in v))
                    else:
                        aman[k] = v
                elif k.startswith("image/"):
                    if isinstance(v, list):
                        v = "".join(str(x) for x in v)
                    if isinstance(v, str) and v.strip():
                        # ~750 KiB base64 ≈ gambar praktikum wajar
                        aman[k] = v if len(v) <= 750_000 else v[:750_000]
            count = content.get("execution_count")
            if isinstance(count, int):
                execution_count = count
            outputs.append(ExecuteResultOutput(
                data=aman,
                execution_count=execution_count,
            ))
        elif msg_type == "execute_reply":
            status = str(content.get("status") or status)
            count = content.get("execution_count")
            if isinstance(count, int):
                execution_count = count
            if status == "error" and not any(isinstance(o, ErrorOutput) for o in outputs):
                outputs.append(ErrorOutput(
                    ename=str(content.get("ename") or "Error"),
                    evalue=str(content.get("evalue") or ""),
                    traceback=tuple(str(x) for x in (content.get("traceback") or [])),
                ))
        elif msg_type == "status" and content.get("execution_state") == "idle":
            # Idle setelah execute_reply menandai selesai.
            if any(
                (pesan.get("parent_header") or {}).get("msg_id") == msg_id
                for _ in (0,)
            ):
                selesai = True

        # Selesai jika sudah dapat execute_reply (dan idealnya status idle).
        if msg_type == "execute_reply":
            selesai = True

    return ExecutionResult(
        status=status if status in {"ok", "error", "abort"} else "error",
        outputs=tuple(outputs),
        execution_count=execution_count,
    )


class HttpJupyterBackend:
    """Backend terhadap Jupyter Server sungguhan di loopback."""

    def __init__(self, connection: ConnectionInfo, *, timeout: float = 30.0):
        self.connection = connection
        self.rest = RestClient(connection, timeout=timeout)

    def ping(self) -> bool:
        try:
            self.rest.status()
            return True
        except Exception:
            return False

    def start_kernel(self, *, name: str | None = None) -> KernelInfo:
        return self.rest.start_kernel(name=name)

    def stop_kernel(self, kernel_id: str) -> None:
        self.rest.delete_kernel(kernel_id)

    def interrupt_kernel(self, kernel_id: str) -> None:
        self.rest.interrupt_kernel(kernel_id)

    def restart_kernel(self, kernel_id: str) -> KernelInfo:
        return self.rest.restart_kernel(kernel_id)

    def kernel_status(self, kernel_id: str) -> KernelInfo:
        return self.rest.get_kernel(kernel_id)

    def execute(
        self,
        kernel_id: str,
        code: str,
        *,
        timeout: float = DEFAULT_EXECUTE_TIMEOUT_SECONDS,
    ) -> ExecutionResult:
        with KernelChannel(self.connection, kernel_id, timeout=timeout) as channel:
            return execute_on_channel(channel, code, timeout=timeout)


class FakeJupyterBackend:
    """Backend tiruan untuk unit test tanpa Jupyter Server."""

    def __init__(self):
        self._kernels: dict[str, KernelInfo] = {}
        self._busy: set[str] = set()
        self.executed: list[tuple[str, str]] = []
        self.fail_next_execute: str | None = None
        self.alive = True

    def ping(self) -> bool:
        return self.alive

    def start_kernel(self, *, name: str | None = None) -> KernelInfo:
        kid = f"k-{uuid.uuid4().hex[:12]}"
        info = KernelInfo(
            id=kid, name=name or "python3",
            state=KernelState.IDLE, execution_state=KernelState.IDLE,
        )
        self._kernels[kid] = info
        return info

    def stop_kernel(self, kernel_id: str) -> None:
        if kernel_id not in self._kernels:
            raise KernelNotFoundError(kernel_id)
        del self._kernels[kernel_id]
        self._busy.discard(kernel_id)

    def interrupt_kernel(self, kernel_id: str) -> None:
        if kernel_id not in self._kernels:
            raise KernelNotFoundError(kernel_id)
        self._busy.discard(kernel_id)

    def restart_kernel(self, kernel_id: str) -> KernelInfo:
        if kernel_id not in self._kernels:
            raise KernelNotFoundError(kernel_id)
        info = self._kernels[kernel_id]
        baru = KernelInfo(
            id=info.id, name=info.name,
            state=KernelState.IDLE, execution_state=KernelState.IDLE,
        )
        self._kernels[kernel_id] = baru
        self._busy.discard(kernel_id)
        return baru

    def kernel_status(self, kernel_id: str) -> KernelInfo:
        if kernel_id not in self._kernels:
            raise KernelNotFoundError(kernel_id)
        state = KernelState.BUSY if kernel_id in self._busy else KernelState.IDLE
        info = self._kernels[kernel_id]
        return KernelInfo(
            id=info.id, name=info.name, state=state, execution_state=state,
        )

    def execute(
        self,
        kernel_id: str,
        code: str,
        *,
        timeout: float = DEFAULT_EXECUTE_TIMEOUT_SECONDS,
    ) -> ExecutionResult:
        _ = timeout
        if kernel_id not in self._kernels:
            raise KernelNotFoundError(kernel_id)
        self.executed.append((kernel_id, code))
        if self.fail_next_execute:
            pesan = self.fail_next_execute
            self.fail_next_execute = None
            return ExecutionResult(
                status="error",
                outputs=(ErrorOutput(ename="RuntimeError", evalue=pesan),),
            )
        # Simulasi sangat sederhana: `print(x)` → stdout.
        teks = code.strip()
        if teks.startswith("raise "):
            return ExecutionResult(
                status="error",
                outputs=(ErrorOutput(ename="RuntimeError", evalue=teks),),
            )
        keluaran = f"{teks}\n" if teks else ""
        return ExecutionResult(
            status="ok",
            execution_count=len(self.executed),
            outputs=(StreamOutput(name="stdout", text=keluaran),) if keluaran else (),
        )
