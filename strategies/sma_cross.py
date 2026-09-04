# -*- coding: utf-8 -*-
"""SMA 双均线示例策略（金叉做多 / 死叉做空，单标的）。

状态由策略内部维护：每次 on_bar 输入一根 K 线，
策略依据"最新收盘价相对前一根收盘价是否发生均线交叉"输出信号。

参数
----
fast : 快线周期（默认 10）
slow : 慢线周期（默认 30，须大于 fast）
"""

from collections import deque

from .base import Signal, Strategy
from .registry import register


@register
class SmaCross(Strategy):
    """双均线交叉策略：快线上穿慢线 -> LONG；下穿 -> SHORT。"""

    name = "sma_cross"
    params = {"fast": 10, "slow": 30}

    def _post_init(self):
        fast = int(self.params["fast"])
        slow = int(self.params["slow"])
        if fast <= 0 or slow <= 0:
            raise ValueError("fast/slow 必须为正整数")
        if slow <= fast:
            raise ValueError("slow 必须大于 fast")
        self.params["fast"] = fast
        self.params["slow"] = slow
        # 内部状态：收盘价缓冲（需 slow+1 根才能算"当前/上一根"慢均线）与上一信号
        self._closes: deque = deque(maxlen=slow + 1)
        self._prev_signal: Signal = Signal.HOLD

    def reset(self):
        """清空内部状态，便于在其它数据集上重放。"""
        self._closes.clear()
        self._prev_signal = Signal.HOLD

    # ------------------------------------------------------------------ #
    def on_bar(self, bar) -> Signal:
        close = float(bar["close"])
        self._closes.append(close)

        # 需 slow+1 根收盘价才能同时算出"当前"与"上一根"慢均线，不足则不发信号
        if len(self._closes) < self.params["slow"] + 1:
            return Signal.HOLD

        fast = self.params["fast"]
        slow = self.params["slow"]
        closes = list(self._closes)

        fast_ma = sum(closes[-fast:]) / fast
        slow_ma = sum(closes[-slow:]) / slow
        # 与上一根收盘的均线值比较，判断是否发生交叉
        prev_fast = sum(closes[-fast - 1:-1]) / fast
        prev_slow = sum(closes[-slow - 1:-1]) / slow

        sig = Signal.HOLD
        if prev_fast <= prev_slow and fast_ma > slow_ma:
            sig = Signal.LONG      # 金叉
        elif prev_fast >= prev_slow and fast_ma < slow_ma:
            sig = Signal.SHORT     # 死叉

        self._prev_signal = sig
        return sig
