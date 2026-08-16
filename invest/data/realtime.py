"""实时行情三源直连轮询：新浪 hq.sinajs.cn / 腾讯 qt.gtimg.cn / 东财 push2。

设计要点（对齐 TODO 阶段 0 / 2.5 实时行情通道）：
- Level-1 快照接口直连轮询，3-5 秒间隔、批量取核心池；
- 三源优先级：sina(最快) -> tencent -> em_push2，任一源失败自动切换并留痕；
- 东财内部多域名容灾：push2 -> push2delay -> push2his（主域名偶发 RemoteDisconnected）；
- 直连必须绕过 Windows 系统代理（trust_env=False）：
  Python 在 Windows 上会读取注册表代理（WinINET），若代理软件未运行，
  所有请求都会打到 127.0.0.1:<port> 被拒（本机 2026-08-15 实测即此问题）；
- 新鲜度守卫：行情时间戳 vs 接收时间差值 <= max_lag 秒才算有效，
  超阈值标记 stale，不得支撑 P0 决策（数据失效即防守）；
- 单源解析失败/连接失败自动尝试下一源，全部失败抛 RuntimeError。
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass

import requests

# 统一 User-Agent
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 源优先级（新浪最快且免费额度宽松）
SOURCE_ORDER = ("sina", "tencent", "em_push2")

# 东财字段：f2=最新价 f3=涨跌幅% f12=代码 f14=名称 f17=今开 f18=昨收 f124=行情时间戳(ms)
_EM_FIELDS = "f2,f3,f12,f14,f17,f18,f124"


def _to_market_symbol(symbol: str) -> str:
    """000001 -> sz000001；600519 -> sh600519；4/8 开头 -> bj。"""
    s = symbol.lower()
    if s.startswith(("sh", "sz", "bj")):
        return s
    if s.startswith("6"):
        return "sh" + s
    if s.startswith(("4", "8")):
        return "bj" + s
    return "sz" + s


def _em_secid(symbol: str) -> str:
    """600519 -> 1.600519；000001 -> 0.000001；北交所 -> 0.xxx。"""
    s = symbol.lower()
    if s.startswith(("sh", "sz", "bj")):
        code = s[2:]
        return f"{'1' if s.startswith('sh') else '0'}.{code}"
    if s.startswith("6"):
        return f"1.{s}"
    return f"0.{s}"


@dataclass
class Quote:
    """统一快照：price 最新价、pct 涨跌幅（-1..1，如 0.05 表示 +5%）、ts 行情时间戳。"""

    symbol: str
    price: float | None = None
    pct: float | None = None
    ts: dt.datetime | None = None
    src: str = ""


def _f(v) -> float | None:
    """宽松转 float；空/异常返回 None。"""
    try:
        if v is None or v == "" or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_ts(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _parse_sina_line(line: str) -> Quote | None:
    """解析新浪单行: var hq_str_sh600519="名称,今开,昨收,最新,...,日期,时间,00";"""
    line = line.strip()
    if "=" not in line or "hq_str_" not in line:
        return None
    var, _, payload = line.partition("=")
    msym = var.replace("hq_str_", "").replace("var", "").strip()
    seg = payload.strip().strip('";').split(",")
    if len(seg) < 32:
        return None
    price = _f(seg[3])
    prev_close = _f(seg[2])
    if price is None or price <= 0:
        return None
    pct = (price / prev_close - 1.0) if prev_close and prev_close > 0 else None
    return Quote(
        symbol=msym,
        price=price,
        pct=pct,
        ts=_parse_ts(f"{seg[30]} {seg[31]}"),
        src="sina",
    )


def _fetch_sina(session: requests.Session, symbols: list[str]) -> dict[str, Quote]:
    """新浪批量快照。"""
    if not symbols:
        return {}
    msyms = [_to_market_symbol(s) for s in symbols]
    url = "http://hq.sinajs.cn/list=" + ",".join(msyms)
    resp = session.get(
        url,
        headers={**_UA, "Referer": "https://finance.sina.com.cn"},
        timeout=8,
    )
    resp.raise_for_status()
    resp.encoding = "gbk"
    out: dict[str, Quote] = {}
    for line in resp.text.splitlines():
        q = _parse_sina_line(line)
        if q is not None:
            out[q.symbol] = q
    return out


def _parse_tencent_line(line: str) -> Quote | None:
    """解析腾讯单行: v_sh600519="1~名称~代码~最新~昨收~...~YYYYMMDDHHMMSS~涨跌额~涨跌幅%...";"""
    line = line.strip()
    if "=" not in line or "v_" not in line:
        return None
    var, _, payload = line.partition("=")
    msym = var.replace("v_", "").strip()
    seg = payload.strip().strip('";').split("~")
    if len(seg) < 40:
        return None
    price = _f(seg[3])
    if price is None or price <= 0:
        return None
    pct = _f(seg[32])
    ts = None
    try:
        ts = dt.datetime.strptime(seg[30], "%Y%m%d%H%M%S")
    except (ValueError, IndexError):
        ts = None
    return Quote(
        symbol=msym,
        price=price,
        pct=(pct / 100.0) if pct is not None else None,
        ts=ts,
        src="tencent",
    )


def _fetch_tencent(session: requests.Session, symbols: list[str]) -> dict[str, Quote]:
    """腾讯批量快照。"""
    if not symbols:
        return {}
    msyms = [_to_market_symbol(s) for s in symbols]
    url = "https://qt.gtimg.cn/q=" + ",".join(msyms)
    resp = session.get(url, headers=_UA, timeout=8)
    resp.raise_for_status()
    resp.encoding = "gbk"
    out: dict[str, Quote] = {}
    for line in resp.text.splitlines():
        q = _parse_tencent_line(line)
        if q is not None:
            out[q.symbol] = q
    return out


def _fetch_em(session: requests.Session, symbols: list[str]) -> dict[str, Quote]:
    """东财批量快照（JSON）。f124=行情时间戳(毫秒)，f3=涨跌幅%。

    内部多域名容灾：push2 -> push2delay -> push2his（主域名偶发 RemoteDisconnected）。
    """
    if not symbols:
        return {}
    secids = ",".join(_em_secid(s) for s in symbols)
    hosts = ("push2.eastmoney.com", "push2delay.eastmoney.com", "push2his.eastmoney.com")
    last_exc: Exception | None = None
    for host in hosts:
        url = (
            "https://" + host + "/api/qt/ulist.np/get"
            + f"?secids={secids}&fields={_EM_FIELDS}&fltt=2"
        )
        try:
            resp = session.get(
                url,
                headers={**_UA, "Referer": "https://quote.eastmoney.com"},
                timeout=8,
            )
            resp.raise_for_status()
            data = resp.json()
            raw = (data.get("data") or {}).get("diff") or []
            items = list(raw.values()) if isinstance(raw, dict) else raw
            out: dict[str, Quote] = {}
            for it in items:
                if not isinstance(it, dict):
                    continue
                key = str(it.get("f12", "")).lower()
                price = _f(it.get("f2"))
                if not key or price is None or price <= 0:
                    continue
                pct = _f(it.get("f3"))
                ts_ms = it.get("f124")
                ts = None
                if ts_ms:
                    try:
                        ts = dt.datetime.fromtimestamp(int(ts_ms) / 1000)
                    except (ValueError, OSError, OverflowError):
                        ts = None
                out[key] = Quote(
                    symbol=key,
                    price=price,
                    pct=(pct / 100.0) if pct is not None else None,
                    ts=ts,
                    src="em_push2",
                )
            if out:
                return out
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    return {}


def _fetcher_for(src: str):
    """动态取抓取函数（globals 查找，便于单测 mock 替换模块属性）。

    em_push2 逻辑源对应 _fetch_em（多域名容灾实现）。
    """
    alias = {"em_push2": "em"}
    return globals().get(f"_fetch_{alias.get(src, src)}")


class RealtimeQuoter:
    """三源轮询器：批量快照 + 自动切换 + 源健康状态。

    用法:
        q = RealtimeQuoter()
        quotes = q.fetch(["600519", "000001"])   # {sym: Quote}
    """

    def __init__(
        self,
        source_order: tuple[str, ...] = SOURCE_ORDER,
        retries: int = 1,
        timeout: float = 8.0,
    ) -> None:
        self.source_order = source_order
        self.retries = retries
        self.timeout = timeout
        self.session = requests.Session()
        # 关键：忽略 Windows 系统代理（WinINET 注册表），否则代理未运行时全部请求被拒
        self.session.trust_env = False
        # 源健康：连续失败计数（用于日志/告警）
        self.source_failures: dict[str, int] = {s: 0 for s in source_order}

    def fetch(self, symbols: list[str]) -> dict[str, Quote]:
        """按优先级尝试各源，首个成功即返回（含部分成功）；全部失败抛 RuntimeError。"""
        symbols = list(dict.fromkeys(symbols))
        if not symbols:
            return {}
        last_err: Exception | None = None
        for src in self.source_order:
            fetcher = _fetcher_for(src)
            if fetcher is None:
                continue
            for attempt in range(self.retries + 1):
                try:
                    quotes = fetcher(self.session, symbols)
                    if quotes:
                        self.source_failures[src] = 0
                        return quotes
                    self.source_failures[src] += 1
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    self.source_failures[src] += 1
                    if attempt < self.retries:
                        time.sleep(0.3)
        raise RuntimeError(f"实时行情三源全部失败: {last_err}")

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "RealtimeQuoter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def fetch_current_prices(
    symbols: list[str], quoter: RealtimeQuoter | None = None
) -> dict[str, Quote]:
    """便捷入口：单次批量快照（自建临时轮询器）。"""
    own = quoter is None
    q = quoter or RealtimeQuoter()
    try:
        return q.fetch(symbols)
    finally:
        if own:
            q.close()


def is_fresh(quote: Quote, now: dt.datetime | None = None, max_lag: float = 10.0) -> bool:
    """新鲜度判定：行情时间戳距接收时刻 <= max_lag 秒；无时间戳视为不新鲜。"""
    if quote.ts is None:
        return False
    now = now or dt.datetime.now()
    lag = (now - quote.ts).total_seconds()
    return 0 <= lag <= max_lag


def quote_lag_seconds(quote: Quote, now: dt.datetime | None = None) -> float | None:
    """行情时间戳距当前时刻的秒数（延迟监控入库用）；无时间戳返回 None。"""
    if quote.ts is None:
        return None
    now = now or dt.datetime.now()
    return abs((now - quote.ts).total_seconds())


"""节流版 log_realtime_health 替换（_write_health 替换为带节流版本）。"""
import time as _time

_LAST_HEALTH_LOG: dict[str, float] = {}  # db_path -> 上次写库时间戳
_HEALTH_BASELINE_INTERVAL = 60.0  # 正常状态下每 60 秒写一条基线


def log_realtime_health(db_path, quotes, failures):
    """延迟监控留痕：行情时间戳 vs 接收时刻差值写入 job_runs(job='realtime')。

    节流规则（4 秒轮询下避免 job_runs 膨胀）：
    - 正常（无 stale、无源失败）：每 60 秒写一条基线；
    - 异常（stale>0 或任一源 failures>0）：立即写，不节流。
    返回 detail 文本。失败静默（监控本身不阻断）。
    """
    now = dt.datetime.now()
    lags = [quote_lag_seconds(q, now) for q in quotes.values()]
    lags = [l for l in lags if l is not None]
    stale = [q.symbol for q in quotes.values() if not is_fresh(q, now=now)]
    srcs = sorted({q.src for q in quotes.values()})
    if lags:
        lag_part = f"lag_avg={sum(lags)/len(lags):.1f}s lag_max={max(lags):.1f}s"
    else:
        lag_part = "lag_avg=n/a lag_max=n/a"
    detail = (
        f"src={','.join(srcs) or 'none'} n={len(quotes)} "
        f"{lag_part} stale={len(stale)} failures={failures}"
    )
    abnormal = bool(stale) or any(v > 0 for v in failures.values())
    last = _LAST_HEALTH_LOG.get(db_path, 0.0)
    now_ts = _time.time()
    if not abnormal and (now_ts - last) < _HEALTH_BASELINE_INTERVAL:
        return detail  # 正常且未到基线间隔：只返回不落库
    _LAST_HEALTH_LOG[db_path] = now_ts
    try:
        from invest.db import connect
        conn = connect(db_path)
        try:
            with conn:
                conn.execute(
                    """INSERT INTO job_runs(job, status, started_at, finished_at, detail)
                       VALUES('realtime', 'ok', datetime('now','localtime'), datetime('now','localtime'), ?)""",
                    (detail,),
                )
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass
    return detail


def realtime_health(db_path: str, max_lag: float = 10.0) -> dict:
    """数据健康查询（数据失效即防守的决策前置）。

    从 job_runs(job='realtime') 取最近一条留痕，结合当前时间判定
    实时行情是否新鲜可用。返回:
      ok          True=行情新鲜可支撑 P0 决策
      last_detail 最近一条留痕 detail（无留痕返回 ""）
      last_check  最近一次轮询时间（无留痕返回 None）
      stale       最近留痕中的 stale 计数
    """
    out = {"ok": True, "last_detail": "", "last_check": None, "stale": 0}
    try:
        from invest.db import connect
        conn = connect(db_path)
        try:
            row = conn.execute(
                """SELECT finished_at, detail FROM job_runs
                   WHERE job='realtime' ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            out["ok"] = False  # 从未轮询过：视为不可用
            return out
        detail = row["detail"] or ""
        out["last_detail"] = detail
        out["last_check"] = row["finished_at"]
        # stale 计数
        for part in detail.split():
            if part.startswith("stale="):
                try:
                    out["stale"] = int(part.split("=")[1])
                except (ValueError, IndexError):
                    pass
        # 最近留痕时间太旧 -> 失效（超 max_lag*60 无新轮询）
        if row["finished_at"]:
            try:
                from datetime import datetime as _dt
                last = _dt.strptime(row["finished_at"], "%Y-%m-%d %H:%M:%S")
                age = (_dt.now() - last).total_seconds()
                if age > max_lag * 60:
                    out["ok"] = False
            except (ValueError, TypeError):
                pass
        if out["stale"] > 0:
            out["ok"] = False  # 最近留痕有 stale -> 不可支撑 P0
    except Exception:  # noqa: BLE001
        out["ok"] = False  # 查询失败保守视为不可用
    return out
