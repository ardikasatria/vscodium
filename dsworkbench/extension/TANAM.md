# DSWorkbench — ekstensi siap tanam (catatan untuk pembangun aplikasi)

Hasil bangun `apps/ide/extension` (ekstensi `sditera.dsworkbench` 0.1.6).
Local Runner bawaan: 0.4.10 @ af188e5.

## Menanam ke aplikasi bermerek (fork skrip VSCodium)

1. Salin SELURUH folder ini menjadi `extensions/dsworkbench/` di pohon sumber sebelum
   pengemasan, atau langsung ke aplikasi hasil bangun:
   - macOS: `DSWorkbench.app/Contents/Resources/app/extensions/dsworkbench/`
   - Windows/Linux: `resources/app/extensions/dsworkbench/`
   Nama folder bebas; yang dibaca adalah `package.json` di dalamnya.
2. Jangan menjalankan `npm install` di sini: ekstensi sudah dibundel menjadi satu berkas
   (`dist/extension.js`) dan tidak punya dependensi runtime.
3. Tema bawaan dan izin pembaruan diatur saat menanam, bukan di folder ini: pakai
   `embed_extension.sh` + `tema_bawaan.sh` dari `apps/ide/product/` (menambahkan
   `configurationDefaults` ke salinan tertanam) dan tambalan `product.json` di sana
   (`builtInExtensionsEnabledWithAutoUpdates`). Kunci `configurationDefaults` di
   `product.json` tidak dibaca aplikasi desktop. Sebagai jaring pengaman, ekstensi
   menerapkan "DSWorkbench Gelap" sekali saat pertama aktif bila nama aplikasi diawali
   `DSWorkbench` dan pengguna belum memilih tema.
4. Di macOS, tanda tangani aplikasi SETELAH folder ini disalin. `agent-payload/` berisi
   sumber Python saja (tanpa biner); ekstensi menjalankannya dengan
   `PYTHONDONTWRITEBYTECODE=1` sehingga tidak ada berkas yang ditulis ke dalam bundel.

## Isi

| Jalur | Isi |
|---|---|
| `package.json` | manifest ekstensi (perintah, pengaturan, tema, view) |
| `dist/extension.js` | kode ekstensi, satu berkas |
| `media/` | ikon bilah aktivitas; skrip panel (`panel/`); KaTeX (`katex/`, MIT, huruf OFL); pemutar Lottie (`lottie/`, MIT); karakter Bravais (`bravais/`); suara panel (`suara/`, turunan aset stok, lisensi belum dipastikan: lihat `suara/CATATAN.txt`) |
| `themes/` | `DSWorkbench Gelap` dan `DSWorkbench Terang` |
| `agent-payload/` | sumber Local Runner + `MANIFEST.json` (versi, commit, sha256 tiap berkas) |

Memasang sebagai ekstensi biasa: pakai `dsworkbench-0.1.6.vsix` di folder induk
(`code --install-extension …` atau *Extensions: Install from VSIX*).
