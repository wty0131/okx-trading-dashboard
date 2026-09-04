# -*- coding: utf-8 -*-
"""MACD 金叉死叉趋势策略（单标的，双向趋势跟踪）。

思路
----
MACD = 快线 EMA(fast) - 慢线 EMA(slow)，信号线 = MACD 的 EMA(signal)。
金叉（MACD 上穿信号线）视为趋势转多 -> LONG；死叉（下穿）视为趋势
转空 -> SHORT。与 SmaCross 风格一致：信号即"目标仓位"，发出 LONG 后
引擎持续持多，直到出现 SHORT（翻空）为止；不另发平仓信号。

实现方式：递推 EMA 维护 MACD 与信号线，跨根比较判断交叉；预热期
（不足 slow 根）不发信号，其余时间 HOLD。

参数
----
fast   : 快线 EMA 周期（默认 12）
slow   : 慢线 EMA 周期（默认 26，须大于 fast）
signal : 信号线 EMA 周期（默认 9）
"""

from .base import Signal, Strategy
from .registry import register


@register
class MacdTrend(Strategy):
    """MACD 趋势策略：金叉做多 / 死叉做空。"""

    name = "macd_trend"
    category = "trend"
    description = (
        "MACD 金叉死叉趋势跟踪：MACD 上穿信号线做多，下穿做空"
    )
    params = {"fast": 12, "slow": 26, "signal": 9}

    def _post_init(self):
        fast = int(self.params["fast"])
        slow = int(self.params["slow"])
        signal = int(self.params["signal"])
        if fast <= 0 or slow <= 0 or signal <= 0:
            raise ValueError("fast/slow/signal 必须为正整数")
        if slow <= fast:
            raise ValueError("slow 必须大于 fast")
        self.params["fast"] = fast
        self.params["slow"] = slow
        self.params["signal"] = signal
        # 内部状态：EMA 递推值、上一根 MACD/信号线、已处理根数与预热计数
        self._ema_fast: float = None
        self._ema_slow: float = None
        self._ema_sig: float = None
        self._macd_prev: float = None
        self._sig_prev: float = None
        self._n: int = 0

    def reset(self):
        """清空内部状态，便于在其它数据集上重放。"""
        self._ema_fast = None
        self._ema_slow = None
        self._ema_sig = None
        self._macd_prev = None
        self._sig_prev = None
        self._n = 0

    # ------------------------------------------------------------------ #
    def on_bar(self, bar) -> Signal:
        close = float(bar["close"])
        self._n += 1
        fast = self.params["fast"]
        slow = self.params["slow"]

        # 递推 EMA（首根用收盘价自身初始化）
        alpha_f = 2.0 / (fast + 1.0)
        alpha_s = 2.0 / (slow + 1.0)
        self._ema_fast = close if self._ema_fast is None \
            else alpha_f * close + (1.0 - alpha_f) * self._ema_fast
        self._ema_slow = close if self._ema_slow is None \
            else alpha_s * close + (1.0 - alpha_s) * self._ema_slow

        macd = self._ema_fast - self._ema_slow

        # 信号线 EMA：MACD 自身的递推 EMA（首根用 MACD 初始化）
        alpha_sig = 2.0 / (self.params["signal"] + 1.0)
        self._ema_sig = macd if self._ema_sig is None \
            else alpha_sig * macd + (1.0 - alpha_sig) * self._ema_sig

        sig = Signal.HOLD
        # 预热：慢线 EMA 收敛需至少 slow 根，且需有上一根 MACD/信号线可比较
        if self._n >= slow and self._sig_prev is not None:
            if self._macd_prev <= self._sig_prev and macd > self._ema_sig:
                sig = Signal.LONG        # 金叉
            elif self._macd_prev >= self._sig_prev and macd < self._ema_sig:
                sig = Signal.SHORT       # 死叉

        self._macd_prev = macd
        self._sig_prev = self._ema_sig
        return sig
