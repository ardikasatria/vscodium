#!/usr/bin/env bash
# Tanam ekstensi DSWorkbench (hasil bangun, bukan sumber) sebagai ekstensi bawaan
# aplikasi yang baru dibangun. Dipanggil workflow setelah langkah Build dan
# sebelum aset dikemas/ditandatangani.
#
# Pengganti dsworkbench/embed_extension.sh di fork (sumber: apps/ide/product/ di
# repo utama). Tambahannya dibanding versi sebelumnya:
#   - tema bawaan aplikasi lewat tema_bawaan.sh (configurationDefaults pada
#     salinan tertanam);
#   - pemeriksaan bahwa product.json hasil bangun mengizinkan ekstensi ini
#     diperbarui (builtInExtensionsEnabledWithAutoUpdates) — tanpa itu pembaruan
#     ekstensi dari kanal ditolak aplikasi.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${ROOT}/dsworkbench/extension"
[[ -f "${SRC}/package.json" ]] || { echo "ekstensi tidak ada di ${SRC}" >&2; exit 1; }

case "${OS_NAME}" in
  osx)     DEST_GLOB="${ROOT}/VSCode-darwin-${VSCODE_ARCH}/*.app/Contents/Resources/app/extensions" ;;
  windows) DEST_GLOB="${ROOT}/VSCode-win32-${VSCODE_ARCH}/resources/app/extensions" ;;
  *)       DEST_GLOB="${ROOT}/VSCode-linux-${VSCODE_ARCH}/resources/app/extensions" ;;
esac

# shellcheck disable=SC2086
DEST="$( ls -d ${DEST_GLOB} 2>/dev/null | head -n 1 )"
[[ -d "${DEST}" ]] || { echo "folder ekstensi bawaan tidak ditemukan: ${DEST_GLOB}" >&2; exit 1; }

rm -rf "${DEST}/dsworkbench"
cp -R "${SRC}" "${DEST}/dsworkbench"

# Tema bawaan: hanya pada salinan tertanam.
"${ROOT}/dsworkbench/tema_bawaan.sh" "${DEST}/dsworkbench"

# Pembaruan ekstensi: aplikasi menolak memasang versi baru dari ekstensi bawaan
# kecuali id-nya terdaftar di product.json (vscode: installExtensionTask,
# "builtinAutoUpdate"). Gagal keras bila tambalan product.json belum masuk.
PRODUK="$( dirname "${DEST}" )/product.json"
# Seluruh isi ekstensi tertanam harus terbaca semua pengguna (paket sistem dimiliki root).
chmod -R a+rX "${DEST}/dsworkbench"
[[ -z "$( find "${DEST}/dsworkbench" ! -perm -004 -print -quit )" ]] || { echo "ada berkas ekstensi yang tidak terbaca pengguna lain" >&2; exit 1; }

ID="$( jq -r '"\(.publisher).\(.name)"' "${DEST}/dsworkbench/package.json" )"
if ! jq -e --arg id "${ID}" '(.builtInExtensionsEnabledWithAutoUpdates // []) | map(ascii_downcase) | index($id | ascii_downcase) != null' "${PRODUK}" > /dev/null; then
  echo "product.json hasil bangun tidak memuat \"${ID}\" di builtInExtensionsEnabledWithAutoUpdates (lihat product.tambalan.json)" >&2
  # Sengaja membangun aplikasi yang ekstensinya TIDAK dapat diperbarui dari kanal: setel DSW_TANPA_PEMBARUAN_EKSTENSI=1.
  [[ "${DSW_TANPA_PEMBARUAN_EKSTENSI:-}" == "1" ]] || exit 1
fi
[[ "$( jq -r '.quality' "${PRODUK}" )" == "stable" ]] \
  || { echo "peringatan: quality bukan \"stable\"; pembaruan ekstensi bawaan hanya diizinkan pada quality stable" >&2; }

echo "Ekstensi DSWorkbench $( jq -r '.version' "${DEST}/dsworkbench/package.json" ) ditanam di ${DEST}/dsworkbench"
