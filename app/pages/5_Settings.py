# -*- coding: utf-8 -*-
"""设置 Settings：行情源/代理（只读）、缓存管理、API 连通性测试、.env 说明。"""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
import pandas as pd

from app import common as C

st.set_page_config(page_title="设置 · OKX 交易面板", page_icon="⚙️",
                   layout="wide")
C.inject_css()

st.markdown('<div class="okx-title">设置 Settings</div>', unsafe_allow_html=True)
st.markdown('<div class="okx-sub">行情源 / 代理（只读）· 缓存 · API 连通性 · .env 说明</div>',
            unsafe_allow_html=True)

env_sum = C.env_summary()

# --------------------------------------------------------------------------- #
# 行情源与代理（只读）
# --------------------------------------------------------------------------- #
st.subheader("🌐 行情源与代理（只读）")
left_s, right_s = st.columns(2, gap="large")
with left_s:
    st.markdown("##### 数据链路")
    st.markdown(f"- 主源：**OKX** — 当前：`{env_sum['proxy']}`")
    st.markdown("- 备用源：**Binance** klines（失败静默降级并记录 `logs/market.log`）")
    st.markdown("- 次级备用：**CoinGecko** 日线（仅 1D 粒度）")
    st.markdown(f"- 本地缓存：`data/cache/*.csv`（增量补段，非全量重拉）")
    st.markdown(f"- 离线开关：`{env_sum['offline']}`")
    st.caption("代理来源：环境变量 `OKX_PROXIES`(JSON) / `OKX_PROXY` / "
               "`OKX_HTTP_PROXY`+`OKX_HTTPS_PROXY` → 无则自动采用系统代理/本机 7897 惯例。"
               "账户真实请求必须命中该 key 的 IP 白名单（当前出口 7897 已加白名单）；"
               "下方账户测试仅为 dry_run，不发真实请求。")
with right_s:
    stats = C.market.cache_stats()
    st.markdown("##### 缓存统计")
    st.markdown(f"- 缓存文件数：**{stats['count']}**")
    st.markdown(f"- K 线总行数：**{stats['rows']:,}**")
    st.markdown(f"- 占用空间：**{stats['size_kb']} KB**")
    if stats["detail"]:
        st.dataframe(pd.DataFrame(stats["detail"]), width="stretch",
                     hide_index=True)

st.write("---")

