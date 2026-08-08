"""数据源适配器注册表。"""
from .akshare_source import AkShareSource
from .tushare_source import TushareSource
from invest.config import get_settings

SOURCE_REGISTRY = {
    "akshare": AkShareSource(),
    # 备用源：token 从 .env 注入（TUSHARE_TOKEN），避免构造时永远为空
    "tushare": TushareSource(token=get_settings().tushare_token),
}