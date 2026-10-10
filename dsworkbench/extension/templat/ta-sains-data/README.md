# Format Tugas Akhir Sains Data ITERA (LaTeX)

Versi rapi dari "Format Penulisan TA Sains Data". Isi dan tampilan halaman sama dengan
format asli (27 halaman); yang berubah hanya susunan berkas dan dua perbaikan
di bawah.

## Susunan

| Berkas / folder | Isi |
|---|---|
| `main.tex` | Berkas utama: judul, nama, dan urutan bagian. **Ini yang dikompilasi.** |
| `tasainsdata.cls` | Seluruh pengaturan format. Tidak perlu diubah saat menulis. |
| `awal/` | Sampul, pengesahan, orisinalitas, persetujuan publikasi, abstrak, motto, persembahan, kata pengantar |
| `bab/` | `bab1.tex` … `bab5.tex` |
| `akhir/` | Daftar pustaka |
| `gambar/` | Logo ITERA, pasfoto, dan gambar Anda |
| `build/` | Hasil kompilasi (PDF dan berkas bantu) bila dikompilasi dari DSWorkbench |

## Mengompilasi

Di DSWorkbench: buka `main.tex`, lalu **DSWorkbench: Menulis — Kompilasi** (atau simpan
berkas bila LaTeX Workshop terpasang). Hasilnya `build/main.pdf`; berkas bantu (`.aux`,
`.log`, `.toc`, …) juga di `build/` dan boleh dihapus kapan saja.

Di luar DSWorkbench:

```sh
latexmk -pdf main.tex
```

atau `pdflatex main.tex` dua kali, supaya daftar isi, daftar gambar, dan daftar tabel
terisi. Paket yang dipakai antara lain `thmtools`, `algorithms`, `algorithmicx`,
`cleveref`, `titlesec`, `breqn`, dan `babel-indonesian`; profil **Menulis (LaTeX)**
DSWorkbench sudah memasangnya.

Karena bagian dimuat dengan `\masuk{…}` (bukan `\include`), berkas bantu hanya satu
set untuk `main.tex`, tidak di tiap folder.

## Menambah bab atau lampiran

Buat `bab/bab6.tex` (diawali `\chapter{JUDUL}`), lalu tambahkan `\masuk{bab/bab6}` di
`main.tex`.

## Yang berbeda dari format asli

1. **Tautan tanpa kotak/garis.** `hyperref` dimuat dengan `hidelinks`: daftar isi, daftar
   gambar, daftar tabel, dan rujukan tetap bisa diklik di PDF, tetapi tidak lagi
   berbingkai warna.
2. **Huruf khusus tampil benar.** Berkas dibaca sebagai UTF-8 (asalnya `latin1`), sehingga
   mis. "1½ spasi" tidak lagi tercetak "1Â½ spasi".
3. `\sectiontitle`, yang dipakai format asli tetapi tidak pernah didefinisikan, kini
   didefinisikan kosong: tampilan sama, galat kompilasi di tiap `\section` hilang.
4. Berkas hasil kompilasi (`.aux`, `.log`, `.synctex.gz`, …) dan `desktop.ini` dibuang.
