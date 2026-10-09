"""Local Runner -- agent di komputer mahasiswa.

Agent membuat koneksi **outbound** ke Control API di VM dosen. Ia tidak membuka
port ke LAN, tidak menerima perintah shell, dan hanya mengerjakan operation ID
yang ada dalam allowlist.

Empat hal yang menentukan bentuk paket ini:

1. **Pairing sekali.** Kode berumur pendek ditukar menjadi kredensial perangkat
   yang disimpan lokal; kode itu sendiri tidak pernah dipakai ulang.
2. **Heartbeat.** VM mengetahui perangkat masih hidup hanya dari denyut yang
   terbukti kredensialnya.
3. **Allowlist tertutup.** Operasi di luar daftar ditolak di agent -- bahkan
   bila kelak lolos dari VM.
4. **Reconnect dengan backoff.** Laptop yang tidur atau jaringan yang putus
   tidak menghabiskan Control API dengan percobaan tanpa jeda.

Pemakaian:

    from workbench_agent import Agent, AgentConfig, pair_device

    state = pair_device(config, code="ABCD-...")
    agent = Agent(config, state)
    agent.run_forever()
"""

from __future__ import annotations

from .backoff import Backoff
from .client import ControlPlaneClient, HttpError
from .config import AGENT_VERSION, AgentConfig
from .errors import AgentError, NotPairedError, OperationRejectedError
from .operations import OperationAllowlist, OperationHandler
from .runner import Agent, load_state, pair_device
from .state import DeviceState, StateStore

__all__ = [
    "AGENT_VERSION",
    "Agent",
    "AgentConfig",
    "AgentError",
    "Backoff",
    "ControlPlaneClient",
    "DeviceState",
    "HttpError",
    "NotPairedError",
    "OperationAllowlist",
    "OperationHandler",
    "OperationRejectedError",
    "StateStore",
    "load_state",
    "pair_device",
]

__version__ = AGENT_VERSION
