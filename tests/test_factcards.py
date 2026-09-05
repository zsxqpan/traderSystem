"""中期证据驾驶舱：FactCard / 发现器 / 人工比较 / 仪表盘 / 重要变化推送。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from invest.data.storage import upsert_df
from invest.db import SCHEMA_SQL, connect, init_db

ROOT = Path(__file__).resolve().parents[1]
AS_OF_OLD = "2026-08-20"
AS_OF_NEW = "2026-08-27"
FORBIDDEN_RANK_KEYS = {
    "buy_rank",
    "sell_rank",
    "trade_rank",
    "score_rank",
    "综合排名",
    "买卖排名",
}


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "invest.db")
    init_db(path)
    return path


def _seed_industry(
    conn: sqlite3.Connection,
    industry: str,
    *,
    as_of: str,
    rs: float,
    rotation_rank: int,
    pe_pct: float,
    crowding: float,
    crowding_state: str,
    style: str,
    cycle_phase: str,
    main_net: float,
    pe: float = 20.0,
) -> None:
    conn.execute(
        """INSERT INTO quant_strength(
               run_date, obj_type, obj, period, rs, momentum, trend_stage
           ) VALUES(?, 'industry', ?, 'mid', ?, 0.1, '上升')""",
        (as_of, industry, rs),
    )
    conn.execute(
        """INSERT INTO quant_rotation(run_date, industry, rank, lead_lag, turnover_share)
           VALUES(?, ?, ?, 'lead', 0.04)""",
        (as_of, industry, rotation_rank),
    )
    conn.execute(
        """INSERT INTO quant_valuation(run_date, obj, pe_pct, pb_pct, crowding, crowding_state)
           VALUES(?, ?, ?, 0.4, ?, ?)""",
        (as_of, industry, pe_pct, crowding, crowding_state),
    )
    conn.execute(
        """INSERT INTO quant_capital(run_date, obj, obj_type, fund_type, style, confidence)
           VALUES(?, ?, 'industry', '主力', ?, 0.8)""",
        (as_of, industry, style),
    )
    conn.execute(
        """INSERT OR REPLACE INTO industry_cycle(industry, phase, notes, updated_at)
           VALUES(?, ?, '', ?)""",
        (industry, cycle_phase, as_of),
    )
    conn.execute(
        """INSERT INTO sector_fund_flow(date, industry, main_net, main_net_pct)
           VALUES(?, ?, ?, 0.02)""",
        (as_of, industry, main_net),
    )
    conn.execute(
        """INSERT INTO industry_valuation(date, industry, pe, pb, level, src)
           VALUES(?, ?, ?, 2.0, 1, 'akshare')""",
        (as_of, industry, pe),
    )
    conn.commit()


def _seed_macro(conn: sqlite3.Connection, as_of: str, env: str = "中性") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ratings(date, kind, value, basis_json) VALUES(?, 'macro', ?, '{}')",
        (as_of, env),
    )
    conn.commit()


def _seed_stock(conn: sqlite3.Connection, symbol: str, industry: str) -> None:
    import datetime as dt

    rows = []
    start = dt.date(2025, 1, 1)
    for i in range(80):
        rows.append({
            "symbol": symbol,
            "date": (start + dt.timedelta(days=i)).isoformat(),
            "close": 10.0 + i * 0.02,
            "amount": 1e8,
            "src": "akshare",
        })
    upsert_df(conn, "daily_bars", pd.DataFrame(rows))
    conn.execute(
        """INSERT OR REPLACE INTO candidate_pool(symbol, level, industry, reason, in_date)
           VALUES(?, 'track', ?, 'seed', ?)""",
        (symbol, industry, AS_OF_NEW),
    )
    conn.commit()


def _news_fn(obj: str, obj_type: str, as_of: str) -> list[dict]:
    return [{
        "kind": "news",
        "source": "财联社",
        "url": f"https://example.com/{obj}",
        "published_at": "2026-08-25",
        "fetched_at": as_of,
        "summary": f"{obj} 近一周产业政策落地",
    }]


def _build_and_save(db_path: str, industries: list[str], as_of: str, **kwargs):
    from invest.evidence.factcards import build_industry_card, persist_card

    conn = connect(db_path)
    try:
        cards = []
        for ind in industries:
            card = build_industry_card(conn, ind, as_of=as_of, news_fn=_news_fn, **kwargs)
            persist_card(conn, card)
            cards.append(card)
        return cards
    finally:
        conn.close()


def test_schema_has_factcard_evidence_and_comparison_tables(db_path: str):
    assert "CREATE TABLE IF NOT EXISTS fact_cards" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS fact_evidence" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS comparison_records" in SCHEMA_SQL
    conn = connect(db_path)
    try:
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"fact_cards", "fact_evidence", "comparison_records"} <= names
        fact_cols = {r["name"] for r in conn.execute("PRAGMA table_info(fact_cards)")}
        assert {
            "obj_type", "obj", "as_of", "data_version", "rule_version",
            "dimensions_json", "missing_json",
        } <= fact_cols
        ev_cols = {r["name"] for r in conn.execute("PRAGMA table_info(fact_evidence)")}
        assert {"evidence_id", "card_id", "kind", "source", "url", "as_of", "summary"} <= ev_cols
        cmp_cols = {r["name"] for r in conn.execute("PRAGMA table_info(comparison_records)")}
        assert {"as_of", "peer_set_json", "conclusion", "notes"} <= cmp_cols
    finally:
        conn.close()


def test_industry_factcard_structure_and_missing_items(db_path: str):
    from invest.evidence.factcards import DIMENSIONS, build_industry_card

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.12, rotation_rank=2,
            pe_pct=0.35, crowding=0.55, crowding_state="正常",
            style="主力", cycle_phase="上行", main_net=1.2e8,
        )
        complete = build_industry_card(conn, "有色", as_of=AS_OF_NEW, news_fn=_news_fn)
        assert complete.obj_type == "industry"
        assert complete.obj == "有色"
        assert complete.as_of == AS_OF_NEW
        assert complete.rule_version
        assert complete.data_version
        assert set(DIMENSIONS) <= set(complete.dimensions)
        assert complete.missing == []
        assert complete.evidence
        assert all(item.source and item.evidence_id.startswith("EVID-") for item in complete.evidence)

        sparse = build_industry_card(conn, "煤炭", as_of=AS_OF_NEW, news_fn=_news_fn)
        assert "strength" in sparse.missing
        assert sparse.dimensions["strength"] is None
    finally:
        conn.close()


def test_factcard_does_not_emit_buy_sell_ranking(db_path: str):
    from invest.evidence.factcards import FORBIDDEN_RANK_KEYS as PROD_KEYS
    from invest.evidence.factcards import build_industry_card, discover_industries

    injected = {
        "buy_rank": 1,
        "sell_rank": 2,
        "trade_rank": 3,
        "score_rank": 4,
        "综合排名": 5,
        "买卖排名": 6,
        "ranking": 7,
    }

    def ranked_news(obj, obj_type, as_of):
        row = _news_fn(obj, obj_type, as_of)
        row[0] = {**row[0], **injected}
        return row

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW, env="收紧")
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.18, rotation_rank=1,
            pe_pct=0.2, crowding=0.9, crowding_state="拥挤",
            style="游资", cycle_phase="过热", main_net=3e8,
        )
        card = build_industry_card(conn, "有色", as_of=AS_OF_NEW, news_fn=ranked_news)
        dumped = json.dumps(card.to_dict(), ensure_ascii=False)
        for key in PROD_KEYS | FORBIDDEN_RANK_KEYS:
            assert f'"{key}"' not in dumped
        finder = discover_industries(conn, as_of=AS_OF_NEW)
        finder_dump = json.dumps(finder, ensure_ascii=False)
        for key in PROD_KEYS | FORBIDDEN_RANK_KEYS:
            assert f'"{key}"' not in finder_dump
        for item in finder["industries"]:
            assert "buy" not in item and "sell" not in item
    finally:
        conn.close()


def test_factcard_reproducible_by_as_of(db_path: str):
    from invest.evidence.factcards import build_industry_card, load_card, persist_card

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_OLD)
        _seed_industry(
            conn, "有色", as_of=AS_OF_OLD, rs=0.05, rotation_rank=8,
            pe_pct=0.6, crowding=0.3, crowding_state="正常",
            style="主力", cycle_phase="筑底", main_net=-1e8, pe=28.0,
        )
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.15, rotation_rank=2,
            pe_pct=0.4, crowding=0.7, crowding_state="偏拥挤",
            style="游资", cycle_phase="上行", main_net=2e8, pe=22.0,
        )
        old = build_industry_card(conn, "有色", as_of=AS_OF_OLD, news_fn=_news_fn)
        new = build_industry_card(conn, "有色", as_of=AS_OF_NEW, news_fn=_news_fn)
        assert old.dimensions["strength"]["rs"] == pytest.approx(0.05)
        assert new.dimensions["strength"]["rs"] == pytest.approx(0.15)
        persist_card(conn, old)
        persist_card(conn, new)
        loaded_old = load_card(conn, "industry", "有色", AS_OF_OLD)
        rebuilt = build_industry_card(conn, "有色", as_of=AS_OF_OLD, news_fn=_news_fn)
        assert loaded_old.as_of == AS_OF_OLD
        assert rebuilt.dimensions["strength"]["rs"] == loaded_old.dimensions["strength"]["rs"]
        assert rebuilt.rule_version == loaded_old.rule_version
    finally:
        conn.close()


def test_factcard_as_of_reproduces_all_dimensions_not_just_rs(db_path: str):
    """as_of 复现必须锁住轮动/估值/拥挤/资金等带日期维度，不能只比对 RS。"""
    from invest.evidence.factcards import build_industry_card, load_card, persist_card

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_OLD, env="收紧")
        _seed_macro(conn, AS_OF_NEW, env="宽松")
        _seed_industry(
            conn, "有色", as_of=AS_OF_OLD, rs=0.05, rotation_rank=8,
            pe_pct=0.6, crowding=0.3, crowding_state="正常",
            style="主力", cycle_phase="筑底", main_net=-1e8, pe=28.0,
        )
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.15, rotation_rank=2,
            pe_pct=0.4, crowding=0.7, crowding_state="偏拥挤",
            style="游资", cycle_phase="上行", main_net=2e8, pe=22.0,
        )
        old = build_industry_card(conn, "有色", as_of=AS_OF_OLD, news_fn=_news_fn)
        new = build_industry_card(conn, "有色", as_of=AS_OF_NEW, news_fn=_news_fn)
        persist_card(conn, old)
        persist_card(conn, new)
        loaded_old = load_card(conn, "industry", "有色", AS_OF_OLD)
        rebuilt = build_industry_card(conn, "有色", as_of=AS_OF_OLD, news_fn=_news_fn)

        dated = ("strength", "rotation", "valuation", "crowding", "capital")
        for dim in dated:
            assert old.dimensions.get(dim), f"旧卡缺 {dim}"
            assert new.dimensions.get(dim), f"新卡缺 {dim}"
            assert old.dimensions[dim] != new.dimensions[dim], f"{dim} 未随 as_of 变化"
            assert rebuilt.dimensions[dim] == loaded_old.dimensions[dim], f"{dim} 无法按 as_of 复现"
        assert rebuilt.dimensions["rotation"]["rank"] == 8
        assert new.dimensions["rotation"]["rank"] == 2
        assert rebuilt.dimensions["crowding"]["crowding_state"] == "正常"
        assert new.dimensions["crowding"]["crowding_state"] == "偏拥挤"
        assert rebuilt.dimensions["capital"]["style"] == "主力"
        assert new.dimensions["capital"]["style"] == "游资"
        assert rebuilt.dimensions["macro"]["env"] == "收紧"
        assert new.dimensions["macro"]["env"] == "宽松"
        assert rebuilt.rule_version == loaded_old.rule_version
        assert rebuilt.data_version == loaded_old.data_version
    finally:
        conn.close()


def test_workbench_empty_as_of_resolves_latest_card_date(db_path: str):
    """工作台空 as_of 必须落到已落库最新时点，不能默认今天或混读全部历史。"""
    from dashboard import queries as q
    from invest.evidence.factcards import build_industry_card, persist_card

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_OLD)
        _seed_macro(conn, AS_OF_NEW)
        for as_of, rs, rank in ((AS_OF_OLD, 0.05, 8), (AS_OF_NEW, 0.15, 2)):
            _seed_industry(
                conn, "有色", as_of=as_of, rs=rs, rotation_rank=rank,
                pe_pct=0.4, crowding=0.4, crowding_state="正常",
                style="主力", cycle_phase="上行", main_net=1e8,
            )
            persist_card(
                conn,
                build_industry_card(conn, "有色", as_of=as_of, news_fn=_news_fn),
            )
    finally:
        conn.close()

    assert q.resolve_workbench_as_of(db_path, None) == AS_OF_NEW
    assert q.resolve_workbench_as_of(db_path, "") == AS_OF_NEW
    assert q.resolve_workbench_as_of(db_path, "  ") == AS_OF_NEW
    assert q.resolve_workbench_as_of(db_path, AS_OF_OLD) == AS_OF_OLD
    cards = q.load_fact_cards(db_path, as_of=q.resolve_workbench_as_of(db_path, None))
    assert not cards.empty
    assert set(cards["as_of"].unique()) == {AS_OF_NEW}


def test_workbench_empty_as_of_on_empty_db_is_iso_date(db_path: str):
    import datetime as dt

    from dashboard import queries as q

    resolved = q.resolve_workbench_as_of(db_path, None)
    assert resolved == dt.date.today().isoformat()


def test_workbench_page_uses_resolved_as_of_not_today():
    text = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert "resolve_workbench_as_of" in text
    assert "date.today().isoformat()" not in text


def test_human_comparison_record_stores_peer_set_and_conclusion(db_path: str):
    from invest.evidence.factcards import load_comparison, record_comparison

    conn = connect(db_path)
    try:
        rec = record_comparison(
            conn,
            as_of=AS_OF_NEW,
            peer_set=["有色", "煤炭", "钢铁"],
            conclusion="有色相对拥挤，煤炭估值更低",
            notes="人工并排后倾向煤炭",
        )
        assert rec["id"]
        loaded = load_comparison(conn, rec["id"])
        assert loaded["peer_set"] == ["有色", "煤炭", "钢铁"]
        assert loaded["conclusion"] == "有色相对拥挤，煤炭估值更低"
        assert loaded["as_of"] == AS_OF_NEW
        assert loaded["data_version"]
        assert loaded["rule_version"]
    finally:
        conn.close()


def test_discoverer_then_deep_dive_scope(db_path: str):
    from invest.evidence.factcards import (
        MAX_DEEP_INDUSTRIES,
        MAX_DEEP_STOCKS,
        deep_dive,
        discover_industries,
    )

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        for i, name in enumerate(["有色", "煤炭", "钢铁", "化工", "银行", "电子"]):
            _seed_industry(
                conn, name, as_of=AS_OF_NEW, rs=0.2 - i * 0.03, rotation_rank=i + 1,
                pe_pct=0.3 + i * 0.05, crowding=0.4, crowding_state="正常",
                style="主力", cycle_phase="上行", main_net=1e8,
            )
        for n in range(21):
            _seed_stock(conn, f"6000{n:02d}"[:6] if n < 10 else f"600{n:03d}"[:6], "有色")
        symbols = [r["symbol"] for r in conn.execute(
            "SELECT symbol FROM candidate_pool WHERE out_date IS NULL"
        )]

        finder = discover_industries(conn, as_of=AS_OF_NEW)
        names = [x["obj"] for x in finder["industries"]]
        assert names[0] == "有色"
        assert set(names) >= {"有色", "煤炭", "钢铁"}

        with pytest.raises(ValueError, match="3–5|最多 5"):
            deep_dive(conn, industries=names[:6], as_of=AS_OF_NEW, news_fn=_news_fn)
        with pytest.raises(ValueError, match="20"):
            deep_dive(
                conn, industries=["有色", "煤炭", "钢铁"],
                symbols=symbols[:21], as_of=AS_OF_NEW, news_fn=_news_fn,
            )
        result = deep_dive(
            conn,
            industries=["有色", "煤炭", "钢铁"],
            symbols=symbols[:20],
            as_of=AS_OF_NEW,
            news_fn=_news_fn,
        )
        assert len(result["industry_cards"]) == 3
        assert len(result["stock_cards"]) <= MAX_DEEP_STOCKS
        assert MAX_DEEP_INDUSTRIES == 5
        assert all(c.obj_type == "industry" for c in result["industry_cards"])
        assert all(c.obj_type == "stock" for c in result["stock_cards"])
    finally:
        conn.close()


def test_ai_news_requires_source_and_is_not_a_ranking(db_path: str):
    from invest.evidence.factcards import build_industry_card

    def bad_news(obj, obj_type, as_of):
        return [
            {"kind": "news", "source": "", "summary": "无来源应丢弃", "url": ""},
            {
                "kind": "sentiment",
                "source": "雪球",
                "url": "https://xueqiu.com/x",
                "published_at": "2026-08-24",
                "summary": f"{obj} 社区讨论升温",
                "buy_rank": 1,
            },
        ]

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.1, rotation_rank=3,
            pe_pct=0.4, crowding=0.5, crowding_state="正常",
            style="主力", cycle_phase="上行", main_net=1e8,
        )
        card = build_industry_card(conn, "有色", as_of=AS_OF_NEW, news_fn=bad_news)
        kinds = {e.kind for e in card.evidence if e.kind in {"news", "announcement", "sentiment"}}
        assert "sentiment" in kinds
        assert all(e.source for e in card.evidence if e.kind in {"news", "announcement", "sentiment"})
        dumped = json.dumps(card.to_dict(), ensure_ascii=False)
        assert "buy_rank" not in dumped
    finally:
        conn.close()


def test_dashboard_queries_filter_side_by_side_and_promote(db_path: str):
    from dashboard import queries as q
    from invest.evidence.factcards import record_comparison

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        for name, crowd in (("有色", 0.92), ("煤炭", 0.31), ("钢铁", 0.55)):
            _seed_industry(
                conn, name, as_of=AS_OF_NEW, rs=0.1, rotation_rank=2,
                pe_pct=0.4, crowding=crowd,
                crowding_state="拥挤" if crowd > 0.8 else "正常",
                style="主力", cycle_phase="上行", main_net=1e8,
            )
        _seed_stock(conn, "600519", "白酒")
    finally:
        conn.close()

    _build_and_save(db_path, ["有色", "煤炭", "钢铁"], AS_OF_NEW)
    cards = q.load_fact_cards(db_path, as_of=AS_OF_NEW)
    assert len(cards) == 3
    assert {"obj", "as_of", "missing_json", "dimensions_json"} <= set(cards.columns)
    crowded = q.load_fact_cards(db_path, as_of=AS_OF_NEW, dimension="crowding")
    assert not crowded.empty
    detail = q.load_fact_card_detail(db_path, int(cards.iloc[0]["id"]))
    assert detail["obj"]
    evidence = q.load_fact_evidence(db_path, int(cards.iloc[0]["id"]))
    assert not evidence.empty
    assert evidence.iloc[0]["evidence_id"].startswith("EVID-")

    conn = connect(db_path)
    try:
        record_comparison(
            conn, as_of=AS_OF_NEW, peer_set=["有色", "煤炭"],
            conclusion="煤炭更便宜", notes="",
        )
    finally:
        conn.close()
    comps = q.load_comparisons(db_path)
    assert comps.iloc[0]["conclusion"] == "煤炭更便宜"
    saved = q.save_comparison(
        db_path, as_of=AS_OF_NEW, peer_set=["有色", "钢铁"],
        conclusion="钢铁更稳", notes="工作台",
    )
    assert saved["id"]

    promoted = q.promote_factcard_to_pool(db_path, "600519", industry="白酒", reason="人工比价")
    assert promoted["symbol"] == "600519"
    card_row = q.promote_factcard_to_card(
        db_path, "600519", thesis="人工比价后认为中期赔率可接受，先观察",
    )
    assert card_row["status"] == "candidate"


def test_dashboard_mid_compare_page_is_wired():
    text = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert "中期比价" in text
    assert "page_mid_compare" in text
    queries = (ROOT / "dashboard" / "queries.py").read_text(encoding="utf-8")
    assert "load_fact_cards" in queries
    assert "save_comparison" in queries


def test_important_change_push_includes_evidence_ids(db_path: str):
    from invest.evidence.factcards import (
        detect_important_changes,
        format_change_digest,
        persist_card,
        run_factcard_refresh,
    )
    from invest.scheduler import JobResult

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_OLD)
        _seed_industry(
            conn, "有色", as_of=AS_OF_OLD, rs=0.04, rotation_rank=9,
            pe_pct=0.7, crowding=0.2, crowding_state="正常",
            style="主力", cycle_phase="筑底", main_net=-5e7,
        )
        _seed_macro(conn, AS_OF_NEW, env="收紧")
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.16, rotation_rank=1,
            pe_pct=0.35, crowding=0.88, crowding_state="拥挤",
            style="游资", cycle_phase="过热", main_net=4e8,
        )
        from invest.evidence.factcards import build_industry_card

        persist_card(conn, build_industry_card(conn, "有色", as_of=AS_OF_OLD, news_fn=_news_fn))
        persist_card(conn, build_industry_card(conn, "有色", as_of=AS_OF_NEW, news_fn=_news_fn))
        changes = detect_important_changes(conn, as_of=AS_OF_NEW)
        assert changes
        digest = format_change_digest(changes)
        assert "有色" in digest
        assert "EVID-" in digest
        sent: list[str] = []

        class _FakeNotifier:
            def send_text(self, content, **kwargs):
                sent.append(content)
                assert kwargs.get("message_kind") == "alert"
                return {"feishu": True}

        result = run_factcard_refresh(
            db_path, conn, as_of=AS_OF_NEW, push=True,
            news_fn=_news_fn, notifier=_FakeNotifier(),
        )
        assert isinstance(result, JobResult)
        assert result.success
        assert sent and "EVID-" in sent[0]
        assert "综合买卖" not in sent[0] and "全市场排名" not in sent[0]
    finally:
        conn.close()


def test_no_important_change_does_not_push_market_verdict(db_path: str):
    from invest.evidence.factcards import persist_card, run_factcard_refresh

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        _seed_industry(
            conn, "银行", as_of=AS_OF_NEW, rs=0.02, rotation_rank=10,
            pe_pct=0.5, crowding=0.4, crowding_state="正常",
            style="主力", cycle_phase="震荡", main_net=1e7,
        )
        from invest.evidence.factcards import build_industry_card
        persist_card(conn, build_industry_card(conn, "银行", as_of=AS_OF_NEW, news_fn=_news_fn))
        notifier = mock.Mock()
        result = run_factcard_refresh(
            db_path, conn, as_of=AS_OF_NEW, push=True,
            news_fn=_news_fn, notifier=notifier,
        )
        assert result.success
        assert "无重要变化" in result.detail
        notifier.send_text.assert_not_called()
    finally:
        conn.close()


def test_news_without_published_at_is_rejected(db_path: str):
    from invest.evidence.factcards import build_industry_card

    def no_date(obj, obj_type, as_of):
        return [{
            "kind": "news",
            "source": "财联社",
            "url": "https://example.com/nodate",
            "summary": f"{obj} 无发布日不得当近3-7日收下",
        }]

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.1, rotation_rank=3,
            pe_pct=0.4, crowding=0.5, crowding_state="正常",
            style="主力", cycle_phase="上行", main_net=1e8,
        )
        card = build_industry_card(conn, "有色", as_of=AS_OF_NEW, news_fn=no_date)
        news = [e for e in card.evidence if e.kind in {"news", "announcement", "sentiment"}]
        assert news == []
    finally:
        conn.close()


def test_factcard_refresh_production_entry_calls_extractor(db_path: str, monkeypatch):
    from invest.evidence import factcards as fc
    from invest.scheduler import _factcard_refresh

    called: list[tuple[str, str, str]] = []

    def fake_extract(obj: str, obj_type: str, as_of: str):
        called.append((obj, obj_type, as_of))
        return _news_fn(obj, obj_type, as_of)

    monkeypatch.setattr(fc, "extract_recent_facts", fake_extract)

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.1, rotation_rank=2,
            pe_pct=0.4, crowding=0.5, crowding_state="正常",
            style="主力", cycle_phase="上行", main_net=1e8,
        )
        result = _factcard_refresh(db_path, conn)
        assert result.success
        assert called
        assert all(item[1] == "industry" and item[2] for item in called)
        assert any(item[0] == "有色" for item in called)
    finally:
        conn.close()


def test_news_extract_failure_records_missing_not_fabricated(db_path: str):
    from invest.evidence.factcards import build_industry_card

    def boom(obj, obj_type, as_of):
        raise RuntimeError("llm down")

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.1, rotation_rank=2,
            pe_pct=0.4, crowding=0.5, crowding_state="正常",
            style="主力", cycle_phase="上行", main_net=1e8,
        )
        card = build_industry_card(conn, "有色", as_of=AS_OF_NEW, news_fn=boom)
        assert "news" in card.missing
        news = [e for e in card.evidence if e.kind in {"news", "announcement", "sentiment"}]
        assert news == []
    finally:
        conn.close()


def test_deep_dive_defaults_to_pool_and_persists(db_path: str):
    from invest.evidence.factcards import deep_dive, load_card

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        for i, name in enumerate(["有色", "煤炭", "钢铁"]):
            _seed_industry(
                conn, name, as_of=AS_OF_NEW, rs=0.1 - i * 0.01, rotation_rank=i + 1,
                pe_pct=0.4, crowding=0.4, crowding_state="正常",
                style="主力", cycle_phase="上行", main_net=1e8,
            )
        _seed_stock(conn, "600111", "有色")
        _seed_stock(conn, "601088", "煤炭")
        result = deep_dive(conn, industries=["有色", "煤炭", "钢铁"], as_of=AS_OF_NEW, news_fn=_news_fn)
        assert {c.obj for c in result["stock_cards"]} == {"600111", "601088"}
        assert load_card(conn, "industry", "有色", AS_OF_NEW) is not None
        assert load_card(conn, "stock", "600111", AS_OF_NEW) is not None
    finally:
        conn.close()


def test_deep_dive_evidence_ids_unique_and_query_keeps_first_card(db_path: str):
    from dashboard import queries as q
    from invest.agent.tools import query_evidence
    from invest.evidence.factcards import deep_dive

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        for i, name in enumerate(["有色", "煤炭", "钢铁"]):
            _seed_industry(
                conn, name, as_of=AS_OF_NEW, rs=0.1 - i * 0.01, rotation_rank=i + 1,
                pe_pct=0.4, crowding=0.4, crowding_state="正常",
                style="主力", cycle_phase="上行", main_net=1e8,
            )
        _seed_stock(conn, "600111", "有色")
        result = deep_dive(
            conn, industries=["有色", "煤炭", "钢铁"], as_of=AS_OF_NEW, news_fn=_news_fn,
        )
        cards = result["industry_cards"] + result["stock_cards"]
        assert len(cards) >= 2
        ids_by_card = [[item.evidence_id for item in card.evidence] for card in cards]
        all_ids = [eid for eids in ids_by_card for eid in eids]
        assert all_ids
        assert len(all_ids) == len(set(all_ids))
        first = cards[0]
        first_eid = first.evidence[0].evidence_id
        found = query_evidence(conn, evidence_id=first_eid)
        assert found["evidence_id"] == first_eid
        assert found["obj"] == first.obj
        assert found["card_id"] == first.card_id
        dash = q.find_evidence(db_path, first_eid)
        assert dash["evidence_id"] == first_eid
        assert dash["obj"] == first.obj
        assert dash["card_id"] == first.card_id
    finally:
        conn.close()


def test_refresh_then_deep_dive_other_keeps_first_card_evidence(db_path: str):
    """同 as_of 已有卡后再深查：不得复用批号抢走先写卡证据。"""
    from invest.agent.tools import query_evidence
    from invest.evidence.factcards import deep_dive, load_card, run_factcard_refresh

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.12, rotation_rank=1,
            pe_pct=0.4, crowding=0.4, crowding_state="正常",
            style="主力", cycle_phase="上行", main_net=1e8,
        )
        # 先落库再 refresh：persist DELETE+INSERT，行数不变但编号已抬升
        run_factcard_refresh(db_path, conn, as_of=AS_OF_NEW, push=False, news_fn=_news_fn)
        run_factcard_refresh(db_path, conn, as_of=AS_OF_NEW, push=False, news_fn=_news_fn)
        first = load_card(conn, "industry", "有色", AS_OF_NEW)
        assert first is not None and first.evidence
        first_eids = [item.evidence_id for item in first.evidence]
        first_eid = first_eids[0]
        first_card_id = first.card_id

        for i, name in enumerate(["煤炭", "钢铁", "银行"]):
            _seed_industry(
                conn, name, as_of=AS_OF_NEW, rs=0.1 - i * 0.01, rotation_rank=i + 2,
                pe_pct=0.4, crowding=0.4, crowding_state="正常",
                style="主力", cycle_phase="上行", main_net=1e8,
            )
        _seed_stock(conn, "601088", "煤炭")
        result = deep_dive(
            conn, industries=["煤炭", "钢铁", "银行"], as_of=AS_OF_NEW, news_fn=_news_fn,
        )
        later = result["industry_cards"] + result["stock_cards"]
        later_eids = [item.evidence_id for card in later for item in card.evidence]
        assert later_eids
        assert set(first_eids).isdisjoint(later_eids)
        found = query_evidence(conn, evidence_id=first_eid)
        assert found["evidence_id"] == first_eid
        assert found["obj"] == "有色"
        assert found["card_id"] == first_card_id
    finally:
        conn.close()


def test_stock_card_truncates_at_as_of(db_path: str):
    from invest.evidence.factcards import build_stock_card

    conn = connect(db_path)
    try:
        _seed_stock(conn, "600519", "白酒")
        later = [
            {"symbol": "600519", "date": "2026-08-26", "close": 999.0, "amount": 1e8, "src": "akshare"},
            {"symbol": "600519", "date": "2026-08-27", "close": 1001.0, "amount": 1e8, "src": "akshare"},
        ]
        upsert_df(conn, "daily_bars", pd.DataFrame(later))
        old = build_stock_card(conn, "600519", as_of=AS_OF_OLD, news_fn=_news_fn)
        new = build_stock_card(conn, "600519", as_of=AS_OF_NEW, news_fn=_news_fn)
        old_px = (old.dimensions.get("spread") or {}).get("current")
        new_px = (new.dimensions.get("spread") or {}).get("current")
        assert old_px is not None
        assert old_px != pytest.approx(1001.0)
        assert old_px < 50
        assert new_px == pytest.approx(1001.0)
    finally:
        conn.close()


def test_dashboard_deep_dive_rejects_bad_scope(db_path: str):
    from dashboard import queries as q

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        for i, name in enumerate(["有色", "煤炭", "钢铁", "化工", "银行", "电子"]):
            _seed_industry(
                conn, name, as_of=AS_OF_NEW, rs=0.1, rotation_rank=i + 1,
                pe_pct=0.4, crowding=0.4, crowding_state="正常",
                style="主力", cycle_phase="上行", main_net=1e8,
            )
        for n in range(21):
            _seed_stock(conn, f"600{n:03d}", "有色")
    finally:
        conn.close()

    with pytest.raises(ValueError, match="3–5|最多 5"):
        q.run_deep_dive(db_path, industries=["有色", "煤炭"], as_of=AS_OF_NEW)
    with pytest.raises(ValueError, match="3–5|最多 5"):
        q.run_deep_dive(
            db_path,
            industries=["有色", "煤炭", "钢铁", "化工", "银行", "电子"],
            as_of=AS_OF_NEW,
        )
    with pytest.raises(ValueError, match="20"):
        q.run_deep_dive(
            db_path,
            industries=["有色", "煤炭", "钢铁"],
            as_of=AS_OF_NEW,
            symbols=[f"600{n:03d}" for n in range(21)],
        )


def test_dashboard_deep_dive_persists_selected_scope(db_path: str):
    from dashboard import queries as q
    from invest.evidence.factcards import load_card

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        for i, name in enumerate(["有色", "煤炭", "钢铁"]):
            _seed_industry(
                conn, name, as_of=AS_OF_NEW, rs=0.1, rotation_rank=i + 1,
                pe_pct=0.4, crowding=0.4, crowding_state="正常",
                style="主力", cycle_phase="上行", main_net=1e8,
            )
        _seed_stock(conn, "600111", "有色")
    finally:
        conn.close()

    result = q.run_deep_dive(
        db_path, industries=["有色", "煤炭", "钢铁"], as_of=AS_OF_NEW, news_fn=_news_fn,
    )
    assert len(result["industry_cards"]) == 3
    assert [c.obj for c in result["stock_cards"]] == ["600111"]
    conn = connect(db_path)
    try:
        assert load_card(conn, "industry", "钢铁", AS_OF_NEW) is not None
        assert load_card(conn, "stock", "600111", AS_OF_NEW) is not None
    finally:
        conn.close()


def test_dashboard_mid_compare_page_has_deep_dive_and_evidence_search():
    text = (ROOT / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert "run_deep_dive" in text
    assert "深查" in text
    queries = (ROOT / "dashboard" / "queries.py").read_text(encoding="utf-8")
    assert "run_deep_dive" in queries
    assert "find_evidence" in queries


def test_query_evidence_by_id_and_dashboard_search(db_path: str):
    from dashboard import queries as q
    from invest.agent.tools import TOOL_SCHEMAS, build_dispatch, query_evidence
    from invest.evidence.factcards import build_industry_card, persist_card

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.1, rotation_rank=2,
            pe_pct=0.4, crowding=0.5, crowding_state="正常",
            style="主力", cycle_phase="上行", main_net=1e8,
        )
        card = build_industry_card(conn, "有色", as_of=AS_OF_NEW, news_fn=_news_fn)
        persist_card(conn, card)
        eid = next(e.evidence_id for e in card.evidence if e.kind == "news")
        found = query_evidence(conn, evidence_id=eid)
        assert found["evidence_id"] == eid
        assert found["summary"]
        assert found["source"] == "财联社"
        assert found["obj"] == "有色"
        assert found["card_id"]
        names = [t["function"]["name"] for t in TOOL_SCHEMAS]
        assert "query_evidence" in names
        dispatched = build_dispatch(conn)["query_evidence"](evidence_id=eid)
        assert dispatched["evidence_id"] == eid
        assert "error" in query_evidence(conn, evidence_id="EVID-19990101-9999")
    finally:
        conn.close()

    dash = q.find_evidence(db_path, eid)
    assert dash["evidence_id"] == eid
    assert dash["source"] == "财联社"
    assert dash["obj"] == "有色"


def test_pushed_evidence_ids_are_queryable(db_path: str):
    from invest.agent.tools import query_evidence
    from invest.evidence.factcards import build_industry_card, persist_card, run_factcard_refresh

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_OLD)
        _seed_industry(
            conn, "有色", as_of=AS_OF_OLD, rs=0.04, rotation_rank=9,
            pe_pct=0.7, crowding=0.2, crowding_state="正常",
            style="主力", cycle_phase="筑底", main_net=-5e7,
        )
        _seed_macro(conn, AS_OF_NEW, env="收紧")
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.16, rotation_rank=1,
            pe_pct=0.35, crowding=0.88, crowding_state="拥挤",
            style="游资", cycle_phase="过热", main_net=4e8,
        )
        persist_card(conn, build_industry_card(conn, "有色", as_of=AS_OF_OLD, news_fn=_news_fn))
        sent: list[str] = []

        class _FakeNotifier:
            def send_text(self, content, **kwargs):
                sent.append(content)
                return {"feishu": True}

        result = run_factcard_refresh(
            db_path, conn, as_of=AS_OF_NEW, push=True,
            news_fn=_news_fn, notifier=_FakeNotifier(),
        )
        assert result.success
        assert sent
        import re
        ids = re.findall(r"EVID-\d{8}-\d{4}", sent[0])
        assert ids
        looked = query_evidence(conn, evidence_id=ids[0])
        assert looked["evidence_id"] == ids[0]
        assert looked["summary"]
        assert looked["source"]
        assert looked["obj"] == "有色"
    finally:
        conn.close()


def test_refresh_then_same_industry_deep_dive_keeps_pushed_evidence(db_path: str):
    """同一 (card_id, as_of) 再 refresh/deep_dive：原推送 EVID 仍指向同一 obj。"""
    from invest.agent.tools import query_evidence
    from invest.evidence.factcards import deep_dive, load_card, run_factcard_refresh

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        for i, name in enumerate(["有色", "煤炭", "钢铁"]):
            _seed_industry(
                conn, name, as_of=AS_OF_NEW, rs=0.12 - i * 0.01, rotation_rank=i + 1,
                pe_pct=0.4, crowding=0.4, crowding_state="正常",
                style="主力", cycle_phase="上行", main_net=1e8,
            )
        result = run_factcard_refresh(
            db_path, conn, as_of=AS_OF_NEW, push=False, news_fn=_news_fn,
        )
        assert result.success
        first = load_card(conn, "industry", "有色", AS_OF_NEW)
        assert first is not None and first.evidence
        pushed = [e.evidence_id for e in first.evidence]
        assert pushed
        deep_dive(
            conn, industries=["有色", "煤炭", "钢铁"], as_of=AS_OF_NEW, news_fn=_news_fn,
        )
        for eid in pushed:
            found = query_evidence(conn, evidence_id=eid)
            assert found["evidence_id"] == eid
            assert found["obj"] == "有色"
            assert found["card_id"] == first.card_id
    finally:
        conn.close()


def test_persist_unique_conflict_does_not_steal_other_card_id(db_path: str):
    """他卡已占用的 evidence_id 不得被 INSERT OR REPLACE 改挂。"""
    from invest.agent.tools import query_evidence
    from invest.evidence.factcards import build_industry_card, persist_card

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.12, rotation_rank=1,
            pe_pct=0.4, crowding=0.4, crowding_state="正常",
            style="主力", cycle_phase="上行", main_net=1e8,
        )
        _seed_industry(
            conn, "煤炭", as_of=AS_OF_NEW, rs=0.08, rotation_rank=2,
            pe_pct=0.4, crowding=0.4, crowding_state="正常",
            style="主力", cycle_phase="上行", main_net=1e8,
        )
        first = build_industry_card(conn, "有色", as_of=AS_OF_NEW, news_fn=_news_fn)
        persist_card(conn, first)
        stolen = first.evidence[0].evidence_id
        first_card_id = first.card_id
        later = build_industry_card(conn, "煤炭", as_of=AS_OF_NEW, news_fn=_news_fn)
        for item in later.evidence:
            item.evidence_id = stolen
        persist_card(conn, later)
        found = query_evidence(conn, evidence_id=stolen)
        assert found["evidence_id"] == stolen
        assert found["obj"] == "有色"
        assert found["card_id"] == first_card_id
        later_ids = [item.evidence_id for item in later.evidence]
        assert stolen not in later_ids
        assert all(eid != stolen for eid in later_ids)
    finally:
        conn.close()


def test_extract_recent_facts_requires_hit_url(monkeypatch):
    """LLM 自填日期/来源/URL 对不上检索 hit 则丢弃；对上的用 hit 的来源键。"""
    from invest.evidence import factcards as fc

    hit_url = "https://www.cls.cn/detail/hit-1"
    hits = [{
        "kind": "news",
        "source": "财联社",
        "url": hit_url,
        "published_at": "2026-08-25",
        "fetched_at": AS_OF_NEW,
        "summary": "检索命中",
        "title": "检索命中",
    }]
    monkeypatch.setattr(fc, "_collect_web_hits", lambda obj, as_of: hits)
    monkeypatch.setattr(fc, "_telegraph_industry_facts", lambda obj, as_of, max_items=5: [])
    monkeypatch.setattr(
        fc, "_llm_refine_news",
        lambda obj, as_of, raw: [
            {
                "kind": "news",
                "source": "胡编日报",
                "url": hit_url,
                "published_at": "2019-01-01",
                "summary": "日期来源都是模型自填，但 url 对得上",
            },
            {
                "kind": "news",
                "source": "财联社",
                "url": "https://fake.example/invented",
                "published_at": "2026-08-24",
                "summary": "url 对不上检索",
            },
            {
                "kind": "news",
                "source": "财联社",
                "url": "",
                "published_at": "2026-08-23",
                "summary": "无 url 也对不上",
            },
        ],
    )
    out = fc.extract_recent_facts("有色", "industry", AS_OF_NEW)
    assert [row["url"] for row in out] == [hit_url]
    assert out[0]["source"] == "财联社"
    assert out[0]["published_at"] == "2026-08-25"


def test_snippet_prefix_date_without_published_at_does_not_enter_facts(monkeypatch):
    """只有 snippet 前缀日期、无 published_at 字段的 hit 不得进入事实；引擎发布日在窗口内仍可入。"""
    from invest.agent import web_tools
    from invest.evidence import factcards as fc

    snippet_url = "https://www.cls.cn/detail/old-snippet"
    dated_url = "https://www.cls.cn/detail/engine-date"

    def fake_search(query, n=5):
        return [
            {
                "title": "有色旧闻",
                "url": snippet_url,
                "snippet": "2026-08-26 某旧文摘要前缀，不是引擎发布日",
            },
            {
                "title": "有色新讯",
                "url": dated_url,
                "snippet": "产业政策落地",
                "published_at": "2026-08-25",
            },
        ]

    monkeypatch.setattr(web_tools, "web_search", fake_search)
    monkeypatch.setattr(fc, "_telegraph_industry_facts", lambda obj, as_of, max_items=5: [])
    monkeypatch.setattr(fc, "_llm_refine_news", lambda obj, as_of, raw: list(raw))
    out = fc.extract_recent_facts("有色", "industry", AS_OF_NEW)
    urls = [row["url"] for row in out]
    assert snippet_url not in urls
    assert dated_url in urls
    assert all(row["published_at"] == "2026-08-25" for row in out if row["url"] == dated_url)


def test_snippet_only_search_hits_record_missing_news(db_path: str, monkeypatch):
    """无引擎/页面级发布日时不得用 snippet 顶发布日，事实卡记 missing news。"""
    from invest.agent import web_tools
    from invest.evidence.factcards import build_industry_card, extract_recent_facts

    def fake_search(query, n=5):
        return [{
            "title": "有色旧闻",
            "url": "https://www.cls.cn/detail/old-snippet",
            "snippet": "2026-08-26 某旧文摘要前缀，不是引擎发布日",
        }]

    monkeypatch.setattr(web_tools, "web_search", fake_search)
    monkeypatch.setattr(
        "invest.evidence.factcards._telegraph_industry_facts",
        lambda obj, as_of, max_items=5: [],
    )
    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.1, rotation_rank=2,
            pe_pct=0.4, crowding=0.5, crowding_state="正常",
            style="主力", cycle_phase="上行", main_net=1e8,
        )
        card = build_industry_card(
            conn, "有色", as_of=AS_OF_NEW, news_fn=extract_recent_facts,
        )
        news = [e for e in card.evidence if e.kind in {"news", "announcement", "sentiment"}]
        assert news == []
        assert "news" in card.missing
    finally:
        conn.close()


def test_telegraph_facts_preferred_over_web(monkeypatch):
    """2026-08-31：财联社电报命中时优先返回电报事实，不再走 web 检索。"""
    from invest.evidence import factcards as fc

    telegraph_hit = [{
        "kind": "news",
        "source": "财联社",
        "url": "",
        "published_at": "2026-08-26",
        "fetched_at": AS_OF_NEW,
        "summary": "有色行业政策落地",
    }]
    monkeypatch.setattr(fc, "_telegraph_industry_facts", lambda obj, as_of, max_items=5: telegraph_hit)
    called = {"n": 0}
    monkeypatch.setattr(fc, "_collect_web_hits", lambda obj, as_of: called.__setitem__("n", called["n"] + 1) or [])
    out = fc.extract_recent_facts("有色", "industry", AS_OF_NEW)
    assert out == telegraph_hit
    assert called["n"] == 0  # 电报命中时不触发 web 检索


def test_telegraph_industry_facts_matches_keyword_and_window(monkeypatch):
    """电报快讯：主题关键词命中 + 发布日期在 3-7 日窗口内才进入；无命中返回 []。
    财联社为兜底源（东财快讯为空时启用）。"""
    import pandas as pd

    from invest.evidence import factcards as fc

    df = pd.DataFrame([
        {"标题": "半导体龙头获大基金增持，产业链景气回升", "内容": "", "发布日期": "2026-08-26", "发布时间": "09:30:00"},
        {"标题": "半导体设备招标放量", "内容": "国产替代加速", "发布日期": "2026-08-15", "发布时间": "10:00:00"},
        {"标题": "白酒动销数据验证中", "内容": "渠道库存去化", "发布日期": "2026-08-26", "发布时间": "11:00:00"},
    ])
    empty = pd.DataFrame(columns=["标题", "内容", "发布日期", "发布时间"])
    fc._telegraph_cache.clear()
    try:
        monkeypatch.setattr("akshare.stock_info_global_em", lambda: empty)
        monkeypatch.setattr("akshare.stock_info_global_cls", lambda: df)
        out = fc._telegraph_industry_facts("半导体", AS_OF_NEW)
        assert len(out) == 1  # 8-15 超出 7 日窗口被丢弃，白酒不命中关键词
        assert out[0]["source"] == "财联社"
        assert out[0]["published_at"] == "2026-08-26"
        assert "半导体" in out[0]["summary"]
        assert fc._telegraph_industry_facts("医疗器械", AS_OF_NEW) == []  # 无关键词命中
    finally:
        fc._telegraph_cache.clear()


def test_telegraph_industry_facts_prefers_em_source(monkeypatch):
    """2026-08-31：东财全球财经快讯优先（本机实测覆盖更全），来源标注东财快讯。"""
    import pandas as pd

    from invest.evidence import factcards as fc

    em = pd.DataFrame([
        {"标题": "半导体产业链午后拉升", "摘要": "设备/材料方向领涨", "发布时间": "2026-08-26 14:30:00", "链接": ""},
    ])
    fc._telegraph_cache.clear()
    try:
        monkeypatch.setattr("akshare.stock_info_global_em", lambda: em)
        monkeypatch.setattr("akshare.stock_info_global_cls", lambda: pd.DataFrame())
        out = fc._telegraph_industry_facts("半导体", AS_OF_NEW)
        assert len(out) == 1
        assert out[0]["source"] == "东财快讯"
        assert out[0]["published_at"] == "2026-08-26"
    finally:
        fc._telegraph_cache.clear()


def test_date_from_url_patterns():
    """URL 日期提取：/2026/8/29/ 与 20260829 模式可提；无日期返回空（不编造）。"""
    from invest.evidence import factcards as fc

    assert fc._date_from_url("https://news.qq.com/rain/a/20260830A05B9600") == "2026-08-30"
    assert fc._date_from_url("https://x.com/2026/08/29/abc.html") == "2026-08-29"
    assert fc._date_from_url("https://www.sdenews.com/html/2026/8/387275.shtml") == ""
    assert fc._date_from_url("https://k.sina.com.cn/article_1702925432_6580947801901alke.html") == ""


def test_deep_dive_news_uses_web_with_url_dates(monkeypatch):
    """2026-08-31 深查 web 增强：URL 带真实日期且在窗口内才入 news；无日期丢弃。"""
    from invest.evidence import factcards as fc

    fc._telegraph_cache.clear()
    monkeypatch.setattr(fc, "_telegraph_industry_facts", lambda obj, as_of, max_items=5: [])

    def fake_search(query, n=5):
        return [
            {"title": "半导体政策落地", "url": "https://eastmoney.com/news/1354,202608253859224213.html", "snippet": "s"},
            {"title": "无日期文章", "url": "https://k.sina.com.cn/article_1702925432_6580947801901alke.html", "snippet": "s"},
        ]

    monkeypatch.setattr("invest.agent.web_tools.web_search", fake_search)
    try:
        out = fc.deep_dive_news("半导体", "industry", AS_OF_NEW)
        assert len(out) == 1
        assert out[0]["published_at"] == "2026-08-25"
        assert out[0]["source"] == "eastmoney.com"
        assert out[0]["url"].startswith("https://eastmoney.com")
    finally:
        fc._telegraph_cache.clear()


def test_deep_dive_news_telegraph_preferred(monkeypatch):
    """2026-08-31 深查 news：电报命中时不再走 web（省 token）。"""
    from invest.evidence import factcards as fc

    telegraph_hit = [{
        "kind": "news", "source": "东财快讯", "url": "",
        "published_at": "2026-08-26", "fetched_at": AS_OF_NEW, "summary": "半导体快讯",
    }]
    called = {"n": 0}
    monkeypatch.setattr(fc, "_telegraph_industry_facts", lambda obj, as_of, max_items=5: telegraph_hit)
    monkeypatch.setattr("invest.agent.web_tools.web_search",
                        lambda query, n=5: called.__setitem__("n", called["n"] + 1) or [])
    out = fc.deep_dive_news("半导体", "industry", AS_OF_NEW)
    assert out == telegraph_hit
    assert called["n"] == 0


def test_macro_retrigger_truncated_by_as_of(db_path: str):
    """历史 as_of 回放不得偷看更新的 macro_series。"""
    from invest.data.storage import upsert_df
    from invest.discipline.macro_gate import check_env_retrigger
    from invest.evidence.factcards import build_industry_card

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_OLD)
        _seed_industry(
            conn, "有色", as_of=AS_OF_OLD, rs=0.05, rotation_rank=8,
            pe_pct=0.6, crowding=0.3, crowding_state="正常",
            style="主力", cycle_phase="筑底", main_net=-1e8,
        )
        _seed_macro(conn, AS_OF_NEW)
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.15, rotation_rank=2,
            pe_pct=0.4, crowding=0.7, crowding_state="偏拥挤",
            style="游资", cycle_phase="上行", main_net=2e8,
        )
        upsert_df(conn, "macro_series", pd.DataFrame([
            {"indicator": "全A中位PE近10年分位", "date": "2026-08-14", "value": 0.50, "src": "akshare"},
            {"indicator": "全A中位PE近10年分位", "date": "2026-08-26", "value": 0.90, "src": "akshare"},
            {"indicator": "社会融资规模增量", "date": "2026年03月份", "value": 30000.0, "src": "akshare"},
            {"indicator": "社会融资规模增量", "date": "2026年08月份", "value": 10000.0, "src": "akshare"},
            {"indicator": "中国国债收益率10年", "date": "2026-08-07", "value": 1.70, "src": "akshare"},
            {"indicator": "中国国债收益率10年", "date": "2026-08-26", "value": 2.20, "src": "akshare"},
        ]))
        latest = check_env_retrigger(conn)
        assert latest["n"] >= 1
        replay = check_env_retrigger(conn, as_of=AS_OF_OLD)
        joined = "\n".join(replay.get("triggers") or [])
        assert "0.90" not in joined
        assert "2.20" not in joined
        assert replay["n"] == 0
        old = build_industry_card(conn, "有色", as_of=AS_OF_OLD, news_fn=_news_fn)
        new = build_industry_card(conn, "有色", as_of=AS_OF_NEW, news_fn=_news_fn)
        old_rt = (old.dimensions.get("macro") or {}).get("retrigger") or []
        new_rt = (new.dimensions.get("macro") or {}).get("retrigger") or []
        assert old_rt == []
        assert new_rt
    finally:
        conn.close()


def test_workbench_pool_and_card_share_normalized_symbol(db_path: str):
    """工作台入池+建卡共用归一化：600519.SH → 600519。"""
    from dashboard import queries as q

    conn = connect(db_path)
    try:
        _seed_stock(conn, "600519", "白酒")
        conn.execute("DELETE FROM candidate_pool")
        conn.commit()
    finally:
        conn.close()

    pooled = q.promote_factcard_to_pool(db_path, "600519.SH", industry="白酒", reason="人工比价")
    assert pooled["symbol"] == "600519"
    card_row = q.promote_factcard_to_card(
        db_path, "600519.SH", thesis="人工比价后认为中期赔率可接受，先观察",
    )
    assert card_row["symbol"] == "600519"
    assert card_row["status"] == "candidate"
    conn = connect(db_path)
    try:
        pool = conn.execute(
            "SELECT symbol FROM candidate_pool WHERE out_date IS NULL"
        ).fetchall()
        cards = conn.execute("SELECT symbol FROM cards").fetchall()
        assert [r["symbol"] for r in pool] == ["600519"]
        assert [r["symbol"] for r in cards] == ["600519"]
    finally:
        conn.close()


def test_null_strength_rs_records_missing_not_typeerror(db_path: str):
    """quant_strength.rs 为 NULL 时记 missing，refresh 不得 TypeError。"""
    from invest.evidence.factcards import build_industry_card, run_factcard_refresh

    conn = connect(db_path)
    try:
        _seed_macro(conn, AS_OF_NEW)
        _seed_industry(
            conn, "有色", as_of=AS_OF_NEW, rs=0.1, rotation_rank=2,
            pe_pct=0.4, crowding=0.5, crowding_state="正常",
            style="主力", cycle_phase="上行", main_net=1e8,
        )
        conn.execute(
            "UPDATE quant_strength SET rs=NULL WHERE obj='有色' AND run_date=?",
            (AS_OF_NEW,),
        )
        conn.commit()
        card = build_industry_card(conn, "有色", as_of=AS_OF_NEW, news_fn=_news_fn)
        assert "strength" in card.missing
        result = run_factcard_refresh(db_path, conn, as_of=AS_OF_NEW, push=False, news_fn=_news_fn)
        assert result.success
    finally:
        conn.close()
