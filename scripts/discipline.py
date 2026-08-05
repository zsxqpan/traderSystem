"""执行纪律层命令行入口。

用法: myenv\\Scripts\\python.exe scripts/discipline.py <命令> ...
  pool add <symbol> [--level core|track|rest] [--reason ...]
  pool list
  pool remove <symbol>
  rating set <macro|market> <value> [--basis ...]
  rating get <macro|market>
  plan create <symbol> --stop 10.0 [--target 0.15] [--buy 9.5,10.5] [--take ...] [--invalid ...]
  plan list
  plan close <plan_id>
  trade add <plan_id> <buy|sell> <price> <qty> [--emotion ...]
  risk check <plan_id> <price>
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest.db import connect, init_db


def _conn():
    db = str(ROOT / "data" / "invest.db")
    init_db(db)
    return connect(db)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    conn = _conn()
    try:
        if cmd == "pool" and len(args) >= 2 and args[1] == "add":
            from invest.discipline.pool import add_to_pool
            rest = args[2:]
            symbol = rest[0]
            kwargs = {}
            if "--level" in rest:
                kwargs["level"] = rest[rest.index("--level") + 1]
            if "--industry" in rest:
                kwargs["industry"] = rest[rest.index("--industry") + 1]
            if "--reason" in rest:
                kwargs["reason"] = rest[rest.index("--reason") + 1]
            print(add_to_pool(conn, symbol, **kwargs))
        elif cmd == "pool" and len(args) >= 2 and args[1] == "list":
            from invest.discipline.pool import list_pool
            for p in list_pool(conn):
                print(p["symbol"], p["level"], p["in_date"])
        elif cmd == "pool" and len(args) >= 3 and args[1] == "remove":
            from invest.discipline.pool import remove_from_pool
            remove_from_pool(conn, args[2])
            print("removed")
        elif cmd == "rating" and len(args) >= 3 and args[1] == "set":
            from invest.discipline.rating import set_rating
            set_rating(conn, args[2], args[3])
            print("rating set")
        elif cmd == "rating" and len(args) >= 3 and args[1] == "get":
            from invest.discipline.rating import get_rating
            print(get_rating(conn, args[2]))
        elif cmd == "plan" and len(args) >= 3 and args[1] == "create":
            from invest.discipline.plans import create_plan
            rest = args[2:]
            symbol = rest[0]
            kwargs = {}
            if "--stop" in rest:
                kwargs["stop_loss"] = float(rest[rest.index("--stop") + 1])
            if "--target" in rest:
                kwargs["target_position"] = float(rest[rest.index("--target") + 1])
            if "--buy" in rest:
                kwargs["buy_range"] = rest[rest.index("--buy") + 1]
            if "--take" in rest:
                kwargs["take_profit"] = rest[rest.index("--take") + 1]
            print(create_plan(conn, symbol, **kwargs))
        elif cmd == "plan" and args[1] == "list":
            from invest.discipline.plans import list_active_plans
            for p in list_active_plans(conn):
                print(p["id"], p["symbol"], p["buy_range"], "stop=", p["stop_loss"], "target=", p["target_position"])
        elif cmd == "plan" and len(args) >= 3 and args[1] == "close":
            from invest.discipline.plans import close_plan
            close_plan(conn, int(args[2]))
            print("closed")
        elif cmd == "trade" and len(args) >= 5 and args[1] == "add":
            from invest.discipline.records import record_trade
            print(record_trade(conn, int(args[2]), args[3], float(args[4]), int(args[5])))
        elif cmd == "risk" and len(args) >= 4 and args[1] == "check":
            from invest.discipline.risk import check_stop_loss
            plan = conn.execute("SELECT * FROM trade_plans WHERE id=?", (int(args[2]),)).fetchone()
            print("止损触发:", check_stop_loss(dict(plan), float(args[3])) if plan else "计划不存在")
        else:
            print(__doc__)
    finally:
        conn.close()


if __name__ == "__main__":
    main()