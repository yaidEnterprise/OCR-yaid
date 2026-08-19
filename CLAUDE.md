# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OCRyaid — a single-endpoint OCR API (FastAPI + Tesseract). The entire app is `main.py`; it runs both as a local
uvicorn server and, unmodified, as an AWS Lambda behind API Gateway via Mangum. There is no framework beyond
FastAPI and no database — state is entirely in the request/response cycle.

## Commands

```bash
# Local dev (requires Tesseract installed natively — see README for OS-specific install)
pip install -r requirements-dev.txt
python main.py                    # serves http://localhost:8000, docs at /docs

# Tests
pytest                            # run all tests
pytest tests/test_main.py::test_ocr_accepts_correct_api_key   # single test

# Lambda build artifacts (produces build/function.zip and build/layer.zip, consumed by Terraform)
scripts/build_function.sh
scripts/build_layer.sh

# Terraform (run from iac/)
cd iac && terraform init -backend-config="bucket=<TF_STATE_BUCKET>"
terraform fmt -check -recursive
terraform validate
terraform plan
```

## Architecture

- **`main.py`** is the whole application: two routes (`GET /health`, `POST /ocr`), an API-key dependency
  (`verify_api_key`), and a Tesseract wrapper (`run_tesseract`). At the bottom, `handler = Mangum(app)` is what
  Lambda invokes; the `if __name__ == "__main__"` block is only for local `uvicorn` runs. Both paths share
  the exact same `app` — don't add local-only or Lambda-only code paths.
- **Tesseract binary resolution** (`TESSERACT_CMD` near the top of `main.py`) tries, in order: the `TESSERACT_CMD`
  env var, `shutil.which("tesseract")`, a hardcoded Windows default, then falls back to the bare `"tesseract"`
  string. In Lambda this is pinned to `/opt/bin/tesseract` via the Terraform Lambda `environment` block, since
  Tesseract ships as a separate Lambda Layer (see below), not as a pip package.
- **Auth**: `x-api-key` header, checked with `secrets.compare_digest` against the `API_KEY` env var, only on
  `/ocr` (`/health` is always open — it's the post-deploy smoke test target). If `API_KEY` is unset, `/ocr` is
  open too — this is the local-dev default, not a bug.
- **Two-artifact Lambda deploy**: `scripts/build_function.sh` pip-installs `requirements.txt` (app deps only)
  into `build/function.zip`. `scripts/build_layer.sh` downloads a prebuilt Tesseract-on-AL2023 release
  (`bweigel/aws-lambda-tesseract-layer`) plus the Portuguese trained-data file into `build/layer.zip` — Tesseract
  itself is never compiled or pip-installed here. Both scripts are idempotent (they `rm -rf` their build dirs
  first) and must be run before any `terraform plan`/`apply` that changes function or layer code, since Terraform
  reads `filebase64sha256(var.function_zip_path)` / `filemd5(...)` to detect changes.
- **`iac/`** (Terraform, AWS provider ~>6.0, S3 backend with `use_lockfile = true`, no DynamoDB table): API
  Gateway HTTP API with a single `$default` proxy route → Lambda → CloudWatch (basic execution role only, no
  extra IAM). `local.name_prefix = "${project_name}-${stage}"` namespaces every resource. `project_name` is
  derived from the GitHub repo name in CI, not hardcoded.
- **CI/CD** (`.github/workflows/deploy-prod.yml`): triggers only on push to `prod` (this repo's working branch is
  `vicgasp`, PRs target `dev` — `prod` is a separate deploy branch). Three sequential jobs — `build` (produces
  the two zips, uploaded as a short-lived `actions/upload-artifact`), `plan`, `apply` — all under the `prod`
  GitHub Environment, so AWS creds and `API_KEY` come from environment-scoped secrets/vars
  (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `API_KEY`, `AWS_REGION`, `TF_STATE_BUCKET`). `apply` ends with a
  smoke test that curls `/health` on the freshly-deployed API. `terraform plan` output (which can contain the
  `api_key` value in cleartext) is intentionally *not* uploaded as a build artifact — don't reintroduce that.
- **Tests** (`tests/test_main.py`) use FastAPI's `TestClient` and `monkeypatch` env vars per-test rather than
  a shared fixture, since auth behavior depends on `API_KEY` presence/absence. `run_tesseract` is monkeypatched
  out in tests that don't care about actual OCR output, since Tesseract may not be installed in the test
  environment.
