#!/usr/bin/env bash
# Tanam Material Icon Theme (PKief.material-icon-theme, lisensi MIT) sebagai ekstensi
# bawaan kedua dan jadikan tema ikon berkas bawaan APLIKASI BERMEREK. Tanpa menambal
# sumber VS Code; pengguna tetap dapat memilih tema ikon lain.
#
#   ./dsworkbench/ikon_bawaan.sh <folder extensions aplikasi> <folder ekstensi DSWorkbench tertanam>
#
# Paket diambil dari Open VSX saat membangun, pada versi yang dipatok, dan ditolak bila
# sha256-nya tidak cocok. Menaikkan versi = mengubah VERSI dan SHA256 di bawah.
# Berkas lisensinya (LICENSE.txt) ikut ditanam apa adanya.
# Gagal keras (keluar != 0) bila ada yang tidak sesuai harapan.
set -euo pipefail

VERSI="5.39.0"
SHA256="f3fda4e1d6095d942c92467c1ad6f322a74c62160f7d29741058e4d726922fbd"
URL="https://open-vsx.org/api/PKief/material-icon-theme/${VERSI}/file/PKief.material-icon-theme-${VERSI}.vsix"
ID_TEMA="material-icon-theme"
NAMA_FOLDER="material-icon-theme"

DEST="${1:?pemakaian: ikon_bawaan.sh <folder extensions> <folder ekstensi DSWorkbench tertanam>}"
EXT_DSW="${2:?pemakaian: ikon_bawaan.sh <folder extensions> <folder ekstensi DSWorkbench tertanam>}"
PKG_DSW="${EXT_DSW}/package.json"

for alat in jq curl unzip; do
  command -v "${alat}" > /dev/null || { echo "ikon_bawaan: ${alat} tidak ditemukan" >&2; exit 1; }
done
[[ -d "${DEST}" ]] || { echo "ikon_bawaan: ${DEST} tidak ada" >&2; exit 1; }
[[ -f "${PKG_DSW}" ]] || { echo "ikon_bawaan: ${PKG_DSW} tidak ada" >&2; exit 1; }

sha256() {
  if command -v sha256sum > /dev/null; then sha256sum "$1" | cut -d ' ' -f 1; else shasum -a 256 "$1" | cut -d ' ' -f 1; fi
}

KERJA="$( mktemp -d )"
TMP=""
trap 'rm -rf "${KERJA}"; [[ -z "${TMP}" ]] || rm -f "${TMP}"' EXIT

# DSW_IKON_VSIX=<berkas> memakai unduhan yang sudah ada (uji lokal); sha256 tetap diperiksa.
VSIX="${DSW_IKON_VSIX:-}"
if [[ -z "${VSIX}" ]]; then
  VSIX="${KERJA}/ikon.vsix"
  curl --fail --silent --show-error --location --retry 3 --retry-delay 5 --max-time 300 -o "${VSIX}" "${URL}"
fi
DAPAT="$( sha256 "${VSIX}" )"
[[ "${DAPAT}" == "${SHA256}" ]] || { echo "ikon_bawaan: sha256 paket tidak cocok (dapat ${DAPAT})" >&2; exit 1; }

# Seluruh arsip diekstrak: pola 'extension/*' tidak menjangkau subfolder pada unzip di runner Windows.
unzip -q "${VSIX}" -d "${KERJA}/isi"
SRC="${KERJA}/isi/extension"
PKG="${SRC}/package.json"

# Paket harus benar-benar yang dimaksud: nama, versi, lisensi MIT beserta berkasnya, dan tema ikonnya.
jq -e --arg v "${VERSI}" --arg id "${ID_TEMA}" '
  .publisher == "PKief" and .name == "material-icon-theme" and .version == $v and .license == "MIT"
  and ([.contributes.iconThemes[]? | select(.id == $id)] | length == 1)' "${PKG}" > /dev/null \
  || { echo "ikon_bawaan: isi paket tidak sesuai harapan" >&2; exit 1; }
grep -q 'MIT License' "${SRC}/LICENSE.txt" || { echo "ikon_bawaan: berkas lisensi MIT tidak ada di paket" >&2; exit 1; }
# tr: jq di Windows mengakhiri baris dengan CRLF.
JALUR_TEMA="$( jq -r --arg id "${ID_TEMA}" '.contributes.iconThemes[] | select(.id == $id) | .path' "${PKG}" | tr -d '\r' )"
[[ -f "${SRC}/${JALUR_TEMA#./}" ]] || { echo "ikon_bawaan: berkas tema ikon ${JALUR_TEMA} tidak ada" >&2; exit 1; }

rm -rf "${DEST:?}/${NAMA_FOLDER}"
cp -R "${SRC}" "${DEST}/${NAMA_FOLDER}"
chmod -R a+rX "${DEST}/${NAMA_FOLDER}"
[[ -z "$( find "${DEST}/${NAMA_FOLDER}" ! -perm -004 -print -quit )" ]] || { echo "ikon_bawaan: ada berkas yang tidak terbaca pengguna lain" >&2; exit 1; }

# Tema ikon bawaan: lewat configurationDefaults salinan ekstensi DSWorkbench yang ditanam
# (cara yang sama dengan tema warna; lihat tema_bawaan.sh).
TMP="$( mktemp "${PKG_DSW}.XXXXXX" )"
jq --arg id "${ID_TEMA}" '.contributes.configurationDefaults = ((.contributes.configurationDefaults // {}) + {"workbench.iconTheme": $id})' "${PKG_DSW}" > "${TMP}"
jq -e --arg id "${ID_TEMA}" '.contributes.configurationDefaults["workbench.iconTheme"] == $id and (.name | type == "string") and (.main | type == "string")' "${TMP}" > /dev/null \
  || { echo "ikon_bawaan: hasil penambahan tidak sesuai harapan" >&2; exit 1; }
[[ "$( jq -S 'del(.contributes.configurationDefaults["workbench.iconTheme"])' "${PKG_DSW}" )" == "$( jq -S 'del(.contributes.configurationDefaults["workbench.iconTheme"])' "${TMP}" )" ]] \
  || { echo "ikon_bawaan: manifest berubah di luar workbench.iconTheme" >&2; exit 1; }
mv "${TMP}" "${PKG_DSW}"
TMP=""
# mktemp membuat berkas 0600 (lihat tema_bawaan.sh).
chmod 644 "${PKG_DSW}"

echo "Tema ikon bawaan aplikasi: Material Icon Theme ${VERSI} ditanam di ${DEST}/${NAMA_FOLDER}"
