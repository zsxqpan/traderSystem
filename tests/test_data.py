"""数据层单元测试（无网络依赖）。

用法: python tests/test_data.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from invest.data.calendar import get_trading_days, is_trading_day
from invest.data.collector import run_collection
from invest.data.sources.akshare_source import AkShareSource
from invest.data.storage import upsert_df
from invest.data.validator import cross_check
from invest.db import connect, init_db


def _raw_pmi():
    return pd.DataFrame({
        "月份": ["2024-06", "2024-07"],
        "制造业-指数": [49.5, 49.4],
        "制造业-同比增长": [1.0, 0.9],
        "非制造业-指数": [50.5, 50.2],
        "非制造业-同比增长": [0.5, 0.4],
    })


def test_margin_normalize():
    src = AkShareSource()
    df = pd.DataFrame({
        "信用交易日期": ["2024-07-01", "2024-07-02"],
        "融资余额": [100.0, 101.0],
        "融资买入额": [10.0, 12.0],
        "融券余量": [1.0, 1.1],
        "融券余量金额": [2.0, 2.1],
        "融券卖出量": [0.5, 0.6],
        "融资融券余额": [102.0, 103.1],
    })
    out = src.normalize(df, {"kind": "margin"})
    assert out.columns.tolist() == ["date", "balance", "buy", "src"]
    assert out.iloc[0]["date"] == "2024-07-01"
    assert out.iloc[0]["balance"] == 102.0
    assert out.iloc[0]["buy"] == 10.0
    print("test_margin_normalize OK")


def test_macro_pmi_normalize():
    src = AkShareSource()
    out = src.normalize(_raw_pmi(), {"kind": "macro_series", "macro": "pmi"})
    assert out.columns.tolist() == ["indicator", "date", "value", "unit", "src"]
    assert out["indicator"].nunique() == 4
    assert out[out["indicator"] == "制造业-指数"].iloc[0]["value"] == 49.5
    print("test_macro_pmi_normalize OK")


def test_macro_normalize_idempotent():
    """双重 normalize 不应报错（回归：value_name 冲突）。"""
    src = AkShareSource()
    once = src.normalize(_raw_pmi(), {"kind": "macro_series", "macro": "pmi"})
    twice = src.normalize(once, {"kind": "macro_series", "macro": "pmi"})
    pd.testing.assert_frame_equal(once.reset_index(drop=True), twice.reset_index(drop=True))
    print("test_macro_normalize_idempotent OK")


def test_macro_money_supply_normalize():
    src = AkShareSource()
    df = pd.DataFrame({
        "月份": ["2024-06", "2024-07"],
        "货币和准货币(M2)-数量(亿元)": [301.0, 302.0],
        "货币(M1)-数量(亿元)": [66.0, 66.3],
        "流通中的现金(M0)-数量(亿元)": [11.5, 11.6],
    })
    out = src.normalize(df, {"kind": "macro_series", "macro": "money_supply"})
    assert out["indicator"].nunique() == 3
    assert out[out["indicator"] == "货币(M1)-数量(亿元)"].iloc[0]["value"] == 66.0
    print("test_macro_money_supply_normalize OK")


def test_macro_new_financial_credit_normalize():
    src = AkShareSource()
    df = pd.DataFrame({
        "月份": ["2024-06", "2024-07"],
        "当月": [2.13, 1.09],
        "当月-同比增长": [5.0, -3.0],
        "累计": [13.2, 14.3],
    })
    out = src.normalize(df, {"kind": "macro_series", "macro": "new_financial_credit"})
    assert out["indicator"].nunique() == 3
    assert out[out["indicator"] == "当月"].iloc[0]["value"] == 2.13
    print("test_macro_new_financial_credit_normalize OK")


def test_macro_shrzgm_normalize():
    """真实社融增量（macro_china_shrzgm）：月份 YYYYMM 应统一为 YYYY年MM月份。"""
    from invest.data.sources.akshare_source import _fmt_month
    src = AkShareSource()
    df = pd.DataFrame({
        "月份": ["202602", "202603"],
        "社会融资规模增量": [23837.0, 52240.0],
        "其中-人民币贷款": [8458.0, 31522.0],
    })
    out = src.normalize(df, {"kind": "macro_series", "macro": "shrzgm"})
    assert out["date"].iloc[0] == "2026年02月份"
    assert out["date"].iloc[-1] == "2026年03月份"
    assert out["unit"].iloc[0] == "亿元"
    v = out[out["indicator"] == "社会融资规模增量"].iloc[0]["value"]
    assert v == 23837.0
    # 月份格式化单测
    assert _fmt_month("202602") == "2026年02月份"
    assert _fmt_month("2026-02") == "2026-02"  # 非 YYYYMM 原样返回
    print("test_macro_shrzgm_normalize OK")


def test_macro_no_date_col_error():
    src = AkShareSource()
    df = pd.DataFrame({"foo": [1.0]})
    try:
        src.normalize(df, {"kind": "macro_series", "macro": "pmi"})
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "实际列" in str(e)
    print("test_macro_no_date_col_error OK")


class _FakeAkShare(AkShareSource):
    """模拟 akshare 返回原始 PMI 宽表，验证 collector 全流程。"""

    def fetch(self, task):
        return _raw_pmi()


def test_collector_macro_flow():
    """回归：fetch 返回原始数据 → collector normalize 一次 → 落库。"""
    tmp = os.path.join(tempfile.gettempdir(), "invest_collect_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    init_db(tmp)
    task = {
        "name": "macro_pmi", "kind": "macro_series", "table": "macro_series",
        "sources": ["fake"], "cross_check": False,
        "params": {"macro": "pmi"},
    }
    registry = {"fake": _FakeAkShare()}
    summary = run_collection(tmp, tasks=[task], registry=registry)
    assert summary[0]["status"] == "ok", summary
    conn = connect(tmp)
    n = conn.execute("SELECT COUNT(*) FROM macro_series").fetchone()[0]
    job = conn.execute("SELECT status FROM job_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.close()
    assert n == 8, f"expect 8 rows (2 dates x 4 indicators), got {n}"
    assert job == "ok"
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    print("test_collector_macro_flow OK")


def test_industry_normalize():
    src = AkShareSource()
    df = pd.DataFrame({
        "日期": ["2024-07-01"], "开盘": [1.0], "收盘": [1.1],
        "最高": [1.2], "最低": [0.9], "成交量": [100], "成交额": [110.0],
        "振幅": [3.0], "涨跌幅": [1.0], "涨跌额": [0.01], "换手率": [0.5],
    })
    out = src.normalize(df, {"kind": "industry_bars", "symbol": "半导体"})
    assert out["industry"].iloc[0] == "半导体"
    assert out["close"].iloc[0] == 1.1
    assert "涨跌幅" not in out.columns
    print("test_industry_normalize OK")


def test_daily_normalize():
    src = AkShareSource()
    df = pd.DataFrame({
        "日期": ["2024-07-01"], "股票代码": ["000001"],
        "开盘": [8.89], "收盘": [9.15], "最高": [9.15], "最低": [8.84],
        "成交量": [1343051], "成交额": [1372550000.0],
        "振幅": [3.46], "涨跌幅": [2.23], "涨跌额": [0.20], "换手率": [0.69],
    })
    out = src.normalize(df, {"kind": "daily_bars", "symbol": "000001"})
    assert out["symbol"].iloc[0] == "000001"
    assert out["close"].iloc[0] == 9.15
    assert "涨跌幅" not in out.columns
    print("test_daily_normalize OK")


def test_cross_check_and_upsert():
    d1 = pd.DataFrame({"date": ["2024-01-02", "2024-01-03"], "close": [10.0, 11.0]})
    d2 = pd.DataFrame({"date": ["2024-01-02", "2024-01-03"], "close": [10.001, 11.0]})
    ok, _rep = cross_check(d1, d2)
    assert ok is True
    d3 = d2.copy()
    d3.loc[0, "close"] = 99.0
    ok2, _ = cross_check(d1, d3)
    assert ok2 is False

    tmp = os.path.join(tempfile.gettempdir(), "invest_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    init_db(tmp)
    conn = connect(tmp)
    fake = pd.DataFrame({
        "symbol": ["TEST1", "TEST1"], "date": ["2026-08-03", "2026-08-04"],
        "open": [1.0, 1.1], "high": [1.2, 1.3], "low": [0.9, 1.0],
        "close": [1.1, 1.2], "volume": [1000, 1200], "amount": [1100.0, 1300.0],
        "src": ["akshare", "akshare"],
    })
    assert upsert_df(conn, "daily_bars", fake) == 2
    assert upsert_df(conn, "daily_bars", fake) == 2
    n = conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
    assert n == 2
    conn.close()
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    print("test_cross_check_and_upsert OK")


def test_calendar():
    assert is_trading_day(__import__("datetime").date(2026, 8, 3)) is True
    days = get_trading_days(__import__("datetime").date(2026, 8, 3), __import__("datetime").date(2026, 8, 9))
    assert len(days) == 5
    print("test_calendar OK")




def test_industry_cache_and_parse():
    import json
    from pathlib import Path

    from invest.data import industry

    tmp = Path(tempfile.gettempdir()) / "industry_list_test.json"
    if tmp.exists():
        tmp.unlink()
    records = [{"code": "BK0475", "name": "半导体"}, {"code": "BK1027", "name": "小金属"}]
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)
    df = industry.fetch_industry_list(cache_path=tmp)
    assert df["name"].tolist() == ["半导体", "小金属"]

    klines = ["2024-07-01,1.0,1.1,1.2,0.9,100,110.0,3.0,1.0,0.01,0.5"]
    dfk = pd.DataFrame([line.split(",") for line in klines], columns=industry._KLINE_COLUMNS)
    assert dfk["收盘"].iloc[0] == "1.1"
    tmp.unlink()
    print("test_industry_cache_and_parse OK")



def test_split_windows():
    from invest.data.industry import _split_windows
    wins = _split_windows("20240101", "20250101")
    assert len(wins) > 1
    assert wins[0] == ("20240101", "20240628")
    assert wins[-1][1] == "20250101"
    print("test_split_windows OK")



def test_industry_ths_normalize():
    """同花顺行业指数列名标准化。"""
    src = AkShareSource()
    df = pd.DataFrame({
        "日期": ["2024-08-01"], "开盘价": [1.0], "最高价": [1.2],
        "最低价": [0.9], "收盘价": [1.1], "成交量": [100], "成交额": [110.0],
    })
    out = src.normalize(df, {"kind": "industry_bars", "symbol": "半导体"})
    assert out["industry"].iloc[0] == "半导体"
    assert out["close"].iloc[0] == 1.1
    assert out["open"].iloc[0] == 1.0
    assert "最高价" not in out.columns
    print("test_industry_ths_normalize OK")



def test_backfill_tasks():
    from invest.data.backfill import build_backfill_tasks
    tasks = build_backfill_tasks("20200101")
    assert len(tasks) == 3
    assert tasks[0]["params"]["start_date"] == "20200101"
    assert tasks[1]["params"]["symbol"] == "000300"
    assert tasks[2]["params"]["industries"] == []  # 全量行业模式
    print("test_backfill_tasks OK")



def test_emotion_build_and_collect():
    import tempfile

    from invest.data.collector import run_collection
    from invest.data.emotion import build_emotion_df
    from invest.data.sources.akshare_source import AkShareSource

    zt = [{"lbc": 1}, {"lbc": 4}, {"lbc": 7}]
    zb = [{"x": 1}, {"x": 2}]
    df = build_emotion_df("20260803", zt, zb)
    assert df.iloc[0]["limit_up_count"] == 3
    assert df.iloc[0]["max_lianban"] == 7
    assert df.iloc[0]["zhaban_count"] == 2
    assert abs(df.iloc[0]["zhaban_rate"] - 0.4) < 1e-9

    class FakeEmotion(AkShareSource):
        def fetch(self, task):
            return build_emotion_df(task["date"], zt, None)

    tmp = os.path.join(tempfile.gettempdir(), "invest_emotion_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    init_db(tmp)
    task = {"name": "market_emotion", "kind": "market_emotion", "table": "market_emotion",
            "sources": ["fake"], "cross_check": False, "params": {}}
    run_collection(tmp, tasks=[task], registry={"fake": FakeEmotion()})
    conn = connect(tmp)
    row = conn.execute("SELECT * FROM market_emotion").fetchone()
    conn.close()
    assert row is not None
    assert row["limit_up_count"] == 3
    assert row["date"]  # date 被自动注入
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    print("test_emotion_build_and_collect OK")



def test_stock_daily_all_normalize():
    src = AkShareSource()
    df = pd.DataFrame({
        "日期": ["2024-07-01", "2024-07-01"], "股票代码": ["000001", "600000"],
        "开盘": [8.0, 9.0], "收盘": [8.5, 9.5], "最高": [8.6, 9.6], "最低": [7.9, 8.9],
        "成交量": [100, 200], "成交额": [850.0, 1900.0],
        "振幅": [1.0, 1.0], "涨跌幅": [1.0, 1.0], "涨跌额": [0.1, 0.1], "换手率": [0.5, 0.5],
    })
    out = src.normalize(df, {"kind": "stock_daily_all"})
    assert out["symbol"].tolist() == ["000001", "600000"]
    assert out["close"].tolist() == [8.5, 9.5]
    assert "涨跌幅" not in out.columns
    print("test_stock_daily_all_normalize OK")



def test_seat_detail_normalize_and_migration():
    src = AkShareSource()
    df = pd.DataFrame({
        "seat_name": ["机构专用", "中信证券上海分公司", "深股通专用"],
        "buy": [1000.0, 500.0, 300.0], "sell": [100.0, 200.0, 50.0], "net": [900.0, 300.0, 250.0],
        "date": ["20260803"] * 3, "symbol": ["000001"] * 3,
    })
    out = src.normalize(df, {"kind": "seat_detail"})
    assert out.columns.tolist() == ["date", "symbol", "seat_type", "buy", "sell", "net", "src"]
    assert out["seat_type"].iloc[0] == "机构专用"

    # 迁移幂等：同一库 init 两次不报错且列存在
    tmp = os.path.join(tempfile.gettempdir(), "invest_migrate_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    init_db(tmp)
    init_db(tmp)
    conn = connect(tmp)
    cap_cols = [r["name"] for r in conn.execute("PRAGMA table_info(quant_capital)")]
    pool_cols = [r["name"] for r in conn.execute("PRAGMA table_info(candidate_pool)")]
    conn.close()
    assert "obj_type" in cap_cols
    assert "industry" in pool_cols
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    print("test_seat_detail_normalize_and_migration OK")



def test_valuation_normalize_and_tasks():
    src = AkShareSource()
    df = pd.DataFrame({
        "变动日期": ["2026-08-03", "2026-08-03"],
        "行业名称": ["半导体", "银行"], "行业层级": [2, 2],
        "静态市盈率-加权平均": [45.0, 6.5],
        "行业编码": ["x", "y"], "公司数量": [100, 40],
    })
    out = src.normalize(df, {"kind": "industry_valuation"})
    assert out.columns.tolist() == ["date", "industry", "pe", "level", "src"]
    assert out["pe"].tolist() == [45.0, 6.5]

    from invest.data.backfill import build_valuation_tasks
    tasks = build_valuation_tasks(years=1)
    assert len(tasks) >= 11
    assert all(t["kind"] == "industry_valuation" for t in tasks)
    print("test_valuation_normalize_and_tasks OK")



def test_ths_parse_and_map_cache():
    from pathlib import Path

    from invest.data.industry import _parse_ths_year_text, load_ths_map
    text = 'quotebridge_v6_line_bk_881101_01_2024({"data":"2024-01-02,10.0,11.0,12.0,9.0,100,1100.0;2024-01-03,11.0,12.0,13.0,10.0,200,2200.0"})'
    df = _parse_ths_year_text(text)
    assert df.columns.tolist() == ["日期", "开盘价", "最高价", "最低价", "收盘价", "成交量", "成交额"]
    assert df["收盘价"].tolist() == ["9.0", "10.0"]  # 第5列
    compact = _parse_ths_year_text('x({"data":"20260804,10,11,12,9,100,1100"})')
    assert compact["日期"].iloc[0] == "2026-08-04"  # 紧凑日期归一化\n    assert df["最高价"].tolist() == ["11.0", "12.0"]  # 第3列

    # 映射缓存：写假缓存后 load 应直接返回（不联网）
    tmp = Path(tempfile.gettempdir()) / "ths_map_test.json"
    tmp.write_text('{"半导体": "881101", "白酒": "881180"}', encoding="utf-8")
    import invest.data.industry as ind_mod
    orig = ind_mod._THS_MAP_CACHE
    ind_mod._THS_MAP_CACHE = tmp
    try:
        m = load_ths_map()
        assert m["半导体"] == "881101"
    finally:
        ind_mod._THS_MAP_CACHE = orig
        tmp.unlink()
    print("test_ths_parse_and_map_cache OK")


def test_industry_all_normalize():
    src = AkShareSource()
    df = pd.DataFrame({
        "日期": ["2024-07-01", "2024-07-01"], "行业": ["半导体", "银行"],
        "开盘价": [1.0, 2.0], "收盘价": [1.1, 2.1], "最高价": [1.2, 2.2],
        "最低价": [0.9, 1.9], "成交量": [100, 200], "成交额": [110.0, 420.0],
    })
    out = src.normalize(df, {"kind": "industry_all"})
    assert out["industry"].tolist() == ["半导体", "银行"]
    assert out["close"].tolist() == [1.1, 2.1]
    assert "最高价" not in out.columns
    print("test_industry_all_normalize OK")



def test_call_with_timeout():
    import time

    from invest.data.sources.akshare_source import call_with_timeout
    assert call_with_timeout(lambda: 42) == 42
    try:
        call_with_timeout(lambda: time.sleep(5), timeout=0.2)
        raise AssertionError("should timeout")
    except TimeoutError:
        pass
    print("test_call_with_timeout OK")



def test_dragon_tiger_normalize_and_dedupe():
    """回归：榜单行写非空 seat_type（主键可去重）；迁移清理历史 NULL 重复行。"""
    src = AkShareSource()
    df = pd.DataFrame({
        "date": ["20260805", "20260805"], "symbol": ["000001", "600000"],
        "name": ["平安银行", "浦发银行"], "buy": [100.0, 200.0], "sell": [50.0, 60.0],
        "net": [50.0, 140.0],
    })
    out = src.normalize(df, {"kind": "dragon_tiger"})
    assert (out["seat_type"] == "list").all()

    tmp = os.path.join(tempfile.gettempdir(), "invest_dt_dedupe_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    init_db(tmp)
    conn = connect(tmp)
    dup = pd.DataFrame({
        "date": ["20260805"] * 3, "symbol": ["000001"] * 3,
        "name": ["x"] * 3, "buy": [1.0] * 3, "sell": [0.5] * 3, "net": [0.5] * 3,
        "src": ["akshare"] * 3,
    })
    upsert_df(conn, "dragon_tiger", dup)
    assert conn.execute("SELECT COUNT(*) FROM dragon_tiger").fetchone()[0] == 3
    conn.close()
    init_db(tmp)  # 触发 _migrate 去重
    conn = connect(tmp)
    n = conn.execute("SELECT COUNT(*) FROM dragon_tiger").fetchone()[0]
    conn.close()
    assert n == 1, f"expect 1 after dedupe, got {n}"
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    print("test_dragon_tiger_normalize_and_dedupe OK")


def test_tushare_ts_code_and_normalize():
    """备用源：代码转交易所后缀；normalize 只保留 schema 列并统一日期格式。"""
    from invest.data.sources.tushare_source import (
        TushareSource,
        _index_ts_code,
        _stock_ts_code,
    )
    assert _stock_ts_code("000001") == "000001.SZ"
    assert _stock_ts_code("600519") == "600519.SH"
    assert _stock_ts_code("830799") == "830799.BJ"
    assert _stock_ts_code("000001.SZ") == "000001.SZ"
    assert _index_ts_code("000300") == "000300.SH"
    assert _index_ts_code("399006") == "399006.SZ"

    src = TushareSource(token="test")
    raw = pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"], "trade_date": ["20240102", "20240103"],
        "open": [9.0, 9.1], "high": [9.2, 9.3], "low": [8.9, 9.0], "close": [9.15, 9.25],
        "pre_close": [9.0, 9.15], "pct_chg": [1.0, 1.1], "vol": [1000, 1100],
        "amount": [9100.0, 9300.0],
    })
    # fetch 阶段已把 tushare 列名转成统一口径，normalize 只做裁剪/日期归一
    raw = raw.rename(columns={"trade_date": "date", "ts_code": "symbol", "vol": "volume"})
    out = src.normalize(raw, {"kind": "daily_bars", "symbol": "000001"})
    assert out.columns.tolist() == ["date", "symbol", "open", "high", "low", "close", "volume", "amount", "src"]
    assert out["date"].tolist() == ["2024-01-02", "2024-01-03"]
    assert (out["symbol"] == "000001").all()
    assert (out["src"] == "tushare").all()
    print("test_tushare_ts_code_and_normalize OK")



def test_emotion_holiday_skipped():
    """回归：涨停/炸板双空（节假日）不写 0 涨停行。"""
    import invest.data.emotion as emo
    orig = emo._fetch_pool
    emo._fetch_pool = lambda endpoint, date: {"data": {"pool": []}}
    try:
        df = emo.fetch_emotion("20261001")
        assert df.empty
    finally:
        emo._fetch_pool = orig

    class FakeEmpty(AkShareSource):
        def fetch(self, task):
            return pd.DataFrame()

    tmp = os.path.join(tempfile.gettempdir(), "invest_emotion_holiday_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    init_db(tmp)
    task = {"name": "market_emotion", "kind": "market_emotion", "table": "market_emotion",
            "sources": ["fake"], "cross_check": False, "params": {}}
    summary = run_collection(tmp, tasks=[task], registry={"fake": FakeEmpty()})
    assert summary[0]["status"] == "ok", summary
    conn = connect(tmp)
    n = conn.execute("SELECT COUNT(*) FROM market_emotion").fetchone()[0]
    conn.close()
    assert n == 0
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    print("test_emotion_holiday_skipped OK")


def test_ths_concat_dedupe_sort():
    """回归：同花顺年份文件跨年重叠时，拼接结果去重且按日期排序。"""
    from invest.data.industry import _concat_industry_years
    a = pd.DataFrame({"日期": ["2024-12-31", "2024-01-02", "2024-06-01"], "收盘价": [1.0, 2.0, 3.0]})
    b = pd.DataFrame({"日期": ["2024-06-01", "2025-01-02"], "收盘价": [30.0, 4.0]})
    out = _concat_industry_years([a, b])
    assert out["日期"].tolist() == ["2024-01-02", "2024-06-01", "2024-12-31", "2025-01-02"]
    assert out.loc[out["日期"] == "2024-06-01", "收盘价"].iloc[0] == 3.0  # 保留先出现者
    assert _concat_industry_years([]).empty
    print("test_ths_concat_dedupe_sort OK")



def test_date_normalization_kline():
    """回归：K 线类日期统一为 YYYY-MM-DD（历史曾混写两种格式）。"""
    src = AkShareSource()
    df = pd.DataFrame({
        "日期": ["20260804"], "开盘价": [1.0], "最高价": [1.2],
        "最低价": [0.9], "收盘价": [1.1], "成交量": [100], "成交额": [110.0],
    })
    out = src.normalize(df, {"kind": "industry_all"})
    assert out["date"].iloc[0] == "2026-08-04"
    print("test_date_normalization_kline OK")


def test_migration_v4_dedupe():
    """回归：v4 迁移把 compact/dashed 同日重复清理为单行 dashed。"""
    tmp = os.path.join(tempfile.gettempdir(), "invest_v4_migrate_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    init_db(tmp)
    conn = connect(tmp)
    conn.execute("PRAGMA user_version = 3")  # 模拟旧版本库
    conn.commit()
    from invest.data.storage import upsert_df
    upsert_df(conn, "industry_bars", pd.DataFrame([
        {"date": "2026-08-04", "industry": "semicon", "close": 100.0, "src": "akshare"},
        {"date": "20260804", "industry": "semicon", "close": 100.0, "src": "akshare"},
    ]))
    assert conn.execute("SELECT COUNT(*) FROM industry_bars").fetchone()[0] == 2
    conn.close()
    init_db(tmp)  # 触发 v4 清理
    conn = connect(tmp)
    rows = conn.execute("SELECT date, close FROM industry_bars").fetchall()
    assert len(rows) == 1 and rows[0]["date"] == "2026-08-04"
    conn.close()
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(tmp + s)
        except OSError:
            pass
    print("test_migration_v4_dedupe OK")

if __name__ == "__main__":
    test_margin_normalize()
    test_macro_pmi_normalize()
    test_macro_normalize_idempotent()
    test_macro_money_supply_normalize()
    test_macro_new_financial_credit_normalize()
    test_macro_shrzgm_normalize()
    test_macro_no_date_col_error()
    test_collector_macro_flow()
    test_industry_normalize()
    test_daily_normalize()
    test_cross_check_and_upsert()
    test_calendar()
    test_industry_cache_and_parse()
    test_industry_ths_normalize()
    test_split_windows()
    test_backfill_tasks()
    test_emotion_build_and_collect()
    test_stock_daily_all_normalize()
    test_seat_detail_normalize_and_migration()
    test_valuation_normalize_and_tasks()
    test_ths_parse_and_map_cache()
    test_industry_all_normalize()
    test_call_with_timeout()
    test_dragon_tiger_normalize_and_dedupe()
    test_tushare_ts_code_and_normalize()
    test_emotion_holiday_skipped()
    test_ths_concat_dedupe_sort()
    test_date_normalization_kline()
    test_migration_v4_dedupe()
    print("\nALL TESTS PASSED")