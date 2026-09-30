#!/usr/bin/env bash
# Scheme A–style packaging (see office_skills scheme-a-linux-to-*-desktop).
#
#   ./build.sh                     → Linux onedir  → release/cad-browser-desktop-linux-x64-<ts>/
#   ./build.sh --target=windows    → Windows onedir via Wine → release/...-windows-x64-<ts>/
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

TARGET="linux"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target=*) TARGET="${1#*=}" ;;
    --target)
      TARGET="${2:?}"
      shift
      ;;
    -h|--help)
      sed -n '2,7p' "$0"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
  shift
done

case "$TARGET" in
  linux|native) TARGET=linux ;;
  windows) ;;
  *)
    echo "unsupported --target=$TARGET (use linux|windows)" >&2
    exit 2
    ;;
esac

exec python3 "$ROOT/scripts/build_desktop.py" --target "$TARGET"
