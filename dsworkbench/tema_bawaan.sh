#!/usr/bin/env bash
# Jadikan tema DSWorkbench bawaan APLIKASI BERMEREK, tanpa menambal sumber VS Code:
# menambahkan `contributes.configurationDefaults` ke package.json salinan ekstensi
# yang DITANAM di aplikasi (bukan ke sumber ekstensi dan bukan ke .vsix).
#
#   ./dsworkbench/tema_bawaan.sh <folder ekstensi tertanam>
#
# Mengapa di sini: `configurationDefaults` di product.json tidak dibaca aplikasi
# desktop (lihat apps/ide/product/README.md); sumbangan ekstensi bawaan dibaca
# saat ekstensi didaftarkan, sebelum tema dari pengaturan dimuat.
# Gagal keras (keluar != 0) bila ada yang tidak sesuai harapan.
set -euo pipefail

EXT="${1:?pemakaian: tema_bawaan.sh <folder ekstensi tertanam>}"
PKG="${EXT}/package.json"
GELAP="DSWorkbench Gelap"
TERANG="DSWorkbench Terang"

command -v jq > /dev/null || { echo "tema_bawaan: jq tidak ditemukan" >&2; exit 1; }
[[ -f "${PKG}" ]] || { echo "tema_bawaan: ${PKG} tidak ada" >&2; exit 1; }

# Kedua tema harus benar-benar disumbangkan ekstensi ini, dengan jenis yang benar, dan berkasnya ada.
periksa_tema() {
  local label="$1" jenis="$2" jalur
  jalur="$( jq -r --arg l "${label}" --arg j "${jenis}" \
    '[.contributes.themes[]? | select(.label == $l and .uiTheme == $j) | .path] | if length == 1 then .[0] else empty end' "${PKG}" )"
  [[ -n "${jalur}" ]] || { echo "tema_bawaan: tema \"${label}\" (${jenis}) tidak disumbangkan ${PKG}" >&2; exit 1; }
  [[ -f "${EXT}/${jalur#./}" ]] || { echo "tema_bawaan: berkas tema ${jalur} tidak ada di ${EXT}" >&2; exit 1; }
  # Berkas tema boleh berkomentar (JSONC), jadi tidak diurai dengan jq.
  grep -q '"editor.background"' "${EXT}/${jalur#./}" \
    || { echo "tema_bawaan: ${jalur} bukan berkas tema yang sah" >&2; exit 1; }
}
periksa_tema "${GELAP}" "vs-dark"
periksa_tema "${TERANG}" "vs"

TMP="$( mktemp "${PKG}.XXXXXX" )"
trap 'rm -f "${TMP}"' EXIT
jq --arg g "${GELAP}" --arg t "${TERANG}" '
  .contributes.configurationDefaults = ((.contributes.configurationDefaults // {}) + {
    "workbench.colorTheme": $g,
    "workbench.preferredDarkColorTheme": $g,
    "workbench.preferredLightColorTheme": $t
  })' "${PKG}" > "${TMP}"

# Hasil harus tetap manifest yang sama, hanya bertambah tiga kunci itu.
jq -e --arg g "${GELAP}" --arg t "${TERANG}" '
  .contributes.configurationDefaults["workbench.colorTheme"] == $g
  and .contributes.configurationDefaults["workbench.preferredDarkColorTheme"] == $g
  and .contributes.configurationDefaults["workbench.preferredLightColorTheme"] == $t
  and (.name | type == "string") and (.version | type == "string") and (.main | type == "string")' "${TMP}" > /dev/null \
  || { echo "tema_bawaan: hasil penambahan tidak sesuai harapan" >&2; exit 1; }
[[ "$( jq -S 'del(.contributes.configurationDefaults)' "${PKG}" )" == "$( jq -S 'del(.contributes.configurationDefaults)' "${TMP}" )" ]] \
  || { echo "tema_bawaan: manifest berubah di luar configurationDefaults" >&2; exit 1; }

mv "${TMP}" "${PKG}"
trap - EXIT
echo "Tema bawaan aplikasi: ${GELAP} (terang: ${TERANG}) ditetapkan di ${PKG}"
