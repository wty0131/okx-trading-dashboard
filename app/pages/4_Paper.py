# -*- coding: utf-8 -*-
"""模拟盘 Paper：每策略一卡 —— 本地前向模拟（真实行情 + 本地撮合，不碰真实资金）。"""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
import pandas as pd

from app import common as C
from paper.simulator import PaperSimulator

st.set_page_config(page_title="模拟盘 · OKX 交易面板", page_icon="🧾",
                   layout="wide")
C.inject_css()

st.markdown('<div class="okx-title">模拟盘 Paper</div>', unsafe_allow_html=True)
st.markdown('<div class="okx-sub">每策略一张卡 · 状态持久化到 `data/paper_state/`</div>',
            unsafe_allow_html=True)

st.info("**模拟盘 = 本地前向模拟**：拉取真实行情，在本地按 K 线逐根撮合（含 0.1% 手续费 + "
        "0.05% 滑点假设），不连接 OKX 账户、不触碰真实资金。策略内部指标会用缓存的历史 K 线"
        "预热，快照始终从空仓开始；「更新一次」会把上次快照后错过的 K 线按序补喂。")

strategies = C.all_strategies()
if not strategies:
    st.error("策略注册表为空，无法创建模拟盘。")
    st.stop()

name_map = {c.name: c for c in strategies}
papers = C.paper_names()


@st.cache_data(ttl=15, show_spinner=False)
def _fetch_candles(inst: str, bar: str, limit: int):
    df = C.market.get_candles_cached(inst, bar, limit=limit,
                                     proxies=C.market.get_proxies())
    return df, dict(C.market.LAST_CALL)


# --------------------------------------------------------------------------- #
# 内部动作
# --------------------------------------------------------------------------- #
def _start(name: str, clazz, params, inst: str, bar: str, cash: float):
    """启动：拉最新历史 K 线预热策略指标 -> 空仓快照 + 记录 start_time。"""
    df, diag = _fetch_candles(inst, bar, 300)
    if df is None or df.empty:
        raise RuntimeError("行情获取失败（所有源不可用且无缓存），无法启动。")
    sim = PaperSimulator(inst, [clazz(**params)], initial_cash=float(cash),
                         fee_rate=C.PAPER_FEE_RATE, slippage=C.PAPER_SLIPPAGE)
    anchor = df.index[-1]
    warm = C.warm_strategy(sim, df, anchor, include_anchor=True)
    sim._last_close = float(df["close"].iloc[-1])
    sim.equity_curve = [[anchor.isoformat(), round(float(cash), 6)]]
    cfg = {
        "strategy": clazz.name, "params": dict(params), "inst_id": inst,
        "bar": bar, "initial_cash": float(cash),
        "fee_rate": C.PAPER_FEE_RATE, "slippage": C.PAPER_SLIPPAGE,
        "started_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "anchor_time": anchor.isoformat(),
    }
    C.save_paper(name, sim, cfg)
    return {"warm": warm, "anchor": anchor}


def _advance(name: str) -> dict:
    """恢复状态 -> 预热 -> 补喂锚点之后的最新 K 线 -> 落盘。"""
    env = C.load_paper(name)
    if not env:
        raise RuntimeError(f"{name} 没有可恢复的状态文件。")
    sim = C.build_sim_from_envelope(env)
    if not sim.strategies:
        raise RuntimeError(f"{name} 的策略类未注册，无法继续撮合。")
    anchor = C.anchor_of(env)
    if anchor is None:
        raise RuntimeError("状态文件缺少锚点时间，无法继续。")
    df, diag = _fetch_candles(env["inst_id"], env["bar"], 300)
    if df is None or df.empty:
        raise RuntimeError("行情获取失败（所有源不可用且无缓存），无法更新。")
    warm = C.warm_strategy(sim, df, anchor, include_anchor=True)
    pending = df[df.index > anchor]
    if pending.empty:
        return {"fed": 0, "warm": warm, "action": None,
                "new_anchor": anchor, "note": "无新 K 线（行情尚未刷新）"}
    fed = 0
    last_action = None
    for _, row in pending.iterrows():
        fed += 1
        summary = sim.update(row)
        if summary.get("action") != "none":
            last_action = summary
        if fed >= 3000:  # 长离线后避免一次补太多，可多次点击
            break
    new_anchor = pending.index[fed - 1]
    env["anchor_time"] = new_anchor.isoformat()
    env["updated_at"] = pd.Timestamp.now(tz="UTC").isoformat()
    C.save_paper(name, sim, env)
    return {"fed": fed, "warm": warm, "action": last_action,
            "new_anchor": new_anchor, "note": None}


# --------------------------------------------------------------------------- #
# 顶部汇总 + 全部更新
# --------------------------------------------------------------------------- #
envs = {}
for n in papers:
    e = C.load_paper(n)
    if e:
        envs[n] = e

