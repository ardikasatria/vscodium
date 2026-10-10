# Uji asap DSWorkbench (bundel)

Hasil bangun `apps/ide/smoke` dari repo utama (privat). Folder ini ditaruh di fork
publik sebagai `dsworkbench/smoke/` dan dijalankan workflow
`.github/workflows/dsworkbench-smoke.yml` pada **aplikasi yang terpasang** di runner
Windows, Linux, dan macOS. Tidak ada sumber, jalur lokal, atau rahasia di dalamnya;
token dan kredensial yang terlihat di `inti.mjs` adalah nilai tiruan untuk server palsu
lokal.

| Berkas | Isi |
|---|---|
| `uji-asap.mjs` | peluncur: menemukan biner dan ekstensi tertanam, menjalankan `inti.mjs`, menulis ringkasan |
| `inti.mjs` | uji integrasi (salinan `scripts/run-integration.mjs`): server HTTP palsu, profil sementara, pemeriksaan |
| `suite.cjs` | suite yang berjalan DI DALAM aplikasi (`--extensionTestsPath`) |
| `fixtures/agen_sungguhan.py` | Local Runner sungguhan (`agent-payload/` ekstensi yang diuji) dengan pengendali relay tiruan |
| `lib/zip.mjs`, `ide-local-ops.json` | pembantu `inti.mjs` |
| `uji-lingkungan.cjs` | uji pemasang lingkungan sungguhan (modul `src/lingkungan/*` terkompilasi) |
| `MANIFEST.json` | waktu bangun, commit, sidik ekstensi yang dipakai membangun suite, sha256 tiap berkas |
| `PICU` | parameter + pemicu workflow (`on.push.paths`) |

## Menjalankan dengan tangan

Butuh Node ≥ 20 dan Python ≥ 3.10 (pustaka standar saja).

```sh
# aplikasi terpasang; ekstensi = yang tertanam di aplikasi itu
node uji-asap.mjs --app /Applications/DSWorkbench.app --keluar hasil
node uji-asap.mjs --app "$LOCALAPPDATA/Programs/DSWorkbench" --keluar hasil      # Windows
xvfb-run -a node uji-asap.mjs --app /usr/share/dsworkbench --keluar hasil \
  --simpanan-rahasia memori                                                      # Linux

# ekstensi lain (mis. build terbaru di dsworkbench/extension) pada aplikasi yang sama
node uji-asap.mjs --app <aplikasi> --ext ../extension --keluar hasil

# pemasang lingkungan: jalur kering (tanpa jaringan), lalu sungguhan (pip dari PyPI, ±1 GB)
node uji-lingkungan.cjs --ext <folder ekstensi> --runtime /tmp/rt --kering
node uji-lingkungan.cjs --ext <folder ekstensi> --runtime /tmp/rt --keluar hasil/ringkasan-lingkungan.json
node uji-lingkungan.cjs --ext <folder ekstensi> --runtime %TEMP%\rt --pasang-python   # Windows: unduh + pasang Python resmi dulu
```

`uji-asap.mjs` keluar 0 hanya bila semua pemeriksaan lulus dan menulis
`ringkasan-uji-asap.json` (aplikasi, ekstensi, Python, daftar pemeriksaan, galat).
Suite dibangun bersama satu build ekstensi (`MANIFEST.json → ekstensi.sha256`); bila
ekstensi yang diuji build lain, ringkasan menandai `cocokDenganBundel: false` dan
kegagalan bisa berarti fitur itu memang belum ada di build tersebut.

## Yang dibutuhkan Python

Kernel notebook dan Local Runner pada uji ini hanya memakai pustaka standar
(`workbench_jupyter.worker`: "hanya memakai pustaka standar dan paket
`workbench_jupyter`"). `IPython`, `matplotlib`, `certifi`, `dulwich`, dan `urllib3`
diimpor malas di dalam `try` dan tidak dibutuhkan suite. `actions/setup-python` tanpa
paket tambahan sudah cukup.
