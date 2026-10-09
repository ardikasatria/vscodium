"""Penyimpanan keadaan pairing di komputer mahasiswa.

Kredensial perangkat disimpan di berkas lokal dengan izin ``0600``. Ia tidak
pernah ditulis ke log, tidak pernah dikirim ulang ke browser, dan tidak pernah
masuk Git -- direktori keadaan berada di luar repository.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass
class DeviceState:
    """Keadaan pairing yang disimpan lokal."""

    device_id: str
    credential: str
    control_plane_url: str
    name: str
    os: str
    arch: str
    agent_version: str
    paired_at: str
    #: Nama akun Workbench pemilik pairing (NIM/username) dari Control API. Agent
    #: lama tidak menyimpannya; diisi saat connect berikutnya. Menentukan folder
    #: kerja dan data per akun (lihat ``akun.py``).
    account: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DeviceState":
        wajib = (
            "device_id", "credential", "control_plane_url",
            "name", "os", "arch", "agent_version", "paired_at",
        )
        for kunci in wajib:
            nilai = data.get(kunci)
            if not isinstance(nilai, str) or not nilai:
                raise ValueError(f"berkas keadaan rusak: field '{kunci}' hilang")
        return cls(
            device_id=data["device_id"],
            credential=data["credential"],
            control_plane_url=data["control_plane_url"],
            name=data["name"],
            os=data["os"],
            arch=data["arch"],
            agent_version=data["agent_version"],
            paired_at=data["paired_at"],
            account=data["account"] if isinstance(data.get("account"), str) and data["account"] else None,
        )


class StateStore:
    """Baca/tulis ``DeviceState`` secara atomik."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "device.json"

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> DeviceState:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise FileNotFoundError(
                f"berkas keadaan tidak ada: {self.path}"
            ) from None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"berkas keadaan tidak dapat dibaca: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("berkas keadaan harus berupa objek JSON")
        return DeviceState.from_dict(data)

    def save(self, state: DeviceState) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        # Tulis ke berkas sementara di direktori yang sama lalu rename -- agar
        # crash di tengah jalan tidak meninggalkan JSON setengah jadi yang
        # berisi kredensial rusak.
        fd, sementara = tempfile.mkstemp(
            prefix=".device-", suffix=".tmp", dir=self.directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.chmod(sementara, stat.S_IRUSR | stat.S_IWUSR)
            os.replace(sementara, self.path)
        except Exception:
            try:
                os.unlink(sementara)
            except OSError:
                pass
            raise
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
