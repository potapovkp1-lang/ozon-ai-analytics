from app.services.analytics import snapshot


def test_snapshot_is_safe_before_first_sync():
    result = snapshot()
    assert result["status"] == "waiting_for_sync"
    assert len(result["kpis"]) == 4
