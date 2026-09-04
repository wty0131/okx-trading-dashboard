# -*- coding: utf-8 -*-
"""唐奇安通道突破策略（单标的，多头版本，海龟风格简化）。

思路
----
海龟交易法的核心：价格创出新高意味着趋势启动。本策略只做多：
* 做多：收盘价向上突破前 entry_n 根 K 线的最高价（不含当前根）-> LONG；
* 平仓：持多后收盘价跌破前 exit_n 根 K 线的最低价（不含当前根）-> CLOSE。

开仓后以 HOLD 持有（不加仓），只在从空仓转为突破、或从持多转为跌破
时发信号。退出通道（exit_n）通常小于入场通道（entry_n），让利润奔跑、
及时止损。只做多不做空。

参数
----
entry_n : 入场突破通道长度 N（默认 20）
exit_n  : 离场通道长度 M（默认 10，海龟风格取 M < N）
"""

from collections import deque

from .base import Signal, Strategy
from .registry import register


@register
class DonchianBreakout(Strategy):
    """唐奇安通道突破：N 日高点突破做多，M 日低点跌破平仓。"""

    name = "donchian_breakout"
    category = "breakout"
    description = (
        "唐奇安通道突破（海龟风格）：收盘突破前 N 根高点做多，"
        "跌破前 M 根低点平仓（只做多）"
    )
    params = {"entry_n": 20, "exit_n": 10}

    def _post_init(self):
        entry_n = int(self.params["entry_n"])
        exit_n = int(self.params["exit_n"])
        if entry_n <= 0 or exit_n <= 0:
            raise ValueError("entry_n/exit_n 必须为正整数")
        self.params["entry_n"] = entry_n
        self.params["exit_n"] = exit_n
        # 内部状态：前一根起算的通道（判断时先查通道、后把当前根压入）
        self._highs: deque = deque(maxlen=entry_n)
        self._lows: deque = deque(maxlen=exit_n)
        self._long: bool = False

    def reset(self):
        """清空内部状态，便于在其它数据集上重放。"""
        self._highs.clear()
        self._lows.clear()
        self._long = False

    # ------------------------------------------------------------------ #
    def on_bar(self, bar) -> Signal:
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])

        # 通道判定用"前 entry_n/exit_n 根"（不含当前根），故先查后压
        sig = Signal.HOLD
        if (not self._long and len(self._highs) >= self.params["entry_n"]
                and close > max(self._highs)):
            sig = Signal.LONG            # 突破 N 日高点 -> 趋势启动
            self._long = True
        elif (self._long and len(self._lows) >= self.params["exit_n"]
                and close < min(self._lows)):
            sig = Signal.CLOSE           # 跌破 M 日低点 -> 离场
            self._long = False

        self._highs.append(high)
        self._lows.append(low)
        return sig
