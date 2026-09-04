# -*- coding: utf-8 -*-
"""策略库自测：合成随机游走 K 线跑通全部已注册策略 -> 回测。

覆盖 7 个策略（SmaCross + 6 个新增的 K 线加密货币策略包成员）：
对每个策略用默认参数实例化，apply_strategy 生成信号 -> run_backtest，
断言指标键齐全、净值非空合理、且至少触发过 1 笔已平仓交易
（若某策略无交易会打印原因，便于排查而非静默通过）。

运行（用项目 .venv）：
    cd C:\\Users\\wty0131\\okx_system
    .venv\\Scripts\\python scripts\\selfcheck_strategies.py

全部通过输出末尾打印 PASS；任一步失败抛出 AssertionError / 异常并退出非 0。
完全离线，不发网络请求。
"""

import sys
from pathlib import Path

# 允许从任意 cwd 运行：把项目根加入 sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

import strategies  # noqa: F401  导入即完成全部策略注册
from backtest.engine import apply_strategy, run_backtest
from strategies.base import Strategy
from strategies.registry import REGISTRY, available

N_BARS = 1000
BAR = "1H"
SEED = 42            # 固定随机种子（可复现）
EXPECTED = {
    "sma_cross", "ema_rsi", "bollinger_reversion", "macd_trend",
    "donchian_breakout", "rsi2_reversion", "momentum_follow",
}
METRIC_KEYS = {
    "total_return", "annual_return", "max_drawdown",
    "sharpe", "win_rate", "trade_count", "final_equity",
}


def make_synthetic_ohlcv(n: int = N_BARS, seed: int = SEED) -> pd.DataFrame:
    """随机游走生成 n 根小时级 OHLCV（固定种子，可复现）。"""
    rng = np.random.default_rng(seed)
    step = rng.normal(0.0, 0.002, n)                       # 每小时收益 ~ N(0, 0.2%)
    close = 100.0 * np.cumprod(1.0 + step)

    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    noise = np.abs(rng.normal(0.0, 0.001, n))
    open_ = np.r_[close[0], close[:-1]] * (1.0 + rng.normal(0, 0.0003, n))
    high = np.maximum(open_, close) * (1.0 + noise)
    low = np.minimum(open_, close) * (1.0 - noise)
    vol = rng.uniform(0.5, 5.0, n)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "vol": vol},
        index=idx,
    )
    df.index.name = "time"
    return df


def run_one(df: pd.DataFrame, strategy) -> dict:
    """单策略全链路：信号 -> 回测 -> 汇总断言结果。"""
    name = strategy.name
    st = strategy()                        # 默认参数实例化

    # ---- 可复用性：reset() 后重放应与首次完全一致 ----
    first = apply_strategy(df, st)
    st.reset()
    replayed = apply_strategy(df, st)
    assert first.equals(replayed), \
        f"[{name}] reset() 后重放信号不一致（内部状态未清干净）"

    signals = replayed
    nav, trades, metrics = run_backtest(
        df, signals, initial_cash=10_000.0,
        fee_rate=0.001, slippage=0.0005,
    )

    # ---- 断言：指标键齐全 ----
    missing = METRIC_KEYS - set(metrics)
    assert not missing, f"[{name}] 指标缺键：{sorted(missing)} -> {sorted(metrics)}"
    # ---- 断言：净值非空、无 NaN、长度一致、期末为正 ----
    assert nav is not None and len(nav) == len(df), \
        f"[{name}] 净值长度 {0 if nav is None else len(nav)} != K 线数 {len(df)}"
    assert nav.notna().all(), f"[{name}] 净值序列不应含 NaN"
    assert float(nav.iloc[-1]) > 0, f"[{name}] 期末总资产必须 > 0"
    # ---- 断言：信号列形态正确 ----
    assert set(signals.columns) <= {"long", "short", "close"}, \
        f"[{name}] 信号列异常：{list(signals.columns)}"
    # ---- 交易统计 ----
    closed = metrics["trade_count"]
    open_trades = int((trades["status"] == "open").sum()) if not trades.empty else 0
    n_long = int(signals["long"].sum())
    n_short = int(signals["short"].sum()) if "short" in signals else 0
    n_close = int(signals["close"].sum()) if "close" in signals else 0

    reason = None
    if closed == 0:
        if (n_long + n_short) == 0:
            reason = "整个数据集未出现任何开仓信号（条件从未满足/预热不足）"
        elif open_trades == 0:
            reason = "出现过开仓信号但从未触发平仓/翻转，无已平仓交易"
        else:
            reason = f"仅剩 {open_trades} 笔期末未平仓，无已平仓交易"

    return {
        "name": name, "params": dict(st.params), "closed": closed,
        "open": open_trades, "n_long": n_long, "n_short": n_short,
        "n_close": n_close, "reason": reason,
        "total_return": metrics["total_return"], "final_equity": metrics["final_equity"],
        "max_drawdown": metrics["max_drawdown"],
    }


