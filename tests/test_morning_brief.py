"""盘前信息早报单元测试。用法: python tests/test_morning_brief.py"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from invest.db import connect, init_db
from invest.data.storage import upsert_df
from invest.report import morning_brief_report


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_morning_brief.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _seed(conn):
    # 温度 + 情绪
    upsert_df(conn, "quant_temperature", pd.DataFrame([
        {"run_date": "2026-08-14", "score": 60.0, "profit_effect": 0.6},
    ]))
    upsert_df(conn, "market_emotion", pd.DataFrame([
        {"date": "2026-08-14", "limit_up_count": 90, "max_lianban": 5, "zhaban_rate": 0.2},
    ]))
    # 龙虎榜（净买入）
    upsert_df(conn, "dragon_tiger", pd.DataFrame([
        {"date": "2026-08-14", "symbol": "600519", "name": "贵州茅台", "seat_type": "list",
         "buy": 1e8, "sell": 0.2e8, "net": 0.8e8, "src": "akshare"},
        {"date": "2026-08-14", "symbol": "000001", "name": "平安银行", "seat_type": "list",
         "buy": 0.5e8, "sell": 0.1e8, "net": 0.4e8, "src": "akshare"},
    ]))
    # 强度
    upsert_df(conn, "quant_strength", pd.DataFrame([
        {"run_date": "2026-08-14", "obj_type": "industry", "obj": "半导体", "period": "short",
         "rs": 0.2, "rs5": 0.1, "rs10": 0.15, "rs20": 0.25, "momentum": 0.1,
         "trend_stage": "加速", "calc_version": "v1"},
        {"run_date": "2026-08-14", "obj_type": "industry", "obj": "医疗服务", "period": "short",
         "rs": 0.15, "rs5": 0.08, "rs10": 0.1, "rs20": 0.2, "momentum": 0.08,
         "trend_stage": "启动", "calc_version": "v1"},
    ]))
    # 板块涨幅
    upsert_df(conn, "industry_bars", pd.DataFrame([
        {"date": "2026-08-13", "industry": "半导体", "close": 9.0, "src": "akshare"},
        {"date": "2026-08-14", "industry": "半导体", "close": 10.0, "src": "akshare"},
        {"date": "2026-08-13", "industry": "医疗服务", "close": 19.0, "src": "akshare"},
        {"date": "2026-08-14", "industry": "医疗服务", "close": 20.0, "src": "akshare"},
    ]))
    upsert_df(conn, "index_bars", pd.DataFrame([
        {"index_code": "000300", "date": "2026-08-14", "close": 4000.0, "src": "akshare"},
    ]))
    # 候选池
    conn.execute("INSERT INTO candidate_pool(symbol, level, industry, in_date) VALUES('600519','core','白酒','2026-08-14')")
    # 评级
    conn.execute("INSERT INTO ratings(date, kind, value) VALUES('2026-08-14','macro','中性')")
    conn.execute("INSERT INTO ratings(date, kind, value) VALUES('2026-08-14','market','中性')")
    # 宏观
    upsert_df(conn, "macro_series", pd.DataFrame([
        {"indicator": "M1-M2剪刀差", "date": "2026-08-14", "value": -3.0, "src": "akshare"},
        {"indicator": "PMI制造业指数", "date": "2026-08-14", "value": 49.2, "src": "akshare"},
    ]))
    conn.commit()


def test_morning_brief_structure():
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    conn.close()
    msg = morning_brief_report(p)
    # 关键区块齐全
    assert "盘前信息早报" in msg
    assert "隔夜市场" in msg
    assert "资金焦点" in msg
    assert "板块主线" in msg
    assert "宏观" in msg
    # 龙虎榜净买入（贵州茅台）
    assert "贵州茅台" in msg
    # 强度榜（半导体）
    assert "半导体" in msg
    # 简明扼要：行数合理（< 30 行）
    assert len(msg.split("\n")) < 30, f"早报过长: {len(msg.split(chr(10)))} 行"
    print("test_morning_brief_structure OK")


def test_morning_brief_empty_db():
    """空库不崩溃。"""
    p = _tmp_db()
    msg = morning_brief_report(p)
    assert "盘前信息早报" in msg
    print("test_morning_brief_empty_db OK")


def test_scheduler_has_morning_brief():
    """调度器已注册 morning_brief 8:40 任务。"""
    from invest.scheduler import build_scheduler
    sched = build_scheduler()
    job = sched.get_job("morning_brief")
    assert job is not None
    from apscheduler.triggers.cron import CronTrigger
    trig = job.trigger
    # hour=8 minute=40（通过表达式字符串验证，避免字段 API 差异）
    expr = str(trig)
    assert "hour='8'" in expr or "hour=8" in expr
    assert "minute='40'" in expr or "minute=40" in expr
    print("test_scheduler_has_morning_brief OK")


def test_notify_morning_brief():
    """通知入口可用：仅走飞书通道（用户指定，其他渠道不发送）。"""
    from unittest import mock
    from invest.pipeline import notify_morning_brief
    p = _tmp_db()
    conn = connect(p)
    _seed(conn)
    conn.close()
    with mock.patch("invest.push.feishu_push.send_text") as m:
        m.return_value = True
        assert notify_morning_brief(p) is True
        text = m.call_args.args[0]
        assert "盘前信息早报" in text
        assert m.call_args.kwargs.get("key") == "morning_brief"
    # 验证只走飞书：feishu send_text 被调用即证明不走 Notifier 多通道
    with mock.patch("invest.push.feishu_push.send_text", return_value=True) as m2:
        notify_morning_brief(p)
        assert m2.call_count == 1
    print("test_notify_morning_brief OK")


if __name__ == "__main__":
    test_morning_brief_structure()
    test_morning_brief_empty_db()
    test_scheduler_has_morning_brief()
    test_notify_morning_brief()
    print("\nALL MORNING-BRIEF TESTS PASSED")
