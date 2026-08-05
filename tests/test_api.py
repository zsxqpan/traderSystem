"""FastAPI 接口层测试（TestClient）。用法: python tests/test_api.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from invest.api.app import create_app
from invest.db import init_db


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_api_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_read_endpoints():
    p = _tmp_db()
    client = TestClient(create_app(p))
    assert client.get("/health").status_code == 200
    for path in ("/api/strength", "/api/temperature", "/api/rotation", "/api/capital",
                 "/api/linkage", "/api/crowding", "/api/macro", "/api/viewpoints",
                 "/api/accuracy", "/api/pool", "/api/ratings", "/api/plans",
                 "/api/records", "/api/backtests", "/api/jobs", "/api/coverage"):
        r = client.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text[:100])
        assert isinstance(r.json(), list)
    print("test_read_endpoints OK")


def test_write_flow():
    p = _tmp_db()
    client = TestClient(create_app(p))

    # 候选池
    r = client.post("/api/pool", json={"symbol": "600519", "level": "core", "industry": "白酒"})
    assert r.status_code == 200 and r.json()["symbol"] == "600519"
    assert client.get("/api/pool").json()[0]["symbol"] == "600519"

    # 评级
    assert client.post("/api/rating", json={"kind": "macro", "value": "宽松"}).status_code == 200
    assert client.post("/api/rating", json={"kind": "market", "value": "进攻"}).status_code == 200
    assert client.post("/api/rating", json={"kind": "macro", "value": "错误值"}).status_code == 400

    # 计划：无止损 → 400；合法 → 200
    bad = client.post("/api/plan", json={"symbol": "600519"})
    assert bad.status_code == 400
    r = client.post("/api/plan", json={"symbol": "600519", "stop_loss": 1500.0, "target_position": 0.05})
    assert r.status_code == 200
    plan_id = r.json()["plan_id"]

    # 成交
    tr = client.post("/api/trade", json={"plan_id": plan_id, "action": "buy", "price": 1600.0, "qty": 100})
    assert tr.status_code == 200
    assert tr.json()["actual_vs_plan"] == "unknown"  # 无 buy_range

    # 非法成交 action → 400
    assert client.post("/api/trade", json={"plan_id": plan_id, "action": "hold", "price": 1, "qty": 1}).status_code == 400
    print("test_write_flow OK")


if __name__ == "__main__":
    test_read_endpoints()
    test_write_flow()
    print("\nALL API TESTS PASSED")