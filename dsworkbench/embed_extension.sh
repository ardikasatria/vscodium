#!/usr/bin/env bash
# Tanam ekstensi DSWorkbench (hasil bangun, bukan sumber) sebagai ekstensi bawaan
# aplikasi yang baru dibangun. Dipanggil workflow setelah langkah Build dan
# sebelum aset dikemas/ditandatangani.
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
echo "Ekstensi DSWorkbench $(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "${SRC}/package.json" | head -n 1) ditanam di ${DEST}/dsworkbench"
