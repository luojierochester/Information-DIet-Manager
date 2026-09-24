"""HTTP contract tests with synthetic SQLite data; no model downloads/inference."""
import pytest
import pandas as pd
from fastapi.testclient import TestClient

from src.hyh import app as api
from src.hyh import db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "dashboard.sqlite3")
    with TestClient(api.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000),
                    headers={"Authorization": "Bearer " + "a" * 43}) as client:
        yield client


def seed(client, count):
    items = [{"url": f"https://example.invalid/{i}", "title": f"Synthetic {i}",
              "text": "Synthetic audit text", "ts": 1790208000000 + i,
              "source": "import"} for i in range(count)]
    for item in items:
        response = client.post("/collect", json=item)
        assert response.status_code == 200, response.text
        assert response.json()["inserted"] == 1


def get_analysis(client, **params):
    return client.get("/dashboard/visualization", params={"from_ts": 1790208000000,
                      "to_ts": 1790294400000, **params})


@pytest.mark.parametrize("count,status", [(0, "empty"), (1, "insufficient_data"), (4, "insufficient_data")])
def test_small_samples_return_explicit_state_without_loading_models(client, monkeypatch, count, status):
    seed(client, count)
    def unexpected(_rows):
        pytest.fail("Small samples must not initialize optional model dependencies")
    monkeypatch.setattr(api, "_execute_lsj_pipeline", unexpected)
    response = get_analysis(client)
    assert response.status_code == 200
    result = response.json()
    assert result["analysis_status"] == status
    assert result["window"]["input_count"] == count
    assert result["window"]["available_count"] == count
    assert result["minimum_records"] == 5
    assert result["global"]["time_series"] == []
    assert result["category_counts"] == {}


def test_dependency_failure_has_no_fabricated_measurements(client, monkeypatch):
    seed(client, 5)
    monkeypatch.setattr(api, "_execute_lsj_pipeline", lambda rows: {
        "ok": False, "warning": "synthetic missing dependency", "input_count": len(rows), "repeat_ratio": 0,
    })
    result = get_analysis(client).json()
    assert result["analysis_status"] == "unavailable"
    assert result["pipeline_warning"]
    assert result["global"]["time_series"] == []
    assert result["category_counts"] == {}


class SyntheticEvaluator:
    def __init__(self, fail=False):
        self.fail = fail

    def _preprocess_data(self, df):
        if self.fail:
            raise ValueError("synthetic invalid analysis data")
        return df

    def get_visualization_data(self, df):
        return {"time_series": [], "category_distribution": {"tools": 0.6, "shopping": 0.4}}


def pipeline(fail=False):
    def execute(rows):
        frame = pd.DataFrame({"title": ["sample"] * len(rows),
                              "timestamp": pd.to_datetime([r["ts"] for r in rows], unit="ms"),
                              "sentiment": ["positive"] * len(rows), "polarity": [1.0] * len(rows),
                              "similarity": [0.0] * len(rows), "category": ["tools"] * 3 + ["shopping"] * (len(rows)-3)})
        return {"ok": True, "input_count": len(rows), "evaluator": SyntheticEvaluator(fail), "df3": frame}
    return execute


def test_valid_metrics_preserve_zero_and_actual_date_and_categories(client, monkeypatch):
    seed(client, 5)
    monkeypatch.setattr(api, "_execute_lsj_pipeline", pipeline())
    result = get_analysis(client).json()
    assert result["analysis_status"] == "ready"
    assert result["category_counts"] == {"tools": 3, "shopping": 2}
    row = result["global"]["time_series"][0]
    assert row["date"] == "2026-09-24"
    assert (row["positive_ratio"], row["neutral_ratio"], row["negative_ratio"]) == (1.0, 0.0, 0.0)
    assert result["window"]["processed_count"] == 5
    assert result["date_timezone"] == "UTC"


def test_postprocessing_failure_is_explicit_and_empty(client, monkeypatch):
    seed(client, 5)
    monkeypatch.setattr(api, "_execute_lsj_pipeline", pipeline(fail=True))
    result = get_analysis(client).json()
    assert result["analysis_status"] == "failed"
    assert result["global"]["time_series"] == []
    assert result["categories"] == {}


def test_truncation_is_reported_with_actual_window_coverage(client, monkeypatch):
    seed(client, 6)
    monkeypatch.setattr(api, "_execute_lsj_pipeline", pipeline())
    result = get_analysis(client, limit_rows=5).json()
    assert result["window"]["truncated"] is True
    assert result["window"]["available_count"] == 6
    assert result["window"]["input_count"] == 5


def test_saved_records_total_stays_live_and_uses_real_pagination(client):
    seed(client, 51)
    first = client.get("/items?page=1&page_size=50").json()
    second = client.get("/items?page=2&page_size=50").json()
    assert first["total"] == second["total"] == 51
    assert len(first["items"]) == 50 and len(second["items"]) == 1
    assert {x["id"] for x in first["items"]}.isdisjoint(x["id"] for x in second["items"])
    assert "sentiment" not in first["items"][0]


def test_ready_cache_preserves_contract_and_force_recomputes(client, monkeypatch):
    seed(client, 5)
    monkeypatch.setattr(api, "_execute_lsj_pipeline", pipeline())
    first = get_analysis(client).json()
    assert first["analysis_status"] == "ready" and first["cached"] is False

    def fail(_rows):
        return {"ok": False, "warning": "synthetic pipeline outage", "input_count": 5}

    monkeypatch.setattr(api, "_execute_lsj_pipeline", fail)
    cached = get_analysis(client).json()
    assert cached["cached"] is True and cached["analysis_status"] == "ready"
    assert cached["window"]["available_count"] == 5
    assert cached["category_counts"] == first["category_counts"]

    fresh = get_analysis(client, force=True).json()
    assert fresh["cached"] is False and fresh["analysis_status"] == "unavailable"
    assert fresh["global"]["time_series"] == []
    with db.get_conn() as conn:
        assert conn.execute("SELECT status FROM analysis_jobs ORDER BY id DESC LIMIT 1").fetchone()[0] == api.JOB_FAILED


def test_failed_analysis_does_not_prevent_recovery_via_cache(client, monkeypatch):
    seed(client, 5)
    monkeypatch.setattr(api, "_execute_lsj_pipeline", lambda rows: {"ok": False, "warning": "synthetic outage"})
    assert get_analysis(client).json()["analysis_status"] == "unavailable"
    monkeypatch.setattr(api, "_execute_lsj_pipeline", pipeline())
    recovered = get_analysis(client).json()
    assert recovered["analysis_status"] == "ready"
    assert recovered["cached"] is False
