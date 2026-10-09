"""Reconnect dengan exponential backoff.

Tanpa ini, laptop yang baru bangun dari sleep atau jaringan yang putus akan
membombardir Control API dengan percobaan tanpa jeda -- mengganti satu
kegagalan lokal menjadi beban bersama di VM.
"""

from __future__ import annotations

import random


class Backoff:
    """Hitung jeda berikutnya, dengan jitter agar banyak agent tidak serempak."""

    def __init__(
        self,
        *,
        initial: float = 1.0,
        maximum: float = 60.0,
        multiplier: float = 2.0,
        jitter: float = 0.2,
    ):
        if initial <= 0 or maximum < initial or multiplier < 1:
            raise ValueError("parameter backoff tidak sah")
        self._initial = initial
        self._maximum = maximum
        self._multiplier = multiplier
        self._jitter = jitter
        self._current = initial

    def next(self) -> float:
        """Kembalikan jeda saat ini lalu naikkan untuk percobaan berikutnya."""
        dasar = self._current
        self._current = min(self._current * self._multiplier, self._maximum)
        if self._jitter <= 0:
            return dasar
        sebaran = dasar * self._jitter
        return max(0.0, dasar + random.uniform(-sebaran, sebaran))

    def reset(self) -> None:
        self._current = self._initial

    @property
    def current(self) -> float:
        return self._current
