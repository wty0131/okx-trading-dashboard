# -*- coding: utf-8 -*-
"""总览 Home：模拟盘净值汇总 + 系统状态卡片。"""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
import pandas as pd

from app import common as C

st.set_page_config(page_title="OKX 交易面板 · 总览", page_icon="📊",
                   layout="wide")
C.inject_css()

st.markdown('<div class="okx-title">OKX 加密交易管理面板</div>',
            unsafe_allow_html=True)
st.markdown('<div class="okx-sub">回测 · 模拟盘 · 行情 —— 本地运行，不触碰真实资金</div>',
            unsafe_allow_html=True)

strategies = C.all_strategies()
papers = C.paper_names()

# --------------------------------------------------------------------------- #
# 顶部指标卡片
# --------------------------------------------------------------------------- #
sims = {}
total_equity = 0.0
for name in papers:
    env = C.load_paper(name)
    if not env:
        continue
    sim = C.build_sim_from_envelope(env)
    if sim is None:
        continue
    eq = C.equity_of_sim(sim)
    sims[name] = {"env": env, "sim": sim, "equity": eq}
    total_equity += eq

stats = C.market.cache_stats()
items = [
    {"label": "已注册策略", "value": len(strategies),
     "sub": "strategies 注册表自动发现"},
    {"label": "模拟盘运行中", "value": len(papers),
     "sub": "data/paper_state/*.json"},
    {"label": "组合总权益 (USDT)", "value": C.fmt_money(total_equity, 0) if papers else "—",
     "cls": C.money_cls(total_equity - 0),
     "sub": "全部运行中策略合计（估算）" if papers else "无运行中的策略"},
    {"label": "本地 K 线缓存", "value": f"{stats['rows']:,} 根",
     "sub": f"{stats['count']} 个文件 · {stats['size_kb']} KB"},
]
C.metric_cards(items, key="home_top")

st.write("")

# --------------------------------------------------------------------------- #
# 组合净值曲线
# --------------------------------------------------------------------------- #
st.subheader("📈 模拟盘净值汇总")
if not sims:
    st.info("尚无运行中的模拟盘。请到左侧 **Paper 模拟盘** 页为某策略点击「启动」，"
            "状态将持久化到 `data/paper_state/`。")
else:
    curves = {}
    for name, info in sims.items():
        curve = info["sim"].equity_curve or []
        if curve:
            idx = [pd.Timestamp(t) for t, _ in curve]
            val = [float(e) for _, e in curve]
            curves[name] = pd.Series(val, index=pd.DatetimeIndex(idx))
    norm = st.radio("纵轴", ["相对启动涨跌 %", "绝对权益 (USDT)"],
                    horizontal=True, key="home_axis")
    fig = C.equity_figures(curves, normalize=(norm.startswith("相对")),
                           title="各策略模拟盘净值曲线")
    if fig:
        st.plotly_chart(fig, width="stretch")
    rows = []
    for name, info in sims.items():
        env = info["env"]
        sim = info["sim"]
        rows.append({
            "策略": env.get("strategy"),
            "交易对": env.get("inst_id"),
            "bar": env.get("bar"),
            "权益 (USDT)": round(info["equity"], 2),
            "现金": round(sim.cash, 2),
            "持仓": C.open_position_text(sim),
            "交易笔数": len(sim.trades),
            "启动于(UTC)": str(env.get("started_at", ""))[:19],
            "最后更新(UTC)": str(env.get("updated_at", ""))[:19],
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch",
                     hide_index=True)

st.write("---")

# --------------------------------------------------------------------------- #
# 系统卡片
# --------------------------------------------------------------------------- #
st.subheader("🖥️ 系统状态")
left, right = st.columns([1, 1], gap="large")
with left:
    st.markdown("##### 行情数据链路")
    chain = {
        "OKX 直连": C.env_summary()["proxy"],
        "备用源": "Binance klines → CoinGecko 日线(1D)",
        "缓存目录": "data/cache/（.gitignore 已排除）",
        "离线开关": C.env_summary()["offline"],
    }
    for k, v in chain.items():
        st.markdown(f"- **{k}**：`{v}`")
    st.caption(C.market.source_chain_text())
with right:
    st.markdown("##### 策略注册表")
    if strategies:
        st.dataframe(
            pd.DataFrame([{
                "策略名": c.name,
                "参数默认": C.describe_params(c),
                "模块": c.__module__,
            } for c in strategies]), width="stretch", hide_index=True)
    else:
        st.warning("注册表为空：请确认 strategies/ 下已有策略模块（如 sma_cross.py）。")
    if C.market.cache_stats()["detail"]:
        with st.expander("最近缓存文件"):
            for d in reversed(C.market.cache_stats()["detail"][-6:]):
                st.markdown(f"`{d['file']}` — {d['rows']} 根 · "
                            f"{d['days_span']} 天跨度 · "
                            f"{d['mtime'].strftime('%m-%d %H:%M')}")

st.write("---")
st.caption("⚠️ 免责声明：本面板仅用于行情展示、历史回测与本地模拟盘研究，"
           "不构成任何投资建议；模拟盘使用真实行情 + 本地撮合（0.1% 手续费 + "
           "0.05% 滑点假设），不连接也不操作真实账户资金。")
