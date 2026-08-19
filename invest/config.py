"""配置加载：环境变量（.env）+ config.yaml。

优先级：环境变量 > .env > 代码默认值；config.yaml 存放结构性参数
（评级-仓位映射、指标参数、调度表），由 load_yaml_config() 读取。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """运行环境配置（.env / 环境变量）。"""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_api_key: str = ""

    # 推送：企业微信
    wecom_webhook: str = ""

    # 推送：飞书群（开放平台 API，需走代理）
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_chat_id: str = ""
    feishu_proxy: str = "http://127.0.0.1:7892"

    # 推送：飞书长连接接收（项目本体直连，零 Hermes 依赖）
    # 你的飞书 open_id（群内触发盘中报告的账号）；机器人 open_id 留空则启动时自动查询
    feishu_owner_open_id: str = ""
    feishu_bot_open_id: str = ""
    # 群其他成员艾特机器人可用额度（每日 token 上限，2026-08-18 新增，默认 100 万）
    feishu_nonadmin_daily_token_limit: int = 1_000_000

    # 推送：个人微信（iLink Bot API，凭据已迁入项目本地 data/weixin/）
    weixin_token: str = ""
    weixin_to_user_id: str = ""
    weixin_ctx_path: str = ""  # 留空则用 data/weixin/context-tokens.json

    # 数据源
    tushare_token: str = ""

    # 路径
    db_path: str = str(PROJECT_ROOT / "data" / "invest.db")

    # 成本与关注度
    core_attention_limit: int = 10
    pool_limit: int = 20
    daily_llm_budget_tokens: int = 60_000

    # 重点关注行业名单（2026-08-18 方案C，逗号分隔，如 "半导体,军工,白酒"；
    # 日报/周报会对名单内行业做专项数据+意见，随周报等信息调整）
    focus_industries: str = ""

    # 风控初始参数
    risk_max_drawdown: float = 0.15
    risk_single_position: float = 0.15
    risk_industry_limit: float = 0.30
    risk_cash_floor: float = 0.20


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_yaml_config(path: Path | None = None) -> dict[str, Any]:
    """读取 config/config.yaml，文件缺失时返回空字典。"""
    p = path or (PROJECT_ROOT / "config" / "config.yaml")
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}