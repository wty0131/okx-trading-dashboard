# -*- coding: utf-8 -*-
"""P3d 联调：真实历史回测收益表 + 本地前向模拟盘初始状态（2026-09-04 PM）。
运行：cd C:\\Users\\wty0131\\okx_system && .venv\\Scripts\\python scripts\\run_real_report.py
"""
import sys, json, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

import strategies  # noqa: F401  导入即注册全部策略
from backtest.engine import apply_strategy, run_backtest
from strategies.registry import REGISTRY, available
from data.market import get_candles_cached

PAIRS = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
BAR = "1D"
LIMIT = 300
CASH = 10_000.0
FEE = 0.001
SLIP = 0.0005

def main():
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "docs"
    out_dir.mkdir(exist_ok=True)
    rows = []
    print("=" * 100)
    for pair in PAIRS:
        print(f"\n拉取 {pair} {BAR} x{LIMIT} ...")
        df = get_candles_cached(pair, bar=BAR, limit=LIMIT)
        print(f"  {pair}: {len(df)} 根  {df.index[0]} -> {df.index[-1]}  last_close={float(df['close'].iloc[-1]):.4f}")
        for cls in REGISTRY:
            name = cls.name
            try:
                st = cls()
            except Exception as e:
                print(f"  !! {name} 实例化失败: {e}")
                continue
            try:
                signals = apply_strategy(df, st)
                nav, trades, metrics = run_backtest(df, signals, initial_cash=CASH,
                                                    fee_rate=FEE, slippage=SLIP)
                rows.append({
                    "pair": pair, "bar": BAR, "strategy": name,
                    "start": str(df.index[0].date()), "end": str(df.index[-1].date()),
                    "bars": len(df),
                    "total_return": metrics.get("total_return"),
                    "annual_return": metrics.get("annual_return"),
                    "max_drawdown": metrics.get("max_drawdown"),
                    "sharpe": metrics.get("sharpe"),
                    "win_rate": metrics.get("win_rate"),
                    "trades": metrics.get("trade_count"),
                    "final_equity": metrics.get("final_equity"),
                })
                print(f"  {name:22s} ret={metrics.get('total_return',0):>9.2%}  "
                      f"ann={metrics.get('annual_return',0):>9.2%}  mdd={metrics.get('max_drawdown',0):>8.2%}  "
                      f"sharpe={metrics.get('sharpe',0):>6.2f}  win={metrics.get('win_rate',0):>5.1%}  "
                      f"trades={metrics.get('trade_count',0):>3d}  eq={metrics.get('final_equity',0):>12.2f}")
            except Exception as e:
                print(f"  !! {name} 回测失败: {e!r}")
    rdf = pd.DataFrame(rows)
    csv_path = out_dir / f"backtest_report_{stamp}.csv"
    rdf.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n回测表已存: {csv_path}")

    # ---------------- 模拟盘初始状态 ----------------
    print("\n初始化本地前向模拟盘（基线状态，空仓 10,000 USDT/每策略/每对）...")
    state_dir = ROOT / "data" / "paper_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    from paper.simulator import PaperSimulator
    created = []
    for pair in PAIRS:
        for cls in REGISTRY:
            st = cls()
            sim = PaperSimulator(pair, [st], initial_cash=CASH, fee_rate=FEE, slippage=SLIP)
            payload = {
                "inst_id": pair, "bar": BAR, "strategy": cls.name,
                "initial_cash": CASH, "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "baseline": True, "state": sim.to_dict(),
            }
            fp = state_dir / f"{pair}__{cls.name}.json"
            fp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            created.append(fp.name)
    print(f"已建 {len(created)} 个初始状态文件于 {state_dir}")
    print("=" * 100)
    print("DONE")

if __name__ == "__main__":
    main()
