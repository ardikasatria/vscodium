"""Agent SUNGGUHAN (`workbench_agent.cli`) untuk uji integrasi ekstensi di VS Code.

Pola `agent/local-runner/tests/_agen_stdio.py`: jalur CLI-nya asli (`cli.main`
→ `_cmd_run` → `amankan_kanal` → `_layani_stdio` → `PipaIde` → allowlist →
kernel proses anak). Yang diganti hanya hal-hal yang tidak ada di mesin uji:

- perpindahan ke interpreter praktikum (`_pastikan_interpreter`);
- allowlist bawaan → kernel proses anak ber-`sys.executable` + workspace uji;
- `db.catalog` (hanya bila `DSW_KATALOG_PALSU` disetel) → katalog dari berkas contoh,
  karena mesin uji tidak punya PostgreSQL; gerbang pipa di depannya tetap asli;
- klien HTTP ke server → server kontrol palsu di memori. Ia meneruskan amplop
  sesi yang ditaruh server HTTP palsu (`run-integration.mjs`) di folder pemicu,
  persis seperti relay: ekstensi meminta amplop lewat HTTP, agent menerimanya
  lewat `poll`.

    python agen_sungguhan.py <workspace> <catatan.json> <folder_pemicu> [argumen workbench-agent …]

Subperintah selain `run` (mis. `adopt`, `status`, `unpair`) diteruskan apa adanya
ke CLI asli tanpa tambalan.
"""

from __future__ import annotations

import functools
import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest import mock


class ServerKontrolPalsu:
    """Pengganti `ControlPlaneClient`: antrean relay dari folder pemicu + catatan."""

    def __init__(self, catatan: Path, pemicu: Path):
        self._catatan = catatan
        self._pemicu = pemicu
        self._kunci = threading.Lock()
        self._panggilan: list[dict] = []

    def _catat(self, jenis: str, **isi) -> None:
        with self._kunci:
            self._panggilan.append({"jenis": jenis, **isi})
            sementara = self._catatan.with_suffix(".tmp")
            sementara.write_text(json.dumps(self._panggilan), encoding="utf-8")
            os.replace(sementara, self._catatan)

    def connect(self, **kw):
        self._catat("connect", capabilities=kw.get("capabilities"), deviceId=kw.get("device_id"))
        return {"heartbeatIntervalSeconds": 30}

    def heartbeat(self, **kw):
        return {}

    def disconnect(self, **kw):
        self._catat("disconnect")

    def poll(self, **kw):
        operasi = []
        for berkas in sorted(self._pemicu.glob("amplop-*.json")):
            try:
                muatan = json.loads(berkas.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # masih ditulis; putaran berikutnya
            berkas.unlink()
            operasi.append({"messageId": "relay-" + muatan["envelopeId"],
                            "operation": "session.envelope", "payload": muatan})
        if not operasi:
            time.sleep(0.05)
        return {"operations": operasi, "longPollSeconds": 1}

    def result(self, *, message_id, status, payload, detail=None, **_kw):
        self._catat("result", messageId=message_id, status=status, payload=payload)

    def job_update(self, *, job_id, update, **_kw):
        # Hasil job pipa tidak boleh sampai ke sini (protokol §4); uji memeriksanya.
        self._catat("job_update", jobId=job_id, state=update.get("state"))


def main() -> int:
    ws, catatan, pemicu = sys.argv[1:4]
    argv = sys.argv[4:]
    os.environ["AGENT_WORKSPACE_ROOT"] = ws

    from workbench_agent import cli

    if "run" not in argv:
        return cli.main(argv)

    from workbench_agent import runner
    from workbench_agent.kernels import ProfileKernelRouter
    from workbench_agent.operations import OperationAllowlist
    from workbench_jupyter.local_python import LocalPythonBackend
    from workbench_workspace import PathResolver, WorkspaceFiles

    akar_agent = Path(cli.__file__).resolve().parents[2]

    def allowlist_uji(**_kw):
        # Dibangun setelah `tetapkan_akun`, seperti `default_allowlist` asli.
        dasar = Path(str(runner.akar_workspace())).resolve()
        router = ProfileKernelRouter(
            LocalPythonBackend(cwd=dasar), akar=akar_agent, workspace_root=dasar,
            penyedia_python=lambda _pid: Path(sys.executable))
        daftar = OperationAllowlist(jupyter=router, workspace=WorkspaceFiles(PathResolver(dasar)))
        katalog = os.environ.get("DSW_KATALOG_PALSU")
        if not katalog:
            return daftar

        # Mesin uji tidak punya PostgreSQL: `db.catalog` dijawab dari berkas contoh. Jalur di
        # depannya tetap asli (pipa → kelas A → amplop → blok `services` amplop wajib ada),
        # jadi mata kuliah tanpa layanan di amplop tetap ditolak sebelum sampai ke sini.
        from workbench_agent.operations import HandlerResult, KnownOperation

        def katalog_palsu(payload):
            data = json.loads(Path(katalog).read_text(encoding="utf-8"))
            return HandlerResult(status="ok", payload={
                "alias": payload.get("alias"), "database": payload.get("database"),
                "adaServices": isinstance(payload.get("services"), dict), **data})

        class DaftarUji(OperationAllowlist):
            def implements(self, operation):
                return operation is KnownOperation.DB_CATALOG or super().implements(operation)

            def ikat_layanan(self, **kw):
                # Pengikatan asli memasang `db.catalog` sungguhan (butuh PostgreSQL terpasang);
                # katalog contoh dipasang kembali sesudahnya.
                super().ikat_layanan(**kw)
                self.register(KnownOperation.DB_CATALOG, katalog_palsu)

        daftar.__class__ = DaftarUji
        daftar.register(KnownOperation.DB_CATALOG, katalog_palsu)
        return daftar

    server = ServerKontrolPalsu(Path(catatan), Path(pemicu))
    with mock.patch.object(cli, "_pastikan_interpreter", lambda *a, **k: None), \
            mock.patch.object(cli, "default_allowlist", allowlist_uji), \
            mock.patch.object(cli, "Agent", functools.partial(runner.Agent, client=server)):
        return cli.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
