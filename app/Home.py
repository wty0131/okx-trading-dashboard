# -*- coding: utf-8 -*-
"""应用入口（中文导航）：用 st.navigation 定义侧栏中文页名。

页名与顺序：总览 / 行情 / 策略库 / 回测 / 模拟盘 / 设置 / 实盘交易
（模拟盘与实盘交易完全分开：模拟盘=本地记账不碰真钱；实盘交易=仅当
 OKX_TRADING_ENABLED=1 时可用，真实发送订单到 OKX。）
"""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from app import common as C

st.set_page_config(page_title="OKX 交易面板", page_icon="🪙", layout="wide")
C.inject_css()

_pages = [
    st.Page("pages/0_Overview.py", title="总览", icon="📊", default=True),
    st.Page("pages/1_Market.py", title="行情", icon="📈"),
    st.Page("pages/2_Strategies.py", title="策略库", icon="📚"),
    st.Page("pages/3_Backtest.py", title="回测", icon="🧪"),
    st.Page("pages/4_Paper.py", title="模拟盘", icon="🧾"),
    st.Page("pages/5_Settings.py", title="设置", icon="⚙️"),
    st.Page("pages/6_Trade.py", title="实盘交易", icon="⚡"),
]

st.navigation(_pages).run()
