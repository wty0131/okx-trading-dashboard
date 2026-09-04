# -*- coding: utf-8 -*-
"""模拟盘跟进（1D 日线 + 1H 日内双目录）：对 paper_state*/*.json 各账户拉最新 K 线
并逐根前向 update。规则：以 state 内 meta.last_ts 为界，只喂更新的 bar
（预热用缓存全史但不记账）。PM 2026-09-04
"""
import sys, json, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
import strategies  # noqa
from strategies.registry import REGISTRY
from data.market import get_candles_cached
from paper.simulator import PaperSimulator
from okx.okx_client import OkxClient
from data.market import get_proxies

STATE_DIRS = [ROOT / "data" / "paper_state",
              ROOT / "data" / "paper_state_1h"]

def fetch_df(pair: str, bar: str) -> pd.DataFrame:
    """拉 K 线：先走市场缓存层（多源降级+增量），失败则直连 OKX 兜底。"""
    try:
        return get_candles_cached(pair, bar=bar, limit=1500)
    except Exception:
        px = get_proxies() or None
        client = OkxClient(proxies=px, timeout=40)
        return client.get_candles(pair, bar=bar, limit=300)

def load_state(fp):
    return json.loads(fp.read_text(encoding="utf-8"))

def process_dir(state_dir: Path):
    files = sorted(state_dir.glob("*.json"))
    if not files:
        print(f"{state_dir.name}: 无账户")
        return
    print(f"\n=== {state_dir.name}: {len(files)} 个账户 ===")
    for fp in files:
        st = load_state(fp)
        pair = st["inst_id"]
        bar = st.get("bar", "1D")
        name = st["strategy"]
        df = fetch_df(pair, bar)
        if df.empty:
            print(f"  {pair} {name}: 无行情，跳过")
            continue
        latest = df.index[-1]
        last_ts = st.get("meta", {}).get("last_ts")
        if last_ts is None:
            st.setdefault("meta", {})["last_ts"] = latest.isoformat()
            st["meta"]["note"] = "初始基线(空仓)，今日起前向记账"
            fp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {name:24s} 基线 @ {latest} (空仓)")
            continue
        last = pd.Timestamp(last_ts)
        new_df = df[df.index > last]
        if new_df.empty:
            print(f"  {name:24s} 无新 {bar} bar（截至 {last}），不变")
            continue
        cls = next((x for x in REGISTRY if x.name == name), None)
        if cls is None:
            print(f"  {name}: 策略不存在，跳过")
            continue
        sim = PaperSimulator(pair, [cls()], initial_cash=float(st.get("initial_cash", 10000)),
                             fee_rate=0.001, slippage=0.0005)
        warm = df[df.index <= last]
        for _, b in warm.iterrows():
            try:
                sim.update(b)
            except Exception:
                pass
        sim.reset()
        for _, b in new_df.iterrows():
            sim.update(b)
        st["sim"] = sim.to_dict()
        st.setdefault("meta", {})["last_ts"] = new_df.index[-1].isoformat()
        st["meta"]["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        fp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        eq = sim.equity()
        pnl = eq - float(st.get("initial_cash", 10000))
        print(f"  {name:24s} +{len(new_df)}{bar} 权益={eq:,.2f} 累计PnL={pnl:+,.2f} "
              f"持仓={sim.quantity:g} 交易={len(sim.trades)}")

def main():
    for d in STATE_DIRS:
        if d.exists():
            process_dir(d)
    print("\nDONE")

if __name__ == "__main__":
    main()
