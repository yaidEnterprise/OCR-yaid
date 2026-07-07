# OCR-YaId

API simples de OCR local usando **FastAPI** + **Tesseract**.

## Pré-requisitos

- Python 3.11+
- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) instalado (Windows: `C:\Program Files\Tesseract-OCR\`)

## Instalação (desenvolvimento local)

```bash
pip install -r requirements-dev.txt
```

## Uso local

```bash
python main.py
```

A API sobe em `http://localhost:8000`. Docs interativos em `http://localhost:8000/docs`.

### Autenticação

Se a variável de ambiente `API_KEY` estiver definida, o endpoint `POST /ocr` exige o header `x-api-key` com o mesmo valor. Sem `API_KEY` definida (padrão local), o endpoint fica aberto.

### Endpoint `GET /health`

Retorna `{"status": "ok"}`, sem autenticação. Usado como smoke test pós-deploy.

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

## Deploy (AWS Lambda)

A API roda em produção como AWS Lambda (Python 3.12, x86_64) atrás de um API Gateway HTTP v2, com o Tesseract fornecido por um Lambda Layer. Infraestrutura em [`iac/`](iac/) (Terraform, backend S3). Pipeline de deploy em [`.github/workflows/deploy-prod.yml`](.github/workflows/deploy-prod.yml), disparada em push na branch `prod`.

Detalhes completos: [`specs/2026-07-06-deploy-lambda-terraform.md`](specs/2026-07-06-deploy-lambda-terraform.md).
