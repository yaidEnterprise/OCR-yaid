# Deploy OCRyaid em Lambda (Mangum + Terraform + GitHub Actions) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publicar a API OCRyaid (FastAPI) como Lambda x86_64, atrás de um API Gateway HTTP v2 genérico, com Tesseract fornecido por Lambda Layer, tudo provisionado via Terraform (`iac/`, backend S3) e implantado por uma pipeline de 3 jobs do GitHub Actions disparada em push na branch `prod`.

**Architecture:** `main.py` ganha um handler Mangum e uma dependency de API key; dois scripts de shell empacotam `function.zip` e `layer.zip` (este injeta `por.traineddata` no layer prebuilt do bweigel); o Terraform em `iac/` sobe os zips para S3 e cria Layer, Function, API Gateway HTTP v2 e IAM; a pipeline builda os artefatos, roda `terraform plan` (revisão humana no log, sem persistir plan-file) e depois `terraform apply -auto-approve` (recalcula e aplica o plano em um único passo), usando o environment `prod` do GitHub para credenciais.

**Tech Stack:** FastAPI, Mangum, pytesseract, Terraform ≥ 1.10, provider `hashicorp/aws` ~> 6.0, GitHub Actions, Python 3.12.

**Spec de referência:** [`specs/2026-07-06-deploy-lambda-terraform.md`](2026-07-06-deploy-lambda-terraform.md)

## Global Constraints

