"""报告生成（日报优化 + 盘中实时报告）单元测试。用法: python tests/test_report.py"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from invest.db import connect, init_db
from invest.data.storage import upsert_df


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_report_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _seed(conn):
    """造最小数据：温度 / 行业 / 指数 / 候选池 / 卡片 / 评级 / 观点。"""
    # 温度
    upsert_df(conn, "quant_temperature", pd.DataFrame([
        {"run_date": "2026-08-14", "score": 55.0, "profit_effect": 0.55},
    ]))
    # 行业与指数（供 _freshness / 板块榜）
    upsert_df(conn, "industry_bars", pd.DataFrame([
        {"date": "2026-08-13", "industry": "A", "close": 9.0, "src": "akshare"},
        {"date": "2026-08-14", "industry": "A", "close": 10.0, "src": "akshare"},
        {"date": "2026-08-15", "industry": "A", "close": 11.0, "src": "akshare"},
        {"date": "2026-08-13", "industry": "B", "close": 19.0, "src": "akshare"},
        {"date": "2026-08-14", "industry": "B", "close": 20.0, "src": "akshare"},
        {"date": "2026-08-15", "industry": "B", "close": 20.5, "src": "akshare"},
    ]))
    upsert_df(conn, "index_bars", pd.DataFrame([
        {"index_code": "000300", "date": "2026-08-15", "close": 4000.0, "src": "akshare"},
    ]))
    # 强度
    upsert_df(conn, "quant_strength", pd.DataFrame([
        {"run_date": "2026-08-15", "obj_type": "industry", "obj": "A", "period": "short",
         "rs": 0.1, "rs5": 0.05, "rs10": 0.08, "rs20": 0.12, "trend_stage": "启动", "calc_version": "v1"},
        {"run_date": "2026-08-15", "obj_type": "industry", "obj": "A", "period": "mid",
         "rs": 0.08, "trend_stage": "加速", "calc_version": "v1"},
    ]))
    # 宏观
    upsert_df(conn, "macro_series", pd.DataFrame([
        {"indicator": "社会融资规模增量", "date": "2026年04月份", "value": 6245.0, "src": "akshare"},
    ]))
    # 候选池 + 日线 + 卡片（触发持仓警戒）
    rows = [{"symbol": "600519", "date": (dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat(),
             "close": 100.0, "amount": 1e9, "src": "akshare"} for i in range(120)]
    upsert_df(conn, "daily_bars", pd.DataFrame(rows))
    conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES('600519','core','白酒','2026-08-15')")
    conn.execute("""INSERT INTO cards(symbol, level, cycle, thesis, status, stop_loss, target, created_at)
                    VALUES('600519','A','short','这是一个足够长的投资逻辑说明文本内容','locked', 95.0, 130.0,
                           datetime('now','localtime'))""")
    # 评级 + 观点
    conn.execute("INSERT INTO ratings(date, kind, value) VALUES('2026-08-15','macro','中性')")
    conn.execute("INSERT INTO ratings(date, kind, value) VALUES('2026-08-15','market','中性')")
    conn.execute("""INSERT INTO viewpoints(source, conclusion, period_tag, confidence, evidence_json,
                   invalid_condition, status, created_at)
                   VALUES('research','测试观点内容','short',0.6,'[]','RS转负','active',datetime('now','localtime'))""")
    conn.commit()


def test_daily_report():
    from invest.report import daily_report
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    conn.close()
    msg = daily_report(p, agent_text="")
    # 结构完整性
    assert "【A股投资系统 · 盘后日报】" in msg
    assert "市场温度" in msg and "→" in msg  # 温度 + 交易倾向
    assert "仓位" in msg and "建议总仓位上限" in msg  # 评级仓位指导
    assert "当日板块" in msg and "短线强度前5" in msg
    assert "Agent 复盘" in msg
    assert "测试观点" in msg
    # 持仓警戒（卡片 stop=95，收盘 100 → 未破止损；若跌破则含警戒）
    print("test_daily_report OK")


def test_daily_report_card_alert():
    from invest.report import daily_report
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    # 把收盘价改到止损下方 → 触发警戒
    conn.execute("UPDATE daily_bars SET close=90.0 WHERE symbol='600519' AND date='2026-05-30'")
    # 直接改最新收盘
    conn.execute("""UPDATE daily_bars SET close=94.0 WHERE symbol='600519'
                    AND date=(SELECT MAX(date) FROM daily_bars WHERE symbol='600519')""")
    conn.commit()
    conn.close()
    msg = daily_report(p)
    assert "⚠️ 持仓警戒" in msg and "破止损" in msg
    print("test_daily_report_card_alert OK")


def test_premarket_report():
    from invest.report import premarket_report
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    conn.close()
    msg = premarket_report(p, agent_text="关注方向测试")
    assert "【A股投资系统 · 盘前】" in msg
    assert "仓位" in msg and "温度" in msg
    assert "关注方向" in msg and "关注方向测试" in msg
    print("test_premarket_report OK")


def test_intraday_report():
    from invest.report import intraday_report
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    conn.close()
    # mock 实时行情：600519 现价 105（较昨收 100 +5%）
    with mock.patch("invest.report._live_quotes", return_value=({"600519": 105.0}, {"600519": 0.05})):
        msg = intraday_report(p)
    assert "【盘中实时报告" in msg
    assert "600519" in msg
    assert "+5.0%" in msg
    assert "温度" in msg and "仓位" in msg
    print("test_intraday_report OK")


def test_intraday_report_no_live():
    from invest.report import intraday_report
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    conn.close()
    # 实时行情不可用 → 回退最近收盘
    with mock.patch("invest.report._live_quotes", return_value=({}, {})):
        msg = intraday_report(p)
    assert "实时行情暂不可用" in msg
    assert "600519" in msg  # 回退收盘数据也列出
    print("test_intraday_report_no_live OK")


if __name__ == "__main__":
    test_daily_report()
    test_daily_report_card_alert()
    test_premarket_report()
    test_intraday_report()
    test_intraday_report_no_live()
    print("\nALL REPORT TESTS PASSED")