total_equity = 0.0
live_rows = []
for n, env in envs.items():
    try:
        sim = C.build_sim_from_envelope(env)
        eq = C.equity_of_sim(sim)
    except Exception:  # noqa: BLE001
        eq = float(env.get("initial_cash", 0))
    total_equity += eq
    live_rows.append({"策略": env.get("strategy"), "交易对": env.get("inst_id"),
                      "权益": round(eq, 2)})

h1, h2, h3, h4 = st.columns([1, 1, 1, 1.8], gap="medium")
with h1:
    st.metric("运行中", len(envs))
with h2:
    st.metric("组合权益 (USDT)", C.fmt_money(total_equity, 2))
with h3:
    st.metric("策略总数", len(strategies))
with h4:
    st.write("")
    if st.button("🔄 全部更新（每卡补喂最新 K 线）", type="primary",
                 width="stretch", key="paper_all_update"):
        msgs = []
        for n in list(envs.keys()):
            try:
                r = _advance(n)
                msgs.append(f"`{n}`: 补喂 {r['fed']} 根"
                            + ("" if r["fed"] else f"（{r['note']}）"))
            except Exception as exc:  # noqa: BLE001
                msgs.append(f"`{n}`: 失败 {str(exc)[:80]}")
        st.toast("  /  ".join(msgs))
        st.rerun()

if live_rows:
    st.caption("运行中：" + "  ·  ".join(
        f"`{r['策略']}` {r['交易对']} {C.fmt_money(r['权益'], 0)} USDT"
        for r in live_rows))

st.write("---")


# --------------------------------------------------------------------------- #
# 每策略一张卡
# --------------------------------------------------------------------------- #
def _render_start_card(name: str, clazz):
    with st.form(f"paper_form_{name}", border=True):
        col_a, col_b, col_c = st.columns([1.6, 1, 1], gap="medium")
        with col_a:
            inst = st.selectbox("交易对", C.market.TRADING_PAIRS,
                                key=f"pp_inst_{name}")
        with col_b:
            bar = st.selectbox("粒度", C.market.UI_BARS, index=4,
                               key=f"pp_bar_{name}")
        with col_c:
            cash = st.number_input("初始资金 (USDT)", value=10_000.0,
                                   step=1_000.0, min_value=100.0,
                                   key=f"pp_cash_{name}")
        with st.expander("⚙️ 策略参数", expanded=True):
            p1, p2 = st.columns(2)
            with p1:
                params = C.param_widgets(clazz, f"pp_param_{name}")
            with p2:
                st.caption(f"撮合成本：手续费 {C.PAPER_FEE_RATE:.1%} / 单边 + "
                           f"滑点 {C.PAPER_SLIPPAGE:.2%} / 单边")
                st.caption("启动动作：拉最新历史 K 线预热策略内部指标，"
                           "以**空仓快照**启动并记录 start_time，之后由"
                           "「更新一次」逐根前向撮合。")
        submitted = st.form_submit_button("🚀 启动 / 继续", type="primary",
                                          width="stretch")
    if submitted:
        try:
            r = _start(name, clazz, params, inst, bar, cash)
            st.toast(f"`{name}` 已启动：预热 {r['warm']} 根历史 K 线，"
                     f"锚点 {C.fmt_dt(r['anchor'])}（空仓快照）")
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"启动失败：{exc}")


