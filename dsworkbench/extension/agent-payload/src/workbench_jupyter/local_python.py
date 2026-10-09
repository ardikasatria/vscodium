"""Backend kernel Python lokal tanpa Jupyter Server.

Dipakai Local Runner bila ``HttpJupyterBackend`` tidak terjangkau. Satu kernel
= satu namespace ``exec`` yang bertahan antar-sel.

Berbeda dari sekadar menangkap stdout: ekspresi terakhir sel dievaluasi
(seperti IPython) dan dikemas sebagai MIME bundle — sehingga ``df.head()``
menghasilkan ``text/html`` (tabel) dan objek dengan ``_repr_png_`` /
matplotlib figure menghasilkan gambar.
"""

from __future__ import annotations

import ast
import base64
import contextlib
import ctypes
import io
import os
import threading
import traceback
import uuid
from typing import Any, Callable, Iterator

from .errors import JupyterTimeoutError, KernelNotFoundError
from .models import (
    DEFAULT_EXECUTE_TIMEOUT_SECONDS,
    MAX_OUTPUT_CHARS,
    ErrorOutput,
    ExecuteResultOutput,
    ExecutionResult,
    KernelInfo,
    KernelState,
    StreamOutput,
)


def _potong(teks: str) -> str:
    if len(teks) <= MAX_OUTPUT_CHARS:
        return teks
    return teks[: MAX_OUTPUT_CHARS - 20] + "\n…[dipotong]"


#: Penerima keluaran bertahap: ``(nama_stream, teks)`` -- ``stdout``/``stderr``.
PenerimaStream = Callable[[str, str], None]


class _TeeIO(io.StringIO):
    """StringIO yang juga meneruskan setiap tulisan ke penerima (log epoch)."""

    def __init__(self, nama: str, penerima: PenerimaStream | None) -> None:
        super().__init__()
        self._nama = nama
        self._penerima = penerima

    def write(self, s: str) -> int:
        n = super().write(s)
        if self._penerima is not None and s:
            try:
                self._penerima(self._nama, s)
            except Exception:  # noqa: BLE001 - penerima rusak tidak boleh menggagalkan sel
                self._penerima = None
        return n


def _naikkan_di_utas(ident: int, exc: type[BaseException] | None) -> bool:
    """``PyThreadState_SetAsyncExc``: naikkan ``exc`` di utas ``ident``.

    ``None`` membatalkan pengecualian yang tertunda. Hanya efektif ketika utas
    itu menjalankan bytecode Python -- panggilan C yang panjang (mis. satu
    operasi pandas besar) baru terhenti setelah kembali ke Python.
    """
    hasil = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(ident), ctypes.py_object(exc) if exc is not None else None)
    if hasil > 1:  # pragma: no cover - tidak pernah terjadi dengan ident sah
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(ident), None)
        return False
    return hasil == 1


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return base64.b64encode(value).decode("ascii")
    if isinstance(value, str):
        return value
    return str(value)


def _mime_from_value(value: Any) -> dict[str, str]:
    """Bangun MIME bundle dari objek Python (pandas, matplotlib, dll.)."""
    data: dict[str, str] = {}

    repr_mime = getattr(value, "_repr_mimebundle_", None)
    if callable(repr_mime):
        try:
            bundle = repr_mime(include=None, exclude=None)
            if isinstance(bundle, tuple):
                bundle = bundle[0] if bundle else {}
            if isinstance(bundle, dict):
                for key, raw in bundle.items():
                    if not isinstance(key, str):
                        continue
                    if key.startswith("image/") and isinstance(raw, (bytes, bytearray)):
                        data[key] = base64.b64encode(bytes(raw)).decode("ascii")
                    else:
                        text = _as_text(raw)
                        if text:
                            data[key] = _potong(text) if key.startswith("text/") else text
        except Exception:  # noqa: BLE001 — jangan gagalkan sel karena repr
            pass

    if "text/html" not in data:
        html_fn = getattr(value, "_repr_html_", None)
        if callable(html_fn):
            try:
                html = html_fn()
                text = _as_text(html)
                if text and text.strip():
                    data["text/html"] = _potong(text)
            except Exception:  # noqa: BLE001
                pass

    if "text/markdown" not in data:
        # IPython.display.Markdown (sering dipakai sel analisis praktikum DL).
        md_fn = getattr(value, "_repr_markdown_", None)
        if callable(md_fn):
            try:
                text = _as_text(md_fn())
                if text and text.strip():
                    data["text/markdown"] = _potong(text)
            except Exception:  # noqa: BLE001
                pass

    if "image/png" not in data:
        png_fn = getattr(value, "_repr_png_", None)
        if callable(png_fn):
            try:
                png = png_fn()
                if isinstance(png, (bytes, bytearray)) and png:
                    data["image/png"] = base64.b64encode(bytes(png)).decode("ascii")
                else:
                    text = _as_text(png)
                    if text:
                        data["image/png"] = text.replace("\n", "").replace(" ", "")
            except Exception:  # noqa: BLE001
                pass

    if "image/svg+xml" not in data:
        svg_fn = getattr(value, "_repr_svg_", None)
        if callable(svg_fn):
            try:
                svg = svg_fn()
                text = _as_text(svg)
                if text and text.strip():
                    data["image/svg+xml"] = text
            except Exception:  # noqa: BLE001
                pass

    if "text/plain" not in data:
        try:
            data["text/plain"] = _potong(repr(value))
        except Exception:  # noqa: BLE001
            data["text/plain"] = _potong(str(type(value)))

    return data


