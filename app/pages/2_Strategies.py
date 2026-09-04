# -*- coding: utf-8 -*-
"""策略库 Strategies：从 registry 动态列出全部策略与参数。"""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
import pandas as pd

from app import common as C

st.set_page_config(page_title="策略库 · OKX 交易面板", page_icon="📚",
                   layout="wide")
C.inject_css()

st.markdown('<div class="okx-title">策略库 Strategies</div>', unsafe_allow_html=True)
st.markdown('<div class="okx-sub">从 `strategies/` 注册表自动枚举 · 新策略文件加入后刷新页面即出现</div>',
            unsafe_allow_html=True)

strategies = C.all_strategies()
if not strategies:
    st.warning("注册表为空：请确认 strategies/ 目录下存在通过 `@register` 注册的策略模块"
               "（例如 strategies/sma_cross.py 的 SmaCross）。")
    st.stop()

st.success(f"共发现 **{len(strategies)}** 个已注册策略")

meta = pd.DataFrame([{
    "策略名": c.name,
    "类名": c.__name__,
    "模块": c.__module__.replace("strategies.", ""),
    "参数与默认值": C.describe_params(c),
    "说明": (c.__doc__ or "").strip().split("\n")[0][:120],
} for c in strategies])
st.dataframe(meta, width="stretch", hide_index=True)

st.write("---")
st.subheader("参数详情")

for c in strategies:
    doc = (c.__doc__ or "").strip()
    with st.expander(f"**{c.name}** `({c.__module__.replace('strategies.', '')}.{c.__name__})`",
                     expanded=False):
        if doc:
            st.markdown(doc)
        params = getattr(c, "params", {}) or {}
        if params:
            rows = [{"参数": k, "默认值": v, "类型": type(v).__name__}
                    for k, v in params.items()]
            st.dataframe(pd.DataFrame(rows), width="stretch",
                         hide_index=True)
            # 参数合法性快速预览
            preview = dict(params)
            st.caption("合法示例：`" + c.name + "(" + ", ".join(
                f"{k}={v}" for k, v in preview.items()) + ")`")
        else:
            st.caption("该策略无参数。")

st.write("---")
st.caption("信号语义：`long` 开多 / `short` 开空 / `close` 平仓 / `hold` 观望。"
           "回测与模拟盘对信号的处理约定见 `backtest/engine.py` 与 `paper/simulator.py`。")
