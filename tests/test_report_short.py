"""短线操作辅助报告测试（异常波动/做T/建仓时机/周报中期）。用法: python tests/test_report_short.py"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from invest.db import connect, init_db
from invest.data.storage import upsert_df
from invest.report import (
    _abnormal_moves,
    _entry_timing_hints,
    _t_trade_hints,
    daily_report,
    intraday_report,
    weekly_report,
)


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_report_short.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _seed(conn):
    """造数据：候选池 2 标的，一只放量异动，一只平缓；情绪/温度/评级。"""
    # 标的 A：放量 + 振幅大（异常波动候选）
    rows = []
    d = dt.date(2026, 7, 1)
    for i in range(30):
        rows.append({"symbol": "600519", "date": (d + dt.timedelta(days=i)).isoformat(),
                     "open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0,
                     "volume": 1_000_000, "amount": 1e8, "src": "akshare"})
    # 最后一天放量 + 振幅大
    rows[-1].update({"volume": 3_000_000, "high": 108.0, "low": 96.0, "close": 103.0})
    # 标的 B：平缓
    for i in range(30):
        rows.append({"symbol": "000001", "date": (d + dt.timedelta(days=i)).isoformat(),
                     "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
                     "volume": 5_000_000, "amount": 5e7, "src": "akshare"})
    upsert_df(conn, "daily_bars", pd.DataFrame(rows))
    conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES('600519','core','白酒','2026-08-15')")
    conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES('000001','track','银行','2026-08-15')")
    # 情绪（启动）
    upsert_df(conn, "market_emotion", pd.DataFrame([
        {"date": "2026-08-14", "limit_up_count": 60, "max_lianban": 4, "zhaban_rate": 0.30},
    ]))
    # 温度
    upsert_df(conn, "quant_temperature", pd.DataFrame([
        {"run_date": "2026-08-14", "score": 55.0, "profit_effect": 0.5},
    ]))
    # 评级
    conn.execute("INSERT INTO ratings(date, kind, value) VALUES('2026-08-14','macro','中性')")
    conn.execute("INSERT INTO ratings(date, kind, value) VALUES('2026-08-14','market','中性')")
    # 估值+强度（低估值启动 → 建仓候选）
    upsert_df(conn, "quant_valuation", pd.DataFrame([
        {"run_date": "2026-08-14", "obj": "白酒", "pe_pct": 0.2, "crowding": 0.3, "crowding_state": "健康"},
    ]))
    upsert_df(conn, "quant_strength", pd.DataFrame([
        {"run_date": "2026-08-14", "obj_type": "industry", "obj": "白酒", "period": "mid",
         "rs": 0.1, "trend_stage": "启动", "calc_version": "v1"},
    ]))
    # 行业/指数（供 _freshness / 周报）
    upsert_df(conn, "industry_bars", pd.DataFrame([
        {"date": "2026-08-14", "industry": "白酒", "close": 100.0, "src": "akshare"},
    ]))
    upsert_df(conn, "index_bars", pd.DataFrame([
        {"index_code": "000300", "date": "2026-08-14", "close": 4000.0, "src": "akshare"},
    ]))
    # 宏观
    upsert_df(conn, "macro_series", pd.DataFrame([
        {"indicator": "M1-M2剪刀差", "date": "2026-08-14", "value": -3.0, "src": "akshare"},
    ]))
    conn.commit()


def test_abnormal_moves():
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    abnormal = _abnormal_moves(conn)
    # 600519 放量3倍+振幅大 → 应命中
    syms = {a["symbol"] for a in abnormal}
    assert "600519" in syms
    hit = next(a for a in abnormal if a["symbol"] == "600519")
    assert "量比" in hit["signal"] or "振幅" in hit["signal"]
    conn.close()
    print("test_abnormal_moves OK")


def test_t_trade_hints():
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    # 600519 实时价贴近日内低点（96）→ 低吸做 T 候选
    hints = _t_trade_hints(conn, {"600519": 96.5, "000001": 10.0}, {"600519": 0.01, "000001": 0.0})
    assert any("600519" in h and "低吸" in h for h in hints)
    # 高位高抛：现价贴近日内高点且涨幅大
    hints2 = _t_trade_hints(conn, {"600519": 107.0}, {"600519": 0.06})
    assert any("600519" in h and "高抛" in h for h in hints2)
    conn.close()
    print("test_t_trade_hints OK")


def test_entry_timing_hints():
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    hints = _entry_timing_hints(conn)
    joined = "\n".join(hints)
    # 情绪周期启动 → 积极提示；白酒低估值+启动 → 建仓候选
    assert "启动" in joined or "积极" in joined
    assert "600519" in joined and "建仓" in joined
    conn.close()
    print("test_entry_timing_hints OK")


def test_daily_report_short_orientation():
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    conn.close()
    msg = daily_report(p)
    # 短线辅助区都在
    assert "异常波动" in msg
    assert "建仓时机" in msg
    assert "情绪周期" in msg
    # 中线内容精简保留
    assert "中线强度前3" in msg
    print("test_daily_report_short_orientation OK")


def test_intraday_report_short():
    from unittest import mock
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    conn.close()
    with mock.patch("invest.report._live_quotes",
                    return_value=({"600519": 96.5}, {"600519": 0.01})):
        msg_brief = intraday_report(p)               # 默认简洁版
        msg_full = intraday_report(p, brief=False)   # 完整版
    assert "600519" in msg_brief
    assert "今日操作" in msg_brief          # 简洁版含操作建议
    assert "做 T 提示" not in msg_brief      # 简洁版不含做T细节
    assert "做 T 提示" in msg_full
    assert "建仓时机" in msg_full
    print("test_intraday_report_short OK")


def test_weekly_report_mid():
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    conn.close()
    msg = weekly_report(p)
    assert "【A股投资系统 · 周报】" in msg
    assert "中线强度前8" in msg
    assert "低估值+趋势候选" in msg
    assert "宏观流动性" in msg
    assert "白酒" in msg  # 中线强度/估值有数据
    print("test_weekly_report_mid OK")


if __name__ == "__main__":
    test_abnormal_moves()
    test_t_trade_hints()
    test_entry_timing_hints()
    test_daily_report_short_orientation()
    test_intraday_report_short()
    test_weekly_report_mid()
    print("\nALL SHORT-REPORT TESTS PASSED")
