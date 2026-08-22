"""端到端验证：真实网络链路 + 留痕入库。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from invest.data.storage import upsert_df
from invest.db import connect, init_db

p = os.path.join(tempfile.gettempdir(), "invest_e2e_realtime.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(p + s)
    except OSError:
        pass
init_db(p)
conn = connect(p)
upsert_df(conn, "candidate_pool", pd.DataFrame([
    {"symbol": "600519", "level": "core", "in_date": "2026-08-01", "out_date": None},
    {"symbol": "000001", "level": "core", "in_date": "2026-08-01", "out_date": None},
    {"symbol": "300750", "level": "track", "in_date": "2026-08-01", "out_date": None},
]))
upsert_df(conn, "daily_bars", pd.DataFrame([
    {"symbol": "600519", "date": "2026-08-14", "close": 1341.99, "src": "akshare"},
    {"symbol": "000001", "date": "2026-08-14", "close": 11.11, "src": "akshare"},
    {"symbol": "300750", "date": "2026-08-14", "close": 393.93, "src": "akshare"},
]))
conn.close()

import time
import traceback

import invest.data.realtime as rt
import invest.intraday as intr

# 1) 三源逐源 HTTP 延迟对比（各 3 次）
syms = ["600519", "000001", "300750"]
sess = rt.requests.Session()
sess.trust_env = False
for name in rt.SOURCE_ORDER:
    lags = []
    for _ in range(3):
        t0 = time.time()
        try:
            qs = rt._fetcher_for(name)(sess, syms)
            lags.append((time.time() - t0) * 1000)
        except Exception:
            print(f"  {name} FAIL:")
            traceback.print_exc()
            break
    if lags:
        print(f"  {name:<8} HTTP {min(lags):.0f}-{max(lags):.0f}ms")

# 2) 全链路 check_core_moves（周六旧行情 -> 默认 fresh 过滤，无异动；留痕仍入库）
alerts = intr.check_core_moves(p, threshold=0.0)
print("check_core_moves alerts (fresh 过滤, 周六预期空):", alerts)

# 3) 放宽新鲜度验证批量价格 + 留痕
prices = intr.fetch_batch_prices(syms, max_lag=86400)
print("fetch_batch_prices (max_lag=24h):", {k: round(v, 2) for k, v in prices.items()})

# 4) 留痕检查
conn = connect(p)
rows = conn.execute("SELECT job, status, detail FROM job_runs WHERE job='realtime' ORDER BY id DESC LIMIT 3").fetchall()
for r in rows:
    print("job_runs:", dict(r))
conn.close()
