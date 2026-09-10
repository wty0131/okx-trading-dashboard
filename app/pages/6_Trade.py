# -*- coding: utf-8 -*-
"""实盘交易 Trade（与模拟盘完全独立）。

与「模拟盘」页的分工：
  * 模拟盘（4_Paper）：真实行情 + 本地撮合记账，**不触碰真实资金**；
  * 实盘交易（本页）：真实下单/撤单到 OKX。**仅当启动环境设置
    OKX_TRADING_ENABLED=1 时才可用**；未开启时本页只展示说明与只读账户数据。

安全设计（请勿移除）：
  1. 实盘开关：环境变量 OKX_TRADING_ENABLED=1（默认关闭）；
  2. 二次确认：勾选风险确认 + 输入 CONFIRM；
  3. 风控闸门：单笔上限 / 单日累计上限；
  4. 密钥仅从环境变量/.env 读取，页面不回显、不落日志。
"""
from pathlib import Path
import os
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 资金安全相关的判定逻辑抽到 app/trade_limits.py —— 本页 import 即渲染，
# 无法被自检脚本直接导入测试；限额判定必须可测。
from app.trade_limits import (  # noqa: E402
    add_daily_used as _add_daily_used,
    check_order_limits as _check_order_limits,
    load_daily_used as _load_daily_used,
)

import streamlit as st
import pandas as pd

from app import common as C
from okx.okx_client import OkxClient

C.inject_css()

LIVE_ENABLED = C.live_trading_enabled()

st.markdown('<div class="okx-title">实盘交易 Trade</div>', unsafe_allow_html=True)
st.markdown('<div class="okx-sub">真实下单到 OKX · 与「模拟盘」页完全独立</div>',
            unsafe_allow_html=True)

if not LIVE_ENABLED:
    st.error("🚫 **实盘未启用**：本页当前不可下单。")
    st.markdown(
        """
**开启方式（推荐）**：左侧 **「设置」页 → ⚡ 实盘交易总开关** → 勾选风险确认后点「开启实盘交易」。
开关会写入本机 `.env`（`OKX_TRADING_ENABLED=1`，已被 gitignore 排除），即时生效并重启保留。

也可以用启动环境变量（效果相同）：

```powershell
# PowerShell
$env:OKX_TRADING_ENABLED="1"
.venv\\Scripts\\python -m streamlit run app\\Home.py
```

开启后还需：① API Key 具备**交易权限**；② 请求出口 IP 在该 Key 的**白名单**内；
③ 页面上勾选风险确认并输入 `CONFIRM`。下单金额受**单笔/单日上限**约束。

> 💡 想先练手请用左侧「模拟盘」页：真实行情 + 本地记账，不会花一分钱。
        """
    )

client = OkxClient(proxies=C.market.get_proxies(), timeout=25)

# --------------------------------------------------------------------------- #
# 只读：余额 / 持仓 / 挂单（无论开关是否开启都可用）
# --------------------------------------------------------------------------- #
st.write("---")
st.subheader("🔍 账户只读信息")
r1, r2 = st.columns([1, 2.2], gap="large")
with r1:
    if st.button("🔄 刷新余额/持仓/挂单", key="tr_refresh", type="primary"):
        st.session_state["tr_refresh_flag"] = st.session_state.get("tr_refresh_flag", 0) + 1
    do_refresh = st.session_state.get("tr_refresh_flag", 0) > 0
with r2:
    trade_ccy = st.selectbox("交易对", C.market.TRADING_PAIRS, key="tr_inst")

balance_txt, pos_rows, pend_rows = None, [], []
if do_refresh:
    try:
        acc = (client.get_balance(dry_run=False).get("data") or [{}])[0]
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

if pos_rows:
    st.dataframe(pd.DataFrame(pos_rows), width="stretch", hide_index=True)
if do_refresh and pend_rows:
    st.dataframe(pd.DataFrame(pend_rows), width="stretch", hide_index=True)

# --------------------------------------------------------------------------- #
# 实盘下单（仅开关开启时显示）
# --------------------------------------------------------------------------- #
if not LIVE_ENABLED:
    st.stop()

st.write("---")
st.subheader("⚡ 实盘下单")
st.warning("以下操作会**真实发送到 OKX 并动用真实资金**，下单后不可撤销（可撤单但需行情配合）。")

