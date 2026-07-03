# OCR-YaId

API simples de OCR local usando **FastAPI** + **Tesseract**.

## Pré-requisitos

- Python 3.11+
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) instalado (Windows: `C:\Program Files\Tesseract-OCR\`)

## Instalação

```bash
pip install -r requirements.txt
```

## Uso

```bash
python main.py
```

A API sobe em `http://localhost:8000`. Docs interativos em `http://localhost:8000/docs`.

### Endpoint `POST /ocr`

Envia uma imagem e recebe o texto extraído.

```bash
curl -X POST http://localhost:8000/ocr \
  -F "image=@foto.png" \
  -F "lang=por"
```

**Resposta:**

```json
{
  "filename": "foto.png",
  "lang": "por",
  "text": "Texto extraído da imagem..."
}
```

**Parâmetros:**

| Parâmetro | Tipo   | Padrão | Descrição                          |
|-----------|--------|--------|------------------------------------|
| `image`   | file   | —      | Imagem para OCR (png, jpg, etc.)   |
| `lang`    | string | `por`  | Idioma do Tesseract (`por`, `eng`) |
