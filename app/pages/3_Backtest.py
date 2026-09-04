# -*- coding: utf-8 -*-
"""回测 Backtest：交易对 × bar × 策略 → 指标卡片 + 净值/回撤图 + 交易明细 + CSV 下载。"""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
import pandas as pd

from app import common as C

st.set_page_config(page_title="回测 · OKX 交易面板", page_icon="🧪",
                   layout="wide")
C.inject_css()

st.markdown('<div class="okx-title">回测 Backtest</div>', unsafe_allow_html=True)
st.markdown('<div class="okx-sub">向量化回测 · 成本假设 0.1% 手续费 + 0.05% 滑点（可调）</div>',
            unsafe_allow_html=True)


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_candles(inst: str, bar: str, limit: int):
    df = C.market.get_candles_cached(inst, bar, limit=limit,
                                     proxies=C.market.get_proxies())
    return df, dict(C.market.LAST_CALL)


strategies = C.all_strategies()
if not strategies:
    st.error("策略注册表为空，无法回测。请先确认 strategies/ 下存在注册的策略。")
    st.stop()

# --------------------------------------------------------------------------- #
# 参数区
# --------------------------------------------------------------------------- #
c1, c2, c3, c4 = st.columns([2, 1, 1.4, 1], gap="medium")
with c1:
    inst = st.selectbox("交易对", C.market.TRADING_PAIRS, key="bt_inst")
with c2:
    bar = st.selectbox("K 线粒度", C.market.UI_BARS, index=4, key="bt_bar")
with c3:
    limit = st.selectbox("K 线数量（不足时按缓存深度自动补拉）",
                         [300, 600, 900], index=0, key="bt_limit")
with c4:
    cash = st.number_input("初始资金 (USDT)", value=10_000.0, step=1_000.0,
                           min_value=100.0, key="bt_cash")

name_map = {c.name: c for c in strategies}
strategy_name = st.selectbox("策略", list(name_map.keys()), key="bt_strat")
clazz = name_map[strategy_name]

with st.expander("⚙️ 策略参数", expanded=True):
    left_p, right_p = st.columns(2)
    with left_p:
        params = C.param_widgets(clazz, "bt_param")
    with right_p:
        st.caption("高级回测参数")
        fee = st.number_input("手续费率（单边）", value=0.001, step=0.0001,
                              min_value=0.0, max_value=0.02, format="%.4f",
                              key="bt_fee")
        slip = st.number_input("滑点（单边）", value=0.0005, step=0.0001,
                               min_value=0.0, max_value=0.02, format="%.4f",
                               key="bt_slip")

run = st.button("▶️ 运行回测", type="primary", key="bt_run")

# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #
if run:
    st.session_state["__bt_run_id"] = st.session_state.get("__bt_run_id", 0) + 1

