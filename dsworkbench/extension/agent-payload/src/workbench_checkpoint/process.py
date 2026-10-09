"""Penjalan proses anak tanpa shell.

Satu-satunya tempat di paket ini yang memanggil ``subprocess``. Seluruh
handler membangun argv sebagai list; tidak ada ``shell=True``.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

#: Batas keluaran yang disimpan. Pemeriksa yang mencetak tanpa henti tidak
#: boleh menghabiskan memori local agent.
OUTPUT_LIMIT = 256_000

ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV", "PYTHONPATH")


@dataclass(frozen=True)
class ProcessOutcome:
    """Hasil satu pemanggilan proses."""

    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def combined(self) -> str:
        parts = [p for p in (self.stdout, self.stderr) if p]
        return "\n".join(parts)


class ProcessRunner:
    """Menjalankan argv array dengan batas waktu dan environment bersih."""

    def __init__(self, *, env: Mapping[str, str] | None = None) -> None:
        self._env_source = dict(env) if env is not None else dict(os.environ)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        timeout_seconds: float,
        env_extra: Mapping[str, str] | None = None,
    ) -> ProcessOutcome:
        if not argv:
            raise ValueError("argv tidak boleh kosong")
        env = self._environment(env_extra)
        try:
            hasil = subprocess.run(  # noqa: S603 - argv array, shell=False
                list(argv),
                cwd=str(cwd) if cwd is not None else None,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            out = _clip((exc.stdout or "") if isinstance(exc.stdout, str) else "")
            err = _clip((exc.stderr or "") if isinstance(exc.stderr, str) else "")
            return ProcessOutcome(
                argv=tuple(argv),
                exit_code=124,
                stdout=out,
                stderr=err or f"batas waktu {timeout_seconds:.0f}s terlampaui",
                timed_out=True,
            )
        except FileNotFoundError as exc:
            return ProcessOutcome(
                argv=tuple(argv),
                exit_code=127,
                stdout="",
                stderr=f"perkakas tidak ditemukan: {exc.filename}",
            )
        return ProcessOutcome(
            argv=tuple(argv),
            exit_code=hasil.returncode,
            stdout=_clip(hasil.stdout or ""),
            stderr=_clip(hasil.stderr or ""),
        )

    def _environment(self, extra: Mapping[str, str] | None) -> dict[str, str]:
        env = {k: v for k, v in self._env_source.items() if k in ENV_ALLOWLIST}
        env.setdefault("LANG", "en_US.UTF-8")
        env.setdefault("LC_ALL", "en_US.UTF-8")
        if extra:
            env.update(extra)
        return env


def _clip(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text
    return text[: OUTPUT_LIMIT - 20] + "\n...[terpotong]..."
