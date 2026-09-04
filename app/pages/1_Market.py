# -*- coding: utf-8 -*-
"""行情 Market：实时价格 + 最近 300 根 K 线蜡烛图（OKX -> Binance 降级 + 缓存）。"""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
import pandas as pd

from app import common as C

st.set_page_config(page_title="行情 · OKX 交易面板", page_icon="📈",
                   layout="wide")
C.inject_css()

st.markdown('<div class="okx-title">行情 Market</div>', unsafe_allow_html=True)
st.markdown('<div class="okx-sub">实时 ticker · 最近 300 根 K 线（时间均为 UTC）</div>',
            unsafe_allow_html=True)


@st.cache_data(ttl=20, show_spinner=False)
def _fetch_candles(inst: str, bar: str, limit: int, force: bool):
    df = C.market.get_candles_cached(inst, bar, limit=limit,
                                     proxies=C.market.get_proxies())
    return df, dict(C.market.LAST_CALL)


left, mid, right = st.columns([2, 1, 1], gap="medium")
with left:
    inst = st.selectbox("交易对", C.market.TRADING_PAIRS, key="mk_inst")
with mid:
    bar = st.selectbox("K 线粒度", C.market.UI_BARS, index=4, key="mk_bar")
with right:
    st.write("")
    st.write("")
    refresh = st.button("🔄 刷新行情", key="mk_refresh", type="primary")

limit = 300
force = bool(refresh)
st.caption("K 线数据链路：OKX → Binance(备用) → 本地缓存；失败自动静默降级并记录日志。")

# --------------------------------------------------------------------------- #
# 实时价格
# --------------------------------------------------------------------------- #
ticker = None
with st.spinner("拉取实时价格 …"):
    try:
        ticker = C.market.get_ticker(inst, proxies=C.market.get_proxies())
    except Exception as exc:  # noqa: BLE001
        st.warning(f"实时价格获取失败（不影响 K 线展示）：{str(exc)[:150]}")

# --------------------------------------------------------------------------- #
# K 线
# --------------------------------------------------------------------------- #
df = None
diag = {}
fetch_warning = None
try:
    df, diag = _fetch_candles(inst, bar, limit, force)
except Exception as exc:  # noqa: BLE001
    fetch_warning = str(exc)

if df is not None and not df.empty:
    last = df.index[-1]
    lag = (pd.Timestamp.now(tz="UTC") - last).total_seconds()

    price_items = []
    if ticker:
        chg = None
        try:
            chg = (float(ticker["last"]) / float(ticker["open24h"]) - 1) \
                if float(ticker.get("open24h", 0)) > 0 else None
        except (TypeError, ValueError, ZeroDivisionError):
            chg = None
        price_items = [
            {"label": "最新价 (USDT)", "value": C.fmt_money(ticker.get("last"), 4),
             "cls": C.money_cls(chg), "sub": f"24h涨跌 {C.fmt_pct(chg)}"},
            {"label": "24h 最高 / 最低", "value": f"{C.fmt_money(ticker.get('high24h'))} / {C.fmt_money(ticker.get('low24h'))}",
             "sub": f"买一 {C.fmt_money(ticker.get('bidPx'))} · 卖一 {C.fmt_money(ticker.get('askPx'))}"},
            {"label": "24h 成交量", "value": C.fmt_money(ticker.get("vol24h"), 0),
             "sub": f"折合 {C.fmt_money(ticker.get('volCcy24h'), 0)} USDT" if ticker.get('volCcy24h') else ""},
        ]
    else:
        price_items = [{"label": "最新价", "value": C.fmt_money(df["close"].iloc[-1], 4),
                        "sub": "（ticker 不可用，取最后一根收盘价）"}]
    C.metric_cards(price_items, key="mk_prices")

    st.write("")
    fig = C.candle_figure(df, title=f"{inst} · {bar} · 最近 {len(df)} 根 K 线",
                          ma_fast=20, ma_slow=60)
    if fig:
        st.plotly_chart(fig, width="stretch")

    src = ",".join(diag.get("sources_used", [])) or "cache"
    st.caption(
        f"数据时间戳：{C.fmt_dt(last)} · {C.lag_text(lag)} · "
        f"数据源：{src} · 缓存行数 {len(df)}")
    for w in diag.get("warnings", []):
        st.caption(f"⚠️ {w}")
else:
    st.error(fetch_warning or "暂无 K 线数据（所有行情源不可用且无缓存）。")

with st.expander("ℹ️ 数据源说明"):
    st.markdown("""
- **主源 OKX**：自动代理解析（`OKX_PROXY` → 系统代理 → 本机 7897 惯例）；单次最多 300 根。
- **备用 Binance**（`api.binance.com/api/v3/klines`）：OKX 失败或深度不足时自动使用；
  失败会静默降级并写入 `logs/market.log`。
- **次级备用 CoinGecko**：仅 1D 粒度（无成交量列）。
- **本地缓存** `data/cache/{inst}_{bar}.csv`：只增量补最新缺失段，不重复全量拉取。
- 时间索引统一为 UTC；交易对 `-` 分隔（OKX 风格），取 Binance 行情时内部转 `BTCUSDT`。
""")
