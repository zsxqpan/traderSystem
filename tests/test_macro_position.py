"""宏观总闸 + 仓位执行单元测试。用法: python tests/test_macro_position.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db
from invest.discipline.macro_gate import (
    ENV_FACTOR,
    apply_gate,
    black_swan_actions,
    check_black_swan,
    macro_rating,
    position_gate,
)
from invest.discipline.position import (
    LEVEL_RISK,
    create_plan_from_card,
    fixed_risk_position,
    single_cap,
)


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_macro_pos_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def test_macro_rating():
    p = _tmp_db()
    conn = connect(p)
    assert macro_rating(conn) == "中性"  # 无评级默认
    from invest.discipline.rating import set_rating
    set_rating(conn, "macro", "收紧")
    assert macro_rating(conn) == "收紧"
    conn.close()
    print("test_macro_rating OK")


def test_env_retrigger():
    """环境重评触发条件（[B]8）：ERP 跨分位 / 社融拐点 / 10Y>20bp。"""
    import pandas as pd

    from invest.data.storage import upsert_df
    from invest.discipline.macro_gate import (
        check_env_retrigger,
        env_retrigger_text,
    )
    p = _tmp_db()
    conn = connect(p)
    # 全 A PE 分位 0.85（>0.80 变贵 → 触发）
    upsert_df(conn, "macro_series", pd.DataFrame([
        {"indicator": "全A中位PE近10年分位", "date": "2026-08-14", "value": 0.85, "src": "akshare"},
    ]))
    # 社融增量：上月 50000 → 本月 30000（转负 → 触发）
    upsert_df(conn, "macro_series", pd.DataFrame([
        {"indicator": "社会融资规模增量", "date": "2026年03月份", "value": 50000.0, "src": "akshare"},
        {"indicator": "社会融资规模增量", "date": "2026年04月份", "value": 30000.0, "src": "akshare"},
    ]))
    # 10Y：7 天内 1.40 → 1.80（+40bp → 触发）
    upsert_df(conn, "macro_series", pd.DataFrame([
        {"indicator": "中国国债收益率10年", "date": "2026-08-07", "value": 1.40, "src": "akshare"},
        {"indicator": "中国国债收益率10年", "date": "2026-08-14", "value": 1.80, "src": "akshare"},
    ]))
    result = check_env_retrigger(conn)
    assert result["n"] == 3, result
    joined = "\n".join(result["triggers"])
    assert "ERP 跨分位" in joined
    assert "社融拐点" in joined
    assert "10Y 利率周变动" in joined
    text = env_retrigger_text(result)
    assert text.startswith("[B8]") and "社融拐点" in text
    conn.close()
    print("test_env_retrigger OK")


def test_env_retrigger_no_trigger():
    """无触发：PE 分位 0.5、社融上行、10Y 微动 → 空触发列表。"""
    import pandas as pd

    from invest.data.storage import upsert_df
    from invest.discipline.macro_gate import check_env_retrigger
    p = _tmp_db()
    conn = connect(p)
    upsert_df(conn, "macro_series", pd.DataFrame([
        {"indicator": "全A中位PE近10年分位", "date": "2026-08-14", "value": 0.50, "src": "akshare"},
        {"indicator": "社会融资规模增量", "date": "2026年03月份", "value": 30000.0, "src": "akshare"},
        {"indicator": "社会融资规模增量", "date": "2026年04月份", "value": 35000.0, "src": "akshare"},
        {"indicator": "中国国债收益率10年", "date": "2026-08-07", "value": 1.70, "src": "akshare"},
        {"indicator": "中国国债收益率10年", "date": "2026-08-14", "value": 1.72, "src": "akshare"},
    ]))
    result = check_env_retrigger(conn)
    assert result["n"] == 0, result
    conn.close()
    print("test_env_retrigger_no_trigger OK")


def test_position_gate():
    # 中性：1.00 系数
    g1 = position_gate(0.40, env="中性")
    assert g1["gate_position"] == 0.40
    # 收紧：0.70
    g2 = position_gate(0.40, env="收紧")
    assert abs(g2["gate_position"] - 0.28) < 1e-9
    # ERP 高（0.8）→ 乘数 0.8+0.32=1.12
    g3 = position_gate(0.40, env="中性", erp_pct=0.8)
    assert abs(g3["gate_position"] - 0.40 * 1.12) < 1e-9
    # ERP 低（0.1）→ 乘数 0.84
    g4 = position_gate(0.40, env="中性", erp_pct=0.1)
    assert abs(g4["gate_position"] - 0.40 * 0.84) < 1e-9
    assert ENV_FACTOR["宽松"] == 1.00 and ENV_FACTOR["收紧"] == 0.70
    print("test_position_gate OK")


def test_black_swan():
    # 未触发
    assert check_black_swan(index_change_pct=-0.03) == []
    # 指数暴跌
    t1 = check_black_swan(index_change_pct=-0.06)
    assert len(t1) == 1 and "5%" in t1[0]
    # 跌停潮
    t2 = check_black_swan(limit_down_count=600)
    assert len(t2) == 1
    # 政策黑天鹅
    t3 = check_black_swan(policy_shock=True)
    assert len(t3) == 1
    # 动作
    actions = black_swan_actions(t1)
    assert any("减半" in a for a in actions)
    assert any("禁新开仓" in a for a in actions)
    assert any("24h" in a for a in actions)
    # 未触发无动作
    assert black_swan_actions([]) == []
    print("test_black_swan OK")


def test_apply_gate():
    p = _tmp_db()
    conn = connect(p)
    from invest.discipline.rating import set_rating
    set_rating(conn, "macro", "收紧")
    # 收紧 + 黑天鹅 → 0.40×0.70×0.5
    r = apply_gate(conn, 0.40, index_change_pct=-0.06)
    assert abs(r["final_gate"] - 0.40 * 0.70 * 0.5) < 1e-9
    assert len(r["black_swans"]) == 1
    assert len(r["actions"]) == 3
    conn.close()
    print("test_apply_gate OK")


def test_fixed_risk_position():
    # A 级：R=0.6%，100 万净值，入场 10，止损 9 → 风险 6000，每股 1 元 → 6000 股 → 6 万 = 6%
    pos = fixed_risk_position("A", 1_000_000, 10.0, 9.0)
    assert pos["ok"] is True
    assert pos["qty"] == 6000
    assert abs(pos["position_fraction"] - 0.06) < 1e-4
    assert pos["risk_fraction"] == 0.006
    # S 级：R=0.8% → 8000 股
    pos2 = fixed_risk_position("S", 1_000_000, 10.0, 9.0)
    assert pos2["qty"] == 8000
    # 等级帽：S 级 20% 但单票 10% → capped
    pos3 = fixed_risk_position("S", 100_000, 10.0, 9.9)  # 风险 800，每股 0.1 → 8000 股 = 80%
    assert pos3["capped"] is True
    assert pos3["final_fraction"] == 0.10  # 单票帽
    # 入场=止损
    pos4 = fixed_risk_position("A", 1_000_000, 10.0, 10.0)
    assert pos4["ok"] is False
    assert LEVEL_RISK["B"] == 0.0035
    print("test_fixed_risk_position OK")


def test_single_cap():
    assert single_cap("600519") == 0.10
    assert single_cap("510300", is_etf=True) == 0.15
    print("test_single_cap OK")


def test_create_plan_from_card():
    p = _tmp_db()
    conn = connect(p)
    import datetime as dt

    import pandas as pd

    from invest.data.storage import upsert_df
    from invest.discipline.cards import create_card, lock_card
    # 入池 + 建卡 + 锁卡
    rows = [{"symbol": "600519", "date": (dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat(),
             "close": 100.0, "amount": 1e9, "src": "akshare"} for i in range(80)]
    upsert_df(conn, "daily_bars", pd.DataFrame(rows))
    from invest.discipline.pool_rules import check_and_add
    check_and_add(conn, "600519", level="core", industry="白酒")
    card = create_card(conn, "600519", level="A", cycle="short",
                       thesis="白酒景气回升，估值低位，动销验证中",
                       falsify="动销转负", entry_range="100,105",
                       stop_loss=95.0, target=120.0)
    lock_card(conn, card["card_id"])
    # 生成计划（入场 100 止损 95：每股风险 5 元，6000/5=1200 股=12万=12%）
    plan = create_plan_from_card(conn, card["card_id"], equity=1_000_000)
    assert plan["plan_id"] > 0
    assert plan["symbol"] == "600519"
    assert plan["suggested_position"]["ok"] is True
    # 卡片引用已写入 invalid_condition
    row = conn.execute("SELECT invalid_condition FROM trade_plans WHERE id=?", (plan["plan_id"],)).fetchone()
    assert "card_id" in row["invalid_condition"]
    # 未锁定卡片拒绝
    card2 = create_card(conn, "600519", level="B", cycle="short",
                        thesis="另一张卡片逻辑说明文字内容",
                        falsify="x", entry_range="10,11", stop_loss=9.0, target=12.0)
    try:
        create_plan_from_card(conn, card2["card_id"])
        raise AssertionError("candidate 卡片应拒绝建计划")
    except ValueError as e:
        assert "locked" in str(e)
    conn.close()
    print("test_create_plan_from_card OK")


if __name__ == "__main__":
    test_macro_rating()
    test_env_retrigger()
    test_env_retrigger_no_trigger()
    test_position_gate()
    test_black_swan()
    test_apply_gate()
    test_fixed_risk_position()
    test_single_cap()
    test_create_plan_from_card()
    print("\nALL MACRO/POSITION TESTS PASSED")
