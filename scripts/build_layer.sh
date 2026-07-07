#!/usr/bin/env bash
set -euo pipefail

LAYER_RELEASE_URL="https://github.com/bweigel/aws-lambda-tesseract-layer/releases/download/v5.4.0/tesseract-al2023-x86.zip"
TESSDATA_POR_URL="https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/por.traineddata"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="$ROOT_DIR/build/layer"
ZIP_PATH="$ROOT_DIR/build/layer.zip"
BASE_ZIP="$ROOT_DIR/build/tesseract-base.zip"

rm -rf "$WORK_DIR" "$ZIP_PATH" "$BASE_ZIP"
mkdir -p "$WORK_DIR" "$ROOT_DIR/build"

curl -fsL -o "$BASE_ZIP" "$LAYER_RELEASE_URL"
unzip -q "$BASE_ZIP" -d "$WORK_DIR"

curl -fsL -o "$WORK_DIR/tesseract/share/tessdata/por.traineddata" "$TESSDATA_POR_URL"

cd "$WORK_DIR"
zip -r -q "$ZIP_PATH" .

echo "layer.zip criado em $ZIP_PATH"
