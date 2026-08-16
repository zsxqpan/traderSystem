"""归因/错误分类/年度复盘单元测试。用法: python tests/test_review2.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db
from invest.review.attribution import (
    attribution_report,
    breakdown_by,
    build_attributed_records,
    top_losers,
)
from invest.review.error_classify import CATEGORIES, classify, error_report
from invest.review.yearly import (
    kelly_calibration,
    level_monotonicity,
    rule_change_archive,
    weight_discrimination,
    yearly_review,
)


def _tmp_db_with_trades():
    p = os.path.join(tempfile.gettempdir(), "invest_review2_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    conn = connect(p)
    from invest.discipline import pool, plans, records
    pool.add_to_pool(conn, "X1", level="core")
    pool.add_to_pool(conn, "X2", level="track")
    plan1 = plans.create_plan(conn, "X1", stop_loss=9.0, buy_range="10.0,10.5")
    plan2 = plans.create_plan(conn, "X2", stop_loss=9.0, buy_range="10.0,10.5")
    # X1(core)：10 笔，8 胜 2 负
    for i in range(10):
        rec = records.record_trade(conn, plan1["plan_id"], "buy", 10.0, 100)
        pnl = 1.0 if i < 8 else -1.0
        conn.execute("UPDATE trade_records SET pnl=? WHERE id=?", (pnl, rec["record_id"]))
    # X2(track)：5 笔，2 胜 3 负
    for i in range(5):
        rec = records.record_trade(conn, plan2["plan_id"], "buy", 10.0, 100)
        pnl = 1.0 if i < 2 else -1.0
        conn.execute("UPDATE trade_records SET pnl=? WHERE id=?", (pnl, rec["record_id"]))
    conn.commit()
    return conn


def test_attribution_report():
    conn = _tmp_db_with_trades()
    recs = build_attributed_records(conn)
    r = attribution_report(conn, recs)
    assert r["ok"] is True
    assert r["n"] == 15 and r["wins"] == 10
    assert "level" in r["dimensions"]
    # core 层胜率 0.8 > track 层 0.4
    by_level = {d["value"]: d for d in r["dimensions"]["level"]}
    assert by_level["core"]["win_rate"] == 0.8
    assert by_level["track"]["win_rate"] == 0.4
    # 亏损集中度：X1 净盈利(+6)、X2 净亏损(-1) -> 仅 X2 为亏损贡献
    losers = top_losers(recs)
    assert len(losers) == 1
    assert losers[0]["symbol"] == "X2"
    assert losers[0]["total_pnl"] < 0
    conn.close()
    print("test_attribution_report OK")


def test_breakdown_by():
    recs = [
        {"cycle": "short", "pnl": 1.0},
        {"cycle": "short", "pnl": -0.5},
        {"cycle": "mid", "pnl": 2.0},
    ]
    b = breakdown_by(recs, "cycle")
    by = {d["value"]: d for d in b}
    assert by["short"]["n"] == 2 and by["short"]["total_pnl"] == 0.5
    assert by["mid"]["n"] == 1 and by["mid"]["total_pnl"] == 2.0
    print("test_breakdown_by OK")


def test_error_classify():
    # 规则内亏损
    assert classify({"pnl": -1.0, "deviation_note": "", "actual_vs_plan": "in_range"}) == "rule_loss"
    # 执行违规：偏离计划
    assert classify({"pnl": -1.0, "deviation_note": "成交价 11 高于计划区间 10-10.5", "actual_vs_plan": "above_range"}) == "execution"
    # 数据错误
    assert classify({"pnl": -1.0, "deviation_note": "行情数据异常", "actual_vs_plan": ""}) == "data_error"
    # 流动性
    assert classify({"pnl": -1.0, "deviation_note": "跌停无法退出", "actual_vs_plan": ""}) == "liquidity"
    # 盈利
    assert classify({"pnl": 2.0, "deviation_note": "", "actual_vs_plan": ""}) == "rule_loss"
    print("test_error_classify OK")


def test_error_report():
    conn = _tmp_db_with_trades()
    r = error_report(conn)
    assert r["ok"] is True
    assert r["n"] == 15
    assert "rule_loss" in r["categories"]
    conn.close()
    print("test_error_report OK")


def test_yearly_review():
    conn = _tmp_db_with_trades()
    # 等级单调性：core 0.8 > track 0.4 -> 单调
    mono = level_monotonicity(conn)
    assert mono["monotonic"] is True
    # 凯利校准：15 笔 10 胜
    kelly = kelly_calibration(conn)
    assert kelly["ok"] is True and kelly["n"] == 15
    # 权重区分度
    w = weight_discrimination(conn)
    assert w["spread"] > 0
    # 变更归档
    arch = rule_change_archive(conn)
    assert isinstance(arch, list)
    # 完整年度复盘
    review = yearly_review(conn)
    assert "level_monotonicity" in review and "kelly_calibration" in review
    assert "weight_discrimination" in review and "rule_changes" in review
    assert "error_classification" in review
    conn.close()
    print("test_yearly_review OK")


if __name__ == "__main__":
    test_attribution_report()
    test_breakdown_by()
    test_error_classify()
    test_error_report()
    test_yearly_review()
    print("\nALL REVIEW2 TESTS PASSED")
