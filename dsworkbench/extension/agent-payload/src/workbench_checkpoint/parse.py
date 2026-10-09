"""Parser keluaran pemeriksa menjadi butir terstruktur.

Adaptor ini **tidak mengubah makna akademik**. Ia hanya membaca status yang
sudah dicetak pemeriksa sumber dan menempatkannya pada bentuk yang sama untuk
kedua handler.
"""

from __future__ import annotations

import re
from typing import Sequence

from .models import CheckpointFinding, CheckpointStatus

#: Baris DW: `[LULUS] judul` atau `[GAGAL ] judul` (lebar 5 di sumber).
_PYTHON_LINE = re.compile(
    r"^\[(?P<status>LULUS|GAGAL|LEWAT)\s*\]\s*(?P<title>.+?)\s*$"
)

#: Baris detail DW yang di-indent di bawah butir.
_PYTHON_DETAIL = re.compile(r"^\s{5,}(?P<detail>\S.*)$")

#: Baris psql aligned / unaligned: butir | status | detail
_SQL_PIPE = re.compile(
    r"^\s*(?P<butir>.+?)\s*\|\s*(?P<status>LULUS|GAGAL|LEWAT)\s*\|\s*(?P<detail>.*?)\s*$"
)

#: Baris psql expanded / tab-separated kasar.
_SQL_TAB = re.compile(
    r"^(?P<butir>[^\t|]+)\t(?P<status>LULUS|GAGAL|LEWAT)\t(?P<detail>.*)$"
)

_STATUS_MAP = {
    "LULUS": CheckpointStatus.PASSED,
    "GAGAL": CheckpointStatus.FAILED,
    "LEWAT": CheckpointStatus.SKIPPED,
}


def parse_python_check_output(text: str) -> tuple[CheckpointFinding, ...]:
    """Urai keluaran ``check_modul_*.py`` bergaya ``[STATUS] judul``."""
    findings: list[CheckpointFinding] = []
    pending_detail: list[str] = []

    def _flush_detail() -> None:
        nonlocal pending_detail
        if findings and pending_detail:
            last = findings[-1]
            findings[-1] = CheckpointFinding(
                status=last.status,
                title=last.title,
                detail=" ".join(pending_detail).strip() or None,
            )
        pending_detail = []

    for raw in text.splitlines():
        baris = raw.rstrip()
        if not baris.strip():
            continue
        m = _PYTHON_LINE.match(baris)
        if m:
            _flush_detail()
            findings.append(
                CheckpointFinding(
                    status=_STATUS_MAP[m.group("status")],
                    title=m.group("title").strip(),
                )
            )
            continue
        d = _PYTHON_DETAIL.match(baris)
        if d and findings:
            pending_detail.append(d.group("detail").strip())
    _flush_detail()
    return tuple(findings)


def parse_sql_check_output(text: str) -> tuple[CheckpointFinding, ...]:
    """Urai keluaran ``psql`` berisi kolom butir / status / detail.

    Menerima bentuk pipe-aligned (``border 2``) dan tab-separated. Baris
    header, separator ``---+``, dan pesan ``SET`` diabaikan.
    """
    findings: list[CheckpointFinding] = []
    for raw in text.splitlines():
        baris = raw.rstrip()
        if not baris.strip():
            continue
        stripped = baris.strip()
        lower = stripped.lower()
        if lower.startswith("set ") or lower.startswith("(") and "row" in lower:
            continue
        if set(stripped) <= {"-", "+", "|", " ", "="}:
            continue
        if "butir" in lower and "status" in lower:
            continue
        # ``border 2`` membingkai baris dengan ``|`` di kedua tepi.
        if len(stripped) > 1 and stripped[0] == "|" and stripped[-1] == "|":
            baris = stripped[1:-1]

        m = _SQL_PIPE.match(baris) or _SQL_TAB.match(baris)
        if not m:
            # Beberapa psql mencetak tanpa spasi rapi di sekitar |
            bagian = [p.strip() for p in baris.split("|")]
            if len(bagian) >= 3 and bagian[1] in _STATUS_MAP:
                findings.append(
                    CheckpointFinding(
                        status=_STATUS_MAP[bagian[1]],
                        title=bagian[0],
                        detail=bagian[2] or None,
                    )
                )
            continue
        status = m.group("status")
        if status not in _STATUS_MAP:
            continue
        detail = (m.group("detail") or "").strip() or None
        findings.append(
            CheckpointFinding(
                status=_STATUS_MAP[status],
                title=m.group("butir").strip(),
                detail=detail,
            )
        )
    return tuple(findings)


def aggregate_status(findings: Sequence[CheckpointFinding]) -> CheckpointStatus:
    """Gabungkan butir menjadi status keseluruhan.

    Aturan akademik sumber DW: ada GAGAL → keseluruhan gagal. Tidak ada butir
    → ERROR (keluaran tidak dapat dibaca). Hanya LEWAT tanpa LULUS → SKIPPED.
    Campuran LULUS+LEWAT tanpa GAGAL → PASSED.
    """
    if not findings:
        return CheckpointStatus.ERROR
    statuses = {f.status for f in findings}
    if CheckpointStatus.ERROR in statuses:
        return CheckpointStatus.ERROR
    if CheckpointStatus.FAILED in statuses:
        return CheckpointStatus.FAILED
    if statuses == {CheckpointStatus.SKIPPED}:
        return CheckpointStatus.SKIPPED
    if CheckpointStatus.PASSED in statuses:
        return CheckpointStatus.PASSED
    return CheckpointStatus.SKIPPED


def summarise(status: CheckpointStatus, findings: Sequence[CheckpointFinding]) -> str:
    """Ringkasan singkat berbahasa Indonesia untuk UI dan event."""
    total = len(findings)
    if status is CheckpointStatus.ERROR and total == 0:
        return "Keluaran pemeriksa tidak dapat dibaca."
    gagal = sum(1 for f in findings if f.status is CheckpointStatus.FAILED)
    lewat = sum(1 for f in findings if f.status is CheckpointStatus.SKIPPED)
    lulus = sum(1 for f in findings if f.status is CheckpointStatus.PASSED)
    if status is CheckpointStatus.PASSED:
        if lewat:
            return f"{lulus} butir lulus, {lewat} dilewati (dari {total})."
        return f"Seluruh {total} butir lulus."
    if status is CheckpointStatus.FAILED:
        return f"{gagal} dari {total} butir gagal."
    if status is CheckpointStatus.SKIPPED:
        return f"Seluruh {total} butir dilewati; pemeriksaan belum dapat dijalankan penuh."
    return f"Pemeriksaan berakhir dengan kesalahan ({total} butir tercatat)."
