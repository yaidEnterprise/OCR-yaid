"""
OCRyaid — API simples de OCR com FastAPI + Tesseract.
"""

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image
import pytesseract
import secrets
import shutil
import io
import os

_WINDOWS_DEFAULT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_CMD = (
    os.environ.get("TESSERACT_CMD")
    or shutil.which("tesseract")
    or (_WINDOWS_DEFAULT if os.path.exists(_WINDOWS_DEFAULT) else "tesseract")
)
pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

app = FastAPI(
    title="OCRyaid",
    description="API de OCR local usando Tesseract",
    version="1.0.0",
)


def verify_api_key(x_api_key: str | None = Header(default=None, alias="x-api-key")) -> None:
    expected = os.environ.get("API_KEY")
    if expected and not (x_api_key and secrets.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="API key inválida ou ausente.")


def run_tesseract(img: Image.Image, lang: str = "por") -> str:
    return pytesseract.image_to_string(img, lang=lang).strip()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/ocr", dependencies=[Depends(verify_api_key)])
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
    except pytesseract.TesseractNotFoundError:
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


from mangum import Mangum

handler = Mangum(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