run_id = st.session_state.get("__bt_run_id", 0)
if run_id > 0:
    from backtest.engine import apply_strategy, run_backtest
    try:
        strat = C.instantiate(clazz, params)
    except ValueError as exc:
        st.error(f"策略参数非法：{exc}")
        st.stop()

    with st.spinner("拉取行情 → 生成信号 → 回测中 …"):
        df = None
        diag = {}
        try:
            df, diag = _fetch_candles(inst, bar, int(limit))
        except Exception as exc:  # noqa: BLE001
            st.error(f"行情获取失败：{exc}")
            st.stop()
        if df is None or len(df) < 2:
            st.error("K 线不足 2 根，无法回测。")
            st.stop()

        signals = apply_strategy(df, strat)
        try:
            nav, trades, metrics = run_backtest(
                df, signals, initial_cash=float(cash),
                fee_rate=float(fee), slippage=float(slip))
        except Exception as exc:  # noqa: BLE001
            st.error(f"回测失败：{exc}")
            st.stop()

    # ---- 指标卡片 ----
    st.subheader("📊 回测结果")
    tr = metrics.get("total_return")
    ar = metrics.get("annual_return")
    mdd = metrics.get("max_drawdown")
    shp = metrics.get("sharpe")
    wr = metrics.get("win_rate")
    tc = metrics.get("trade_count", 0)
    fe = metrics.get("final_equity")
    items = [
        {"label": "总收益率", "value": C.fmt_pct(tr),
         "cls": C.pct_cls(tr), "sub": f"{len(df)} 根 K 线"},
        {"label": "年化收益率", "value": C.fmt_pct(ar), "cls": C.pct_cls(ar),
         "sub": "按时间索引自动推算"},
        {"label": "最大回撤", "value": C.fmt_pct(-mdd if mdd is not None else None),
         "cls": "down", "sub": "净值峰谷回撤"},
        {"label": "夏普比率", "value": f"{shp:.2f}" if shp is not None else "—",
         "sub": "无风险利率=0"},
        {"label": "胜率", "value": C.fmt_pct(wr), "cls": C.pct_cls((wr or 0) - 0.5),
         "sub": f"已平仓 {tc} 笔"},
        {"label": "期末权益 (USDT)", "value": C.fmt_money(fe, 2),
         "cls": C.money_cls((fe or 0) - float(cash)),
         "sub": f"初始 {C.fmt_money(cash, 0)}"},
    ]
    C.metric_cards(items, key="bt_metrics")

    src = ",".join(diag.get("sources_used", [])) or "cache"
    st.caption(f"数据：{C.fmt_dt(df.index[-1])} · {len(df)} 根 · 源 {src} · "
               f"成本 fee={fee:.3%} slip={slip:.3%}")
    for w in diag.get("warnings", []):
        st.caption(f"⚠️ {w}")

    # ---- 图 ----
    st.plotly_chart(C.nav_dd_figure(nav, title=f"净值与回撤 · {inst} {bar} · "
                                              f"{strategy_name} "
                                              f"({C.describe_params(clazz)})"),
                    width="stretch")

    # ---- 交易明细 ----
    left_t, right_t = st.columns([1.6, 1], gap="large")
    with left_t:
        st.markdown("##### 最近 10 笔交易")
        if trades is not None and not trades.empty:
            show = trades.tail(10).copy()
            for col in ("entry_time", "exit_time"):
                if col in show.columns:
                    show[col] = show[col].apply(
                        lambda v: C.fmt_dt(v) if pd.notna(v) else "—")
            if "ret" in show.columns:
                show["ret"] = show["ret"].apply(C.fmt_pct)
            st.dataframe(show, width="stretch", hide_index=False)
        else:
            st.info("本次回测没有产生任何已平仓交易（策略可能一直在观望）。")
    with right_t:
        st.markdown("##### 下载结果 (CSV)")
        if trades is not None and not trades.empty:
            st.download_button("📄 交易明细.csv", trades.to_csv().encode("utf-8-sig"),
                               file_name=f"{strategy_name}_{inst}_{bar}_trades.csv",
                               mime="text/csv", key="bt_dl_trades")
        nav_df = nav.to_frame("nav").copy()
        nav_df.index.name = "time"
        st.download_button("📈 净值曲线.csv", nav_df.to_csv().encode("utf-8-sig"),
                           file_name=f"{strategy_name}_{inst}_{bar}_nav.csv",
                           mime="text/csv", key="bt_dl_nav")
        m_rows = [{"metric": k, "value": v} for k, v in metrics.items()]
        st.download_button("📊 指标.csv",
                           pd.DataFrame(m_rows).to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"{strategy_name}_{inst}_{bar}_metrics.csv",
                           mime="text/csv", key="bt_dl_metrics")
        st.caption("编码 UTF-8-SIG，可直接用 Excel 打开。")

st.write("---")
st.caption("引擎说明：持仓在 bar 收盘价处生效并扣减成本；收益率按收盘价到收盘价计算；"
           "同一根出现多个信号时按 close > short > long 处理。详见 "
           "`backtest/engine.py`。")