def _render_running_card(name: str, env):
    sim = C.build_sim_from_envelope(env)
    stored_params = env.get("params", {}) or {}

    a1, a2 = st.columns([1.3, 2.4], gap="medium")
    with a1:
        if st.button("🔄 更新一次", key=f"pp_update_{name}", type="primary",
                     width="stretch"):
            try:
                r = _advance(name)
                if r["fed"]:
                    tail = ""
                    if r["action"]:
                        ac = r["action"]
                        tail = f" · 最近动作 `{ac['action']}` @ " \
                               f"{C.fmt_money(ac['close'], 4)}"
                    msg = (f"`{name}` 补喂 {r['fed']} 根新 K 线，锚点推进到 "
                           f"{C.fmt_dt(r['new_anchor'])}{tail}")
                else:
                    msg = f"`{name}` {r['note']}"
                st.toast(msg)
            except Exception as exc:  # noqa: BLE001
                st.error(f"`{name}` 更新失败：{exc}")
            st.rerun()
        if st.button("🗑️ 重置（删除状态）", key=f"pp_reset_{name}",
                     width="stretch"):
            st.session_state[f"pp_confirm_{name}"] = True
            st.rerun()
    with a2:
        c1, c2 = st.columns(2)
        with c1:
            st.caption(f"**交易对**：`{env.get('inst_id')}` · `{env.get('bar')}`")
            st.caption(f"**初始资金**：{C.fmt_money(env.get('initial_cash'), 0)} USDT")
            st.caption(f"**启动于 (UTC)**：{str(env.get('started_at'))[:19]}")
        with c2:
            st.caption("**参数**：" +
                       ("，".join(f"{k}={v}" for k, v in stored_params.items())
                        or "无"))
            st.caption(f"**最后更新 (UTC)**：{str(env.get('updated_at'))[:19]}")

    if st.session_state.get(f"pp_confirm_{name}"):
        st.warning(f"确定删除 `{name}` 的模拟盘状态与全部交易记录？此操作不可撤销。")
        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button(f"✅ 确认删除 {name}", key=f"pp_do_reset_{name}",
                         type="primary", width="stretch"):
                C.delete_paper(name)
                st.session_state.pop(f"pp_confirm_{name}", None)
                st.toast(f"`{name}` 已重置")
                st.rerun()
        with cc2:
            if st.button("取消", key=f"pp_cancel_{name}",
                         width="stretch"):
                st.session_state.pop(f"pp_confirm_{name}", None)
                st.rerun()

    eq = C.equity_of_sim(sim)
    pos = sim.position()
    last_close = sim.last_close
    pnl = 0.0
    pnl_pct = None
    if pos != 0 and sim.entry_price:
        pnl = sim.quantity * (last_close - sim.entry_price)
        pnl_pct = pnl / (abs(sim.quantity) * sim.entry_price) \
            if sim.entry_price else None
    day_chg = C.day_change(sim)
    items = [
        {"label": "当前权益 (USDT)", "value": C.fmt_money(eq, 2),
         "cls": C.money_cls(eq - float(env.get("initial_cash", 0))),
         "sub": f"初始 {C.fmt_money(env.get('initial_cash'), 0)}"},
        {"label": "可用现金 (USDT)", "value": C.fmt_money(sim.cash, 2),
         "sub": C.open_position_text(sim)},
        {"label": "浮动盈亏", "value": f"{C.fmt_money(pnl, 2)} "
                                       f"({C.fmt_pct(pnl_pct)})",
         "cls": C.money_cls(pnl),
         "sub": f"持仓方向 {pos} · 数量 {abs(sim.quantity):.6f}"},
        {"label": "当日涨跌 (UTC)", "value": C.fmt_pct(day_chg),
         "cls": C.pct_cls(day_chg), "sub": "以今日首笔权益为基准"},
        {"label": "交易笔数", "value": len(sim.trades),
         "sub": f"权益曲线 {len(sim.equity_curve)} 个点"},
        {"label": "最新收盘价", "value": C.fmt_money(last_close, 4),
         "sub": "mark-to-market 估值价"},
    ]
    C.metric_cards(items, cols=[1.5, 1, 1.4, 1, 1, 1], key=f"pp_m_{name}")

    ex1, ex2 = st.columns(2)
    with ex1:
        with st.expander(f"🧾 最近 5 笔交易（共 {len(sim.trades)} 笔）"):
            if sim.trades:
                rows = []
                for t in sim.trades[-5:][::-1]:
                    rows.append({
                        "方向": t.get("direction"),
                        "开仓时间": str(t.get("entry_time"))[:19],
                        "开仓价": t.get("entry_price"),
                        "平仓时间": str(t.get("exit_time"))[:19]
                        if t.get("exit_time") else "—",
                        "平仓价": t.get("exit_price"),
                        "盈亏 (USDT)": t.get("pnl"),
                        "收益率": C.fmt_pct(t.get("ret")),
                        "状态": t.get("status"),
                    })
                st.dataframe(pd.DataFrame(rows), width="stretch",
                             hide_index=True)
            else:
                st.caption("暂无已平仓交易。")
    with ex2:
        with st.expander("📈 权益曲线预览"):
            curve = sim.equity_curve or []
            if len(curve) > 1:
                s = pd.Series([float(e) for _, e in curve],
                              index=pd.DatetimeIndex(
                                  [pd.Timestamp(t) for t, _ in curve]))
                st.line_chart(s, width="stretch")
            else:
                st.caption("启动快照为空仓；等待首次「更新一次」后出现曲线。")


for clazz in strategies:
    name = clazz.name
    running = name in envs
    env = envs.get(name)
    badge = ("<span class='okx-badge ok'>运行中</span>" if running
             else "<span class='okx-badge bad'>未启动</span>")
    st.markdown(f"#### 📌 策略 `{name}` {badge}", unsafe_allow_html=True)
    if running:
        _render_running_card(name, env)
    else:
        _render_start_card(name, clazz)
    st.write("---")

st.caption("说明：状态跨会话持久化于 `data/paper_state/`（.gitignore 已排除）。策略内部指标"
           "无法序列化，恢复时会用缓存历史 K 线重新预热（滚动窗口内）；若某策略在 registry "
           "中暂时缺失，将无法继续撮合但历史状态仍可查看。")
