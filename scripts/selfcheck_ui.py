# -*- coding: utf-8 -*-
"""UI 层轻量自检（完全离线，不发网络请求）：
1) py_compile 校验全部 .py（含 app 页面模块语法）；
2) import data.market / app.common 共享模块；
3) 行情缓存：写/读/裁尾/合并去重/离线取数路径/无缓存报错；
4) 模拟盘状态 JSON 落盘与还原往返。

运行（用项目 .venv）：
    cd C:\\Users\\wty0131\\okx_system
    .venv\\Scripts\\python scripts\\selfcheck_ui.py

全部通过打印 PASS。自检产生的临时文件（data/cache/*SELFTEST*、
data/paper_state/*SELFTEST*）会在结束时清理。
"""

import os
import shutil
import sys
from pathlib import Path

# 强制离线：禁止行情层联网
os.environ["OKX_MARKET_OFFLINE"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd


def make_synthetic_ohlcv(n: int = 320, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    step = rng.normal(0.0, 0.002, n)
    close = 100.0 * np.cumprod(1.0 + step)
    idx = pd.date_range("2024-06-01", periods=n, freq="1h", tz="UTC")
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0, 0.001, n)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0, 0.001, n)))
    vol = rng.uniform(1.0, 20.0, n)
    df = pd.DataFrame({"open": open_, "high": high, "low": low,
                       "close": close, "vol": vol}, index=idx)
    df.index.name = "time"
    return df


def check_py_compile():
    import py_compile
    files = sorted(
        p for p in ROOT.rglob("*.py")
        if ".venv" not in p.parts and "__pycache__" not in p.parts)
    assert files, "未找到任何 .py"
    for f in files:
        py_compile.compile(str(f), doraise=True)
    print(f"[1/5] py_compile：{len(files)} 个 .py 全部语法通过")
    return files


def check_imports():
    import data.market as market
    import app.common as common
    from paper.simulator import PaperSimulator  # noqa: F401
    from okx.okx_client import OkxClient  # noqa: F401
    from strategies.sma_cross import SmaCross  # noqa: F401
    strat = common.all_strategies()
    assert any(c.name == "sma_cross" for c in strat), "策略自动发现失败"
    assert market.TRADING_PAIRS[0] == "BTC-USDT"
    assert len(market.UI_BARS) >= 6
    print(f"[2/5] import：data.market / app.common OK；自动发现 {len(strat)} 个策略")


def check_cache_roundtrip():
    import data.market as market
    df = make_synthetic_ohlcv(320)
    path = market.cache_path("UI-SELFTEST", "1H")
    market.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    market._write_cache(path, df)
    back = market._read_cache(path)
    assert len(back) == len(df), "缓存读写行数不一致"
    assert back.index.tz is not None and str(back.index.tz) == "UTC"
    assert list(back.columns) == ["open", "high", "low", "close", "vol"]
    assert abs(back["close"].iloc[-1] - df["close"].iloc[-1]) < 1e-9
    # 裁尾
    trimmed = market._trim_tail(back, 100)
    assert len(trimmed) == 100 and trimmed.index[-1] == back.index[-1]
    # 合并去重（先到者优先）
    f1 = df.iloc[:200]
    f2 = df.iloc[100:]
    merged = market._merge_frames([f1, f2])
    assert len(merged) == len(df) and merged.index.is_monotonic_increasing
    # 离线取数：有缓存 -> 应返回缓存并给离线告警
    got = market.get_candles_cached("UI-SELFTEST", "1H", limit=300)
    assert len(got) == 300 and got.index.tz is not None
    assert not market.LAST_CALL["ok"] is False
    assert any("离线" in w for w in market.LAST_CALL.get("warnings", []))
    got2 = market.get_candles_cached("UI-SELFTEST", "1H", limit=200)
    assert len(got2) == 200
    print("[3/5] 行情缓存：写/读/裁尾/合并去重/离线取数 OK")
    return path


def check_no_cache_raises():
    import data.market as market
    p = market.cache_path("UI_NOCACHE", "1H")
    if p.exists():
        p.unlink()
    try:
        market.get_candles_cached("UI-NOCACHE", "1H", limit=100)
        raise AssertionError("无缓存且离线时应抛 RuntimeError")
    except RuntimeError:
        pass
    print("[4/5] 离线无缓存：正确抛错（不联网、不阻塞）")


def check_paper_persistence():
    import json
    import data.market as market  # noqa: F401
    from app import common as common_mod
    from paper.simulator import PaperSimulator
    from strategies.sma_cross import SmaCross

    df = make_synthetic_ohlcv(80)
    sim = PaperSimulator("UI-SELFTEST", [SmaCross(fast=3, slow=8)],
                         initial_cash=10_000.0)
    for _, bar in df.iterrows():
        sim.update(bar)
    cfg = {"strategy": "sma_cross", "params": {"fast": 3, "slow": 8},
           "inst_id": "UI-SELFTEST", "bar": "1H", "initial_cash": 10_000.0,
           "fee_rate": 0.001, "slippage": 0.0005,
           "started_at": "2024-01-01T00:00:00+00:00",
           "anchor_time": df.index[-1].isoformat()}
    path = common_mod.save_paper("UI_SELFTEST", sim, cfg)
    assert path.exists()
    env = common_mod.load_paper("UI_SELFTEST")
    assert env is not None and env["sim"]["trades"] == sim.trades
    restored = common_mod.build_sim_from_envelope(env)
    assert abs(restored.equity(sim.last_close) - sim.equity(sim.last_close)) < 1e-6
    assert len(restored.equity_curve) == len(sim.equity_curve)
    # JSON 结构合法
    json.loads(path.read_text(encoding="utf-8"))
    print(f"[5/5] 模拟盘持久化：JSON 往返一致（{len(sim.trades)} 笔交易，"
          f"期末权益 {restored.equity(sim.last_close):.2f}）")
    return path


def cleanup(tmp_paths):
    for p in tmp_paths:
        try:
            p.unlink()
        except OSError:
            pass
    # 清掉可能的空状态目录不影响；只删自检临时文件


def main():
    print(f"== okx_system UI 层自检 == python={sys.version.split()[0]} "
          f"pandas={pd.__version__}")
    files = check_py_compile()
    check_imports()
    cache_path = check_cache_roundtrip()
    check_no_cache_raises()
    paper_path = check_paper_persistence()
    cleanup([cache_path, paper_path])
    # 确保没有把自检垃圾留在缓存/状态目录
    leftovers = list((ROOT / "data" / "cache").glob("UI_*")) if \
        (ROOT / "data" / "cache").exists() else []
    leftovers += list((ROOT / "data" / "paper_state").glob("UI_*")) if \
        (ROOT / "data" / "paper_state").exists() else []
    assert not leftovers, f"残留自检文件：{leftovers}"
    print("=" * 46)
    print(f"PASS：UI 层自检全部通过 ✔（py_compile {len(files)} 个文件）")


if __name__ == "__main__":
    main()
