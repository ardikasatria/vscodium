"""Model domain backend Jupyter.

Tidak memuat token pada ``__repr__`` / serialisasi publik. Token hanya ada di
:class:`ConnectionInfo` dan tidak ikut ke log hasil eksekusi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class KernelState(str, Enum):
    """Keadaan kernel menurut Jupyter Server."""

    STARTING = "starting"
    IDLE = "idle"
    BUSY = "busy"
    RESTARTING = "restarting"
    DEAD = "dead"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: Any) -> "KernelState":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            return cls.UNKNOWN
        try:
            return cls(value.lower())
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True)
class ConnectionInfo:
    """Cara menghubungi Jupyter Server di loopback.

    ``token`` tidak boleh dicetak, dilog, atau dimasukkan ke audit tanpa redaksi.
    """

    base_url: str
    token: str
    kernel_name: str = "python3"

    def __post_init__(self) -> None:
        url = self.base_url.rstrip("/")
        if not url.startswith(("http://127.0.0.1", "http://localhost",
                               "https://127.0.0.1", "https://localhost")):
            raise ValueError(
                "Jupyter Server hanya boleh dihubungi lewat loopback "
                f"(127.0.0.1/localhost); dapat: {self.base_url!r}"
            )
        object.__setattr__(self, "base_url", url)
        if not self.token:
            raise ValueError("token Jupyter tidak boleh kosong")

    def __repr__(self) -> str:  # pragma: no cover - keamanan representasi
        return (
            f"ConnectionInfo(base_url={self.base_url!r}, token='***', "
            f"kernel_name={self.kernel_name!r})"
        )


@dataclass(frozen=True)
class KernelInfo:
    id: str
    name: str
    state: KernelState = KernelState.UNKNOWN
    execution_state: KernelState = KernelState.UNKNOWN

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state.value,
            "executionState": self.execution_state.value,
        }


@dataclass(frozen=True)
class StreamOutput:
    name: str  # stdout | stderr
    text: str

    def to_public(self) -> dict[str, Any]:
        return {"type": "stream", "name": self.name, "text": self.text}


@dataclass(frozen=True)
class ErrorOutput:
    ename: str
    evalue: str
    traceback: tuple[str, ...] = ()

    def to_public(self) -> dict[str, Any]:
        return {
            "type": "error",
            "ename": self.ename,
            "evalue": self.evalue,
            "traceback": list(self.traceback),
        }


@dataclass(frozen=True)
class ExecuteResultOutput:
    data: Mapping[str, Any]
    execution_count: int | None = None

    def to_public(self) -> dict[str, Any]:
        return {
            "type": "execute_result",
            "executionCount": self.execution_count,
            "data": dict(self.data),
        }


Output = StreamOutput | ErrorOutput | ExecuteResultOutput


@dataclass(frozen=True)
class ExecutionResult:
    """Hasil satu ``execute_request``."""

    status: str  # ok | error | abort
    outputs: tuple[Output, ...] = ()
    execution_count: int | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executionCount": self.execution_count,
            "detail": self.detail,
            "outputs": [o.to_public() for o in self.outputs],
        }

    @classmethod
    def from_public(cls, data: Mapping[str, Any]) -> "ExecutionResult":
        """Kebalikan :meth:`to_public` -- dipakai kernel proses anak.

        Keluaran berbentuk asing dibuang, bukan diteruskan: yang datang dari
        proses anak tetap data, dan hanya tiga jenis keluaran yang dikenal.
        """
        outputs: list[Output] = []
        for o in data.get("outputs") or ():
            if not isinstance(o, Mapping):
                continue
            jenis = o.get("type")
            if jenis == "stream":
                outputs.append(StreamOutput(name=str(o.get("name") or "stdout"),
                                            text=str(o.get("text") or "")))
            elif jenis == "error":
                outputs.append(ErrorOutput(
                    ename=str(o.get("ename") or "Error"),
                    evalue=str(o.get("evalue") or ""),
                    traceback=tuple(str(t) for t in o.get("traceback") or ()),
                ))
            elif jenis == "execute_result" and isinstance(o.get("data"), Mapping):
                hitung = o.get("executionCount")
                outputs.append(ExecuteResultOutput(
                    data=dict(o["data"]),
                    execution_count=hitung if isinstance(hitung, int) else None,
                ))
        hitung = data.get("executionCount")
        detail = data.get("detail")
        return cls(
            status=str(data.get("status") or "error"),
            outputs=tuple(outputs),
            execution_count=hitung if isinstance(hitung, int) else None,
            detail=str(detail) if detail is not None else None,
        )


#: Batas bawaan agar satu cell tidak menggantung sesi mahasiswa.
DEFAULT_EXECUTE_TIMEOUT_SECONDS = 60.0
DEFAULT_START_TIMEOUT_SECONDS = 30.0

#: Batas ukuran teks keluaran yang disimpan per stream/result.
MAX_OUTPUT_CHARS = 100_000
