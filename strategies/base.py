# -*- coding: utf-8 -*-
"""策略基类与信号常量。

信号语义（撮合时按此解释）：
* LONG  : 开多 / 翻多（若已持多则忽略）
* SHORT : 开空 / 翻空
* CLOSE : 平仓转空仓
* HOLD  : 维持当前仓位不动（引擎/模拟器按"不动作"处理）

on_bar 约定：每个 bar 调用一次，输入单根 K 线（dict 或 pandas Series，
含 open/high/low/close/vol），返回一个 Signal。
"""

from enum import Enum


class Signal(str, Enum):
    """策略输出信号常量。"""

    LONG = "long"    # 做多
    SHORT = "short"  # 做空
    CLOSE = "close"  # 平仓
    HOLD = "hold"    # 保持不动（观望）


class Strategy:
    """策略基类：子类需实现 on_bar，并通过类属性给出 name / params 默认值。"""

    #: 策略标识名（子类覆盖）
    name: str = "base"
    #: 参数名 -> 默认值（子类覆盖；运行时可用 kwargs 覆盖）
    params: dict = {}

    def __init__(self, **kwargs):
        """以 params 默认值初始化，kwargs 覆盖同名参数。"""
        self.params = dict(self.__class__.params)
        unknown = set(kwargs) - set(self.params)
        if unknown:
            raise ValueError(
                f"策略 {self.name} 收到未知参数 {sorted(unknown)}，"
                f"可用参数：{sorted(self.params)}"
            )
        self.params.update(kwargs)
        # 子类可在这里做参数预处理（如周期取整）
        self._post_init()

    def _post_init(self):
        """子类可选覆写：参数校验 / 内部状态初始化。"""

    def on_bar(self, bar) -> Signal:
        """输入一根 K 线，返回 Signal（子类必须实现）。"""
        raise NotImplementedError(
            f"策略 {self.name} 未实现 on_bar"
        )

    def reset(self):
        """重置策略内部状态（如需在新数据集上重放时调用）。"""

    def __repr__(self) -> str:  # pragma: no cover - 仅调试
        return f"<{self.__class__.__name__} name={self.name} params={self.params}>"
