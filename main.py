"""
OCRyaid — API simples de OCR com FastAPI + Tesseract.
"""

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image
import subprocess
import tempfile
import io
import os

TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

app = FastAPI(
    title="OCRyaid",
    description="API de OCR local usando Tesseract",
    version="1.0.0",
)


def run_tesseract(img: Image.Image, lang: str = "por") -> str:
    """Executa o Tesseract via subprocess diretamente, sem pytesseract."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
        img.save(tmp, format="PNG")

    try:
        result = subprocess.run(
            [TESSERACT_CMD, tmp_path, "stdout", "-l", lang],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if result.returncode != 0:
            error_msg = (result.stderr or "").strip()
            raise RuntimeError(error_msg or f"Tesseract retornou código {result.returncode}")
        return (result.stdout or "").strip()
    finally:
        os.unlink(tmp_path)



@app.post("/ocr")
async def extract_text(
    image: UploadFile = File(..., description="Imagem para extrair texto"),
    lang: str = "por",
):
    """
    Recebe uma imagem e retorna o texto extraído via Tesseract OCR.

    - **image**: arquivo de imagem (png, jpg, bmp, tiff, etc.)
    - **lang**: idioma do Tesseract (padrão: ``por`` — português).
      Use ``eng`` para inglês ou combine com ``por+eng``.
    """
    # Valida o content-type básico
    if image.content_type and not image.content_type.startswith("image/"):
        raise HTTPException(
            status_code=400,
            detail=f"Tipo de arquivo não suportado: {image.content_type}. Envie uma imagem.",
        )

    try:
        contents = await image.read()
        img = Image.open(io.BytesIO(contents))
    except Exception:
        raise HTTPException(status_code=400, detail="Não foi possível abrir a imagem enviada.")

    try:
        text = run_tesseract(img, lang=lang)
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail=f"Tesseract não encontrado em: {TESSERACT_CMD}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao processar OCR: {e}")

    return JSONResponse(
        content={
            "filename": image.filename,
            "lang": lang,
            "text": text,
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