# --------------------------------------------------------------------------- #
# API 连通性测试
# --------------------------------------------------------------------------- #
st.subheader("🔌 API 连通性测试")
st.markdown("##### 行情源探测（真实发少量请求）")
probe_proxy = env_sum["proxy"]
if st.button("▶️ 测试 OKX / Binance / CoinGecko 连通性", key="set_probe",
             type="primary"):
    with st.spinner("逐个探测（每源最多 5 秒）…"):
        result = C.market.probe_sources(proxies=C.market.get_proxies())
    rows = []
    for name in ("okx", "binance", "coingecko"):
        r = result.get(name, {})
        rows.append({
            "源": name,
            "状态": "✅ 可达" if r.get("ok") else "❌ 不可达",
            "耗时": f"{r.get('ms', 0):.0f} ms" if r.get("ms") is not None else "—",
            "说明": "" if r.get("ok") else (r.get("error") or "")[:100],
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

st.markdown("##### 账户接口（dry_run 安全检查）")
st.markdown("调用 `OkxClient.get_balance(dry_run=True)`：无论是否配置凭据都**不会**发送"
            "真实请求 —— 无凭据时预期提示「未配置凭据」，有凭据时仅构造签名请求描述。")
if st.button("🧪 测试 get_balance(dry_run=True)", key="set_balance"):
    out = C.test_account_api()
    if out["level"] == "success":
        st.success(f"**{out['title']}**  \n{out['detail']}")
    elif out["level"] == "info":
        st.info(f"**{out['title']}**  \n{out['detail']}")
    else:
        st.error(f"**{out['title']}**  \n{out['detail']}")

st.write("---")

# --------------------------------------------------------------------------- #
# 填写 API Key（用户自填；仅本机 .env 或本次会话）
# --------------------------------------------------------------------------- #
import os as _os
st.subheader("🔑 填写你的 OKX API Key")
st.markdown(
    "三种方式任选：**本页填写**（保存到本机 `.env` 或仅本次会话）、或手动编辑项目根 "
    "`.env`。密钥**只在你的电脑上**保存与使用：不会上传、不入日志、界面不回显完整值"
    "（只显示是否已配置 + 末 4 位）。账户真实请求还需该 key 的 IP 白名单包含你当前"
    "出口 IP。"
)
with st.form("api_key_form", border=True):
    k_in = st.text_input("API Key", type="password", key="k_in",
                         value=_os.environ.get("OKX_API_KEY", ""),
                         help="OKX → API 管理 创建后可见；不填则仅走行情/回测/模拟盘")
    s_in = st.text_input("Secret Key", type="password", key="s_in",
                         value=_os.environ.get("OKX_SECRET", ""))
    p_in = st.text_input("Passphrase（创建 API Key 时设置）", type="password",
                         key="p_in", value=_os.environ.get("OKX_PASSPHRASE", ""))
    persist = st.radio("保存方式",
                       ["保存到本机 .env（推荐，重启仍生效）", "仅本次会话（不落盘）"],
                       horizontal=True, key="k_save_mode")
    sub2 = st.form_submit_button("保存并测试（dry_run，不发真实请求）")
if sub2:
    vals = [v.strip() for v in (k_in, s_in, p_in)]
    if persist.startswith("保存到本机"):
        env_path = C.ROOT / ".env"
        lines = ["# okx_system 本地密钥（gitignore 已排除，绝不提交）",
                 f"OKX_API_KEY={vals[0] or ''}",
                 f"OKX_SECRET={vals[1] or ''}",
                 f"OKX_PASSPHRASE={vals[2] or ''}"]
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        st.toast("已写入本机 .env（不入 git）")
    for kk, vv in (("OKX_API_KEY", vals[0]), ("OKX_SECRET", vals[1]),
                   ("OKX_PASSPHRASE", vals[2])):
        if vv:
            _os.environ[kk] = vv
    _os.environ.pop("OKX_API_KEY", None) if not vals[0] else None
    _os.environ.pop("OKX_SECRET", None) if not vals[1] else None
    _os.environ.pop("OKX_PASSPHRASE", None) if not vals[2] else None
    masked = " · ".join(
        f"{name}={'已配置(末4位 ' + v[-4:] + ')' if v else '未填写'}"
        for name, v in (("Key", vals[0]), ("Secret", vals[1]), ("Pass", vals[2])))
    st.success(masked)
    out = C.test_account_api()
    if out["level"] == "success":
        st.success(f"**{out['title']}**  \n{out['detail']}")
    else:
        st.info(f"**{out['title']}**  \n{out['detail']}")

st.write("---")

# --------------------------------------------------------------------------- #
# 缓存管理
# --------------------------------------------------------------------------- #
st.subheader("🗑️ 缓存管理")
st.markdown("清空 `data/cache/` 下所有 `.csv`（本面板生成的运行时缓存，可随时重建）。"
            "此操作不会删除任何源码或状态文件。")
if st.session_state.get("set_cache_confirm"):
    cc1, cc2 = st.columns(2)
    with cc1:
        if st.button("✅ 确认清空缓存", type="primary", key="set_cache_do"):
            n = C.market.clear_cache()
            st.session_state.pop("set_cache_confirm", None)
            st.success(f"已清空 {n} 个缓存文件。")
            st.rerun()
    with cc2:
        if st.button("取消", key="set_cache_no"):
            st.session_state.pop("set_cache_confirm", None)
            st.rerun()
else:
    if st.button("清空缓存", key="set_cache_ask"):
        st.session_state["set_cache_confirm"] = True
        st.rerun()

st.write("---")

# --------------------------------------------------------------------------- #
# .env 配置说明（不显示密钥）
# --------------------------------------------------------------------------- #
st.subheader("🔑 凭据与 .env 说明")
st.markdown("""
面板启动时自动加载项目根 `.env`（优先 `python-dotenv`，未安装则内置轻量解析兜底）。
界面**从不回显任何密钥**；账户类接口默认 `dry_run`，不发送真实请求。

```
# 项目根 .env（.gitignore 已排除，切勿提交）
OKX_API_KEY=你的APIKey
OKX_SECRET=你的Secret
OKX_PASSPHRASE=你的Passphrase

# 可选：行情代理（公开行情接口使用）
# OKX_PROXY=http://127.0.0.1:7897
```

当前凭据状态：API Key {ok} · Secret {ok2} · Passphrase {ok3}。
""".format(ok="✅ 已配置" if env_sum["api_key"] == "已配置" else "❌ 未配置（dry_run 模式）",
           ok2="✅ 已配置" if env_sum["secret"] == "已配置" else "❌ 未配置",
           ok3="✅ 已配置" if env_sum["passphrase"] == "已配置" else "❌ 未配置"))

st.write("---")
st.caption("⚠️ 本面板仅用于行情展示、历史回测与本地模拟盘研究，不构成投资建议。")
