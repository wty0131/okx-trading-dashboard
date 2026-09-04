# -*- coding: utf-8 -*-
"""迷你自测：合成 OHLCV 跑通 策略 -> 回测 -> 模拟器 -> 持久化往返。

运行（用项目 .venv）：
    cd C:\\Users\\wty0131\\okx_system
    .venv\\Scripts\\python scripts\\selfcheck.py

全部通过输出末尾打印 PASS；任一步失败抛出 AssertionError / 异常并退出非 0。
默认完全离线（不发网络请求）；OKX 签名与凭据缺失提示只做本地校验。
"""

import json
import os
import sys
from pathlib import Path

# 允许从任意 cwd 运行：把项目根加入 sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from backtest.engine import apply_strategy, run_backtest
from okx.okx_client import OkxClient, sign_request
from paper.simulator import PaperSimulator
from strategies.base import Signal, Strategy
from strategies.registry import REGISTRY
from strategies.sma_cross import SmaCross

N_BARS = 500
BAR = "1H"


def make_synthetic_ohlcv(n: int = N_BARS, seed: int = 42) -> pd.DataFrame:
    """随机游走生成 n 根小时级 OHLCV（固定种子，可复现）。"""
    rng = np.random.default_rng(seed)
    step = rng.normal(0.0, 0.002, n)                      # 每小时收益 ~ N(0, 0.2%)
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


# ---------------------------------------------------------------------- #
# 1) 策略：SmaCross 已注册
# ---------------------------------------------------------------------- #
def check_strategy():
    assert SmaCross in REGISTRY, "SmaCross 应通过 @register 注册"
    try:
        SmaCross(fast=5, slow=3)
        raise AssertionError("slow<=fast 时应抛错")
    except ValueError:
        pass
    # 快速烟测 on_bar 返回类型
    st = SmaCross(fast=2, slow=5)
    bar = {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "vol": 1.0}
    sig = st.on_bar(bar)
    assert isinstance(sig, Signal)
    print("[1/5] 策略：SmaCross 注册与基础行为 OK")


# ---------------------------------------------------------------------- #
# 2) 回测
# ---------------------------------------------------------------------- #
def check_backtest(df: pd.DataFrame):
    sma = SmaCross(fast=10, slow=30)
    signals = apply_strategy(df, sma)
    assert list(signals.columns) == ["long", "short", "close"]
    assert len(signals) == N_BARS

    nav, trades, metrics = run_backtest(
        df, signals, initial_cash=10_000.0,
        fee_rate=0.001, slippage=0.0005,
    )
    # 指标键齐全
    required_keys = {
        "total_return", "annual_return", "max_drawdown",
        "sharpe", "win_rate", "trade_count",
    }
    missing = required_keys - set(metrics)
    assert not missing, f"指标缺键：{missing} -> 实际 {sorted(metrics)}"
    # 净值合理：无 NaN、总资产 > 0、曲线长度一致
    assert nav.notna().all(), "净值序列不应含 NaN"
    assert nav.iloc[0] == 10_000.0, "空仓起步时首日净值应等于初始资金"
    assert nav.iloc[-1] > 0, "期末总资产必须 > 0"
    assert len(nav) == N_BARS and len(trades) == metrics["trade_count"] + int(
        (trades["status"] == "open").sum() if not trades.empty else 0
    )
    print(f"[2/5] 回测：bar={N_BARS} 交易数={metrics['trade_count']} "
          f"总收益={metrics['total_return']:.2%} "
          f"期末净值={metrics['final_equity']:.2f} OK")
    return nav, trades, metrics


# ---------------------------------------------------------------------- #
# 3) 模拟器
# ---------------------------------------------------------------------- #
def check_simulator(df: pd.DataFrame):
    sim = PaperSimulator(
        "BTC-USDT", [SmaCross(fast=10, slow=30)],
        initial_cash=10_000.0, fee_rate=0.001, slippage=0.0005,
    )
    for _, bar in df.iterrows():
        sim.update(bar)

    # 权益曲线逐根记录
    assert len(sim.equity_curve) == N_BARS, \
        f"equity_curve 应为 {N_BARS} 条，实际 {len(sim.equity_curve)}"
    final_eq = sim.equity_curve[-1][1]
    assert final_eq > 0, "期末总资产必须 > 0"
    # 交易记录字段齐全
    for tr in sim.trades:
        for key in ("direction", "entry_time", "entry_price",
                    "exit_time", "exit_price", "pnl", "ret", "status"):
            assert key in tr, f"交易记录缺字段 {key}"
    print(f"[3/5] 模拟器：trades={len(sim.trades)} "
          f"期末权益={final_eq:.2f} OK")
    return sim


# ---------------------------------------------------------------------- #
# 4) JSON 序列化往返
# ---------------------------------------------------------------------- #
def check_json_roundtrip(sim: PaperSimulator):
    d1 = sim.to_dict()
    restored = PaperSimulator.from_dict(
        d1, strategies=[SmaCross(fast=10, slow=30)]
    )
    d2 = restored.to_dict()
    s1 = json.dumps(d1, ensure_ascii=False, sort_keys=True, default=str)
    s2 = json.dumps(d2, ensure_ascii=False, sort_keys=True, default=str)
    assert s1 == s2, "to_dict/from_dict 往返不一致"
    print("[4/5] 持久化：to_dict/from_dict JSON 往返一致 OK")


# ---------------------------------------------------------------------- #
# 5) OKX 客户端离线校验（签名 + 凭据缺失提示，不发网络请求）
# ---------------------------------------------------------------------- #
def check_okx_client_offline():
    # 签名头齐全且为字符串
    headers = sign_request(
        "GET", "/api/v5/market/candles",
        query="instId=BTC-USDT&bar=1H&limit=100",
        api_key="k", secret="s", passphrase="p",
    )
    for h in ("OK-ACCESS-KEY", "OK-ACCESS-SIGN",
              "OK-ACCESS-TIMESTAMP", "OK-ACCESS-PASSPHRASE"):
        assert h in headers and isinstance(headers[h], str) and headers[h], h
    assert len(headers["OK-ACCESS-SIGN"]) > 20, "base64 签名过短"

    # 凭据缺失（环境变量无 OKX_*）时私有方法应抛错并提示 .env
    os.environ.pop("OKX_API_KEY", None)
    os.environ.pop("OKX_SECRET", None)
    os.environ.pop("OKX_PASSPHRASE", None)
    client = OkxClient()
    for fn in (client.get_balance, client.get_positions):
        try:
            fn()  # dry_run 默认 True 也需先有凭据
            raise AssertionError("缺少凭据时应抛错")
        except RuntimeError as exc:
            assert "请在 .env 配置" in str(exc), str(exc)

    # get_candles 参数白名单（不真正请求）
    try:
        client.get_candles("BTC-USDT", bar="2x")
        raise AssertionError("非法 bar 应抛错")
    except ValueError:
        pass
    print("[5/5] OKX 客户端：签名与凭据缺失提示离线校验 OK")


# ---------------------------------------------------------------------- #
def main():
    print(f"== okx_system 基础层自测 ==  python={sys.version.split()[0]} "
          f"pandas={pd.__version__} numpy={np.__version__}")
    df = make_synthetic_ohlcv()
    print(f"合成数据：{len(df)} 根 {BAR} K 线 "
          f"{df.index[0]} ~ {df.index[-1]}")

    check_strategy()
    check_backtest(df)
    sim = check_simulator(df)
    check_json_roundtrip(sim)
    check_okx_client_offline()

    print("=" * 46)
    print("PASS：全部自测通过 ✔")


if __name__ == "__main__":
    main()
