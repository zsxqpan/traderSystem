"""涨停池个股明细 / 行业板块主力资金（2026-08-20）+ 报告段渲染。全 mock，不连网。"""
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_pool_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_fetch_limit_up_pool():
    from invest.data.emotion import fetch_limit_up_pool

    zt = {"data": {"pool": [
        {"c": "600519", "n": "贵州茅台", "lbc": 2, "fbt": "093000", "fund": 52000.0},
        {"c": "300750", "n": "宁德时代", "lbc": 0, "fbt": "100500", "fund": 8000.0},
    ]}}
    zb = {"data": {"pool": [
        {"c": "000001", "n": "平安银行", "lbc": 1, "fbt": "101000", "fund": 100.0},
    ]}}
    with mock.patch("invest.data.emotion._fetch_pool", side_effect=[zt, zb]):
        df = fetch_limit_up_pool("20260820")
    assert len(df) == 3
    moutai = df[df["symbol"] == "600519"].iloc[0]
    assert moutai["lianban"] == 2 and moutai["name"] == "贵州茅台"
    assert int(moutai["seal_amount"]) == 52000
    assert df[df["symbol"] == "000001"].iloc[0]["zhaban"] == 1  # 炸板标记
    print("test_fetch_limit_up_pool OK")


def test_fetch_sector_fund_flow():
    from invest.data.fund_flow import fetch_sector_fund_flow

    fake_diff = [
        {"f12": "BK0447", "f14": "半导体", "f62": 1.2e9, "f184": 3.5},
        {"f12": "BK0896", "f14": "白酒", "f62": -5e8, "f184": "-1.2"},
    ]
    with mock.patch("invest.data.fund_flow.requests.get") as m:
        m.return_value.json.return_value = {"data": {"diff": fake_diff}}
        df = fetch_sector_fund_flow()
    assert len(df) == 2
    assert df.iloc[0]["industry"] == "半导体"
    assert abs(df.iloc[0]["main_net"] - 1.2e9) < 1
    assert df.iloc[1]["main_net_pct"] == -1.2  # 字符串 "-1.2" 也能转 float
    print("test_fetch_sector_fund_flow OK")


def test_report_ladder_and_fund_blocks():
    from invest.report import _fund_line_block, _limit_up_ladder_block

    p = _tmp_db()
    conn = connect(p)
    import pandas as pd

    from invest.data.storage import upsert_df
    upsert_df(conn, "limit_up_pool", pd.DataFrame([
        {"date": "2026-08-20", "symbol": "600001", "name": "五板王", "lianban": 5,
         "first_seal_time": "092500", "seal_amount": 3e7, "zhaban": 0, "src": "eastmoney"},
        {"date": "2026-08-20", "symbol": "300001", "name": "三板客", "lianban": 3,
         "first_seal_time": "093000", "seal_amount": 1e7, "zhaban": 0, "src": "eastmoney"},
        {"date": "2026-08-20", "symbol": "000001", "name": "炸板股", "lianban": 2,
         "first_seal_time": "094000", "seal_amount": 1e6, "zhaban": 1, "src": "eastmoney"},
    ]))
    upsert_df(conn, "sector_fund_flow", pd.DataFrame([
        {"date": "2026-08-20", "industry": "半导体", "main_net": 1.2e9, "main_net_pct": 3.5, "src": "eastmoney"},
        {"date": "2026-08-20", "industry": "军工", "main_net": 8e8, "main_net_pct": 2.0, "src": "eastmoney"},
    ]))
    ladder = _limit_up_ladder_block(conn)
    assert "600001" in ladder and "5板" in ladder and "炸板股" not in ladder  # 炸板不进梯队
    fund = _fund_line_block(conn)
    assert "半导体" in fund and "主力净流入+12.00亿" in fund  # 1.2e9 元 = 12 亿
    conn.close()
    print("test_report_ladder_and_fund_blocks OK")


def test_global_snapshot_parse():
    """2026-08-21：隔夜外围快照解析（新浪 gb_ 美股 parts[2]=涨跌幅 / hf_ 期货 (现价/昨收-1) + 腾讯汇率逗号），全 mock。"""
    from invest.data.global_snapshot import fetch_global_snapshot, global_snapshot_text

    # 真实格式：gb_ 美股（2=涨跌幅%）；hf_ 期货（0=现价 2=昨收）
    sina_raw = (
        'var hq_str_gb_$dji="道琼斯,35000.12,-0.35,2026-08-21 00:53:25,-120.5";\n'
        'var hq_str_gb_$ixic="纳斯达克,14000.88,0.57,2026-08-21 00:53:25,80.2";\n'
        'var hq_str_hf_CHA50CFD="12808,,12800,12802,12850,12700,00:53:31";\n'
    )
    tencent_raw = 'v_whUSDCNY="USDCNY,7.2450,0.0010,0.014";\n'
    with mock.patch("invest.data.global_snapshot._get",
                    side_effect=[sina_raw, tencent_raw, sina_raw, tencent_raw]):
        snap = fetch_global_snapshot()
        text = global_snapshot_text()  # 内部会再次 fetch，复用 mock
    assert abs(snap["us_dji"] - (-0.35)) < 1e-6
    assert abs(snap["us_ixic"] - 0.57) < 1e-6
    # 富时A50：12808/12800-1 = +0.0625%
    assert abs(snap["a50"] - round((12808 / 12800 - 1) * 100, 4)) < 1e-6
    assert abs(snap["usdcny"] - 7.245) < 1e-6
    assert "道指-0.35%" in text and "USDCNY 7.2450" in text
    print("test_global_snapshot_parse OK")


if __name__ == "__main__":
    test_fetch_limit_up_pool()
    test_fetch_sector_fund_flow()
    test_report_ladder_and_fund_blocks()
    test_global_snapshot_parse()
    print("\nALL POOL TESTS PASSED")
