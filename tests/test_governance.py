"""权重治理与规则版本管理单元测试。用法: python tests/test_governance.py"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db
from invest.governance import (
    WEIGHT_RULES,
    current_versions,
    freeze_weights,
    params_for,
    quarterly_oos_eval,
    rollback,
)


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_gov_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_freeze_weights():
    p = _tmp_db()
    conn = connect(p)
    written = freeze_weights(
        conn, "v3.1", change_reason="季度校准",
        validation_sample="2024-01~2025-12", rollback_condition="季度 OOS mean<0",
    )
    assert len(written) == len(WEIGHT_RULES)
    versions = current_versions(conn)
    assert len(versions) == len(WEIGHT_RULES)
    assert all(v["status"] == "frozen" for v in versions)
    # 参数快照非空
    for v in versions:
        assert json.loads(v["params_json"]) != {}
        assert v["effective_date"] is not None
    conn.close()
    print("test_freeze_weights OK")


def test_rollback():
    p = _tmp_db()
    conn = connect(p)
    freeze_weights(conn, "v3.1", change_reason="初始")
    freeze_weights(conn, "v3.2", change_reason="再校准")
    # v3.2 冻结后，v3.1 应已历史化
    conn.close()
    conn = connect(p)
    versions = current_versions(conn, status=None)
    v31 = [v for v in versions if v["version"] == "v3.1"]
    v32 = [v for v in versions if v["version"] == "v3.2"]
    assert all(v["status"] == "rolled_back" for v in v31)
    assert all(v["status"] == "frozen" for v in v32)
    # 回滚到 v3.1
    rule = WEIGHT_RULES[0]
    r = rollback(conn, rule, "v3.1", reason="新版本失效")
    assert r["status"] == "active"
    params = params_for(conn, rule)
    assert params != {}  # 回滚后可取到参数
    conn.close()
    print("test_rollback OK")


def test_params_for():
    p = _tmp_db()
    conn = connect(p)
    freeze_weights(conn, "v3.1")
    params = params_for(conn, "rating_position_map", status="frozen")
    assert "attack" in params and "defense" in params
    # 未冻结的规则名返回空
    assert params_for(conn, "nonexistent") == {}
    conn.close()
    print("test_params_for OK")


def test_oos_eval_empty():
    p = _tmp_db()
    conn = connect(p)
    r = quarterly_oos_eval(conn)
    assert r["ok"] is False  # 空库 -> 数据不足
    conn.close()
    print("test_oos_eval_empty OK")




def test_oos_eval_correct_path():
    import numpy as np
    import pandas as pd
    from invest.data.storage import upsert_df
    p = _tmp_db()
    conn = connect(p)
    dates = pd.date_range("2026-01-01", periods=120, freq="B")
    rng = np.random.default_rng(7)
    for i, ind_name in enumerate(["行业A", "行业B", "行业C"]):
        trend = 1 + np.linspace(0, 0.1, 120) + rng.normal(0, 0.01, 120)
        upsert_df(conn, "industry_bars", pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"), "industry": ind_name,
            "close": 100 * trend, "amount": 1e8, "src": "akshare",
        }))
    upsert_df(conn, "index_bars", pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"), "index_code": "000300",
        "close": 100 * (1 + np.linspace(0, 0.05, 120)), "src": "akshare",
    }))
    for i in range(20, 120, 10):
        rd = dates[i].strftime("%Y-%m-%d")
        for j, ind_name in enumerate(["行业A", "行业B", "行业C"]):
            conn.execute(
                "INSERT OR REPLACE INTO quant_strength(run_date, obj_type, obj, period, rs) VALUES(?,?,?,?,?)",
                (rd, "industry", ind_name, "short", 1.0 - j * 0.1),
            )
    conn.commit()
    r = quarterly_oos_eval(conn, top_n=2, horizon=10)
    assert r["ok"] is True
    assert r["n_periods"] >= 1
    assert r["mean_excess"] is not None
    assert "overlap_note" in r
    conn.close()
    print("test_oos_eval_correct_path OK")


if __name__ == "__main__":
    test_freeze_weights()
    test_rollback()
    test_params_for()
    test_oos_eval_empty()
    test_oos_eval_correct_path()
    print("\nALL GOVERNANCE TESTS PASSED")
