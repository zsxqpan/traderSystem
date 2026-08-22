"""收盘扫描（快照/变化检测/P1）单元测试。用法: python tests/test_scan.py"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

from invest.db import connect, init_db
from invest.discipline import pool, rating
from invest.scan import (
    detect_changes,
    run_scan_and_notify,
    snapshot_exists,
    take_snapshot,
)


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_scan_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _tmp_snapshot_dir(prefix: str = "snap_"):
    """隔离的快照目录（os.makedirs 创建；mkdtemp 在部分沙箱环境被拒写）。"""
    import uuid
    d = os.path.join(tempfile.gettempdir(), f"{prefix}{uuid.uuid4().hex[:8]}")
    os.makedirs(d, exist_ok=True)
    return Path(d)


def test_detect_changes():
    prev = {
        "pool": {"600519": {"level": "core"}, "000001": {"level": "track"}},
        "ratings": {"macro": "中性", "market": "中性"},
    }
    curr = {
        "pool": {
            "600519": {"level": "core"},          # 不变
            "000001": {"level": "core"},          # 升级 track->core
            "300750": {"level": "track"},         # 新入池
        },
        "ratings": {"macro": "收紧", "market": "中性"},  # macro 变化
    }
    p = _tmp_db()
    conn = connect(p)
    changes = detect_changes(conn, prev, curr)
    conn.close()
    text = "\n".join(changes)
    assert "300750" in text and "新入池" in text
    assert "000001" in text and "升级" in text
    assert "宏观" in text or "macro" in text
    assert "600519" not in text  # 无变化的不报
    print("test_detect_changes OK")


def test_snapshot_roundtrip():
    p = _tmp_db()
    conn = connect(p)
    pool.add_to_pool(conn, "600519", level="core")
    pool.add_to_pool(conn, "000001", level="track")
    rating.set_rating(conn, "macro", "宽松")
    rating.set_rating(conn, "market", "进攻")
    conn.close()
    with mock.patch("invest.scan.SNAPSHOT_DIR", _tmp_snapshot_dir()):
        snap = take_snapshot(p)
        assert snap["pool"]["600519"]["level"] == "core"
        assert snap["ratings"]["macro"] == "宽松"
        assert snapshot_exists(p, date=snap["date"]) is True
    print("test_snapshot_roundtrip OK")


def test_run_scan_and_notify():
    p = _tmp_db()
    with mock.patch("invest.scan.SNAPSHOT_DIR", _tmp_snapshot_dir()):
        # 首次：无 prev -> 只快照不推送（无变化可比对）
        changes1 = run_scan_and_notify(p)
        assert changes1 == []
        # 同日再次扫描（无 force）-> 去重，不重复推送
        with mock.patch("invest.scan.Notifier") as m0:
            changes_dup = run_scan_and_notify(p)
        assert changes_dup == []
        m0.return_value.send_text.assert_not_called()
        # 加一个 core 后 force 扫描 -> 检测新入池并推送
        conn = connect(p)
        pool.add_to_pool(conn, "600519", level="core")
        conn.close()
        with mock.patch("invest.scan.Notifier") as m:
            m.return_value.send_text.return_value = True
            changes2 = run_scan_and_notify(p, force=True)
        assert any("600519" in c for c in changes2)
        assert m.return_value.send_text.called
        msg = m.return_value.send_text.call_args.args[0]
        assert "[P1]" in msg
        # 状态不变 force 扫描 -> 无变化不推送
        with mock.patch("invest.scan.Notifier") as m2:
            changes3 = run_scan_and_notify(p, force=True)
        assert changes3 == []
        m2.return_value.send_text.assert_not_called()
    print("test_run_scan_and_notify OK")


if __name__ == "__main__":
    test_detect_changes()
    test_snapshot_roundtrip()
    test_run_scan_and_notify()
    print("\nALL SCAN TESTS PASSED")
