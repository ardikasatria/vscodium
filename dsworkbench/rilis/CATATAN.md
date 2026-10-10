## DSWorkbench 0.1.0 — uji 3 (pra-rilis)

Aplikasi DSWorkbench yang baru: satu aplikasi untuk notebook, SQL, terminal, dan Git, dengan Local Runner di dalamnya. Masuk dengan akun Workbench, tanpa kode pairing.

**Ini pra-rilis untuk uji terbatas.** Workbench di peramban tetap bisa dipakai. Tutup aplikasi "Data Science Workbench" yang lama sebelum membuka yang ini: aplikasi baru memakai lingkungan Python yang sama.

### Yang baru sejak uji 2

- **Mode lab** untuk komputer yang dipakai bergantian dengan satu akun komputer: masuk tidak disimpan, keluar otomatis saat aplikasi ditutup atau ditinggal, jejak dibersihkan. Diaktifkan pengelola lab per komputer. Belum pernah dicoba di Windows.
- **Ganti avatar** langsung dari panel Sosial; perbaikan avatar yang tampil sebagai inisial.
- **Ikon berkas** Material Icon Theme menjadi bawaan (bisa diganti di pengaturan).
- Linux: arsip **`.tar.gz`** untuk distro selain Debian/Ubuntu, menggantikan AppImage.
- Ekstensi DSWorkbench 0.1.1.

### Unduh

| Komputer | Berkas |
|---|---|
| Windows 10/11 (64-bit) | `DSWorkbenchUserSetup-x64-<versi>.exe` |
| Mac Apple Silicon (M1 dan seterusnya) | `DSWorkbench-darwin-arm64-<versi>.zip` |
| Mac Intel | `DSWorkbench-darwin-x64-<versi>.zip` |
| Debian/Ubuntu (64-bit) | `dsworkbench_<versi>_amd64.deb` |
| Linux lain (Fedora, Arch, …) | `DSWorkbench-linux-x64-<versi>.tar.gz` |

Cocokkan unduhan dengan `SHA256SUMS`.

### Memasang

Aplikasi belum bertanda tangan penerbit, jadi sistem operasi memberi peringatan saat pertama dibuka.

- **Windows:** jalankan `.exe` (tanpa administrator). Bila muncul "Windows protected your PC": **More info** → **Run anyway**.
- **macOS:** buka `.zip`, seret **DSWorkbench** ke **Applications**, buka. Bila ditolak: **System Settings → Privacy & Security → Open Anyway**. Bila disebut "rusak": `xattr -cr /Applications/DSWorkbench.app`.
- **Debian/Ubuntu:** `sudo apt install ./dsworkbench_*.deb`.
- **Linux lain:** `mkdir -p ~/DSWorkbench && tar -xzf DSWorkbench-linux-x64-*.tar.gz -C ~/DSWorkbench`, lalu jalankan `~/DSWorkbench/bin/dsworkbench`. Di Ubuntu 24.04 atau lebih baru pakai `.deb`.

Lalu ikuti panduan **Mulai dengan DSWorkbench** di dalam aplikasi: masuk, siapkan lingkungan praktikum, buka Kelas.

### Yang sudah dan belum diuji

- Terhadap server sungguhan baru dicoba di macOS (uji 2): masuk akun, Kelas, notebook, tugas, checkpoint, pengumpulan, naskah, Bravais.
- **Belum dicoba orang** di Windows dan Linux. Mode lab, SQL Pergudangan Data, dan lingkungan Deep Learning belum diuji orang di aplikasi ini.
- Di Windows, menghentikan sel memulai ulang kernel (variabel hilang), sama seperti di Workbench web.

Berbasis [VSCodium](https://github.com/VSCodium/vscodium) 1.135 (Code - OSS, lisensi MIT). Galeri ekstensi: Open VSX. Ikon berkas: [Material Icon Theme](https://github.com/material-extensions/vscode-material-icon-theme) (lisensi MIT).
