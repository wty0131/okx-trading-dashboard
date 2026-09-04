# -*- coding: utf-8 -*-
"""布林带均值回归策略（单标的，多头版本）。

思路
----
价格沿布林带中轨（滚动 SMA）上下波动，触下轨（< 中轨 - k*标准差）视为
超卖买点，发出 LONG；价格回到中轨附近（>= 中轨）视为回归完成，发出
CLOSE 平仓。典型逆势（均值回归）打法，只做多不做空。

实现方式与 sma_cross 一致：on_bar 逐根喂入 K 线，deque 缓存最近
period 根收盘价，滚动计算中轨与总体标准差（含当前根），仅在状态切换
时发信号（其余时间 HOLD）。

参数
----
period : 布林带窗口周期（默认 20）
k      : 标准差倍数（默认 2.0）
"""

import math
from collections import deque

from .base import Signal, Strategy
from .registry import register


@register
class BollingerReversion(Strategy):
    """布林带均值回归：触下轨做多，回中轨平仓。"""

    name = "bollinger_reversion"
    category = "reversion"
    description = (
        "布林带均值回归：收盘触下轨做多，价格回到中轨平仓（只做多）"
    )
    params = {"period": 20, "k": 2.0}

    def _post_init(self):
        period = int(self.params["period"])
        k = float(self.params["k"])
        if period <= 0:
            raise ValueError("period 必须为正整数")
        if k <= 0:
            raise ValueError("k 必须为正数")
        self.params["period"] = period
        self.params["k"] = k
        # 内部状态：收盘价缓冲与当前是否持多
        self._closes: deque = deque(maxlen=period)
        self._long: bool = False

    def reset(self):
        """清空内部状态，便于在其它数据集上重放。"""
        self._closes.clear()
        self._long = False

    # ------------------------------------------------------------------ #
    def on_bar(self, bar) -> Signal:
        close = float(bar["close"])
        self._closes.append(close)
        period = self.params["period"]
        if len(self._closes) < period:
            return Signal.HOLD

        # 滚动中轨与总体标准差（含当前根，标准布林带口径）
        closes = list(self._closes)
        mid = sum(closes) / period
        var = sum((c - mid) ** 2 for c in closes) / period
        std = math.sqrt(var)
        k = self.params["k"]
        lower = mid - k * std

        sig = Signal.HOLD
        if not self._long and close < lower:
            sig = Signal.LONG            # 触下轨 -> 超卖买点
            self._long = True
        elif self._long and close >= mid:
            sig = Signal.CLOSE           # 回到中轨 -> 均值回归完成
            self._long = False
        return sig
