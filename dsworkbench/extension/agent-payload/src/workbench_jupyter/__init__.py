"""Backend Jupyter Server untuk Data Science Workbench.

Component 09: lifecycle kernel dan eksekusi kode terhadap Jupyter Server lokal
di komputer mahasiswa. Bukan editor notebook (Component 11), bukan ekspor
(Component 12).

Dua aturan:

1. **Server hanya di loopback.** Paket ini berbicara ke ``127.0.0.1``, bukan ke
   LAN. Provider yang mempublikasikan port container ke loopback.
2. **Tidak ada shell arbitrer.** Eksekusi adalah pesan ``execute_request`` pada
   kanal kernel, bukan ``docker exec`` atau ``bash -c``.

Pemakaian:

    from workbench_jupyter import ConnectionInfo, HttpJupyterBackend, FakeJupyterBackend

    backend = HttpJupyterBackend(ConnectionInfo(base_url="http://127.0.0.1:8888",
                                                token="..."))
    kernel = backend.start_kernel()
    hasil = backend.execute(kernel.id, "print(1+1)")
"""

from __future__ import annotations

from .backend import FakeJupyterBackend, HttpJupyterBackend, JupyterBackend
from .local_python import LocalPythonBackend
from .subprocess_backend import KERNEL_RESTARTED, SubprocessKernelBackend
from .errors import (
    JupyterError,
    JupyterProtocolError,
    JupyterTimeoutError,
    KernelNotFoundError,
)
from .models import (
    ConnectionInfo,
    ErrorOutput,
    ExecuteResultOutput,
    ExecutionResult,
    KernelInfo,
    KernelState,
    Output,
    StreamOutput,
)
from .tokens import generate_token

__all__ = [
    "ConnectionInfo",
    "ErrorOutput",
    "ExecuteResultOutput",
    "ExecutionResult",
    "FakeJupyterBackend",
    "HttpJupyterBackend",
    "JupyterBackend",
    "JupyterError",
    "JupyterProtocolError",
    "JupyterTimeoutError",
    "KernelInfo",
    "KernelNotFoundError",
    "KernelState",
    "LocalPythonBackend",
    "Output",
    "StreamOutput",
    "SubprocessKernelBackend",
    "KERNEL_RESTARTED",
    "generate_token",
]

__version__ = "0.1.0"
