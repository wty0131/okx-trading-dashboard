# -*- coding: utf-8 -*-
"""N 日动量趋势跟随策略（单标的，多头版本）。

思路
----
动量 = close[t] / close[t-N] - 1，衡量过去 N 根的涨跌幅。动量策略认为
"强者恒强"：N 日动量 > 0（近 N 日上涨）时持有（LONG）；动量转负或归零
（近 N 日不涨）则空仓离场（CLOSE）。纯多头版本：不做空，动量非正一律
平仓观望。

实现方式与 sma_cross 一致：on_bar 逐根喂入 K 线，deque 缓存最近
lookback+1 根收盘价，仅在状态切换（空仓->持多 / 持多->空仓）时发信号。

参数
----
lookback : 动量回看周期 N（默认 20）
"""

from collections import deque

from .base import Signal, Strategy
from .registry import register


@register
class MomentumFollow(Strategy):
    """N 日动量跟随：动量>0 做多，否则空仓（多头版本）。"""

    name = "momentum_follow"
    category = "trend"
    description = (
        "N 日动量趋势跟随：过去 N 根收益为正则持有，否则空仓（只做多）"
    )
    params = {"lookback": 20}

    def _post_init(self):
        lookback = int(self.params["lookback"])
        if lookback <= 0:
            raise ValueError("lookback 必须为正整数")
        self.params["lookback"] = lookback
        # 内部状态：最近 lookback+1 根收盘价（closes[0] 即 N 根前的收盘）
        self._closes: deque = deque(maxlen=lookback + 1)
        self._long: bool = False

    def reset(self):
        """清空内部状态，便于在其它数据集上重放。"""
        self._closes.clear()
        self._long = False

    # ------------------------------------------------------------------ #
    def on_bar(self, bar) -> Signal:
        close = float(bar["close"])
        self._closes.append(close)
        if len(self._closes) < self.params["lookback"] + 1:
            return Signal.HOLD           # 预热：需 lookback 根前收盘

        momentum = close / self._closes[0] - 1.0

        sig = Signal.HOLD
        if not self._long and momentum > 0.0:
            sig = Signal.LONG            # 动量转正 -> 持有
            self._long = True
        elif self._long and momentum <= 0.0:
            sig = Signal.CLOSE           # 动量转负/归零 -> 空仓
            self._long = False
        return sig
