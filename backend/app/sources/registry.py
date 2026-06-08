"""來源註冊 / DI（架構②：config 決定能力→實作映射，建構子注入）。

加新來源 = 在 _SOURCE_CLASSES 註冊一個類；換來源 = 改 config.source_bindings。
下游（FetchStep / 引擎）只透過 provider("price") 取能力，不認得具體類別。
"""

from __future__ import annotations

from ..config import settings
from .base import BaseSource
from .finmind import FinMindSource
from .fugle import FugleSource
from .interfaces import (
    ChipProvider,
    FundamentalProvider,
    NewsProvider,
    PriceProvider,
    UniverseProvider,
)
from .twse import TwseSource

# 來源名稱 → 類別
_SOURCE_CLASSES: dict[str, type[BaseSource]] = {
    "finmind": FinMindSource,
    "fugle": FugleSource,
    "twse": TwseSource,
}

# 能力 → 期望介面（給型別檢查 / 文件）
_CAPABILITY_INTERFACES = {
    "universe": UniverseProvider,
    "price": PriceProvider,
    "chip": ChipProvider,
    "fundamental": FundamentalProvider,
    "news": NewsProvider,
}

_instances: dict[str, BaseSource] = {}


def get_source(name: str) -> BaseSource:
    """取得來源單例。"""
    if name not in _instances:
        if name not in _SOURCE_CLASSES:
            raise KeyError(f"未註冊的來源：{name}")
        _instances[name] = _SOURCE_CLASSES[name]()
    return _instances[name]


def provider(capability: str) -> BaseSource:
    """依 config.source_bindings 解析能力對應的來源實例。"""
    name = settings.source_bindings.get(capability)
    if not name:
        raise KeyError(f"能力未綁定來源：{capability}")
    src = get_source(name)
    iface = _CAPABILITY_INTERFACES.get(capability)
    if iface and not isinstance(src, iface):
        raise TypeError(f"來源 {name} 未實作 {capability} 能力（{iface.__name__}）")
    return src


def bound_source_names() -> list[str]:
    """目前綁定用到的來源名稱（去重），供設定頁列健康狀態。"""
    return sorted(set(settings.source_bindings.values()))


def all_sources() -> dict[str, BaseSource]:
    return {name: get_source(name) for name in bound_source_names()}


def reset() -> None:
    """測試 / 換 token 後清快取。"""
    for src in _instances.values():
        src.close()
    _instances.clear()
