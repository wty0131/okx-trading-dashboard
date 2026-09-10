# -*- coding: utf-8 -*-
"""交易 Trade：手动下单 / 撤单 / 挂单与持仓查看。

安全设计（务必保留）：
1. 默认 dry-run：只展示"将要发送什么"，绝不发送；
2. 真实发送需同时满足：环境变量 OKX_TRADING_ENABLED=1（启动时显式开启）
   + 页面关闭 dry-run 开关 + 勾选风险确认 + 输入 CONFIRM 字样；
3. 风控闸门：单笔金额上限、单日累计上限（按当日下单金额累计）；
4. 密钥仅从环境变量/.env 读取，页面不回显、不落日志。
"""
from pathlib import Path
import os
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
import pandas as pd

from app import common as C
from okx.okx_client import OkxClient

st.set_page_config(page_title="交易 · OKX 交易面板", page_icon="⚡", layout="wide")
C.inject_css()

st.markdown('<div class="okx-title">交易 Trade</div>', unsafe_allow_html=True)
st.markdown('<div class="okx-sub">手动下单 / 撤单 · 默认 dry-run 不发送真实指令</div>',
            unsafe_allow_html=True)

LIVE_ENABLED = os.environ.get("OKX_TRADING_ENABLED", "0") == "1"
if LIVE_ENABLED:
    st.error("⚠️ **实盘开关已开启**（OKX_TRADING_ENABLED=1）：关闭下方 dry-run 并确认后，"
             "你的操作将真实发送到 OKX 并动用真实资金。")
else:
    st.info("🔒 当前为 **dry-run 安全模式**（未设置 `OKX_TRADING_ENABLED=1`）："
            "下单只生成请求预览，不会发送到 OKX。想实盘需先在启动环境显式开启该变量。")

client = OkxClient(proxies=C.market.get_proxies(), timeout=25)

# --------------------------------------------------------------------------- #
# 账户概览（只读）
# --------------------------------------------------------------------------- #
c1, c2 = st.columns([1, 2.2], gap="large")
with c1:
    st.markdown("##### 账户概览")
    if st.button("🔄 刷新余额/持仓/挂单", key="tr_refresh", type="primary"):
        st.session_state["tr_refresh_flag"] = st.session_state.get("tr_refresh_flag", 0) + 1
    do_refresh = st.session_state.get("tr_refresh_flag", 0) > 0
with c2:
    trade_ccy = st.selectbox("交易对", C.market.TRADING_PAIRS, key="tr_inst")

balance_txt, pos_rows, pend_rows = None, [], []
if do_refresh:
    try:
        b = client.get_balance(dry_run=False)
        acc = (b.get("data") or [{}])[0]
        balance_txt = f"总权益 ≈ {acc.get('totalEq')} USDT · 币种 {len(acc.get('details') or [])} 个"
    except Exception as exc:  # noqa: BLE001
        balance_txt = f"余额查询失败：{str(exc)[:120]}"
    try:
        for p in (client.get_positions(dry_run=False).get("data") or []):
            pos_rows.append({"交易对": p.get("instId"), "方向": p.get("posSide"),
                             "数量": p.get("pos"), "均价": p.get("avgPx"),
                             "未实现盈亏": p.get("upl")})
    except Exception:  # noqa: BLE001
        pass
    try:
        for o in (client.get_pending_orders(inst_id=trade_ccy).get("data") or []):
            pend_rows.append({"订单ID": o.get("ordId"), "交易对": o.get("instId"),
                              "方向": o.get("side"), "类型": o.get("ordType"),
                              "价格": o.get("px"), "数量": o.get("sz"),
                              "已成交": o.get("accFillSz"), "状态": o.get("state")})
    except Exception:  # noqa: BLE001
        pass
    if balance_txt:
        st.caption(balance_txt)

st.write("---")

# --------------------------------------------------------------------------- #
# 下单
# --------------------------------------------------------------------------- #
st.subheader("📤 手动下单")

with st.form("tr_order_form", border=True):
    f1, f2, f3, f4 = st.columns([1, 1, 1.4, 1.2], gap="medium")
    with f1:
        side = st.radio("方向", ["buy（买入）", "sell（卖出）"], horizontal=False,
                        key="tr_side")
    with f2:
        ord_type = st.radio("类型", ["market（市价）", "limit（限价）"],
                            horizontal=False, key="tr_type")
    with f3:
        market_buy_by_amount = st.checkbox(
            "市价买入按金额下单（USDT）", value=True, key="tr_by_amt",
            help="OKX 市价买单：勾选表示 sz 为计价币（USDT）金额；卖出时 sz 一律为基础币数量")
        sz = st.number_input("数量 / 金额", value=10.0, min_value=0.0, step=1.0,
                             format="%.6f", key="tr_sz")
    with f4:
        px = st.number_input("限价价格（限价单必填）", value=0.0, min_value=0.0,
                             step=1.0, format="%.4f", key="tr_px")
        st.caption("市价单忽略此价格。")

    g1, g2, g3 = st.columns([1, 1, 1.6], gap="medium")
    with g1:
        cap_single = st.number_input("单笔上限 (USDT)", value=10.0, min_value=1.0,
                                     step=1.0, key="tr_cap1")
    with g2:
        cap_day = st.number_input("单日累计上限 (USDT)", value=30.0, min_value=1.0,
                                  step=1.0, key="tr_cap2")
    with g3:
        dry_run = st.checkbox("dry-run（只预览不发送）", value=True, key="tr_dry",
                              disabled=not LIVE_ENABLED) if LIVE_ENABLED else True
        st.caption("dry-run 默认开启；关闭后才会真实下单（需启动时开 OKX_TRADING_ENABLED=1）。")

    if LIVE_ENABLED:
        ack = st.checkbox("我已知悉：真金白银、下单不可撤销、风险自担", key="tr_ack")
        confirm_word = st.text_input("输入 CONFIRM 以确认实盘下单", key="tr_confirm_word")
    else:
        ack, confirm_word = False, ""

    submitted = st.form_submit_button("提交", type="primary")

