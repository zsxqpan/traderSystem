"""观点库单元测试。用法: python tests/test_viewpoints.py"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.db import connect, init_db
from invest.viewpoints import accuracy, store
from invest.viewpoints.schema import validate_viewpoint


def _tmp_db():
    p = os.path.join(tempfile.gettempdir(), "invest_vp_test.db")
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(p + s)
        except OSError:
            pass
    init_db(p)
    return p


def _base():
    return {
        "source": "research",
        "obj_type": "industry",
        "obj": "半导体",
        "conclusion": "半导体中期向上",
        "period_tag": "mid",
        "confidence": 0.7,
        "evidence": [{"indicator": "rs", "value": 0.05}],
        "invalid_condition": "周线RS转负即失效",
    }


def test_validate_five_elements():
    good = _base()
    validate_viewpoint(good)
    for field in ("conclusion", "period_tag", "confidence", "evidence", "invalid_condition"):
        bad = dict(good)
        if field == "conclusion":
            bad["conclusion"] = "  "
        elif field == "period_tag":
            bad["period_tag"] = "hourly"
        elif field == "confidence":
            bad["confidence"] = 1.5
        elif field == "evidence":
            bad["evidence"] = []
        else:
            bad["invalid_condition"] = ""
        try:
            validate_viewpoint(bad)
            raise AssertionError(f"should reject missing {field}")
        except ValueError as e:
            assert field in str(e)
    print("test_validate_five_elements OK")


def test_crud_and_versioning():
    p = _tmp_db()
    conn = connect(p)
    v1 = store.create_viewpoint(conn, **_base())
    assert store.get_viewpoint(conn, v1)["status"] == "active"
    assert len(store.list_viewpoints(conn, obj="半导体")) == 1
    v2 = store.update_viewpoint(conn, v1, conclusion="半导体中期强向上", review_note="周度复核")
    old = store.get_viewpoint(conn, v1)
    new = store.get_viewpoint(conn, v2)
    assert old["status"] == "updated"
    assert new["status"] == "active"
    assert new["conclusion"] == "半导体中期强向上"
    assert new["id"] != v1
    conn.close()
    print("test_crud_and_versioning OK")


def test_lifecycle():
    p = _tmp_db()
    conn = connect(p)
    v = store.create_viewpoint(conn, **_base(), status="draft")
    store.transition(conn, v, "active")
    store.transition(conn, v, "verifying")
    store.transition(conn, v, "verified")
    assert store.get_viewpoint(conn, v)["status"] == "verified"
    try:
        store.transition(conn, v, "active")
        raise AssertionError("verified -> active should be illegal")
    except ValueError:
        pass
    conn.close()
    print("test_lifecycle OK")


def test_expire_due():
    p = _tmp_db()
    conn = connect(p)
    data = _base()
    data["valid_until"] = "2020-01-01"
    store.create_viewpoint(conn, **data)
    n = store.expire_due(conn)
    assert n == 1
    assert store.list_viewpoints(conn, status="pending_review")
    conn.close()
    print("test_expire_due OK")


def test_accuracy():
    p = _tmp_db()
    conn = connect(p)
    for i in range(3):
        store.create_viewpoint(conn, **_base())
        v = store.create_viewpoint(conn, **_base())
        store.transition(conn, v, "verifying")
        store.transition(conn, v, "verified")
    for i in range(1):
        v = store.create_viewpoint(conn, **_base())
        store.transition(conn, v, "verifying")
        store.transition(conn, v, "invalidated")
    stats = accuracy.accuracy_stats(conn)
    assert len(stats) == 1
    assert stats[0]["verified"] == 3 and stats[0]["invalidated"] == 1
    assert abs(stats[0]["accuracy"] - 0.75) < 1e-6
    conn.close()
    print("test_accuracy OK")




def test_updated_transition():
    """回归：旧版本（updated）可复活/归档/证伪，不再死胡同。"""
    p = _tmp_db()
    conn = connect(p)
    v1 = store.create_viewpoint(conn, **_base())
    v2 = store.update_viewpoint(conn, v1, conclusion="半导体中期强向上")
    assert store.get_viewpoint(conn, v1)["status"] == "updated"
    store.transition(conn, v1, "invalidated", note="旧版本证伪")
    assert store.get_viewpoint(conn, v1)["status"] == "invalidated"
    # 另一个旧版本复活为 active
    v3 = store.update_viewpoint(conn, v2, conclusion="半导体中期再复核")
    store.transition(conn, v2, "active", note="重新激活旧版本")
    assert store.get_viewpoint(conn, v2)["status"] == "active"
    conn.close()
    print("test_updated_transition OK")

if __name__ == "__main__":
    test_validate_five_elements()
    test_crud_and_versioning()
    test_lifecycle()
    test_expire_due()
    test_accuracy()
    test_updated_transition()
    print("\nALL VIEWPOINTS TESTS PASSED")