"""运行复盘。用法: myenv\\Scripts\\python.exe scripts/run_review.py <weekly|monthly|yearly>"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from invest.db import connect, init_db
from invest.review.monthly import monthly_review
from invest.review.report import save_report
from invest.review.weekly import weekly_review
from invest.review.yearly import yearly_review


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "weekly"
    db = str(ROOT / "data" / "invest.db")
    init_db(db)
    conn = connect(db)
    try:
        if stage == "weekly":
            content = weekly_review(conn)
            period = content["period"]
            report_type = "weekly"
        elif stage == "monthly":
            content = monthly_review(conn)
            period = "monthly"
            report_type = "monthly"
        elif stage == "yearly":
            content = yearly_review(conn)
            period = "yearly"
            report_type = "yearly"
        else:
            print(__doc__)
            return
        save_report(conn, period, report_type, content)
        print(json.dumps(content, ensure_ascii=False, indent=2))
        print(f"\n已写入 review_reports ({report_type})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()