if submitted:
    side_v = "buy" if side.startswith("buy") else "sell"
    type_v = "market" if ord_type.startswith("market") else "limit"
    tgt = "quote_ccy" if (type_v == "market" and side_v == "buy" and market_buy_by_amount) else None

    # 风控闸门 1：金额估算（市价卖出按最新价估值；无法估值时按 0 处理）
    est_usdt = None
    try:
        if type_v == "limit" and px > 0:
            est_usdt = sz * px if side_v == "buy" else sz * px
        elif side_v == "buy" and tgt == "quote_ccy":
            est_usdt = sz
        else:
            df = C.market.get_candles_cached(trade_ccy, "1H", limit=2)
            last = float(df["close"].iloc[-1]) if not df.empty else None
            if last:
                est_usdt = sz * last
    except Exception:  # noqa: BLE001
        est_usdt = None

    used_today = float(st.session_state.get("tr_used_today", 0.0))
    blocked = None
    if est_usdt is not None:
        if est_usdt > cap_single:
            blocked = f"单笔金额 ≈{est_usdt:.2f} USDT 超过单笔上限 {cap_single:.2f}"
        elif used_today + est_usdt > cap_day:
            blocked = (f"今日累计 ≈{used_today + est_usdt:.2f} USDT 超过单日上限 {cap_day:.2f}")
    if not LIVE_ENABLED:
        pass  # dry-run 模式：预览不做拦截（仅展示估算）
    elif (not ack) or confirm_word.strip().upper() != "CONFIRM":
        blocked = blocked or "实盘需勾选风险确认并输入 CONFIRM"
    if blocked and LIVE_ENABLED:
        st.error(f"🛑 已拦截：{blocked}")
    else:
        try:
            result = client.place_order(
                trade_ccy, side_v, sz, ord_type=type_v,
                px=(px if type_v == "limit" else None),
                tgt_ccy=tgt, dry_run=dry_run,
            )
            if result.get("dry_run"):
                st.warning("🧪 dry-run 预览（未发送到 OKX）")
                st.json({"url": result["url"], "body": result["body"]})
                st.caption(f"估算金额 ≈ {est_usdt:.2f} USDT" if est_usdt else "金额未能估算")
            else:
                st.success("✅ 订单已提交（真实）")
                st.json(result.get("data") or result)
                if est_usdt:
                    st.session_state["tr_used_today"] = used_today + est_usdt
                st.session_state["tr_refresh_flag"] = st.session_state.get("tr_refresh_flag", 0) + 1
        except Exception as exc:  # noqa: BLE001
            st.error(f"下单失败：{exc}")

st.caption(f"今日已用量（估算）≈ {st.session_state.get('tr_used_today', 0.0):.2f} USDT")

st.write("---")

# --------------------------------------------------------------------------- #
# 挂单与撤单
# --------------------------------------------------------------------------- #
st.subheader("📋 当前挂单")
if not do_refresh:
    st.caption("点上方「刷新余额/持仓/挂单」加载。")
elif not pend_rows:
    st.caption("无挂单（或查询失败）。")
else:
    st.dataframe(pd.DataFrame(pend_rows), width="stretch", hide_index=True)
    oid = st.text_input("要撤销的订单 ID", key="tr_cancel_id")
    if st.button("🗑️ 撤单", key="tr_cancel_btn"):
        try:
            r = client.cancel_order(trade_ccy, ord_id=oid.strip(), dry_run=dry_run)
            if r.get("dry_run"):
                st.warning("🧪 dry-run 撤单预览（未发送）")
                st.json({"url": r["url"], "body": r["body"]})
            else:
                st.success("撤单请求已发送")
                st.json(r.get("data") or r)
        except Exception as exc:  # noqa: BLE001
            st.error(f"撤单失败：{exc}")

if pos_rows:
    st.subheader("📌 持仓")
    st.dataframe(pd.DataFrame(pos_rows), width="stretch", hide_index=True)

st.write("---")
st.caption("⚠️ 免责声明：本页仅为便于研究与小额手动执行，不构成投资建议；实盘风险（含本金损失）由操作者自负。"
           "API Key 需具备交易权限，且请求出口 IP 必须命中该 key 的 IP 白名单。")
