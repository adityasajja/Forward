from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from forward.api.legacy import dashboard, frontend


def test_asset_compression_and_cache_headers(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "main-AbCd1234.js").write_text("const payload = '" + "data " * 2000 + "';")
    (assets / "plain.js").write_text("ok")
    (tmp_path / "index.html").write_text("<html>app</html>")
    monkeypatch.setattr(frontend, "_WEB_DIST", tmp_path)
    app = FastAPI()
    frontend.mount_frontend(app)
    with TestClient(app) as client:
        response = client.get("/assets/main-AbCd1234.js", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200
        assert response.headers["content-encoding"] == "gzip"
        assert "immutable" in response.headers["cache-control"]
        assert response.text.startswith("const payload")
        assert int(response.headers["content-length"]) < len(response.content) / 10
        assert client.get("/").headers["cache-control"] == "no-store"
        assert "immutable" not in client.get("/assets/plain.js").headers.get("cache-control", "")
        assert client.get("/assets/missing-AbCd1234.js").status_code == 404


def test_duplicate_portfolio_sections_fetch_once(monkeypatch):
    client = Mock()
    client.limits.return_value = [{"tradingLimit": 100}]
    monkeypatch.setattr(dashboard, "_authed_client", lambda: client)
    result = dashboard.portfolio("limits,limits,limits")
    client.limits.assert_called_once()
    assert list(result["sections"]) == ["limits"]


@pytest.mark.parametrize("sections", [", ,", "unknown"])
def test_invalid_portfolio_sections_do_not_contact_broker(monkeypatch, sections):
    client = Mock()
    monkeypatch.setattr(dashboard, "_authed_client", client)
    with pytest.raises(HTTPException) as exc:
        dashboard.portfolio(sections)
    assert exc.value.status_code == 400
    client.assert_not_called()