- Runtime Lambda: **Python 3.12**, arquitetura **x86_64** (imposta pelo layer, que é x86_64-only).
- Região AWS: **us-east-1**.
- API Gateway: **HTTP API (v2)**, rota única `$default` (proxy total — sem controle de rota no Terraform).
- API key: header **`x-api-key`**, comparada com `secrets.compare_digest` contra a env var `API_KEY` da Lambda.
- Idioma de OCR no layer: **somente `por`** (injetado a partir de `tessdata_fast` tag **`4.1.0`**, mesma versão dos demais dados do layer).
- Layer Tesseract: asset fixo **`tesseract-al2023-x86.zip`** do release **`v5.4.0`** de `bweigel/aws-lambda-tesseract-layer` (sha256 `f6312eed51baea7514a32a79036908b782dde7a558d4f5817a0a206221797868`). Estrutura real verificada: binário em `bin/tesseract`, libs em `lib/*.so`, tessdata em `tesseract/share/tessdata/`.
- Env vars da Lambda: `TESSERACT_CMD=/opt/bin/tesseract`, `TESSDATA_PREFIX=/opt/tesseract/share/tessdata`, `LD_LIBRARY_PATH=/opt/lib`.
- Backend do Terraform: **um único bucket S3 do projeto**, `key = "state/terraform.tfstate"`, `use_lockfile = true` (sem DynamoDB). Artefatos de build no mesmo bucket sob `artifacts/`.
- `STAGE` **não** é secret/variable do GitHub — é derivado em runtime de `github.ref_name` dentro dos jobs e repassado como `TF_VAR_stage`.
- `PROJECT_NAME` **também não** é secret/variable do GitHub — é derivado em runtime do nome do repositório (`github.event.repository.name`, ex.: `OCR-yaid`), normalizado para **minúsculo e sem caracteres especiais** (`tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9'`, resultando em `ocryaid`), e repassado como `TF_VAR_project_name`.
- Auth AWS na pipeline: **access keys** (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`) do environment `prod`.
- Trigger da pipeline: `push` na branch `prod`; environment do job = `prod`.
- Fora de escopo: domínio customizado, WAF/rate limiting, OIDC, arquitetura arm64, outros idiomas, múltiplos ambientes.

---

## Pré-requisitos manuais (fazer antes de rodar a pipeline de verdade)

Estes passos **não fazem parte das tasks abaixo** — são ações fora do repositório, do dono do projeto (ver spec §10):

1. Criar o bucket S3 do projeto em `us-east-1` (versionamento recomendado).
2. Criar o environment `prod` no GitHub e cadastrar:
   - Secrets: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `API_KEY` (gerado com `openssl rand -hex 32`).
   - Variables: `AWS_REGION=us-east-1`, `TF_STATE_BUCKET=<nome do bucket>`.
   - **Não** cadastrar `PROJECT_NAME` — é derivado em runtime do nome do repositório (ver Global Constraints e Task 9).
3. Criar a branch `prod` no repositório.
4. Garantir que as credenciais AWS tenham permissão para: S3, Lambda, API Gateway v2, IAM, CloudWatch Logs.

Sem isso, a pipeline (Task 9) falha nos jobs `plan`/`apply` — o que é esperado até esses pré-requisitos existirem.

---

### Task 1: Test tooling + endpoint `/health` + dependency de API key

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/test_main.py`
- Modify: `main.py`

**Interfaces:**
- Produces: `main.verify_api_key` (dependency FastAPI, lê `os.environ["API_KEY"]` a cada chamada); rota `GET /health` → `{"status": "ok"}`; rota `POST /ocr` passa a exigir `Depends(verify_api_key)`.
- Consumes: nenhuma (primeira task).

- [ ] **Step 1: Criar `requirements-dev.txt`**

```
-r requirements.txt
uvicorn[standard]==0.30.6
pytest==8.3.3
httpx==0.27.2
```

- [ ] **Step 2: Instalar dependências de dev**

Run: `pip install -r requirements-dev.txt`
Expected: instalação sem erros (pytest e httpx passam a estar disponíveis).

- [ ] **Step 3: Escrever os testes falhando em `tests/test_main.py`**

```python
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def sample_png_bytes():
    img = Image.new("RGB", (10, 10), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def test_health_returns_ok_even_with_api_key_configured(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "super-secret")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ocr_allows_request_when_api_key_not_configured(client, monkeypatch, sample_png_bytes):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(main, "run_tesseract", lambda img, lang="por": "texto")
    response = client.post(
        "/ocr",
        files={"image": ("test.png", sample_png_bytes, "image/png")},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "texto"


def test_ocr_rejects_missing_api_key(client, monkeypatch, sample_png_bytes):
    monkeypatch.setenv("API_KEY", "super-secret")
    response = client.post(
        "/ocr",
        files={"image": ("test.png", sample_png_bytes, "image/png")},
    )
    assert response.status_code == 401


def test_ocr_rejects_wrong_api_key(client, monkeypatch, sample_png_bytes):
    monkeypatch.setenv("API_KEY", "super-secret")
    response = client.post(
        "/ocr",
        files={"image": ("test.png", sample_png_bytes, "image/png")},
        headers={"x-api-key": "wrong"},
    )
    assert response.status_code == 401


def test_ocr_accepts_correct_api_key(client, monkeypatch, sample_png_bytes):
    monkeypatch.setenv("API_KEY", "super-secret")
    monkeypatch.setattr(main, "run_tesseract", lambda img, lang="por": "texto")
    response = client.post(
        "/ocr",
        files={"image": ("test.png", sample_png_bytes, "image/png")},
        headers={"x-api-key": "super-secret"},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "texto"
```

- [ ] **Step 4: Rodar os testes e confirmar que falham**

Run: `pytest tests/test_main.py -v`
Expected: `test_health_returns_ok_even_with_api_key_configured` falha com 404 (rota não existe); os testes de API key falham porque `/ocr` hoje aceita qualquer request sem checar header (retornam 200 quando o teste espera 401).

- [ ] **Step 5: Implementar em `main.py`**

Adicionar imports e a dependency logo após a definição de `TESSERACT_CMD`/`app`:

```python
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image
import pytesseract
import secrets
import shutil
import io
import os
```

```python
def verify_api_key(x_api_key: str | None = Header(default=None, alias="x-api-key")) -> None:
    expected = os.environ.get("API_KEY")
    if expected and not (x_api_key and secrets.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="API key inválida ou ausente.")
```

Adicionar rota de health (sem dependency) antes ou depois de `/ocr`:

```python
@app.get("/health")
async def health():
    return {"status": "ok"}
```

Alterar a assinatura de `extract_text` para exigir a dependency:

```python
@app.post("/ocr", dependencies=[Depends(verify_api_key)])
async def extract_text(
    image: UploadFile = File(..., description="Imagem para extrair texto"),
    lang: str = "por",
):
```

- [ ] **Step 6: Rodar os testes e confirmar que passam**

Run: `pytest tests/test_main.py -v`
Expected: 5 testes `PASSED`.

- [ ] **Step 7: Commit**

```bash
git add requirements-dev.txt tests/test_main.py main.py
git commit -m "feat: adiciona endpoint /health e autenticação por API key"
```

---

### Task 2: Handler Mangum + `TESSERACT_CMD` por env var + `requirements.txt` de runtime

**Files:**
- Modify: `main.py`
- Modify: `requirements.txt`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `main.app` (Task 1).
- Produces: `main.handler` (instância `Mangum(main.app)`, usada como handler da Lambda: `main.handler`); `main.TESSERACT_CMD` prioriza `os.environ["TESSERACT_CMD"]`.

- [ ] **Step 1: Escrever os testes falhando**

Adicionar ao final de `tests/test_main.py`:

```python
import importlib
import os


def test_handler_is_a_mangum_instance():
    from mangum import Mangum

    assert isinstance(main.handler, Mangum)


def test_tesseract_cmd_prefers_env_var(monkeypatch):
    monkeypatch.setenv("TESSERACT_CMD", "/opt/bin/tesseract")
    reloaded = importlib.reload(main)
    try:
        assert reloaded.TESSERACT_CMD == "/opt/bin/tesseract"
    finally:
        monkeypatch.delenv("TESSERACT_CMD", raising=False)
        importlib.reload(main)
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `pytest tests/test_main.py -v -k "handler or tesseract_cmd_prefers"`
Expected: `test_handler_is_a_mangum_instance` falha com `AttributeError: module 'main' has no attribute 'handler'`; `ModuleNotFoundError: No module named 'mangum'` até o pacote ser instalado (ver Step 3).

- [ ] **Step 3: Adicionar `mangum` e remover `uvicorn` de `requirements.txt`**

`requirements.txt` (runtime da Lambda) passa a ser:

```
fastapi==0.115.0
mangum==0.21.0
python-multipart==0.0.9
pytesseract==0.3.13
Pillow==10.4.0
```

Run: `pip install -r requirements.txt`

- [ ] **Step 4: Implementar em `main.py`**

Substituir o bloco de `TESSERACT_CMD`:

```python
_WINDOWS_DEFAULT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_CMD = (
    os.environ.get("TESSERACT_CMD")
    or shutil.which("tesseract")
    or (_WINDOWS_DEFAULT if os.path.exists(_WINDOWS_DEFAULT) else "tesseract")
)
pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
```

Adicionar o import e o handler no final do arquivo, antes do bloco `if __name__ == "__main__":`:

```python
from mangum import Mangum

# ...

handler = Mangum(app)
```

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `pytest tests/test_main.py -v`
Expected: todos os 7 testes `PASSED`.

- [ ] **Step 6: Confirmar que a execução local continua funcionando**

Run: `python main.py &` seguido de `curl -s http://localhost:8000/health` (depois `kill %1`)
Expected: `{"status":"ok"}`.

- [ ] **Step 7: Commit**

```bash
git add main.py requirements.txt
git commit -m "feat: adiciona handler Mangum e TESSERACT_CMD configurável via env var"
```

---

### Task 3: Script de build do `function.zip`

**Files:**
- Create: `scripts/build_function.sh`

**Interfaces:**
- Consumes: `main.py`, `requirements.txt` (Task 2).
- Produces: `build/function.zip` (código + deps, layout plano na raiz do zip — consumido pelo Terraform na Task 7).

- [ ] **Step 1: Criar o script**

```bash
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
```

- [ ] **Step 2: Tornar executável e rodar**

Run:
```bash
chmod +x scripts/build_function.sh
./scripts/build_function.sh
```
Expected: saída `function.zip criado em .../build/function.zip`, sem erros de `pip install`.

- [ ] **Step 3: Verificar o conteúdo do zip**

Run: `unzip -l build/function.zip | grep -E "main.py|fastapi|mangum|pytesseract|PIL"`
Expected: lista mostra `main.py` na raiz do zip e os diretórios dos pacotes (`fastapi/`, `mangum/`, `pytesseract/`, `PIL/`) também na raiz — confirma layout plano exigido pela Lambda.

- [ ] **Step 4: Commit**

```bash
git add scripts/build_function.sh
git commit -m "feat: adiciona script de build do function.zip para a Lambda"
```

---

### Task 4: Script de build do `layer.zip` (Tesseract + `por.traineddata`)

**Files:**
- Create: `scripts/build_layer.sh`

**Interfaces:**
- Consumes: nenhuma dependência de código do projeto — baixa artefatos externos fixos (versões pinadas no Global Constraints).
- Produces: `build/layer.zip` (estrutura `bin/tesseract`, `lib/*.so`, `tesseract/share/tessdata/{osd,deu,eng,por}.traineddata` — consumido pelo Terraform na Task 7).

- [ ] **Step 1: Criar o script**

```bash
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

curl -sL -o "$BASE_ZIP" "$LAYER_RELEASE_URL"
unzip -q "$BASE_ZIP" -d "$WORK_DIR"

curl -sL -o "$WORK_DIR/tesseract/share/tessdata/por.traineddata" "$TESSDATA_POR_URL"

cd "$WORK_DIR"
zip -r -q "$ZIP_PATH" .

echo "layer.zip criado em $ZIP_PATH"
```

- [ ] **Step 2: Tornar executável e rodar**

Run:
```bash
chmod +x scripts/build_layer.sh
./scripts/build_layer.sh
```
Expected: saída `layer.zip criado em .../build/layer.zip`, sem erros de download.

- [ ] **Step 3: Verificar o conteúdo do zip**

Run: `unzip -l build/layer.zip | grep -E "bin/tesseract$|lib/libtesseract|tessdata/por.traineddata|tessdata/eng.traineddata"`
Expected: as 4 linhas aparecem — confirma binário, lib principal, e os dois idiomas (`por` injetado + `eng` original) presentes.

- [ ] **Step 4: Commit**

```bash
git add scripts/build_layer.sh
git commit -m "feat: adiciona script de build do layer.zip com Tesseract + por.traineddata"
```

---

### Task 5: Terraform — fundação (`versions.tf`, `variables.tf`)

**Files:**
- Create: `iac/versions.tf`
- Create: `iac/variables.tf`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `var.region`, `var.project_name`, `var.stage`, `var.api_key`, `var.artifacts_bucket`, `var.function_zip_path`, `var.layer_zip_path`, `local.name_prefix`, `local.common_tags` — usados por todas as tasks Terraform seguintes.
- Consumes: nenhuma (primeira task de IaC).

- [ ] **Step 1: Adicionar entradas do Terraform ao `.gitignore`**

Adicionar ao final de `.gitignore`:

```
iac/.terraform/
iac/tfplan
iac/*.tfstate
iac/*.tfstate.backup
```

> Nota: `iac/.terraform.lock.hcl` **não** entra no `.gitignore` — o lock file do provider é commitado para garantir que os jobs `plan` e `apply` da pipeline resolvam sempre a mesma versão do provider `aws`.

- [ ] **Step 2: Criar `iac/versions.tf`**

```hcl
terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    key          = "state/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
}
```

- [ ] **Step 3: Criar `iac/variables.tf`**

```hcl
variable "region" {
  description = "Região AWS onde os recursos são provisionados"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefixo usado para nomear os recursos"
  type        = string
}

variable "stage" {
  description = "Estágio de deploy, igual ao nome da branch (ex.: prod)"
  type        = string
}

variable "api_key" {
  description = "Chave de API validada pelo FastAPI no header x-api-key"
  type        = string
  sensitive   = true
}

variable "artifacts_bucket" {
  description = "Bucket S3 (mesmo do backend) que hospeda os artefatos de build sob artifacts/"
  type        = string
}

variable "function_zip_path" {
  description = "Caminho local do function.zip a ser enviado ao S3"
  type        = string
  default     = "../build/function.zip"
}

variable "layer_zip_path" {
  description = "Caminho local do layer.zip a ser enviado ao S3"
  type        = string
  default     = "../build/layer.zip"
}

locals {
  name_prefix = "${var.project_name}-${var.stage}"

  common_tags = {
    Project   = var.project_name
    Stage     = var.stage
    ManagedBy = "terraform"
  }
}
```

- [ ] **Step 4: Validar sintaxe (sem backend remoto)**

Run:
```bash
cd iac
terraform init -backend=false
terraform validate
terraform fmt -check
```
Expected: `terraform init` baixa o provider `aws` com sucesso; `terraform validate` retorna `Success! The configuration is valid.`; `terraform fmt -check` não imprime nada (arquivos já formatados).

- [ ] **Step 5: Commit**

```bash
cd ..
git add iac/versions.tf iac/variables.tf .gitignore
git commit -m "feat: adiciona fundação Terraform (backend S3, provider, variáveis)"
```

---

### Task 6: Terraform — artefatos S3 + IAM role da Lambda

**Files:**
- Create: `iac/s3.tf`
- Create: `iac/iam.tf`

**Interfaces:**
- Consumes: `var.artifacts_bucket`, `var.function_zip_path`, `var.layer_zip_path`, `local.name_prefix`, `local.common_tags` (Task 5).
- Produces: `aws_s3_object.function_zip`, `aws_s3_object.layer_zip` (com `.key` usado na Task 7); `aws_iam_role.lambda_exec` (com `.arn` usado na Task 7).

- [ ] **Step 1: Criar `iac/s3.tf`**

```hcl
resource "aws_s3_object" "function_zip" {
  bucket = var.artifacts_bucket
  key    = "artifacts/${local.name_prefix}/function.zip"
  source = var.function_zip_path
  etag   = filemd5(var.function_zip_path)
  tags   = local.common_tags
}

resource "aws_s3_object" "layer_zip" {
  bucket = var.artifacts_bucket
  key    = "artifacts/${local.name_prefix}/layer.zip"
  source = var.layer_zip_path
  etag   = filemd5(var.layer_zip_path)
  tags   = local.common_tags
}
```

- [ ] **Step 2: Criar `iac/iam.tf`**

```hcl
data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_exec" {
  name               = "${local.name_prefix}-lambda-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
```

- [ ] **Step 3: Garantir que os zips existem e validar**

Run:
```bash
./scripts/build_function.sh
./scripts/build_layer.sh
cd iac
terraform init -backend=false
terraform validate
terraform fmt -check
```
Expected: `terraform validate` → `Success! The configuration is valid.` (as chamadas `filemd5()` resolvem porque os zips existem em `build/`, criados pelos scripts das Tasks 3 e 4).

- [ ] **Step 4: Commit**

```bash
cd ..
git add iac/s3.tf iac/iam.tf
git commit -m "feat: adiciona recursos Terraform de artefatos S3 e IAM role da Lambda"
```

---

### Task 7: Terraform — Lambda Layer + Function

**Files:**
- Create: `iac/lambda.tf`

**Interfaces:**
- Consumes: `aws_s3_object.function_zip`, `aws_s3_object.layer_zip` (Task 6), `aws_iam_role.lambda_exec` (Task 6), `var.artifacts_bucket`, `var.function_zip_path`, `var.layer_zip_path`, `var.api_key`, `local.name_prefix`, `local.common_tags` (Task 5).
- Produces: `aws_lambda_layer_version.tesseract` (`.arn` usado na Task 8 se necessário), `aws_lambda_function.ocr` (`.arn`, `.invoke_arn`, `.function_name` usados na Task 8).

- [ ] **Step 1: Criar `iac/lambda.tf`**

```hcl
resource "aws_lambda_layer_version" "tesseract" {
  layer_name                = "${local.name_prefix}-tesseract"
  s3_bucket                 = var.artifacts_bucket
  s3_key                    = aws_s3_object.layer_zip.key
  compatible_architectures  = ["x86_64"]
  compatible_runtimes       = ["python3.12"]
  source_code_hash          = filebase64sha256(var.layer_zip_path)
}

resource "aws_lambda_function" "ocr" {
  function_name = "${local.name_prefix}-ocr"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "main.handler"
  runtime       = "python3.12"
  architectures = ["x86_64"]

  s3_bucket        = var.artifacts_bucket
  s3_key           = aws_s3_object.function_zip.key
  source_code_hash = filebase64sha256(var.function_zip_path)

  layers      = [aws_lambda_layer_version.tesseract.arn]
  memory_size = 2048
  timeout     = 30

  environment {
    variables = {
      TESSERACT_CMD   = "/opt/bin/tesseract"
      TESSDATA_PREFIX = "/opt/tesseract/share/tessdata"
      LD_LIBRARY_PATH = "/opt/lib"
      API_KEY         = var.api_key
    }
  }

  tags = local.common_tags

  depends_on = [aws_iam_role_policy_attachment.lambda_basic_execution]
}
```

- [ ] **Step 2: Validar**

Run:
```bash
cd iac
terraform init -backend=false
terraform validate
terraform fmt
terraform fmt -check
```
Expected: `terraform validate` → `Success! The configuration is valid.`; `terraform fmt` pode reescrever o alinhamento dos `=` em `lambda.tf` (normal, HCL não exige alinhamento manual); após rodar `terraform fmt`, o `terraform fmt -check` seguinte não deve imprimir nada.

- [ ] **Step 3: Commit**

```bash
cd ..
git add iac/lambda.tf
git commit -m "feat: adiciona recursos Terraform da Lambda function e do layer do Tesseract"
```

---

### Task 8: Terraform — API Gateway HTTP v2 + outputs

**Files:**
- Create: `iac/apigateway.tf`
- Create: `iac/outputs.tf`

**Interfaces:**
- Consumes: `aws_lambda_function.ocr` (Task 7), `local.name_prefix`, `local.common_tags` (Task 5).
- Produces: outputs `api_endpoint`, `lambda_function_name`, `tesseract_layer_arn` (lidos pela pipeline no smoke test da Task 9).

- [ ] **Step 1: Criar `iac/apigateway.tf`**

```hcl
resource "aws_apigatewayv2_api" "ocr" {
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"
  tags          = local.common_tags
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.ocr.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ocr.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.ocr.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.ocr.id
  name        = "$default"
  auto_deploy = true
  tags        = local.common_tags
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ocr.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ocr.execution_arn}/*/*"
}
```

- [ ] **Step 2: Criar `iac/outputs.tf`**

```hcl
output "api_endpoint" {
  description = "URL pública da API"
  value       = aws_apigatewayv2_api.ocr.api_endpoint
}

output "lambda_function_name" {
  description = "Nome da função Lambda implantada"
  value       = aws_lambda_function.ocr.function_name
}

output "tesseract_layer_arn" {
  description = "ARN da versão do layer do Tesseract"
  value       = aws_lambda_layer_version.tesseract.arn
}
```

- [ ] **Step 3: Validar o módulo Terraform completo**

Run:
```bash
cd iac
terraform init -backend=false
terraform validate
terraform fmt -check -recursive
```
Expected: `Success! The configuration is valid.`; nenhuma saída do `fmt -check`.

- [ ] **Step 4: Commit**

```bash
cd ..
git add iac/apigateway.tf iac/outputs.tf
git commit -m "feat: adiciona API Gateway HTTP v2 e outputs Terraform"
```

---

### Task 9: Pipeline GitHub Actions (`build` → `plan` → `apply`)

**Files:**
- Create: `.github/workflows/deploy-prod.yml`

**Interfaces:**
- Consumes: `scripts/build_function.sh` (Task 3), `scripts/build_layer.sh` (Task 4), todo o módulo `iac/` (Tasks 5–8), secrets/variables do environment `prod` (ver Pré-requisitos manuais).
- Produces: deploy real em `us-east-1` ao rodar em push na branch `prod`.

- [ ] **Step 1: Criar `.github/workflows/deploy-prod.yml`**

```yaml
name: Deploy prod

on:
  push:
    branches: [prod]

env:
  STAGE: ${{ github.ref_name }}

jobs:
  build:
    runs-on: ubuntu-latest
    environment: prod
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Build function.zip
        run: |
          chmod +x scripts/build_function.sh
          scripts/build_function.sh

      - name: Build layer.zip
        run: |
          chmod +x scripts/build_layer.sh
          scripts/build_layer.sh

      - uses: actions/upload-artifact@v4
        with:
          name: build-artifacts
          path: |
            build/function.zip
            build/layer.zip
          retention-days: 1

  plan:
    runs-on: ubuntu-latest
    needs: build
    environment: prod
    defaults:
      run:
        working-directory: iac
    steps:
      - uses: actions/checkout@v4

      - uses: actions/download-artifact@v4
        with:
          name: build-artifacts
          path: build

      - name: Derive PROJECT_NAME from repo name
        run: |
          RAW_NAME="${{ github.event.repository.name }}"
          PROJECT_NAME=$(echo "$RAW_NAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')
          echo "PROJECT_NAME=$PROJECT_NAME" >> "$GITHUB_ENV"

      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "1.10.5"

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ${{ vars.AWS_REGION }}

      - name: Terraform init
        run: terraform init -backend-config="bucket=${{ vars.TF_STATE_BUCKET }}"

      - name: Terraform fmt check
        run: terraform fmt -check -recursive

      - name: Terraform validate
        run: terraform validate

      - name: Terraform plan
        env:
          TF_VAR_project_name: ${{ env.PROJECT_NAME }}
          TF_VAR_stage: ${{ env.STAGE }}
          TF_VAR_api_key: ${{ secrets.API_KEY }}
          TF_VAR_artifacts_bucket: ${{ vars.TF_STATE_BUCKET }}
          TF_VAR_region: ${{ vars.AWS_REGION }}
          TF_VAR_function_zip_path: ${{ github.workspace }}/build/function.zip
          TF_VAR_layer_zip_path: ${{ github.workspace }}/build/layer.zip
        run: terraform plan

  apply:
    runs-on: ubuntu-latest
    needs: plan
    environment: prod
    defaults:
      run:
        working-directory: iac
    steps:
      - uses: actions/checkout@v4

      - uses: actions/download-artifact@v4
        with:
          name: build-artifacts
          path: build

      - name: Derive PROJECT_NAME from repo name
        run: |
          RAW_NAME="${{ github.event.repository.name }}"
          PROJECT_NAME=$(echo "$RAW_NAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')
          echo "PROJECT_NAME=$PROJECT_NAME" >> "$GITHUB_ENV"

      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "1.10.5"

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ${{ vars.AWS_REGION }}

      - name: Terraform init
        run: terraform init -backend-config="bucket=${{ vars.TF_STATE_BUCKET }}"

      - name: Terraform apply
        env:
          TF_VAR_project_name: ${{ env.PROJECT_NAME }}
          TF_VAR_stage: ${{ env.STAGE }}
          TF_VAR_api_key: ${{ secrets.API_KEY }}
          TF_VAR_artifacts_bucket: ${{ vars.TF_STATE_BUCKET }}
          TF_VAR_region: ${{ vars.AWS_REGION }}
          TF_VAR_function_zip_path: ${{ github.workspace }}/build/function.zip
          TF_VAR_layer_zip_path: ${{ github.workspace }}/build/layer.zip
        run: terraform apply -auto-approve

      - name: Smoke test /health
        run: |
          API_URL=$(terraform output -raw api_endpoint)
          curl -sf "$API_URL/health" | tee /dev/stderr | grep -q '"status":"ok"'
```

- [ ] **Step 2: Validar sintaxe YAML localmente**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy-prod.yml'))" && echo "YAML válido"
```
Expected: `YAML válido`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy-prod.yml
git commit -m "feat: adiciona pipeline GitHub Actions de deploy em prod (build, plan, apply)"
```

> **Nota:** este workflow só é exercitado de ponta a ponta com um push real na branch `prod`, após os Pré-requisitos manuais estarem satisfeitos. Isso é validação pós-implementação, fora do controle deste plano.

---

### Task 10: Atualizar `README.md`

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nada de código — apenas documenta o que as Tasks 1–9 produziram.

- [ ] **Step 1: Adicionar seção de desenvolvimento local atualizada**

Substituir a seção `## Instalação` / `## Uso` por:

```markdown
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
```

- [ ] **Step 2: Adicionar seção de deploy**

Adicionar ao final do arquivo:

```markdown
## Deploy (AWS Lambda)

A API roda em produção como AWS Lambda (Python 3.12, x86_64) atrás de um API Gateway HTTP v2, com o Tesseract fornecido por um Lambda Layer. Infraestrutura em [`iac/`](iac/) (Terraform, backend S3). Pipeline de deploy em [`.github/workflows/deploy-prod.yml`](.github/workflows/deploy-prod.yml), disparada em push na branch `prod`.

Detalhes completos: [`specs/2026-07-06-deploy-lambda-terraform.md`](specs/2026-07-06-deploy-lambda-terraform.md).
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: atualiza README com instruções de dev local, auth e deploy"
```

---

### Task 11: Checagem de integração local final

**Files:** nenhum arquivo novo — apenas validação.

**Interfaces:** consome todos os artefatos das Tasks 1–10 juntos.

- [ ] **Step 1: Suite de testes completa**

Run: `pytest tests/ -v`
Expected: todos os testes `PASSED`.

- [ ] **Step 2: Build completo dos dois zips do zero**

Run:
```bash
rm -rf build
./scripts/build_function.sh
./scripts/build_layer.sh
ls -la build/*.zip
```
Expected: `function.zip` e `layer.zip` presentes em `build/`, sem erros.

- [ ] **Step 3: Validação Terraform do módulo inteiro**

Run:
```bash
cd iac
terraform init -backend=false
terraform validate
terraform fmt -check -recursive
cd ..
```
Expected: `Success! The configuration is valid.`; sem saída do `fmt -check`.

- [ ] **Step 4: Verificação manual do checklist de pré-requisitos**

Confirmar com o usuário que os itens da seção "Pré-requisitos manuais" (bucket S3, environment `prod`, secrets/variables, branch `prod`) estão feitos antes de considerar a feature pronta para uso real. Se algum item faltar, **não** fazer push na branch `prod` ainda.

- [ ] **Step 5: Commit final (se houver arquivos pendentes)**

```bash
git status
```
Expected: `working tree clean` (todas as tasks anteriores já commitaram suas mudanças). Se houver algo pendente, revisar antes de encerrar.
