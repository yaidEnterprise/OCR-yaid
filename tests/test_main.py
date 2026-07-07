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
