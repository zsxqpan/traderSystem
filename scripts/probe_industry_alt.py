import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import akshare as ak

print("1) THS industry index:")
try:
    df = ak.stock_board_industry_index_ths(symbol="半导体", start_date="20240801", end_date="20240805")
    print("   OK rows:", len(df), "cols:", df.columns.tolist())
except Exception as e:
    print("   FAIL:", type(e).__name__, str(e)[:120])

print("2) Sina ETF hist (sh512480):")
try:
    df = ak.fund_etf_hist_sina(symbol="sh512480")
    print("   OK rows:", len(df), "cols:", df.columns.tolist())
    print("   tail:", df.tail(1).to_dict("records"))
except Exception as e:
    print("   FAIL:", type(e).__name__, str(e)[:120])

print("3) Sina ETF hist (sz159995):")
try:
    df = ak.fund_etf_hist_sina(symbol="sz159995")
    print("   OK rows:", len(df), "cols:", df.columns.tolist())
except Exception as e:
    print("   FAIL:", type(e).__name__, str(e)[:120])

print("DONE")