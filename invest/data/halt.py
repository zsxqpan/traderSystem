"""当日停牌列表（best-effort，2026-08-22 盘前报告「涨停异动监控」用）。

数据源：akshare stock_zh_a_stop_em（东财当日停牌）。当日实测该接口偶发连接
失败 → 重试 1 次，仍失败返回 []（报告省略停牌列，不阻断）。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


def fetch_halt_list() -> list[dict]:
    """当日停牌列表：[{symbol, name, reason}]。失败返回 []。"""
    try:
        import akshare as ak

        df = None
        last_err = None
        for _attempt in range(2):
            try:
                df = ak.stock_zh_a_stop_em()
                if df is not None and not df.empty:
                    break
            except Exception as exc:
                last_err = exc
                time.sleep(1.0)
        if df is None or df.empty:
            if last_err:
                logger.warning("停牌列表获取失败: %s", last_err)
            return []
        rows: list[dict] = []
        for _, r in df.iterrows():
            reason = str(r.get("停牌原因", "")) if "停牌原因" in df.columns else ""
            rows.append({
                "symbol": str(r.get("代码", "")),
                "name": str(r.get("名称", "")),
                "reason": reason[:60],
            })
        return rows
    except Exception as exc:
        logger.warning("停牌列表异常: %s", exc)
        return []
