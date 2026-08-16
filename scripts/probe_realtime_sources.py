"""三源实时行情可达性探针 v2：支持 --no-proxy 直连（绕过 Windows 系统代理）。
用法: python scripts/probe_realtime_sources.py [--symbols 600519,000001] [--rounds 5] [--no-proxy]
"""
from __future__ import annotations
import argparse
import time
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def to_sina(sym: str) -> str:
    s = sym.lower()
    if s.startswith(("sh", "sz", "bj")):
        return s
    if s.startswith("6"):
        return "sh" + s
    if s.startswith(("4", "8")):
        return "bj" + s
    return "sz" + s


def to_tencent(sym: str) -> str:
    return to_sina(sym)


def to_em_secid(sym: str) -> str:
    s = sym.lower()
    if s.startswith(("sh", "sz", "bj")):
        code, mkt = s[2:], {"sh": "1", "sz": "0", "bj": "0"}[s[:2]]
        return f"{mkt}.{code}"
    if s.startswith("6"):
        return f"1.{s}"
    return f"0.{s}"


def probe(name: str, url: str, headers: dict, parse, no_proxy: bool) -> tuple[bool, float, str]:
    sess = requests.Session()
    if no_proxy:
        sess.trust_env = False  # 忽略 Windows 系统代理 / 环境变量
    t0 = time.time()
    try:
        r = sess.get(url, headers={**UA, **headers}, timeout=8)
        el = (time.time() - t0) * 1000
        if r.status_code != 200:
            return False, el, f"HTTP {r.status_code}"
        r.encoding = "gbk"
        ok, note = parse(r.text)
        return ok, el, f"{len(r.content)}B {note}"
    except Exception as exc:  # noqa: BLE001
        return False, (time.time() - t0) * 1000, f"{type(exc).__name__}: {exc}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="600519,000001,300750,601318,000858")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--no-proxy", action="store_true", help="直连，忽略系统代理")
    args = ap.parse_args()
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]

    def parse_sina(text: str):
        lines = [l for l in text.strip().splitlines() if "=" in l]
        if not lines:
            return False, "empty"
        seg = lines[0].split('"')[-2].split(",")
        return len(seg) >= 32 and float(seg[3] or 0) > 0, f"p={seg[3]}"

    def parse_tencent(text: str):
        if "v_sh" not in text and "v_sz" not in text and "v_bj" not in text:
            return False, "no marker"
        seg = text.split('"')[-2].split("~")
        return len(seg) >= 40 and float(seg[3] or 0) > 0, f"p={seg[3]}"

    def parse_em(text: str):
        import json as _json
        try:
            data = _json.loads(text)
            diff = data.get("data", {}).get("diff")
            if not diff:
                return False, "no diff"
            items = diff if isinstance(diff, list) else list(diff.values())
            return len(items) > 0, f"n={len(items)}"
        except Exception as exc:  # noqa: BLE001
            return False, f"json: {exc}"

    targets = {
        "sina": (
            "http://hq.sinajs.cn/list=" + ",".join(to_sina(s) for s in syms),
            {"Referer": "https://finance.sina.com.cn"},
            parse_sina,
        ),
        "tencent": (
            "https://qt.gtimg.cn/q=" + ",".join(to_tencent(s) for s in syms),
            {},
            parse_tencent,
        ),
        "em_push2": (
            "https://push2.eastmoney.com/api/qt/ulist.np/get?secids="
            + ",".join(to_em_secid(s) for s in syms)
            + "&fields=f2,f3,f12,f14&fltt=2",
            {"Referer": "https://quote.eastmoney.com"},
            parse_em,
        ),
        "em_push2delay": (
            "https://push2delay.eastmoney.com/api/qt/ulist.np/get?secids="
            + ",".join(to_em_secid(s) for s in syms)
            + "&fields=f2,f3,f12,f14&fltt=2",
            {"Referer": "https://quote.eastmoney.com"},
            parse_em,
        ),
        "em_push2his": (
            "https://push2his.eastmoney.com/api/qt/ulist.np/get?secids="
            + ",".join(to_em_secid(s) for s in syms)
            + "&fields=f2,f3,f12,f14&fltt=2",
            {"Referer": "https://quote.eastmoney.com"},
            parse_em,
        ),
    }
    stats = {k: [] for k in targets}
    mode = "no-proxy direct" if args.no_proxy else "system proxy"
    print(f"mode: {mode}")
    for rnd in range(1, args.rounds + 1):
        print(f"--- round {rnd} ---")
        for name, (url, hdr, parse) in targets.items():
            ok, ms, note = probe(name, url, hdr, parse, args.no_proxy)
            stats[name].append(ms)
            print(f"  {name:<14} {'OK ' if ok else 'FAIL'} {ms:7.0f}ms  {note}")
        time.sleep(0.4)
    print("--- summary (ms) ---")
    for name, ms_list in stats.items():
        print(f"  {name:<14} min={min(ms_list):.0f} avg={sum(ms_list)/len(ms_list):.0f} max={max(ms_list):.0f}")


if __name__ == "__main__":
    main()
