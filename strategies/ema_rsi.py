# -*- coding: utf-8 -*-
"""EMA 趋势过滤 + RSI 超卖反弹策略（单标的，多头版本）。

思路
----
先看大方向：收盘价站上长周期 EMA 视为"趋势向上"（EMA 趋势过滤）；
再看超卖：趋势向上时若 RSI 跌破超卖线（< oversold），视为上升趋势中的
回调买点，发出 LONG；持多后一旦 RSI 升破超买线（> overbought）或收盘价
跌破 EMA（趋势转弱），发出 CLOSE 平仓。只做多，不做空。

RSI 周期取 5 而非经典 14 的原因：RSI(14)<30 的"深度超卖"几乎必然伴随
价格跌到 30 周期 EMA 之下，与"站上 EMA 的趋势过滤"天然互斥，会导致
入场条件几乎永不成立；改用 RSI(5)（较快超卖、温和回调即可触发）保持
同样的"超卖<30 做多 / 超买>70 或跌破 EMA 平仓"语义，并能稳定触发交易。

实现方式与 sma_cross 一致：on_bar 逐根喂入 K 线，用 deque 缓存收盘价、
用递推式 EMA 维护指标状态，仅在状态切换时发信号（其余时间 HOLD）。

参数
----
ema_period : EMA 趋势过滤周期（默认 30）
rsi_period : RSI 周期（默认 5，见上）
oversold   : RSI 超卖阈值，低于则视为回调买点（默认 30）
overbought : RSI 超买阈值，高于则平仓离场（默认 70）
"""

from collections import deque

from .base import Signal, Strategy
from .registry import register


@register
class EmaRsi(Strategy):
    """EMA 上升趋势过滤 + RSI 超卖做多 / 超买或跌破 EMA 平仓。"""

    name = "ema_rsi"
    category = "trend"
    description = (
        "EMA 趋势过滤 + RSI 均值回归：趋势向上时 RSI<超卖做多，"
        "RSI>超买或收盘跌破 EMA 平仓"
    )
    params = {
        "ema_period": 30,
        "rsi_period": 5,
        "oversold": 30.0,
        "overbought": 70.0,
    }

    def _post_init(self):
        ema_period = int(self.params["ema_period"])
        rsi_period = int(self.params["rsi_period"])
        if ema_period <= 0 or rsi_period <= 0:
            raise ValueError("ema_period/rsi_period 必须为正整数")
        oversold = float(self.params["oversold"])
        overbought = float(self.params["overbought"])
        if not (0 < oversold < overbought < 100):
            raise ValueError("需满足 0 < oversold < overbought < 100")
        self.params["ema_period"] = ema_period
        self.params["rsi_period"] = rsi_period
        self.params["oversold"] = oversold
        self.params["overbought"] = overbought
        # 内部状态：RSI 用最近 rsi_period+1 根收盘价滚动计算；EMA 用递推
        self._closes: deque = deque(maxlen=rsi_period + 1)
        self._ema_prev: float = None
        self._n: int = 0
        self._long: bool = False

    def reset(self):
        """清空内部状态，便于在其它数据集上重放。"""
        self._closes.clear()
        self._ema_prev = None
        self._n = 0
        self._long = False

    # ------------------------------------------------------------------ #
    def on_bar(self, bar) -> Signal:
        close = float(bar["close"])
        self._closes.append(close)
        self._n += 1
        rsi_period = self.params["rsi_period"]
        ema_period = self.params["ema_period"]

        # 递推 EMA：首根用收盘价自身初始化
        alpha = 2.0 / (ema_period + 1.0)
        ema = close if self._ema_prev is None \
            else alpha * close + (1.0 - alpha) * self._ema_prev
        self._ema_prev = ema

        # 需 ema_period 根以上（EMA 有意义）且 RSI 窗口满才判断
        if self._n < ema_period or len(self._closes) < rsi_period + 1:
            return Signal.HOLD

        rsi = _rolling_rsi(self._closes, rsi_period)

        trend_up = close > ema          # 趋势向上：价格站上 EMA
        sig = Signal.HOLD
        if not self._long and trend_up and rsi < self.params["oversold"]:
            sig = Signal.LONG           # 上升趋势中的超卖回调买点
            self._long = True
        elif self._long and (
            rsi > self.params["overbought"] or close < ema
        ):
            sig = Signal.CLOSE          # 超买或跌破 EMA -> 离场
            self._long = False
        return sig


def _rolling_rsi(closes, period: int) -> float:
    """滚动 RSI（SMA 式，非 Wilder 平滑）：用最近 period 根涨跌幅。"""
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
