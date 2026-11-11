"""API access controls that do not need a pipeline run: identity, path traversal."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.main import DIST, app  # noqa: E402

client = TestClient(app)


def test_unknown_user_is_rejected():
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/overview", headers={"X-Verity-User": "mallory"}).status_code == 401


def test_report_download_is_whitelisted():
    r = client.get("/api/reports/file/reports/..%2F..%2Fconfig%2Fconfig.yaml")
    assert r.status_code == 404 or "Verity central configuration" not in r.text


@pytest.mark.skipif(not DIST.exists(), reason="web app not built")
def test_spa_fallback_cannot_escape_dist():
    for path in ("/..%2F..%2Fconfig%2Fconfig.yaml", "/..%2Fconfig%2Fconfig.yaml", "/%2E%2E/%2E%2E/config/config.yaml"):
        r = client.get(path)
        assert "Verity central configuration" not in r.text, path


def test_users_endpoint_lists_demo_roles():
    roles = {u["id"]: u["roles"] for u in client.get("/api/users").json()}
    assert roles["neha"] == ["analyst", "reviewer"] and roles["kabir"] == ["steward"]

