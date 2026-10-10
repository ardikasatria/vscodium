## DSWorkbench 0.1.0 — uji 2 (pra-rilis)

Aplikasi DSWorkbench yang baru: satu aplikasi untuk notebook, SQL, terminal, dan Git, dengan Local Runner di dalamnya. Masuk dengan akun Workbench, tanpa kode pairing.

**Ini pra-rilis untuk uji terbatas.** Workbench di peramban tetap bisa dipakai. Tutup aplikasi "Data Science Workbench" yang lama sebelum membuka yang ini: aplikasi baru memakai lingkungan Python yang sama.

### Yang baru sejak uji 1

- Panel **Naskah modul**, **Beranda**, **Bravais**, dan **Sosial** di dalam aplikasi.
- **Siapkan lingkungan praktikum** dari dalam aplikasi; di Windows Python 3.12 dipasang otomatis ke folder aplikasi.
- Ekstensi DSWorkbench dapat **memperbarui dirinya sendiri** (pengguna uji 1 perlu memasang versi ini sekali).
- Tema DSWorkbench Gelap menjadi bawaan sejak pembukaan pertama.
- Installer Windows kini memuat nomor versi.

### Unduh

| Komputer | Berkas |
|---|---|
| Windows 10/11 (64-bit) | `DSWorkbenchUserSetup-x64-<versi>.exe` |
| Mac Apple Silicon (M1 dan seterusnya) | `DSWorkbench-darwin-arm64-<versi>.zip` |
| Mac Intel | `DSWorkbench-darwin-x64-<versi>.zip` |
| Debian/Ubuntu (64-bit) | `dsworkbench_<versi>_amd64.deb` |

Cocokkan unduhan dengan `SHA256SUMS`. AppImage tidak disertakan: belum terbukti berjalan (gagal di Ubuntu 24.04).

### Memasang

Aplikasi belum bertanda tangan penerbit, jadi sistem operasi memberi peringatan saat pertama dibuka.

- **Windows:** jalankan `.exe` (tanpa administrator). Bila muncul "Windows protected your PC": **More info** → **Run anyway**.
- **macOS:** buka `.zip`, seret **DSWorkbench** ke **Applications**, buka. Bila ditolak: **System Settings → Privacy & Security → Open Anyway**. Bila disebut "rusak": `xattr -cr /Applications/DSWorkbench.app`.
- **Debian/Ubuntu:** `sudo apt install ./dsworkbench_*.deb`.

Lalu ikuti panduan **Mulai dengan DSWorkbench** di dalam aplikasi: masuk, siapkan lingkungan praktikum, buka Kelas.

### Yang sudah dan belum diuji

- Uji otomatis pada aplikasi terpasang (Windows, macOS Intel dan Apple Silicon, Ubuntu `.deb`): aplikasi dan ekstensi berjalan, notebook dijalankan lewat Local Runner, dan lingkungan Data Wrangling terpasang dari nol (di Windows termasuk Python). Uji itu memakai server tiruan.
- Terhadap server sungguhan baru dicoba di macOS: masuk akun, Kelas, notebook, tugas, checkpoint, pengumpulan, naskah, Bravais.
- **Belum dicoba orang** di Windows dan Linux. SQL Pergudangan Data dan lingkungan Deep Learning belum diuji di aplikasi ini.
- Di Windows, menghentikan sel memulai ulang kernel (variabel hilang), sama seperti di Workbench web.

Berbasis [VSCodium](https://github.com/VSCodium/vscodium) 1.135 (Code - OSS, lisensi MIT). Galeri ekstensi: Open VSX.