def main():
    print(f"== 策略库自测 == python={sys.version.split()[0]} "
          f"pandas={pd.__version__} numpy={np.__version__} "
          f"seed={SEED} n_bars={N_BARS}")

    # ---- 0) 注册完整性 ----
    names = set(available())
    missing_reg = EXPECTED - names
    assert not missing_reg, f"注册表缺少策略：{sorted(missing_reg)}"
    assert len(names) == len(EXPECTED), f"注册表多余策略：{sorted(names - EXPECTED)}"
    assert all(issubclass(cls, Strategy) for cls in REGISTRY), \
        "REGISTRY 中应有且仅有 Strategy 子类"
    print(f"[0] 注册表：{len(names)} 个策略已自动收集 -> "
          f"{', '.join(sorted(names))}")

    # ---- 1) 合成数据 ----
    df = make_synthetic_ohlcv()
    print(f"[1] 合成数据：{len(df)} 根 {BAR} K 线 "
          f"{df.index[0]} ~ {df.index[-1]}，收盘 {df['close'].iloc[0]:.2f} -> "
          f"{df['close'].iloc[-1]:.2f}")

    # ---- 2) 逐策略回测 ----
    results = [run_one(df, cls) for cls in REGISTRY]

    # ---- 2.5) 参数校验冒烟：非法参数须抛 ValueError ----
    _bad_params = {
        "ema_rsi": {"ema_period": -1},
        "bollinger_reversion": {"k": 0.0},
        "macd_trend": {"fast": 30, "slow": 10},
        "donchian_breakout": {"exit_n": 0},
        "rsi2_reversion": {"oversold": 95, "overbought": 90},
        "momentum_follow": {"lookback": 0},
    }
    for cls in REGISTRY:
        bad = _bad_params.get(cls.name)
        if not bad:
            continue
        try:
            cls(**bad)
            raise AssertionError(f"[{cls.name}] 非法参数 {bad} 应抛 ValueError")
        except ValueError:
            pass
    print(f"[1.5] 参数校验：{sum(1 for c in REGISTRY if c.name in _bad_params)}/"
          f"{len(_bad_params)} 个策略非法参数正确抛错")

    print("-" * 100)
    header = (f"{'策略':<20}{'已平仓':>6}{'持仓中':>6}"
              f"{'LONG':>6}{'SHORT':>6}{'CLOSE':>6}"
              f"{'总收益':>10}{'期末净值':>12}{'最大回撤':>10}")
    print(header)
    for r in sorted(results, key=lambda x: x["name"]):
        print(f"{r['name']:<20}{r['closed']:>6}{r['open']:>6}"
              f"{r['n_long']:>6}{r['n_short']:>6}{r['n_close']:>6}"
              f"{r['total_return']:>10.2%}{r['final_equity']:>12.2f}"
              f"{r['max_drawdown']:>10.2%}")

    # ---- 3) 交易充分性：每策略至少 1 笔已平仓交易，否则打印原因 ----
    print("-" * 100)
    idle = [r for r in results if r["closed"] == 0]
    for r in idle:
        print(f"[注意] {r['name']} 无已平仓交易（params={r['params']}）：{r['reason']}")
    assert not idle, (
        f"{len(idle)} 个策略未触发任何已平仓交易，原因已在上方打印"
    )
    print(f"[2] 交易充分性：{len(results)}/7 个策略均至少触发 1 笔已平仓交易")

    print("=" * 46)
    print("PASS：策略库自测全部通过 ✔")


if __name__ == "__main__":
    main()
