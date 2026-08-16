"""市场风格判断单元测试。用法: python tests/test_style.py"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invest.quant.style import compute_style, style_to_text  # noqa: E402


def _make_closes(n=120):
    """构造 7 个指数日线：中证1000 最强、上证50 最弱（确定性日收益）。"""
    dates = pd.bdate_range("2026-01-01", periods=n)
    codes = ["000016", "000300", "000905", "000852", "000688", "399006", "899050"]
    daily = {
        "000852": 0.0020,   # 小盘最强
        "000905": 0.0015,
        "399006": 0.0010,
        "000300": 0.0005,   # 基准
        "000016": -0.0005,  # 大盘最弱
        "000688": -0.0010,
        "899050": -0.0015,
    }
    closes = {}
    for c in codes:
        closes[c] = pd.Series(100 * np.cumprod(np.full(n, 1 + daily[c])), index=dates)
    df = pd.DataFrame(closes, index=dates)
    return df


def test_compute_style_basic():
    closes = _make_closes()
    bench = closes["000300"]
    r = compute_style(closes, bench)
    assert r["run_date"] is not None
    is_ = r["index_strength"]
    assert set(is_.keys()) == set(closes.columns)
    # 中证1000 RS 应显著高于上证50（日收益差 0.25%，20日窗口累积差约 0.05）
    assert is_["000852"]["rs"] > is_["000016"]["rs"] + 0.02
    # 风格结论应有大小盘判断
    style = r["style"]
    assert "小盘" in style["size"] or "大盘" in style["size"] or "均衡" in style["size"]
    # 文本渲染不抛错
    text = style_to_text(r)
    assert "市场风格" in text and "指数强弱榜" in text
    print("test_compute_style_basic OK")


def test_compute_style_insufficient():
    # 数据不足(<30行) -> 返回空
    closes = _make_closes(n=10)
    r = compute_style(closes, closes["000300"])
    assert r["index_strength"] == {}
    assert style_to_text(r) != ""
    print("test_compute_style_insufficient OK")


if __name__ == "__main__":
    test_compute_style_basic()
    test_compute_style_insufficient()
    print("\nALL STYLE TESTS PASSED")
