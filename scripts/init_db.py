"""初始化数据库。用法: python scripts/init_db.py [db_path]"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from invest.db import init_db, table_names


def main() -> None:
    default = str(Path(__file__).resolve().parents[1] / "data" / "invest.db")
    db_path = sys.argv[1] if len(sys.argv) > 1 else default
    init_db(db_path)
    print(f"DB ready: {db_path}")
    print("tables:", ", ".join(table_names(db_path)))


if __name__ == "__main__":
    main()