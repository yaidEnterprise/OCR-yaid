#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT_DIR/build/function"
ZIP_PATH="$ROOT_DIR/build/function.zip"

rm -rf "$BUILD_DIR" "$ZIP_PATH"
mkdir -p "$BUILD_DIR"

pip install -r "$ROOT_DIR/requirements.txt" -t "$BUILD_DIR"
cp "$ROOT_DIR/main.py" "$BUILD_DIR/"

cd "$BUILD_DIR"
zip -r -q "$ZIP_PATH" .

echo "function.zip criado em $ZIP_PATH"
