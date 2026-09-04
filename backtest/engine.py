# -*- coding: utf-8 -*-
"""简化向量化回测引擎（单标的、单仓位：多/空/空仓）。

核心约定
--------
* 持仓目标（target）：由 signals_df 的列推导，纯向量化（pandas ffill）。
  - long  : 该 bar 目标=做多(+1)
  - short : 该 bar 目标=做空(-1)
  - close : 该 bar 目标=空仓(0)
  - 未出现的列 / 该 bar 全 False : 维持上一根持仓（首根之前视为空仓）
  - 同一根出现多个信号时优先级：close > short > long
* 执行：持仓在 bar 收盘价处生效，费用/滑点在该 bar 变动时一次性扣减；
  收益率按 收盘价到收盘价 计算。
* 成本：每次"单位仓位变动"按成本率 cost = fee_rate + slippage 计
  （滑点按成交价偏移建模为单边成本，简化处理）。

返回 (nav, trades, metrics)
---------------------------
nav    : pd.Series，净值曲线（含初始资金），时间索引同 df
trades : pd.DataFrame，逐笔交易记录（entry/exit 均为成交执行价）
metrics: dict 指标：
    total_return 总收益率 / annual_return 年化收益率 /
    max_drawdown 最大回撤 / sharpe 夏普比率 / win_rate 胜率 /
    trade_count 交易次数（含期末未平仓单）
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

# 默认参数
DEFAULT_FEE_RATE = 0.001      # 单边手续费率
DEFAULT_SLIPPAGE = 0.0005     # 单边滑点

#: 年化用小时数（365.25 天）
_HOURS_PER_YEAR = 365.25 * 24
_SECONDS_PER_YEAR = _HOURS_PER_YEAR * 3600


# ---------------------------------------------------------------------- #
# 信号 -> signals_df
# ---------------------------------------------------------------------- #
def apply_strategy(df: pd.DataFrame, strategy) -> pd.DataFrame:
    """把逐根 K 线喂给策略，生成引擎用的 signals_df（布尔列 long/short/close）。

    df 需含 open/high/low/close/vol，按时间升序；
    strategy 需实现 on_bar(bar) -> Signal（见 strategies.base）。
    """
    rows = {"long": [], "short": [], "close": []}
    for _, bar in df.iterrows():
        sig = strategy.on_bar(bar)
        # Signal 为 str 枚举：统一取 .value（或原生字符串本身）再与列名比较
        name = getattr(sig, "value", sig)
        for key in rows:
            rows[key].append(name == key)
    return pd.DataFrame(rows, index=df.index)


# ---------------------------------------------------------------------- #
# 主回测函数
# ---------------------------------------------------------------------- #
def run_backtest(
    df: pd.DataFrame,
    signals_df: pd.DataFrame,
    initial_cash: float = 10_000.0,
    fee_rate: float = DEFAULT_FEE_RATE,
    slippage: float = DEFAULT_SLIPPAGE,
    bars_per_year: Optional[float] = None,
):
    """简化向量化回测。

    参数
    ----
    df           : OHLCV DataFrame，时间索引升序，列 open/high/low/close/vol
    signals_df   : 与 df 同索引的布尔信号表，列 subset of {long, short, close}
    initial_cash : 初始资金（>=0）
    fee_rate     : 单边手续费率（默认 0.001）
    slippage     : 单边滑点（默认 0.0005）
    bars_per_year: 每年 K 线数；None 时按时间索引中位间隔自动推算
                   （用于年化与夏普）。

    返回 (nav, trades, metrics)，详见模块 docstring。
    """
    if df.empty or len(df) < 2:
        raise ValueError("df 至少需要 2 根 K 线")
    if initial_cash <= 0:
        raise ValueError("initial_cash 必须为正数")

    close = df["close"].astype(float)
    n = len(df)

    # ---------- 1. 目标持仓序列（向量化） ----------
    pos = _resolve_position(df.index, signals_df, n)

    # ---------- 2. 逐 bar 收益率与仓位变动成本 ----------
    r = close.pct_change().fillna(0.0).to_numpy()  # r[t] = close[t]/close[t-1]-1, r[0]=0
    cost = fee_rate + slippage

    # 净值递推：bar0 按 pos[0] 建仓（收成本）-> 之后每根先吃行情再在收盘变动
    equity = float(initial_cash)
    nav = np.empty(n)
    nav[0] = equity * (1.0 - cost) ** abs(pos[0])
    equity = nav[0]
    for t in range(1, n):
        # 区间收益：持仓 pos[t-1] 在 close[t-1] -> close[t] 上的表现
        equity *= 1.0 + pos[t - 1] * r[t]
        # bar t 收盘时若变动持仓，按单位变动数收成本（含直接翻仓=2 单位）
        if pos[t] != pos[t - 1]:
            equity *= (1.0 - cost) ** abs(pos[t] - pos[t - 1])
        nav[t] = equity

    nav = pd.Series(nav, index=df.index, name="nav")

    # ---------- 3. 交易记录 ----------
    trades = _build_trades(df.index, close.to_numpy(), pos, cost, fee_rate, slippage)

    # ---------- 4. 指标 ----------
    metrics = _compute_metrics(nav, trades, bars_per_year, initial_cash)

    return nav, trades, metrics


# ---------------------------------------------------------------------- #
# 内部实现
# ---------------------------------------------------------------------- #
def _resolve_position(index: pd.Index, signals_df: pd.DataFrame, n: int) -> np.ndarray:
    """把动作式信号列解析为逐 bar 目标持仓数组 [-1, 0, 1]。"""
    intent = np.full(n, np.nan, dtype=float)
    sig = signals_df.reindex(index)  # 对齐 df 索引，缺失行视为无信号

    def apply_column(key: str, value: float):
        if key in sig.columns:
            mask = sig[key].fillna(False).astype(bool).to_numpy()
            intent[mask] = value

    # 写入顺序决定优先级：close(0) 最后写 -> 优先级 close > short > long
    apply_column("long", 1.0)
    apply_column("short", -1.0)
    apply_column("close", 0.0)

    # NaN(无信号) 前向填充维持原仓；最前端的 NaN 视为空仓
    pos = pd.Series(intent, index=index).ffill().fillna(0.0)
    return pos.to_numpy()


def _exec_price(base: float, direction: int, slippage: float) -> float:
    """按方向给出含滑点的成交价。direction: +1 买入(吃滑点上移) / -1 卖出(下移)。"""
    return base * (1.0 + direction * slippage)


def _build_trades(index: pd.Index, close: np.ndarray, pos: np.ndarray,
                  cost: float, fee_rate: float, slippage: float) -> pd.DataFrame:
    """从持仓序列还原逐笔交易。

    持仓连续同号段视为一笔：段起点 bar a 开仓（成交价=close[a] 含滑点），
    段终点 bar b 之后若平仓/翻仓则于 bar b+1 收盘成交；若数据末尾仍未平仓，
    标记为 open（status='open'），exit 为空。
    """
    n = len(pos)
    records = []
    run_start = None   # 当前段的起点 bar
    run_sign = 0       # 当前段方向 +1/-1

    def _close_run(end_bar: int, exit_known: bool):
        """结算 run_start..end_bar 这一段。"""
        nonlocal run_start, run_sign
        a = run_start
        sgn = run_sign
        entry_raw = close[a]
        # 开仓：多=买入价上移滑点，空=卖出价下移滑点
        entry_px = _exec_price(entry_raw, sgn, slippage)
        if exit_known:
            x = end_bar + 1
            exit_raw = close[x]
            # 平仓：多=卖出下移，空=买回上移
            exit_px = _exec_price(exit_raw, -sgn, slippage)
            # 单笔收益率（含双边手续费，滑点已体现在执行价里）
            if sgn > 0:
                gross = exit_px / entry_px
            else:
                gross = entry_px / exit_px
            ret = gross * (1.0 - fee_rate) ** 2 - 1.0
            records.append({
                "direction": "long" if sgn > 0 else "short",
                "entry_time": index[a], "exit_time": index[x],
                "entry_price": round(entry_px, 8), "exit_price": round(exit_px, 8),
                "ret": ret, "status": "closed",
                "bars": x - a,
            })
        else:
            # 期末仍未平仓：按最新收盘价做未实现结算参考，但不算作完成交易
            mark = close[n - 1]
            if sgn > 0:
                gross = mark / entry_px
            else:
                gross = entry_px / mark
            ret = gross * (1.0 - fee_rate) ** 2 - 1.0
            records.append({
                "direction": "long" if sgn > 0 else "short",
                "entry_time": index[a], "exit_time": None,
                "entry_price": round(entry_px, 8), "exit_price": round(mark, 8),
                "ret": ret, "status": "open",
                "bars": n - a,
            })
        run_start, run_sign = None, 0

    for t in range(n):
        p = pos[t]
        if p == run_sign and p != 0:
            continue  # 段内延续
        # 与当前段不同 -> 先结旧段（只要存在且开仓后有任何变动）
        if run_sign != 0:
            # 结段发生在 bar t（变动生效于 close[t]，即该处已有 t>=? ）：退出点 close[t]
            if t < n:
                _close_run(t - 1, True)
        # 开新段
        if p != 0:
            run_start, run_sign = t, int(p)
    # 数据末尾可能仍持有
    if run_sign != 0:
        _close_run(n - 1, False)

    trades = pd.DataFrame(records)
    if not trades.empty:
        trades.index.name = "trade_id"
    return trades


def _infer_bars_per_year(index: pd.Index) -> Optional[float]:
    """按时间索引中位间隔推算每年 K 线数；无法推算返回 None。"""
    if not isinstance(index, pd.DatetimeIndex):
        return None
    if len(index) < 2:
        return None
    diffs = index.to_series().diff().dropna()
    if diffs.empty:
        return None
    median_s = float(diffs.median().total_seconds())
    if median_s <= 0:
        return None
    return _SECONDS_PER_YEAR / median_s


def _compute_metrics(nav: pd.Series, trades: pd.DataFrame,
                     bars_per_year: Optional[float],
                     initial_cash: float) -> Dict[str, float]:
    """汇总指标。"""
    n = len(nav)
    total_return = float(nav.iloc[-1] / initial_cash - 1.0)

    if bars_per_year is None:
        bars_per_year = _infer_bars_per_year(nav.index)
    if bars_per_year is None:
        annual_return = float("nan")
    else:
        annual_return = float((1.0 + total_return) ** (bars_per_year / (n - 1)) - 1.0)

    # 最大回撤（基于净值曲线）
    cummax = nav.cummax()
    drawdown = nav / cummax - 1.0
    max_drawdown = float(max(0.0, -drawdown.min()))

    # 夏普：用逐 bar 净值收益率，无风险利率取 0
    bar_ret = nav.pct_change().dropna()
    std = float(bar_ret.std(ddof=1)) if len(bar_ret) > 1 else 0.0
    if bars_per_year and std > 0:
        sharpe = float(bar_ret.mean() / std * np.sqrt(bars_per_year))
    else:
        sharpe = float("nan")

    closed = trades[trades["status"] == "closed"] if not trades.empty else trades
    trade_count = int(len(closed))
    win_rate = float((closed["ret"] > 0).mean()) if trade_count else float("nan")

    return {
        "total_return": round(total_return, 6),
        "annual_return": round(annual_return, 6) if not np.isnan(annual_return) else None,
        "max_drawdown": round(max_drawdown, 6),
        "sharpe": round(sharpe, 6) if not np.isnan(sharpe) else None,
        "win_rate": round(win_rate, 6) if not np.isnan(win_rate) else None,
        "trade_count": trade_count,
        "final_equity": round(float(nav.iloc[-1]), 4),
    }
