"""Validasi path relatif artefak dan parameter.

Menolak traversal, path absolut, null byte, dan pemisah Windows -- sama ketat
dengan LabSpec RelativePath.
"""

from __future__ import annotations

from pathlib import Path

from .errors import InvalidArtifactError


def assert_relative_safe(value: str, *, field: str = "artifact") -> str:
    """Kembalikan path relatif yang aman atau lempar."""
    if not isinstance(value, str) or not value.strip():
        raise InvalidArtifactError(f"{field} wajib diisi.")
    if "\x00" in value:
        raise InvalidArtifactError(f"{field} memuat null byte.")
    if "\\" in value:
        raise InvalidArtifactError(f"{field} tidak boleh memakai pemisah \\\\.")
    if value.startswith("/") or (len(value) > 1 and value[1] == ":"):
        raise InvalidArtifactError(f"{field} harus relatif, bukan path absolut.")
    bagian = Path(value).parts
    if ".." in bagian or bagian[:1] == (".",) and ".." in bagian:
        raise InvalidArtifactError(f"{field} tidak boleh memuat '..'.")
    if any(p == ".." for p in bagian):
        raise InvalidArtifactError(f"{field} tidak boleh memuat '..'.")
    return value.replace("\\", "/")


def resolve_under(root: Path, relative: str, *, field: str = "artifact") -> Path:
    """Resolve path relatif di bawah root; tolak bila keluar."""
    aman = assert_relative_safe(relative, field=field)
    akar = root.resolve()
    target = (akar / aman).resolve()
    try:
        target.relative_to(akar)
    except ValueError as exc:
        raise InvalidArtifactError(
            f"{field} keluar dari batas yang diizinkan."
        ) from exc
    return target
