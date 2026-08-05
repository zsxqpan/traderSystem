"""启动仪表盘。用法: myenv\\Scripts\\python.exe scripts/run_dashboard.py"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    app = ROOT / "dashboard" / "app.py"
    print("仪表盘地址: http://localhost:8501")
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app), "--server.headless", "true"])


if __name__ == "__main__":
    main()