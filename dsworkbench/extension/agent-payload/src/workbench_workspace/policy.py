"""Kebijakan filesystem workspace.

Dua hal yang diatur di sini: area mana yang tidak boleh ditulis, dan batas
ukuran apa yang berlaku. Keduanya data, bukan kode bercabang, sehingga course
dapat menyatakannya lewat LabSpec tanpa Core mengenal nama course.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

#: Direktori internal Workspace Manager di dalam setiap workspace.
#: Diawali titik agar tidak mengganggu daftar berkas mahasiswa, dan selalu
#: read-only bagi mahasiswa: metadata bukan milik mereka untuk diubah.
INTERNAL_DIR = ".workbench"

#: Direktori repo git (ADR-051 §5). Terkunci di kedalaman **mana pun**, bukan
#: hanya di akar: config, hooks, dan index hanya boleh diubah mesin git agent.
#: Menulis ``.git/config`` lewat editor berkas akan membuka jalan ke filter
#: driver atau hook.
GIT_DIR = ".git"


@runtime_checkable
class PolicySource(Protocol):
    """Bentuk ``WorkspacePolicy`` milik Core.

    Dinyatakan sebagai Protocol agar paket ini tidak perlu mengimpor
    ``workbench_core``. Alasannya ada pada ``ports.py`` Core: implementasi port
    tidak boleh memaksa local agent menanggung dependency control plane.

    Lihat ``tests/test_port_conformance.py`` -- kesesuaiannya diuji terhadap
    Core yang sesungguhnya bila paket itu tersedia.
    """

    starter: Sequence[str]
    read_only: Sequence[str]
    prerequisites: Sequence[str]


@dataclass(frozen=True)
class Limits:
    """Batas operasi filesystem.

    Angka bawaan dipilih untuk komputer mahasiswa, bukan untuk server. Membaca
    berkas 3 GB ke memori akan mematikan local agent; batas ini yang
    mencegahnya.
    """

    #: Ukuran maksimum satu berkas yang boleh dibaca sekaligus ke memori.
    max_read_bytes: int = 16 * 1024 * 1024

    #: Ukuran maksimum satu berkas yang boleh ditulis.
    max_write_bytes: int = 64 * 1024 * 1024

    #: Jumlah maksimum entri yang dikembalikan satu pemanggilan daftar isi.
    max_entries: int = 2000

    #: Kedalaman maksimum penelusuran pohon direktori.
    max_depth: int = 12

    #: Panjang maksimum satu komponen path. Batas umum filesystem adalah 255.
    max_component_length: int = 255

    #: Panjang maksimum keseluruhan path relatif.
    max_path_length: int = 1024


@dataclass(frozen=True)
class ReadOnlyPolicy:
    """Daftar prefix path yang tidak boleh ditulis.

    Prefix dibandingkan **per komponen**, bukan sebagai awalan teks. Tanpa itu,
    ``data/rawdata`` akan ikut terkunci oleh aturan ``data/raw`` -- padahal
    keduanya direktori yang berbeda.

    Perbandingan dilakukan **tanpa memperhatikan besar-kecil huruf**, selalu.
    Alasannya praktis: macOS dan Windows memakai filesystem case-insensitive,
    sehingga ``DATA/RAW`` dan ``data/raw`` adalah direktori yang sama di sana.
    Membandingkan secara case-sensitive akan membuka jalan pintas menulis ke
    data mentah hanya dengan mengubah kapitalisasi.

    Pada filesystem case-sensitive, kebijakan ini sedikit lebih ketat daripada
    yang diperlukan: ``DATA/RAW`` ikut terkunci walau ia direktori lain. Itu
    arah kesalahan yang aman, dan jauh lebih baik daripada kebalikannya.
    """

    prefixes: tuple[str, ...] = ()

    @classmethod
    def from_policy(cls, policy: PolicySource | None) -> "ReadOnlyPolicy":
        """Bangun dari ``WorkspacePolicy`` Core atau objek sebentuk."""
        if policy is None:
            return cls()
        return cls.of(getattr(policy, "read_only", ()) or ())

    @classmethod
    def of(cls, prefixes: Sequence[str]) -> "ReadOnlyPolicy":
        bersih: list[str] = []
        for p in prefixes:
            norm = _normalise(p)
            if norm and norm not in bersih:
                bersih.append(norm)
        # Direktori internal selalu read-only bagi mahasiswa.
        internal = _normalise(INTERNAL_DIR)
        if internal not in bersih:
            bersih.append(internal)
        return cls(tuple(bersih))

    def covers(self, relative_path: str) -> str | None:
        """Prefix yang menaungi ``relative_path``, atau ``None``.

        Prefix itu sendiri ikut terkunci, demikian pula seluruh isinya.
        """
        target = _normalise(relative_path)
        if not target:
            return None
        bagian = target.split("/")
        if GIT_DIR in bagian:
            return GIT_DIR
        for prefix in self.prefixes:
            awal = prefix.split("/")
            if len(awal) <= len(bagian) and bagian[: len(awal)] == awal:
                return prefix
        return None

    def __bool__(self) -> bool:
        return bool(self.prefixes)


def _normalise(path: str) -> str:
    """Turunkan path menjadi bentuk yang dapat dibandingkan.

    Hanya untuk perbandingan kebijakan. Pemeriksaan keamanan path dilakukan
    ``resolver.py``; fungsi ini tidak boleh dipakai untuk memutuskan apakah
    sebuah path aman.
    """
    teks = str(path).strip().replace("\\", "/").casefold()
    bagian = [b for b in teks.split("/") if b not in ("", ".")]
    return "/".join(bagian)


@dataclass(frozen=True)
class WorkspaceSettings:
    """Kebijakan lengkap satu workspace."""

    read_only: ReadOnlyPolicy = field(default_factory=ReadOnlyPolicy)
    limits: Limits = field(default_factory=Limits)

    @classmethod
    def from_policy(
        cls,
        policy: PolicySource | None,
        *,
        limits: Limits | None = None,
    ) -> "WorkspaceSettings":
        return cls(
            read_only=ReadOnlyPolicy.from_policy(policy),
            limits=limits or Limits(),
        )


def starter_entries(policy: PolicySource | None) -> tuple[str, ...]:
    """Daftar berkas awal dari kebijakan course."""
    if policy is None:
        return ()
    return tuple(getattr(policy, "starter", ()) or ())


def read_only_entries(policy: PolicySource | None) -> tuple[str, ...]:
    """Daftar path read-only sebagaimana ditulis course.

    Berbeda dari :class:`ReadOnlyPolicy`, daftar ini mempertahankan bentuk asli
    agar dapat ditampilkan kembali kepada mahasiswa apa adanya.
    """
    if policy is None:
        return ()
    return tuple(getattr(policy, "read_only", ()) or ())


def describe(value: Any) -> str:
    """Nama tipe yang dapat dibaca manusia, untuk pesan kesalahan."""
    return type(value).__name__