def _capture_matplotlib_pngs() -> list[dict[str, str]]:
    """Ambil figure matplotlib yang masih terbuka sebagai image/png."""
    try:
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001
        return []
    bundles: list[dict[str, str]] = []
    try:
        for num in list(plt.get_fignums()):
            fig = plt.figure(num)
            buf = io.BytesIO()
            try:
                fig.savefig(buf, format="png", bbox_inches="tight")
            except Exception:  # noqa: BLE001
                continue
            payload = base64.b64encode(buf.getvalue()).decode("ascii")
            if payload:
                bundles.append(
                    {
                        "image/png": payload,
                        "text/plain": f"<Figure size {getattr(fig, 'get_size_inches', lambda: (0, 0))()}>",
                    }
                )
            try:
                plt.close(fig)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        return bundles
    return bundles


def _split_last_expression(code: str) -> tuple[str | None, str | None]:
    """Pisahkan badan exec dan ekspresi terakhir (bila ada)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, None
    if not tree.body:
        return None, None
    last = tree.body[-1]
    if not isinstance(last, ast.Expr):
        return code, None
    body_nodes = tree.body[:-1]
    exec_src = None
    if body_nodes:
        exec_src = ast.unparse(ast.Module(body=body_nodes, type_ignores=[]))
    expr_src = ast.unparse(last.value)
    return exec_src, expr_src


class LocalPythonBackend:
    """Interpreter Python in-process per kernel-id.

    ``cwd``: direktori kerja sel, biasanya akar workspace mahasiswa. Notebook
    praktikum memakai path relatif (``data/raw``, ``Path.cwd()``) dan menulis
    luaran seperti ``metrics.csv`` ke direktori kerja; tanpa ini keduanya
    mengarah ke folder agent, yang tidak terlihat di tab Workspace. Direktori
    kerja proses agent dipulihkan setelah setiap sel, sedangkan ``os.chdir``
    yang dilakukan sel sendiri diingat per kernel -- seperti Jupyter.
    """

    def __init__(self, *, cwd: str | os.PathLike[str] | None = None) -> None:
        self._namespaces: dict[str, dict[str, Any]] = {}
        self._meta: dict[str, KernelInfo] = {}
        #: Mirip In [n] Jupyter — naik tiap execute, reset saat restart.
        self._exec_count: dict[str, int] = {}
        self._cwd_awal = str(cwd) if cwd is not None else None
        self._cwd: dict[str, str | None] = {}
        #: Direktori kerja awal per kernel (dipulihkan saat restart).
        self._cwd_kernel: dict[str, str | None] = {}
        #: kernel → utas yang sedang mengeksekusi selnya (ADR-049 §3).
        self._aktif: dict[str, int] = {}
        self._kunci_aktif = threading.Lock()
        #: Menjaga tabel kernel di atas: start/stop/restart/status datang dari
        #: utas transport relay sementara sel berjalan di utas pekerja job.
        #: Isi sel sendiri TIDAK dijalankan di bawah kunci ini.
        self._kunci = threading.RLock()

    @contextlib.contextmanager
    def _di_direktori_kerja(self, kernel_id: str) -> Iterator[None]:
        tujuan = self._cwd.get(kernel_id, self._cwd_awal)
        if tujuan is None:
            yield
            return
        try:
            sebelumnya: str | None = os.getcwd()
        except OSError:
            sebelumnya = None
        try:
            os.chdir(tujuan)
        except OSError:
            # Workspace hilang: jalankan di tempat semula, jangan gagalkan sel.
            yield
            return
        try:
            yield
        finally:
            try:
                self._cwd[kernel_id] = os.getcwd()
            except OSError:
                self._cwd[kernel_id] = tujuan
            if sebelumnya is not None:
                try:
                    os.chdir(sebelumnya)
                except OSError:
                    pass

    def ping(self) -> bool:
        return True

    def start_kernel(self, *, name: str | None = None,
                     cwd: str | os.PathLike[str] | None = None) -> KernelInfo:
        """``cwd``: direktori kerja kernel ini (mis. akar mata kuliah per-course,
        ADR-050); tanpa itu direktori bawaan backend."""
        kid = f"local-{uuid.uuid4().hex[:12]}"
        info = KernelInfo(
            id=kid,
            name=name or "python3-local",
            state=KernelState.IDLE,
            execution_state=KernelState.IDLE,
        )
        with self._kunci:
            self._namespaces[kid] = {"__name__": "__main__"}
            self._meta[kid] = info
            self._exec_count[kid] = 0
            self._cwd[kid] = str(cwd) if cwd is not None else self._cwd_awal
            self._cwd_kernel[kid] = self._cwd[kid]
        return info

    def stop_kernel(self, kernel_id: str) -> None:
        with self._kunci:
            if kernel_id not in self._namespaces:
                raise KernelNotFoundError(kernel_id)
            del self._namespaces[kernel_id]
            del self._meta[kernel_id]
            self._exec_count.pop(kernel_id, None)
            self._cwd.pop(kernel_id, None)
            self._cwd_kernel.pop(kernel_id, None)

    def interrupt_kernel(self, kernel_id: str) -> None:
        """Hentikan sel yang sedang berjalan dengan ``KeyboardInterrupt``.

        Sel dieksekusi di utas pekerja agent (ADR-049); interrupt dikirim dari
        utas transport. Batasnya jujur: panggilan C yang panjang baru terhenti
        setelah kembali ke Python. Kernel yang menganggur: tidak terjadi apa-apa.
        """
        if kernel_id not in self._namespaces:
            raise KernelNotFoundError(kernel_id)
        with self._kunci_aktif:
            ident = self._aktif.get(kernel_id)
            if ident is not None:
                _naikkan_di_utas(ident, KeyboardInterrupt)

    def is_executing(self, kernel_id: str) -> bool:
        with self._kunci_aktif:
            return kernel_id in self._aktif

    def restart_kernel(self, kernel_id: str) -> KernelInfo:
        with self._kunci:
            if kernel_id not in self._namespaces:
                raise KernelNotFoundError(kernel_id)
            name = self._meta[kernel_id].name
            self._namespaces[kernel_id] = {"__name__": "__main__"}
            self._exec_count[kernel_id] = 0
            self._cwd[kernel_id] = self._cwd_kernel.get(kernel_id, self._cwd_awal)
            info = KernelInfo(
                id=kernel_id,
                name=name,
                state=KernelState.IDLE,
                execution_state=KernelState.IDLE,
            )
            self._meta[kernel_id] = info
        return info

    def kernel_status(self, kernel_id: str) -> KernelInfo:
        with self._kunci:
            if kernel_id not in self._meta:
                raise KernelNotFoundError(kernel_id)
            return self._meta[kernel_id]

    #: Backend ini dapat meneruskan keluaran bertahap (``on_stream``).
    supports_streaming = True

    def execute(
        self,
        kernel_id: str,
        code: str,
        *,
        timeout: float | None = DEFAULT_EXECUTE_TIMEOUT_SECONDS,
        on_stream: PenerimaStream | None = None,
    ) -> ExecutionResult:
        """Eksekusi satu sel.

        ``timeout`` ditegakkan dengan interrupt yang sama seperti tombol
        Hentikan; ``None`` = tanpa batas (dipakai proses kernel anak, yang
        batasnya dijaga induknya). Sel yang dihentikan mengembalikan keluaran
        yang sudah tercetak ditambah ``KeyboardInterrupt``; sel yang melewati
        batas menaikkan :class:`JupyterTimeoutError`.
        """
        if kernel_id not in self._namespaces:
            raise KernelNotFoundError(kernel_id)
        ident = threading.get_ident()
        with self._kunci_aktif:
            self._aktif[kernel_id] = ident
        lewat_batas = threading.Event()
        penjaga: threading.Timer | None = None
        if timeout is not None and timeout > 0:
            def _habis() -> None:
                lewat_batas.set()
                self.interrupt_kernel(kernel_id)

            penjaga = threading.Timer(timeout, _habis)
            penjaga.daemon = True
            penjaga.start()
        try:
            try:
                hasil = self._execute(kernel_id, code, on_stream)
            except KeyboardInterrupt:
                # Tiba di luar blok exec (sangat singkat); keluaran sel hilang.
                hasil = ExecutionResult(status="error", outputs=(_ERROR_DIHENTIKAN,))
        finally:
            if penjaga is not None:
                penjaga.cancel()
            with self._kunci_aktif:
                self._aktif.pop(kernel_id, None)
                # Interrupt yang tiba tepat setelah sel selesai tidak boleh
                # menghantam kode agent sesudahnya.
                _naikkan_di_utas(ident, None)
        if lewat_batas.is_set():
            raise JupyterTimeoutError(
                "Sel melewati batas waktu dan dihentikan. Variabel yang sudah ada "
                "tetap tersimpan di kernel.")
        return hasil

    def _execute(
        self, kernel_id: str, code: str, on_stream: PenerimaStream | None,
    ) -> ExecutionResult:
        with self._kunci:
            ns = self._namespaces.get(kernel_id)
            if ns is None:
                raise KernelNotFoundError(kernel_id)
            next_count = self._exec_count.get(kernel_id, 0) + 1
            self._exec_count[kernel_id] = next_count

        stdout = _TeeIO("stdout", on_stream)
        stderr = _TeeIO("stderr", on_stream)

        exec_src, expr_src = _split_last_expression(code)
        result_value: Any = _SENTINEL
        displayed: list[Any] = []

        def _display(*objs: Any, **_kwargs: Any) -> None:
            for obj in objs:
                displayed.append(obj)

        ns["display"] = _display
        # Sel praktikum sering `from IPython.display import display`.
        try:
            import IPython.display as ipd  # type: ignore

            ipd.display = _display  # type: ignore[method-assign]
        except Exception:  # noqa: BLE001
            pass

        try:
            with self._di_direktori_kerja(kernel_id), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                if exec_src:
                    exec(  # noqa: S102 — sengaja: kernel milik mahasiswa
                        compile(exec_src, "<workbench-cell>", "exec"),
                        ns,
                        ns,
                    )
                elif expr_src is None and code.strip():
                    # Sel penuh tanpa ekspresi terakhir terpisah (atau parse gagal → code utuh).
                    exec(  # noqa: S102
                        compile(code, "<workbench-cell>", "exec"),
                        ns,
                        ns,
                    )
                if expr_src is not None:
                    result_value = eval(  # noqa: S307 — ekspresi terakhir sel praktikum
                        compile(expr_src, "<workbench-cell-expr>", "eval"),
                        ns,
                        ns,
                    )
        except KeyboardInterrupt:
            # Tombol Hentikan / batas waktu: pertahankan log yang sudah tercetak
            # (mis. epoch pelatihan) agar mahasiswa tahu sampai mana selnya.
            parsial: list[Any] = []
            if stdout.getvalue():
                parsial.append(StreamOutput(name="stdout", text=_potong(stdout.getvalue())))
            if stderr.getvalue():
                parsial.append(StreamOutput(name="stderr", text=_potong(stderr.getvalue())))
            return ExecutionResult(
                status="error",
                execution_count=next_count,
                outputs=(*parsial, _ERROR_DIHENTIKAN),
            )
        except Exception as exc:
            tb = traceback.format_exc()
            return ExecutionResult(
                status="error",
                execution_count=next_count,
                outputs=(
                    ErrorOutput(
                        ename=type(exc).__name__,
                        evalue=str(exc),
                        traceback=tuple(tb.splitlines()),
                    ),
                ),
            )

        outputs: list[Any] = []
        out = stdout.getvalue()
        err = stderr.getvalue()
        if out:
            outputs.append(StreamOutput(name="stdout", text=_potong(out)))
        if err:
            outputs.append(StreamOutput(name="stderr", text=_potong(err)))

        for obj in displayed:
            if obj is None:
                continue
            mime = _mime_from_value(obj)
            if mime:
                outputs.append(
                    ExecuteResultOutput(data=mime, execution_count=next_count)
                )

        if result_value is not _SENTINEL and result_value is not None:
            mime = _mime_from_value(result_value)
            if mime:
                outputs.append(
                    ExecuteResultOutput(data=mime, execution_count=next_count)
                )

        for bundle in _capture_matplotlib_pngs():
            outputs.append(
                ExecuteResultOutput(data=bundle, execution_count=next_count)
            )

        return ExecutionResult(
            status="ok", outputs=tuple(outputs), execution_count=next_count
        )


_ERROR_DIHENTIKAN = ErrorOutput(
    ename="KeyboardInterrupt",
    evalue="Eksekusi sel dihentikan.",
    traceback=(),
)


class _Sentinel:
    pass


_SENTINEL = _Sentinel()