with st.form("tr_order_form", border=True):
    f1, f2, f3, f4 = st.columns([1, 1, 1.4, 1.2], gap="medium")
    with f1:
        side = st.radio("方向", ["buy（买入）", "sell（卖出）"], key="tr_side")
    with f2:
        ord_type = st.radio("类型", ["market（市价）", "limit（限价）"], key="tr_type")
    with f3:
        market_buy_by_amount = st.checkbox("市价买入按金额下单（USDT）", value=True,
                                           key="tr_by_amt")
        sz = st.number_input("数量 / 金额", value=10.0, min_value=0.0, step=1.0,
                             format="%.6f", key="tr_sz")
    with f4:
        px = st.number_input("限价价格（限价单必填）", value=0.0, min_value=0.0,
                             step=1.0, format="%.4f", key="tr_px")

    g1, g2, g3 = st.columns([1, 1, 1.6], gap="medium")
    with g1:
        cap_single = st.number_input("单笔上限 (USDT)", value=10.0, min_value=1.0,
                                     step=1.0, key="tr_cap1")
    with g2:
        cap_day = st.number_input("单日累计上限 (USDT)", value=30.0, min_value=1.0,
                                  step=1.0, key="tr_cap2")
    with g3:
        preview_only = st.checkbox("仅预览（不发送，临时干跑）", value=False, key="tr_preview")
        ack = st.checkbox("我已知悉：真实资金、下单不可撤销、风险自担", key="tr_ack")
    confirm_word = st.text_input("输入 CONFIRM 以确认实盘下单", key="tr_confirm_word")
    submitted = st.form_submit_button("提交实盘订单", type="primary")

if submitted:
    side_v = "buy" if side.startswith("buy") else "sell"
    type_v = "market" if ord_type.startswith("market") else "limit"
    tgt = "quote_ccy" if (type_v == "market" and side_v == "buy" and market_buy_by_amount) else None

    est_usdt = None
    try:
        if type_v == "limit" and px > 0:
            est_usdt = sz * px
        elif side_v == "buy" and tgt == "quote_ccy":
            est_usdt = sz
        else:
            df = C.market.get_candles_cached(trade_ccy, "1H", limit=2)
            last = float(df["close"].iloc[-1]) if not df.empty else None
            if last and last > 0:
                est_usdt = sz * last
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 估算下单金额失败：{exc}")
        est_usdt = None

    used_today = _load_daily_used()
    blocked = None
    if not preview_only:
        # 风控闸门（fail-closed）。原实现两道上限都写作
        # `est_usdt is not None and ...`，行情不可用时 est_usdt 为 None →
        # 两道金额上限被整体跳过，无上限的真实订单被静默放行。
        blocked = _check_order_limits(
            est_usdt=est_usdt,
            used_today=used_today,
            cap_single=cap_single,
            cap_day=cap_day,
            ack=ack,
            confirm_word=confirm_word,
            preview_only=preview_only,
        )

    if blocked:
        st.error(f"🛑 已拦截：{blocked}")
    else:
        try:
            result = client.place_order(
                trade_ccy, side_v, sz, ord_type=type_v,
                px=(px if type_v == "limit" else None),
                tgt_ccy=tgt, dry_run=bool(preview_only),
            )
            if result.get("dry_run"):
                st.warning("🧪 仅预览（未发送到 OKX）")
                st.json({"url": result["url"], "body": result["body"]})
            else:
                st.success("✅ 实盘订单已提交")
                st.json(result.get("data") or result)
                if est_usdt:
                    # 落盘累计（原实现只写 session_state，刷新即归零）
                    used_now = _add_daily_used(est_usdt)
                    st.session_state["tr_used_today"] = used_now
                st.session_state["tr_refresh_flag"] = st.session_state.get("tr_refresh_flag", 0) + 1
        except Exception as exc:  # noqa: BLE001
            st.error(f"下单失败：{exc}")

st.caption(f"今日（UTC）已用量（估算）≈ {_load_daily_used():.2f} USDT"
           f"　·　数据落盘于 data/trade_usage.json，刷新页面不清零")

# --------------------------------------------------------------------------- #
# 撤单
# --------------------------------------------------------------------------- #
st.write("---")
st.subheader("🗑️ 撤单")
oid = st.text_input("要撤销的订单 ID（可先刷新上方列表复制 ordId）", key="tr_cancel_id")
if st.button("撤单", key="tr_cancel_btn", type="primary"):
    if not oid.strip():
        st.error("请先填写订单 ID")
    else:
        try:
            r = client.cancel_order(trade_ccy, ord_id=oid.strip(), dry_run=False)
            st.success("撤单请求已发送")
            st.json(r.get("data") or r)
            st.session_state["tr_refresh_flag"] = st.session_state.get("tr_refresh_flag", 0) + 1
        except Exception as exc:  # noqa: BLE001
            st.error(f"撤单失败：{exc}")

st.write("---")
st.caption("⚠️ 免责声明：实盘风险（含本金损失）由操作者自负；API Key 需交易权限且出口 IP 命中白名单。"
           "策略请先在「模拟盘」验证后再考虑实盘。")
