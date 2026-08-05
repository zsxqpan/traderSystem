"""Agent 例行任务。用法: myenv\\Scripts\\python.exe scripts/run_agent.py [premarket|after_close]"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest.agent.agents import run_research, run_trade
from invest.agent.arbiter import find_conflicts, arbitrate
from invest.db import connect, init_db


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "after_close"
    db = str(ROOT / "data" / "invest.db")
    init_db(db)
    conn = connect(db)
    try:
        if mode == "premarket":
            print("投研 Agent（盘前）：生成当日关注清单")
            print(run_research(conn, "基于当前宏观流动性、市场温度与候选池，生成今日关注清单（最多5条方向，含周期与失效条件）"))
        else:
            print("交易 Agent（盘后）：行业强度异动归因并输出观点")
            print(run_trade(conn, "复盘行业强度榜：识别强度异动行业，必要时发起归因请求，输出1-3条短线轨观点"))
            conflicts = find_conflicts(conn)
            for a, b in conflicts:
                print(f"检测到观点冲突 {a} vs {b}，启动仲裁")
                print(arbitrate(conn, a, b))
    finally:
        conn.close()


if __name__ == "__main__":
    main()