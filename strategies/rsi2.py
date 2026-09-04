# -*- coding: utf-8 -*-
"""RSI(2) 超跌反弹策略（单标的，多头版本）。

思路
----
RSI(2) 对最近 2 根 K 线的涨跌极度敏感：连续下跌会让其跌至极低值。
本策略利用这一特性做超跌反弹：
* 做多：空仓且 RSI(2) < oversold（默认 10）-> LONG，并记录开仓价；
* 平仓：持多后 RSI(2) > overbought（默认 90，反弹到位）或收盘价较
  开仓价回撤超过 stop_loss（百分比止损）-> CLOSE。

止损按收盘价判断，与回测引擎"信号在收盘价生效"的口径一致。
RSI 采用滚动口径（最近 period 根涨跌幅的简单平均），非 Wilder 平滑。

参数
----
rsi_period : RSI 周期（默认 2）
oversold   : 超卖阈值，低于则做多（默认 10）
overbought : 超买阈值，高于则平仓（默认 90）
stop_loss  : 止损比例（相对开仓价，默认 0.05 = 5%）
"""

from collections import deque

from .base import Signal, Strategy
from .registry import register


@register
class Rsi2Reversion(Strategy):
    """RSI(2) 超跌反弹：RSI<10 做多，RSI>90 或跌破止损平仓。"""

    name = "rsi2_reversion"
    category = "reversion"
    description = (
        "RSI(2) 超跌反弹：RSI<10 做多，RSI>90 或回撤超止损平仓"
    )
    params = {
        "rsi_period": 2,
        "oversold": 10.0,
        "overbought": 90.0,
        "stop_loss": 0.05,
    }

    def _post_init(self):
        rsi_period = int(self.params["rsi_period"])
        if rsi_period <= 0:
            raise ValueError("rsi_period 必须为正整数")
        oversold = float(self.params["oversold"])
        overbought = float(self.params["overbought"])
        if not (0 < oversold < overbought < 100):
            raise ValueError("需满足 0 < oversold < overbought < 100")
        stop_loss = float(self.params["stop_loss"])
        if not 0 < stop_loss < 1:
            raise ValueError("stop_loss 需在 (0, 1) 区间")
        self.params["rsi_period"] = rsi_period
        self.params["oversold"] = oversold
        self.params["overbought"] = overbought
        self.params["stop_loss"] = stop_loss
        # 内部状态：收盘价缓冲、开仓价与是否持多
        self._closes: deque = deque(maxlen=rsi_period + 1)
        self._entry_price: float = None
        self._long: bool = False

    def reset(self):
        """清空内部状态，便于在其它数据集上重放。"""
        self._closes.clear()
        self._entry_price = None
        self._long = False

    # ------------------------------------------------------------------ #
    def on_bar(self, bar) -> Signal:
        close = float(bar["close"])
        self._closes.append(close)
        period = self.params["rsi_period"]
        if len(self._closes) < period + 1:
            return Signal.HOLD

        rsi = _rolling_rsi(self._closes, period)

        sig = Signal.HOLD
        if not self._long and rsi < self.params["oversold"]:
            sig = Signal.LONG            # 极度超卖 -> 抢反弹
            self._long = True
            self._entry_price = close
        elif self._long:
            stop_price = self._entry_price * (1.0 - self.params["stop_loss"])
            if rsi > self.params["overbought"] or close <= stop_price:
                sig = Signal.CLOSE       # 反弹到位或触发止损 -> 离场
                self._long = False
                self._entry_price = None
        return sig


def _rolling_rsi(closes, period: int) -> float:
    """滚动 RSI（SMA 式）：用最近 period 根涨跌幅的简单平均。"""
    gains = losses = 0.0
    prev = closes[0]
    for c in list(closes)[1:]:
        diff = c - prev
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
        prev = c
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)
