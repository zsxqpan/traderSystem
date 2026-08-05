"""行业数据连通性诊断 v2。用法: myenv\\Scripts\\python.exe scripts/check_industry.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from invest.data import industry

print("=== 1. 缓存文件 ===")
cache = industry.DEFAULT_CACHE
print("cache exists:", cache.exists())
if cache.exists():
    import json
    with open(cache, "r", encoding="utf-8") as f:
        rows = json.load(f)
    print("cached industries:", len(rows), "| sample:", rows[:3])

print("\n=== 2. push2his K线主机逐个测试 (5天窗口) ===")
params = {
    "secid": "90.BK1036",
    "fields1": "f1,f2,f3,f4,f5,f6",
    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    "klt": "101", "fqt": "0", "beg": "20240801", "end": "20240805", "lmt": "1000000",
}
ok_hosts = []
for host in industry._PUSH2HIS_HOSTS:
    try:
        r = requests.get(host + "/api/qt/stock/kline/get", params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
        n = len(((data or {}).get("data") or {}).get("klines") or [])
        ok_hosts.append(host)
        print(f"  OK   {host}  klines={n}")
    except Exception as e:
        print(f"  FAIL {host}  {type(e).__name__}: {str(e)[:60]}")
print("reachable kline hosts:", ok_hosts)

print("\n=== 3a. fetch_industry_hist 5天窗口 (真实函数路径) ===")
try:
    df = industry.fetch_industry_hist("半导体", "20240801", "20240805")
    print("OK rows:", len(df), "| cols:", df.columns.tolist())
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:250])

print("\n=== 3b. fetch_industry_hist 179天窗口 (collect.py 用的窗口) ===")
try:
    df = industry.fetch_industry_hist("半导体", "20240101", "20240628")
    print("OK rows:", len(df))
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:250])

print("\n=== 3c. 直连主机9 拉 179 天窗口 (对照，绕过函数) ===")
params2 = {
    "secid": "90.BK1036",
    "fields1": "f1,f2,f3,f4,f5,f6",
    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    "klt": "101", "fqt": "0", "beg": "20240101", "end": "20240628", "lmt": "1000000",
}
try:
    r = requests.get("https://9.push2his.eastmoney.com/api/qt/stock/kline/get", params=params2, timeout=15)
    r.raise_for_status()
    data = r.json()
    n = len(((data or {}).get("data") or {}).get("klines") or [])
    print("OK klines=", n)
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:200])

print("\n=== 4. 同花顺行业指数 ===")
try:
    import akshare as ak
    df = ak.stock_board_industry_index_ths(symbol="半导体", start_date="20240801", end_date="20240805")
    print("OK rows:", len(df), "| cols:", df.columns.tolist())
    print(df.head(2).to_string())
except Exception as e:
    print("FAIL:", type(e).__name__, str(e)[:200])