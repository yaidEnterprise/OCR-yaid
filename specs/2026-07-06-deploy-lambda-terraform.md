# Spec — Deploy do OCRyaid em AWS Lambda via Mangum + Terraform + GitHub Actions

**Data:** 2026-07-06
**Autor:** Victor Gasperi
**Status:** Aprovado para implementação

---

## 1. Objetivo

Publicar a API FastAPI de OCR (`OCRyaid`) como uma função **AWS Lambda**, exposta por um **API Gateway HTTP (v2)** genérico (proxy total, sem controle de rota — quem roteia é o próprio FastAPI). O binário do Tesseract é fornecido por um **Lambda Layer** derivado do projeto open source [bweigel/aws-lambda-tesseract-layer](https://github.com/bweigel/aws-lambda-tesseract-layer). Toda a infraestrutura é descrita em **Terraform** (em `iac/`) com **backend S3**, e o deploy é automatizado por uma **pipeline do GitHub Actions** disparada por push na branch `prod`.

---

## 2. Estado atual (as-is)

- **Código:** aplicação FastAPI single-file em [`main.py`](../main.py):
  - Endpoint `POST /ocr` recebe imagem (`UploadFile`) + `lang` (default `por`) e retorna `{filename, lang, text}`.
  - OCR via `pytesseract.image_to_string`.
  - `TESSERACT_CMD` resolvido por `shutil.which("tesseract")` com fallback para caminho do Windows.
  - Servido localmente por `uvicorn` (`python main.py`).
- **Dependências** ([`requirements.txt`](../requirements.txt)): `fastapi`, `uvicorn[standard]`, `python-multipart`, `pytesseract`, `Pillow`. **Não há `mangum`.**
- **Sem IaC**, sem pipeline, sem empacotamento para Lambda.
- **Git:** remote `https://github.com/yaidEnterprise/OCR-yaid.git`; existe apenas a branch `main`. **A branch `prod` ainda não existe.**
- **Runtime local:** Python 3.12.7.

---

## 3. Estado final (to-be)

- `main.py` exporta um handler Lambda (`handler = Mangum(app)`) e mantém a execução local funcional.
- A API valida uma **API key** (header `x-api-key`) no endpoint `/ocr`; expõe `/health` aberto.
- Um bundle `function.zip` (código + deps para `linux/x86_64`) e um `layer.zip` (Tesseract + `por.traineddata`) são construídos na pipeline.
- Terraform em `iac/` provisiona: S3 objects dos artefatos, Lambda Layer, Lambda Function, API Gateway HTTP v2 (proxy `$default`), IAM e permissões.
- Pipeline no GitHub Actions com **jobs sequenciais** (`build` → `plan` → `apply`), disparada em `push` para `prod`, usando o **environment `prod`** para secrets/variables.
- Backend do Terraform em **um único bucket S3 do projeto**, com divisão lógica por prefixo: `state/` (tfstate) e `artifacts/` (zips).

---

## 4. Decisões (fechadas)

| Tema | Decisão |
|------|---------|
| Tipo de API Gateway | **HTTP API (v2)**, integração proxy `$default` (sem rotas explícitas). |
| Origem do layer Tesseract | Baixar o **prebuilt `ready-to-use` (Amazon Linux 2023, x86_64)** do release do bweigel e **injetar `por.traineddata`** antes de publicar. |
| Idiomas OCR | **Somente `por`** (português). |
| Backend do state | Bucket S3 **já existente** (bootstrap manual, fora da pipeline). |
| Bucket | **Um único bucket do projeto**, prefixos `state/` e `artifacts/`. Lock nativo do S3 (`use_lockfile`), sem DynamoDB. |
| Auth AWS na pipeline | **Access keys** (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`) vindas do environment `prod`. |
| Região | **us-east-1**. |
| Exposição da API | **Protegida por API key** validada no FastAPI (HTTP API v2 não tem API keys nativas). |
| Geração da API key | **Opção A:** valor gerado manualmente (`openssl rand -hex 32`) e guardado no secret `API_KEY` do environment `prod`. Rotação = atualizar o secret + redeploy. |
| Arquitetura da Lambda | **x86_64** (imposto pelo layer, que é x86_64-only). |
| Runtime | **Python 3.12**. |

---

## 5. Alterações no código da aplicação

### 5.1 `main.py`

- **Handler Lambda:** adicionar, ao final do módulo, `handler = Mangum(app)`. O handler configurado na Lambda será `main.handler`.
- **`TESSERACT_CMD` por env var:** priorizar a variável de ambiente `TESSERACT_CMD` (setada pela Lambda para `/opt/tesseract/bin/tesseract`), com fallback para o auto-detect atual (`shutil.which` / caminho Windows), preservando a execução local.
  ```python
  TESSERACT_CMD = (
      os.environ.get("TESSERACT_CMD")
      or shutil.which("tesseract")
      or (_WINDOWS_DEFAULT if os.path.exists(_WINDOWS_DEFAULT) else "tesseract")
  )
  ```
- **`tessdata` na Lambda:** o Tesseract localiza os dados via `TESSDATA_PREFIX` (env var setada pela Lambda). Nenhuma mudança de código além de garantir que o pytesseract respeite o ambiente (já respeita, pois lê `TESSDATA_PREFIX` do processo).
- **API key:** adicionar uma dependency que compara o header `x-api-key` com a env var `API_KEY`:
  - Se `API_KEY` estiver definida e o header ausente/divergente → `HTTPException(status_code=401)`.
  - Aplicada ao endpoint `/ocr`.
  - Comparação com `secrets.compare_digest` (evita timing attack).
- **`/health`:** endpoint `GET /health` **sem** API key, retornando `{"status": "ok"}`, para health check / smoke test.
- Manter o bloco `if __name__ == "__main__"` (uvicorn) para execução local.

### 5.2 Dependências

- **`requirements.txt`** (runtime da Lambda) passa a conter:
  - `fastapi`, `mangum`, `python-multipart`, `pytesseract`, `Pillow`.
  - **Remover `uvicorn` do bundle da Lambda** (não é usado pelo Mangum).
- **`requirements-dev.txt`** (novo) para execução local: `-r requirements.txt` + `uvicorn[standard]`.

---

## 6. Empacotamento (build no runner)

Roda em `ubuntu-latest` (x86_64 Linux), compatível com o alvo `linux/x86_64` da Lambda.

### 6.1 `function.zip`
1. `pip install -r requirements.txt -t build/`
   - Pillow tem wheel `manylinux` x86_64 para cp312; pytesseract é puro Python. Instalação direta no runner é compatível com a Lambda.
2. Copiar `main.py` para `build/`.
3. Zipar o conteúdo de `build/` na raiz do zip → `function.zip`.

> **Nota:** se algum dia surgir dependência sem wheel manylinux, migrar para build via container `public.ecr.aws/lambda/python:3.12` ou `--platform manylinux2014_x86_64 --only-binary=:all:`. Não necessário hoje.

### 6.2 `layer.zip`
1. Baixar o artefato **`ready-to-use` (amazonlinux-2023, x86_64)** do release do bweigel.
2. Descompactar; **injetar `por.traineddata`** (baixado de `tessdata_fast`) no diretório de tessdata do layer.
3. Re-zipar mantendo a estrutura interna esperada (ver §7.1) → `layer.zip`.

### 6.3 Estrutura interna do layer — **verificado**

Asset real inspecionado: [`tesseract-al2023-x86.zip`](https://github.com/bweigel/aws-lambda-tesseract-layer/releases/download/v5.4.0/tesseract-al2023-x86.zip) (release `v5.4.0`, sha256 `f6312eed51baea7514a32a79036908b782dde7a558d4f5817a0a206221797868`, 11.026.450 bytes). `TESSERACT-README.md` embutido confirma: `TESSERACT_VERSION=5.5.2`, `LEPTONICA_VERSION=1.87.0`, `TESSERACT_DATA_FILES=tessdata_fast/4.1.0`, `TESSERACT_DATA_LANGUAGES=osd,eng,deu`.

Estrutura real do zip (raiz):

| Conteúdo | Caminho no zip | Caminho em runtime (`/opt`) |
|----------|----------------|------------------------------|
| Binário | `bin/tesseract` | `/opt/bin/tesseract` |
| Libs `.so` (`libtesseract.so.5`, `libleptonica.so.6`, `libgomp.so.1`, `libpng16.so.16`, `libjpeg.so.62`, `libwebp.so.7`, `libwebpmux.so.3`, `libjbig.so.2.1`, `libtiff.so.5`) | `lib/` | `/opt/lib/` |
| tessdata (`osd.traineddata`, `deu.traineddata`, `eng.traineddata`) | `tesseract/share/tessdata/` | `/opt/tesseract/share/tessdata/` |

**Injeção do `por.traineddata`:** baixar de `tessdata_fast` **tag `4.1.0`** (mesma versão do resto dos dados do layer, evita mismatch) e adicionar em `tesseract/share/tessdata/por.traineddata` dentro do zip, antes de re-zipar. Não é necessário remover `deu` (não usado, mas inofensivo manter).

---

## 7. Infraestrutura Terraform (`iac/`)

### 7.1 Backend e providers
- Backend **S3**:
  - `bucket = <bucket do projeto>` (via `-backend-config`, valor de `TF_STATE_BUCKET`).
  - `key = "state/terraform.tfstate"`.
  - `region = "us-east-1"`.
  - `use_lockfile = true` (lock nativo do S3; requer **Terraform ≥ 1.10**; sem DynamoDB).
- Provider `aws`, região `us-east-1`.

### 7.2 Artefatos no S3
- `aws_s3_object` para `layer.zip` → `s3://<bucket>/artifacts/layer.zip`.
- `aws_s3_object` para `function.zip` → `s3://<bucket>/artifacts/function.zip`.
- Usar `etag`/`source_hash` para forçar novo deploy quando o zip muda.

> Motivo do S3: o `layer.zip` (Tesseract + tessdata) tende a passar de 50 MB, limite do upload direto. Publicar layer e função a partir do S3.

### 7.3 Lambda
- `aws_lambda_layer_version` a partir de `s3://<bucket>/artifacts/layer.zip`, `compatible_architectures = ["x86_64"]`, `compatible_runtimes = ["python3.12"]`.
- `aws_lambda_function`:
  - `s3_bucket`/`s3_key` = `artifacts/function.zip`; `source_code_hash` para trigger de update.
  - `runtime = "python3.12"`, `architectures = ["x86_64"]`, `handler = "main.handler"`.
  - `layers = [layer_version.arn]`.
  - `memory_size = 2048`, `timeout = 30` (OCR pode ser lento; `/tmp` default 512 MB é suficiente).
  - `environment.variables`:
    - `TESSERACT_CMD = /opt/bin/tesseract`
    - `TESSDATA_PREFIX = /opt/tesseract/share/tessdata`
    - `LD_LIBRARY_PATH = /opt/lib`
    - `API_KEY = var.api_key` (sensível; vem do secret `API_KEY`)
- `aws_iam_role` de execução + attach de `AWSLambdaBasicExecutionRole` (logs no CloudWatch).

### 7.4 API Gateway (HTTP v2)
- `aws_apigatewayv2_api` com `protocol_type = "HTTP"`.
- `aws_apigatewayv2_integration` `AWS_PROXY` apontando para a Lambda (`payload_format_version = "2.0"`, compatível com Mangum).
- `aws_apigatewayv2_route` com `route_key = "$default"` (catch-all — **sem** controle de rota; FastAPI roteia).
- `aws_apigatewayv2_stage` `$default` com `auto_deploy = true`.
- `aws_lambda_permission` autorizando o API Gateway a invocar a Lambda.

### 7.5 Variáveis do Terraform
- `region` (default `us-east-1`), `project_name`/prefixo, `stage`, `api_key` (sensível), referências dos objetos S3.
- `api_key` recebido via `TF_VAR_api_key` (do secret do environment).
- `stage` recebido via `TF_VAR_stage` (derivado do nome da branch do deploy — ver §8.1). Usado para nomear/taguear recursos (ex.: `${project_name}-${stage}`) e como tag `Stage` nos recursos.
- `project_name` recebido via `TF_VAR_project_name` (derivado do nome do repositório GitHub, minúsculo e sem caracteres especiais — ver §8.1). Usado no mesmo padrão de nomeação/tag.

### 7.6 Outputs
- `api_endpoint` (invoke URL do stage `$default`).
- `lambda_function_name`.
- `tesseract_layer_arn`.

---

## 8. Pipeline (GitHub Actions)

- **Arquivo:** `.github/workflows/deploy-prod.yml`.
- **Trigger:** `on: push: branches: [prod]` (merge/push em `prod` = deploy em produção).
- **Environment:** `prod` (fornece secrets e variables; permite proteção/aprovação se configurada no GitHub).
- **Jobs sequenciais** (cada um depende do anterior via `needs`):

### 8.1 `STAGE` e `PROJECT_NAME` (derivados em runtime, não cadastrados no GitHub)

- **`STAGE`**: derivada do nome da branch do deploy — `github.ref_name` (no caso, `prod`).
  ```yaml
  env:
    STAGE: ${{ github.ref_name }}
  ```
  Repassada ao Terraform como `TF_VAR_stage`, alimentando `var.stage` (§7.5).

- **`PROJECT_NAME`**: derivada do **nome do repositório** (`github.event.repository.name`, no caso `OCR-yaid`), normalizado para **minúsculo e sem caracteres especiais** (apenas `[a-z0-9]`, hífens e demais símbolos removidos) — ex.: `OCR-yaid` → `ocryaid`. Calculada em um step de shell (não há expressão nativa do GitHub Actions para strip de caracteres):
  ```yaml
  - name: Derive PROJECT_NAME
    run: |
      RAW_NAME="${{ github.event.repository.name }}"
      PROJECT_NAME=$(echo "$RAW_NAME" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')
      echo "PROJECT_NAME=$PROJECT_NAME" >> "$GITHUB_ENV"
  ```
  Repassada ao Terraform como `TF_VAR_project_name`, alimentando `var.project_name` (§7.5).

- Nenhuma das duas é secret/variable do GitHub — ambas são calculadas dentro dos jobs `plan`/`apply`. Consequência: se o repositório for renomeado ou o deploy passar a rodar em outra branch, ambas acompanham automaticamente, sem hardcode.

### Job 1 — `build`
1. `actions/checkout`.
2. `actions/setup-python` (3.12).
3. Build do `function.zip` (§6.1).
4. Build do `layer.zip` (§6.2): baixar prebuilt + injetar `por.traineddata`.
5. `actions/upload-artifact` para `function.zip` e `layer.zip`.

### Job 2 — `plan` (`needs: build`)
1. `actions/download-artifact` (zips).
2. Configurar credenciais AWS (access keys do environment `prod`).
3. `terraform init` (backend S3, `-backend-config` do bucket).
4. `terraform validate` + `terraform fmt -check`.
5. `terraform plan -out=tfplan` (passando `TF_VAR_api_key`, `TF_VAR_stage` (= `STAGE`), caminhos dos zips).
6. `upload-artifact` do `tfplan` (+ o `.terraform` lockfile se necessário).

### Job 3 — `apply` (`needs: plan`)
1. `download-artifact` (zips + `tfplan`).
2. Configurar credenciais AWS.
3. `terraform init`.
4. `terraform apply -auto-approve tfplan`.
5. (Opcional) smoke test: `curl` em `GET /health` da `api_endpoint`.

> **Observação:** por o backend ser remoto (S3), o state é compartilhado entre os jobs `plan` e `apply`. O `tfplan` é passado como artifact para garantir que o `apply` execute exatamente o plano revisado.

---

## 9. Secrets e Variables (environment `prod`)

### Secrets
| Nome | Uso |
|------|-----|
| `AWS_ACCESS_KEY_ID` | Credencial AWS da pipeline. |
| `AWS_SECRET_ACCESS_KEY` | Credencial AWS da pipeline. |
| `API_KEY` | Valor gerado manualmente (`openssl rand -hex 32`); injetado na Lambda e validado pelo FastAPI. |

### Variables
| Nome | Valor |
|------|-------|
| `AWS_REGION` | `us-east-1` |
| `TF_STATE_BUCKET` | Nome do bucket S3 do projeto |

> `STAGE` e `PROJECT_NAME` **não** são cadastrados aqui: ambos são derivados em runtime dentro dos jobs (§8.1) — `STAGE` do nome da branch (`github.ref_name`), `PROJECT_NAME` do nome do repositório (`github.event.repository.name`, normalizado para minúsculo sem caracteres especiais) — e repassados ao Terraform via `TF_VAR_stage` e `TF_VAR_project_name`.

---

## 10. Pré-requisitos manuais (uma vez, fora da pipeline)

1. **Criar o bucket S3 do projeto** em `us-east-1` (com versionamento recomendado). Este bucket hospeda `state/` e `artifacts/`.
2. **Criar o environment `prod`** no repositório do GitHub e cadastrar os secrets/variables da §9.
3. **Gerar a API key** (`openssl rand -hex 32`) e salvar no secret `API_KEY`.
4. **Criar a branch `prod`** no repositório.
5. **Credenciais AWS** com permissões para: S3 (objetos), Lambda (function + layer), API Gateway v2, IAM (role/policy da Lambda), CloudWatch Logs.

---

## 11. Fora de escopo

- Domínio customizado / TLS próprio no API Gateway (usa o endpoint default do stage).
- WAF, rate limiting, usage plans.
- OIDC para autenticação da pipeline (decidido usar access keys).
- Suporte a arquitetura arm64 (layer é x86_64-only).
- Idiomas além de `por`.
- Ambientes múltiplos (staging/dev) — apenas `prod`.

---

## 12. Riscos e pontos a validar na implementação

1. **Tamanho do bundle:** function + layer descompactados devem caber no limite de 250 MB da Lambda (layer atual ~25 MB descompactado + `por.traineddata` ~1-2 MB; folga confortável).
2. **Cold start:** carregar libs do Tesseract + tessdata pode aumentar o cold start; `memory_size` de 2048 MB ajuda.
3. **`payload_format_version 2.0`** deve casar com a versão do Mangum (usar Mangum recente que suporte o formato 2.0 do HTTP API).
4. **`/tmp` (512 MB):** o pytesseract grava arquivos temporários; suficiente para imagens típicas, mas requests com imagens muito grandes podem exigir aumento de ephemeral storage.
5. **Versão do release do layer:** a pipeline deve fixar a versão (`v5.4.0` / asset `tesseract-al2023-x86.zip`) em vez de sempre puxar "latest", para builds reprodutíveis. Atualizar deliberadamente quando necessário.
