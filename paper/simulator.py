# -*- coding: utf-8 -*-
"""本地前向模拟器（PaperSimulator）骨架：单标的、支持挂载多个策略。

设计要点
--------
* 撮合语义与 backtest.engine 对齐：策略在每根 bar 收盘时给出信号，
  模拟器按该 bar 收盘价（含手续费 fee_rate、滑点 slippage）撮合记账。
* 状态：cash / quantity（带符号数量，>0 多、<0 空、0 空仓）/
  entry_price / entry_time / entry_equity / trades / equity_curve。
* 多策略：strategies 传列表；每根 bar 全部喂给各策略推进其内部状态，
  但撮合只以第一个策略（主策略）的信号为准（骨架预留，多策略组合后续扩展）。
* 持久化：to_dict / from_dict 输出/还原为纯 JSON 兼容结构
  （时间为 ISO 字符串），可接 SQLite / JSON 落盘（占位，本层不做 I/O）。

撮合规则
--------
* 信号 LONG/SHORT：与当前方向一致则忽略；方向相反则先按当前价平旧仓再开新仓。
* 信号 CLOSE：平仓；HOLD：不动。
* 全仓单仓位：开多用全部可用现金买入，开空按等值名义做空（1 倍）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

import pandas as pd

from strategies.base import Signal, Strategy

DEFAULT_FEE_RATE = 0.001   # 单边手续费率
DEFAULT_SLIPPAGE = 0.0005  # 单边滑点


def _bar_time(bar) -> str:
    """从 bar（dict 或带 name 的 Series）里取出时间并转 ISO 字符串。"""
    ts = None
    if isinstance(bar, pd.Series):
        ts = bar.name
    if ts is None and isinstance(bar, dict):
        for key in ("time", "ts", "timestamp"):
            if bar.get(key) is not None:
                ts = bar[key]
                break
    if ts is None:
        raise ValueError("bar 需提供时间（Series.name 或 time/ts/timestamp 键）")
    if isinstance(ts, pd.Timestamp):
        return ts.isoformat()
    return str(ts)


class PaperSimulator:
    """本地前向模拟器（骨架）。

    用法
    ----
    sim = PaperSimulator("BTC-USDT", [SmaCross(fast=10, slow=30)], initial_cash=10_000)
    for _, bar in df.iterrows():
        sim.update(bar)
    sim.equity_curve / sim.trades  # 结果
    """

    def __init__(
        self,
        inst_id: str,
        strategies: Union[Strategy, List[Strategy]],
        initial_cash: float = 10_000.0,
        fee_rate: float = DEFAULT_FEE_RATE,
        slippage: float = DEFAULT_SLIPPAGE,
    ):
        self.inst_id = inst_id
        # 归一化：始终存列表；撮合只认第一个（主策略）
        self.strategies: List[Strategy] = (
            strategies if isinstance(strategies, list) else [strategies]
        )
        if not self.strategies:
            raise ValueError("至少需要一个策略")

        self.initial_cash = float(initial_cash)
        self.fee_rate = float(fee_rate)
        self.slippage = float(slippage)

        self.reset()

    # ------------------------------------------------------------------ #
    # 状态初始化 / 重置
    # ------------------------------------------------------------------ #
    def reset(self):
        """重置为初始状态（不清除挂载的策略对象）。"""
        self.cash = self.initial_cash
        self.quantity = 0.0        # 带符号持仓量（>0 多 / <0 空）
        self.entry_price: Optional[float] = None
        self.entry_time: Optional[str] = None
        self.entry_equity: Optional[float] = None  # 开仓时的权益（用于算 ret）
        self.trades: List[Dict[str, Any]] = []     # 已平仓交易记录
        self.equity_curve: List[List[Any]] = []    # [[iso时间, equity], ...]
        self._pending: Optional[Dict[str, Any]] = None  # 未平仓的开仓记录

    # ------------------------------------------------------------------ #
    # 估值
    # ------------------------------------------------------------------ #
    def position(self) -> int:
        """当前仓位方向：1 多 / -1 空 / 0 空仓。"""
        if self.quantity > 1e-12:
            return 1
        if self.quantity < -1e-12:
            return -1
        return 0

    def equity(self, close: Optional[float] = None) -> float:
        """按最新价 mark-to-market 权益；close 为 None 时用最近收盘价。"""
        if close is None:
            if not self.equity_curve:
                return self.cash
            close = self.last_close
        return self.cash + self.quantity * close

    @property
    def last_close(self) -> Optional[float]:
        """最近一次 update 的收盘价（供估值参考）。"""
        return getattr(self, "_last_close", None)

    # ------------------------------------------------------------------ #
    # 撮合
    # ------------------------------------------------------------------ #
    def update(self, bar) -> Dict[str, Any]:
        """喂入一根新 bar：推进策略状态 -> 按信号撮合 -> 记账。

        bar 需含 close（及时间，见 _bar_time）。返回本次动作摘要。
        """
        t = _bar_time(bar)
        close = float(bar["close"])
        self._last_close = close

        # 1) 所有策略推进内部状态（保持回放顺序一致）；撮合只取主策略信号
        sig: Signal = self.strategies[0].on_bar(bar)
        for s in self.strategies[1:]:
            s.on_bar(bar)

        action = "none"
        # 2) 撮合
        if sig == Signal.LONG:
            if self.position() < 0:          # 空头 -> 先平后开多
                self._close_position(close, t)
            if self.position() == 0:
                self._open_long(close, t)
                action = "open_long"
        elif sig == Signal.SHORT:
            if self.position() > 0:          # 多头 -> 先平后开空
                self._close_position(close, t)
            if self.position() == 0:
                self._open_short(close, t)
                action = "open_short"
        elif sig == Signal.CLOSE:
            if self.position() != 0:
                self._close_position(close, t)
                action = "close"

        # 3) 记账：记录本 bar 收盘后的权益曲线
        eq = self.equity(close)
        self.equity_curve.append([t, round(eq, 6)])
        return {"time": t, "close": close, "signal": sig.value, "action": action,
                "equity": round(eq, 6)}

    # ------------------------------------------------------------------ #
    # 开/平仓内部实现
    # ------------------------------------------------------------------ #
    def _open_long(self, close: float, t: str):
        """全仓买入开多。"""
        exec_px = close * (1.0 + self.slippage)   # 买入价上移
        fee = self.cash * self.fee_rate
        q = (self.cash - fee) / exec_px
        self.entry_equity = self.cash
        self.cash = 0.0
        self.quantity = q
        self.entry_price = exec_px
        self.entry_time = t
        self._pending = {
            "direction": "long", "entry_time": t,
            "entry_price": round(exec_px, 8),
            "entry_equity": round(self.entry_equity, 6),
        }

    def _open_short(self, close: float, t: str):
        """等值名义做空（借币卖出，1 倍）。"""
        exec_px = close * (1.0 - self.slippage)   # 卖出价下移
        q = self.cash / exec_px                   # 名义价值 = 当前现金
        proceeds = q * exec_px
        fee = proceeds * self.fee_rate
        self.entry_equity = self.cash
        self.cash = self.cash + proceeds - fee
        self.quantity = -q
        self.entry_price = exec_px
        self.entry_time = t
        self._pending = {
            "direction": "short", "entry_time": t,
            "entry_price": round(exec_px, 8),
            "entry_equity": round(self.entry_equity, 6),
        }

    def _close_position(self, close: float, t: str):
        """按市价平掉当前仓位并生成交易记录（t 为平仓时间 ISO 串）。"""
        if self.quantity > 0:    # 平多：卖出
            exec_px = close * (1.0 - self.slippage)
            proceeds = self.quantity * exec_px
            fee = proceeds * self.fee_rate
            self.cash = proceeds - fee
        else:                    # 平空：买回
            q = -self.quantity
            exec_px = close * (1.0 + self.slippage)
            cost = q * exec_px
            fee = cost * self.fee_rate
            self.cash = self.cash - cost - fee

        pnl = self.cash - self.entry_equity if self.entry_equity is not None else 0.0
        ret = pnl / self.entry_equity if self.entry_equity else 0.0

        record = dict(self._pending or {})
        record.update({
            "exit_time": t,
            "exit_price": round(exec_px, 8),
            "pnl": round(pnl, 6),
            "ret": round(ret, 6),
            "status": "closed",
        })
        self.trades.append(record)

        self.quantity = 0.0
        self.entry_price = None
        self.entry_time = None
        self.entry_equity = None
        self._pending = None

    # ------------------------------------------------------------------ #
    # 持久化（JSON 兼容占位）
    # ------------------------------------------------------------------ #
    def to_dict(self) -> Dict[str, Any]:
        """导出为 JSON 兼容 dict（时间均为 ISO 字符串）。"""
        return {
            "inst_id": self.inst_id,
            "initial_cash": self.initial_cash,
            "fee_rate": self.fee_rate,
            "slippage": self.slippage,
            "strategy_names": [s.name for s in self.strategies],
            "state": {
                "cash": round(self.cash, 6),
                "quantity": round(self.quantity, 8),
                "entry_price": self.entry_price,
                "entry_time": self.entry_time,
                "entry_equity": self.entry_equity,
                "last_close": getattr(self, "_last_close", None),
            },
            "trades": self.trades,
            "equity_curve": self.equity_curve,
            "pending": self._pending,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any],
                  strategies: Optional[List[Strategy]] = None) -> "PaperSimulator":
        """从 to_dict 结果还原。

        策略对象无法从 dict 还原，需由调用方注入与保存时一致的策略
        （strategies）；不注入也可还原为"仅状态"的实例供查看，
        但继续撮合前必须注入策略。
        """
        inst = cls.__new__(cls)  # 跳过 __init__ 的校验，手动填充状态
        inst.inst_id = data["inst_id"]
        inst.strategies = list(strategies) if strategies else []
        inst.initial_cash = data["initial_cash"]
        inst.fee_rate = data["fee_rate"]
        inst.slippage = data["slippage"]

        st = data.get("state", {})
        inst.cash = st.get("cash", inst.initial_cash)
        inst.quantity = st.get("quantity", 0.0)
        inst.entry_price = st.get("entry_price")
        inst.entry_time = st.get("entry_time")
        inst.entry_equity = st.get("entry_equity")
        inst._last_close = st.get("last_close")
        inst.trades = [dict(r) for r in data.get("trades", [])]
        inst.equity_curve = [list(x) for x in data.get("equity_curve", [])]
        inst._pending = data.get("pending")
        return inst
