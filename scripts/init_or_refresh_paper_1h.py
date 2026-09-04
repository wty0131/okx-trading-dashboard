# -*- coding: utf-8 -*-
"""1H 日内模拟盘：刷新/创建 3 对 × 7 策略基线（锚定最新 1H 收盘）。PM"""
import sys, json, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import strategies  # noqa
from strategies.registry import REGISTRY
from data.market import get_candles_cached
from paper.simulator import PaperSimulator

PAIRS = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
BAR, LIMIT, CASH = "1H", 300, 10000.0
OUT = ROOT / "data" / "paper_state_1h"
OUT.mkdir(parents=True, exist_ok=True)

def main():
    created, fails = 0, []
    for pair in PAIRS:
        try:
            df = get_candles_cached(pair, bar=BAR, limit=LIMIT)
        except Exception as e:
            print(f"{pair}: FAIL {e}"); fails.append(pair); continue
        if df.empty:
            print(f"{pair}: 无数据"); fails.append(pair); continue
        latest = df.index[-1]
        close = float(df["close"].iloc[-1])
        print(f"{pair}: {len(df)} 根 1H，最新 {latest} close={close:.2f}")
        for cls in REGISTRY:
            sim = PaperSimulator(pair, [cls()], initial_cash=CASH,
                                 fee_rate=0.001, slippage=0.0005)
            env = {"version": 1, "strategy": cls.name, "params": {},
                   "inst_id": pair, "bar": BAR, "initial_cash": CASH,
                   "fee_rate": 0.001, "slippage": 0.0005,
                   "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                   "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                   "anchor_time": latest.isoformat(),
                   "meta": {"last_ts": latest.isoformat(),
                            "note": "1H 日内前向模拟（基线=最新 1H 收盘，空仓 10,000）"},
                   "sim": sim.to_dict()}
            (OUT / f"{pair}__{cls.name}.json").write_text(
                json.dumps(env, ensure_ascii=False, indent=1), encoding="utf-8")
            created += 1
    print(f"\n1H 基线就绪: {created} 个；失败对: {fails or '无'}")

if __name__ == "__main__":
    main()
