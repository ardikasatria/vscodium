# DSWorkbench — ekstensi siap tanam

Hasil bangun `apps/ide/extension` (ekstensi `sditera.dsworkbench` 0.1.0).
Local Runner bawaan: 0.4.10 @ e19ddb3.

## Menanam ke aplikasi bermerek (fork skrip VSCodium)

1. Salin SELURUH folder ini menjadi `extensions/dsworkbench/` di pohon sumber sebelum
   pengemasan, atau langsung ke aplikasi hasil bangun:
   - macOS: `DSWorkbench.app/Contents/Resources/app/extensions/dsworkbench/`
   - Windows/Linux: `resources/app/extensions/dsworkbench/`
   Nama folder bebas; yang dibaca adalah `package.json` di dalamnya.
2. Jangan menjalankan `npm install` di sini: ekstensi sudah dibundel menjadi satu berkas
   (`dist/extension.js`) dan tidak punya dependensi runtime.
3. Tema bawaan diatur di sisi produk, bukan oleh ekstensi. Di `product.json`:

   ```json
   "configurationDefaults": { "workbench.colorTheme": "DSWorkbench Gelap" }
   ```

   Tanpa itu, ekstensi menerapkan "DSWorkbench Gelap" sekali saat pertama aktif bila
   nama aplikasi (`nameShort`/`nameLong`) diawali `DSWorkbench` dan pengguna belum
   memilih tema.
4. Di macOS, tanda tangani aplikasi SETELAH folder ini disalin. `agent-payload/` berisi
   sumber Python saja (tanpa biner); ekstensi menjalankannya dengan
   `PYTHONDONTWRITEBYTECODE=1` sehingga tidak ada berkas yang ditulis ke dalam bundel.

## Isi

| Jalur | Isi |
|---|---|
| `package.json` | manifest ekstensi (perintah, pengaturan, tema, view) |
| `dist/extension.js` | kode ekstensi, satu berkas |
| `media/` | ikon bilah aktivitas |
| `themes/` | `DSWorkbench Gelap` dan `DSWorkbench Terang` |
| `agent-payload/` | sumber Local Runner + `MANIFEST.json` (versi, commit, sha256 tiap berkas) |

Memasang sebagai ekstensi biasa: pakai `dsworkbench-0.1.0.vsix` di folder induk
(`code --install-extension …` atau *Extensions: Install from VSIX*).
