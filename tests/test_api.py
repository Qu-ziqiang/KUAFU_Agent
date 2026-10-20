import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.main import create_app
from app.services.briefing import absorb_source_hits


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="",
        openai_base_url="https://example.invalid/v1",
        openai_model="test-model",
        tavily_api_key="",
        database_path=tmp_path / "kuafu.db",
        daily_hour=None,
        max_agent_steps=4,
        search_max_results=5,
        search_max_rounds=2,
        briefing_min_items=3,
        briefing_max_items=5,
        preferred_domains=["jiqizhixin.com"],
        first_ready_seconds=60,
        total_timeout_seconds=180,
        data_dir=tmp_path,
        schema_path=ROOT / "sql" / "schema.sql",
    )


def test_user_preference_and_missing_key(tmp_path: Path):
    with TestClient(create_app(make_settings(tmp_path))) as client:
        health = client.get("/api/health")
        data = health.json()["data"]
        assert data["search_ready"] is False
        assert data["first_ready_seconds"] == 60
        assert health.json()["message"] == "ok"

        created = client.post("/api/users", json={"name": "林夏", "email": "lin@example.com"})
        assert created.status_code == 200
        body = created.json()
        assert body["code"] == 0
        user_id = body["data"]["id"]
        uuid.UUID(user_id)

        saved = client.put(
            f"/api/users/{user_id}/preferences",
            json={
                "topics": ["编程助手", "编程助手"],
                "keywords": ["agent"],
                "language": "zh",
                "recall_count": 12,
                "custom_prompt": "多写综艺",
            },
        )
        prefs = saved.json()["data"]["preferences"]
        assert prefs["topics"] == ["编程助手"]
        assert prefs["recall_count"] == 12
        assert prefs["custom_prompt"] == "多写综艺"
        assert prefs["preferred_sites"] == []
        assert prefs["search_count"] == 0

        with client.stream("POST", f"/api/users/{user_id}/briefings/stream") as response:
            assert response.status_code == 200
            text = "".join(response.iter_text())
        assert "OPENAI_API_KEY" in text

        missing = client.get("/api/users/999")
        assert missing.status_code == 404
        assert missing.json()["code"] == 40401


def test_preferred_sites_follow_source_frequency():
    hits, sites = absorb_source_hits(
        {"cctv.com": 1},
        [
            {"url": "https://www.sohu.com/a/1"},
            {"url": "https://sohu.com/a/2"},
        ],
    )
    assert hits["sohu.com"] == 2
    assert hits["cctv.com"] == 1
    assert sites[0] == "sohu.com"
