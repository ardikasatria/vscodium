"""Eksekutor SQL untuk handler sql-check.

Handler tidak menyusun string shell. Ia meminta :class:`SqlExecutor` menjalankan
skrip terhadap service yang sudah diekspos runtime sebagai endpoint loopback.

Bila executor tidak dipasang atau service belum hidup, hasilnya ``LEWAT`` /
``KESALAHAN`` terstruktur -- bukan ``GAGAL`` akademik.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from .process import ProcessOutcome, ProcessRunner


@dataclass(frozen=True)
class SqlConnectionConfig:
    """Kredensial default praktikum. Disuntikkan, bukan dari YAML course."""

    user: str = "praktikum"
    password: str = "praktikum"
    #: Nama berkas di dalam container bila memakai Docker exec (Component 18+).
    container_script_prefix: str = "/checkpoint"


class SqlExecutor(Protocol):
    """Menjalankan skrip SQL terhadap satu service runtime."""

    def run_script(
        self,
        *,
        service: str,
        database: str,
        script_path: Path,
        timeout_seconds: float,
        endpoints: Mapping[str, str],
        runtime_id: str | None = None,
    ) -> ProcessOutcome: ...


class UnavailableSqlExecutor:
    """Executor penanda: SQL belum dapat dijalankan di lingkungan ini."""

    def run_script(
        self,
        *,
        service: str,
        database: str,
        script_path: Path,
        timeout_seconds: float,
        endpoints: Mapping[str, str],
        runtime_id: str | None = None,
    ) -> ProcessOutcome:
        return ProcessOutcome(
            argv=("psql",),
            exit_code=127,
            stdout="",
            stderr=(
                f"Eksekutor SQL belum dipasang; service '{service}' / "
                f"database '{database}' tidak dijalankan."
            ),
        )


class PsqlCliExecutor:
    """Menjalankan ``psql`` ke endpoint loopback runtime.

    Membaca ``host:port`` dari ``endpoints[service]``. Kredensial berasal dari
    konfigurasi yang disuntikkan -- course package tidak boleh memasok password.
    """

    def __init__(
        self,
        *,
        config: SqlConnectionConfig | None = None,
        process: ProcessRunner | None = None,
        psql: str | None = None,
    ) -> None:
        self._config = config or SqlConnectionConfig()
        self._process = process or ProcessRunner()
        self._psql = psql or shutil.which("psql") or "psql"

    def run_script(
        self,
        *,
        service: str,
        database: str,
        script_path: Path,
        timeout_seconds: float,
        endpoints: Mapping[str, str],
        runtime_id: str | None = None,
    ) -> ProcessOutcome:
        del runtime_id  # dipakai DockerSqlExecutor kelak
        endpoint = endpoints.get(service)
        if not endpoint:
            return ProcessOutcome(
                argv=("psql",),
                exit_code=127,
                stdout="",
                stderr=(
                    f"Service '{service}' belum memiliki endpoint. "
                    "Jalankan runtime modul terlebih dahulu."
                ),
            )
        host, port = _split_endpoint(endpoint)
        if host not in ("127.0.0.1", "localhost", "::1"):
            return ProcessOutcome(
                argv=("psql",),
                exit_code=1,
                stdout="",
                stderr=(
                    f"Endpoint service '{service}' bukan loopback ({endpoint}). "
                    "Checkpoint menolak koneksi non-lokal."
                ),
            )
        argv = [
            self._psql,
            "-h", host,
            "-p", str(port),
            "-U", self._config.user,
            "-d", database,
            "-v", "ON_ERROR_STOP=1",
            "-f", str(script_path),
        ]
        return self._process.run(
            argv,
            timeout_seconds=timeout_seconds,
            env_extra={"PGPASSWORD": self._config.password},
        )


def _split_endpoint(endpoint: str) -> tuple[str, int]:
    """Urai ``host:port``; IPv6 dalam kurung tidak didukung di v1."""
    if endpoint.count(":") != 1:
        raise ValueError(f"endpoint tidak sah: {endpoint!r}")
    host, _, port_s = endpoint.partition(":")
    return host, int(port_s)
