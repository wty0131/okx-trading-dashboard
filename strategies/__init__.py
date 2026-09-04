# -*- coding: utf-8 -*-
"""策略包：基类与注册表。

用法：
    from strategies import Strategy, Signal
    from strategies.sma_cross import SmaCross   # 导入即完成注册

注册表 REGISTRY 先留空列表，具体策略模块在导入时通过 @register 注册，
供后续 UI / 工厂枚举使用。
"""

from .base import Signal, Strategy
from .registry import REGISTRY, available, register

# 导入全部具体策略模块：模块顶层 @register 会把策略类登记进 REGISTRY，
# 因此 import strategies 后即可用 REGISTRY / available() 枚举到全部策略。
from . import sma_cross  # noqa: F401,E402   SmaCross
from . import ema_rsi  # noqa: F401,E402     EmaRsi
from . import bollinger  # noqa: F401,E402   BollingerReversion
from . import macd_trend  # noqa: F401,E402  MacdTrend
from . import donchian  # noqa: F401,E402    DonchianBreakout
from . import rsi2  # noqa: F401,E402        Rsi2Reversion
from . import momentum  # noqa: F401,E402    MomentumFollow

__all__ = ["Signal", "Strategy", "REGISTRY", "available", "register"]
