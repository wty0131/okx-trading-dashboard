# -*- coding: utf-8 -*-
"""Streamlit 面板共享层：路径引导、深色 CSS、指标卡片、Plotly 图表、
策略自动发现与动态参数控件、模拟盘(paper)状态持久化。

注意：模块顶部不调用任何 streamlit 运行时 API（st.* 仅出现在函数内），
因此可被离线自检脚本安全 import。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# --------------------------------------------------------------------------- #
# 路径引导
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parents[1]


def ensure_sys_path():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def bootstrap():
    """页面统一引导：把项目根加入 sys.path 并尝试加载根 .env。"""
    ensure_sys_path()
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(ROOT / ".env")
    except Exception:  # noqa: BLE001
        pass


ensure_sys_path()

try:  # 若装过 python-dotenv 则由 .env 注入环境变量（可选，不强制）
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

# 轻量兜底：未安装 python-dotenv 时手动读取根 .env（只注入缺失键，不覆盖已设值）
def _load_env_file() -> None:
    envf = ROOT / ".env"
    if not envf.exists():
        return
    try:
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:  # noqa: BLE001
        pass


_load_env_file()

import data.market as market  # noqa: E402
from okx.okx_client import OkxClient  # noqa: E402

_log = logging.getLogger("okx_system.ui")
PAPER_DIR = ROOT / "data" / "paper_state"

#: 模拟盘撮合成本假设（与 backtest.engine / paper.simulator 默认一致）
PAPER_FEE_RATE = 0.001
PAPER_SLIPPAGE = 0.0005


# --------------------------------------------------------------------------- #
# 深色主题 CSS
# --------------------------------------------------------------------------- #
def inject_css():
    """注入深色卡片样式（幂等，仅注入一次）。"""
    import streamlit as st
    if st.session_state.get("__okx_css_injected"):
        return
    st.session_state["__okx_css_injected"] = True
    st.markdown("""
<style>
    .block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
    .okx-title {font-size: 1.9rem; font-weight: 800; letter-spacing: .3px;
                background: linear-gradient(90deg,#00d4aa,#38bdf8);
                -webkit-background-clip: text; -webkit-text-fill-color: transparent;}
    .okx-sub {color: #8b98a9; font-size: .92rem; margin-top: -.2rem;}
    .okx-card {background: #141c26; border: 1px solid #223047;
               border-radius: 12px; padding: .8rem 1rem .9rem 1rem;
               box-shadow: 0 2px 10px rgba(0,0,0,.25); height: 100%;}
    .okx-card h4 {margin: 0 0 .25rem 0; font-size: .82rem; color: #8b98a9;
                  font-weight: 600; letter-spacing: .4px;}
    .okx-val {font-size: 1.28rem; font-weight: 700; color: #e6edf3;
              font-variant-numeric: tabular-nums;}
    .okx-val.up {color: #26d07c;}
    .okx-val.down {color: #ff5d6c;}
    .okx-sub2 {font-size: .78rem; color: #64748b; margin-top: .1rem;}
    .okx-hint {color: #7d8b9d; font-size: .86rem;}
    .okx-badge {display:inline-block; padding: .1rem .5rem; border-radius: 6px;
                font-size: .75rem; font-weight: 600; margin-left: .3rem;}
    .okx-badge.ok {background: rgba(38,208,124,.14); color: #26d07c;}
    .okx-badge.bad {background: rgba(255,93,108,.14); color: #ff5d6c;}
    [data-testid="stSidebar"] {background: #0e141c;}
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {color:#9fb0c3;}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# 格式化
# --------------------------------------------------------------------------- #
def fmt_pct(v, digits: int = 2) -> str:
    if v is None or (isinstance(v, float) and v != v):  # NaN
        return "—"
    return f"{v * 100:.{digits}f}%"


def fmt_money(v, digits: int = 2) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "—"
    return f"{v:,.{digits}f}"


def fmt_dt(ts) -> str:
    try:
        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        else:
            t = t.tz_convert("UTC")
        return t.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:  # noqa: BLE001
        return str(ts)


def lag_text(lag_seconds: float) -> str:
    if lag_seconds is None:
        return "—"
    if lag_seconds < 90:
        return f"距当前 {int(lag_seconds)} 秒"
    mins = lag_seconds / 60
    if mins < 90:
        return f"距当前 {mins:.0f} 分钟"
    return f"距当前 {lag_seconds / 3600:.1f} 小时"


# --------------------------------------------------------------------------- #
# 指标卡片
# --------------------------------------------------------------------------- #
def metric_cards(items: List[Dict[str, Any]], cols: Optional[List[int]] = None,
                 key: str = "metrics"):
    """渲染一排 okx-card。item: {label, value, sub?, cls?: 'up'|'down'|''}"""
    import streamlit as st
    n = len(items)
    widths = cols or [1] * n
    col_objs = st.columns(widths)
    html = []
    for it, col in zip(items, col_objs):
        cls = it.get("cls", "")
        val = it.get("value", "—")
        sub = it.get("sub", "")
        with col:
            st.markdown(
                f'<div class="okx-card"><h4>{it.get("label", "")}</h4>'
                f'<div class="okx-val {cls}">{val}</div>'
                f'<div class="okx-sub2">{sub}</div></div>',
                unsafe_allow_html=True)
    return


def pct_cls(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return "up" if v >= 0 else "down"


def money_cls(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return "up" if v >= 0 else "down"


# --------------------------------------------------------------------------- #
# Plotly 图表
# --------------------------------------------------------------------------- #
def candle_figure(df: pd.DataFrame, title: str,
                  ma_fast: int = 20, ma_slow: int = 60):
    """K 线 + MA 叠加 + 成交量副图。"""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    if df is None or df.empty:
        return None
    close = df["close"].astype(float)
    fast = close.rolling(ma_fast).mean()
    slow = close.rolling(ma_slow).mean()
    colors = ["#26d07c" if c >= o else "#ff5d6c"
              for o, c in zip(df["open"], df["close"])]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.76, 0.24], vertical_spacing=0.04)
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"],
        close=close, name="K线",
        increasing_line_color="#26d07c", decreasing_line_color="#ff5d6c",
        increasing_fillcolor="#26d07c", decreasing_fillcolor="#ff5d6c"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=fast, name=f"MA{ma_fast}",
                             line=dict(color="#38bdf8", width=1.4)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=slow, name=f"MA{ma_slow}",
                             line=dict(color="#fbbf24", width=1.4)), row=1, col=1)
    vol = df["vol"].astype(float)
    fig.add_trace(go.Bar(x=df.index, y=vol, name="成交量",
                         marker_color=colors, opacity=.85), row=2, col=1)
    fig.update_layout(
        title=dict(text=title, font=dict(size=15)),
        height=640, margin=dict(l=10, r=10, t=52, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0),
        font=dict(color="#cbd5e1"))
    fig.update_xaxes(gridcolor="rgba(148,163,184,.12)")
    fig.update_yaxes(gridcolor="rgba(148,163,184,.12)")
    return fig


def nav_dd_figure(nav: pd.Series, title: str):
    """净值曲线 + 回撤面积图。"""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    if nav is None or len(nav) < 2:
        return None
    dd = nav / nav.cummax() - 1.0
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.7, 0.3], vertical_spacing=0.05)
    fig.add_trace(go.Scatter(x=nav.index, y=nav.values, name="净值",
                             line=dict(color="#00d4aa", width=1.8),
                             fill="tozeroy",
                             fillcolor="rgba(0,212,170,.08)"), row=1, col=1)
    fig.add_trace(go.Scatter(x=dd.index, y=dd.values * 100, name="回撤%",
                             line=dict(color="#ff5d6c", width=1),
                             fill="tozeroy",
                             fillcolor="rgba(255,93,108,.15)"), row=2, col=1)
    fig.update_layout(title=dict(text=title, font=dict(size=15)), height=520,
                      margin=dict(l=10, r=10, t=52, b=10),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      legend=dict(orientation="h", yanchor="bottom", y=1.01),
                      font=dict(color="#cbd5e1"))
    fig.update_xaxes(gridcolor="rgba(148,163,184,.12)")
    fig.update_yaxes(gridcolor="rgba(148,163,184,.12)")
    return fig


def equity_figures(curves: Dict[str, pd.Series], normalize: bool = True,
                   title: str = "模拟盘净值曲线对比"):
    """多条策略净值曲线叠加。curves: {label: Series}。"""
    import plotly.graph_objects as go
    if not curves:
        return None
    fig = go.Figure()
    palette = ["#00d4aa", "#38bdf8", "#fbbf24", "#a78bfa",
               "#f472b6", "#34d399", "#fb923c", "#22d3ee"]
    for i, (name, s) in enumerate(curves.items()):
        if s is None or len(s) < 1:
            continue
        y = s.values
        if normalize and len(y) > 1:
            base = float(y[0]) if y[0] else 1.0
            y = [v / base * 100 - 100 for v in y]
        fig.add_trace(go.Scatter(
            x=s.index, y=y, name=name, mode="lines",
            line=dict(width=1.8, color=palette[i % len(palette)])))
    fig.update_layout(title=dict(text=title, font=dict(size=15)), height=420,
                      margin=dict(l=10, r=10, t=52, b=10),
                      yaxis_title="相对启动涨跌 %" if normalize else "权益 (USDT)",
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      hovermode="x unified", font=dict(color="#cbd5e1"))
    fig.update_xaxes(gridcolor="rgba(148,163,184,.12)")
    fig.update_yaxes(gridcolor="rgba(148,163,184,.12)")
    return fig


# --------------------------------------------------------------------------- #
# 策略自动发现
# --------------------------------------------------------------------------- #
def all_strategies() -> list:
    """导入 strategies/ 下所有策略模块并返回 REGISTRY 中的类列表。

    未来其它员工新增策略文件后无需改 UI：重新运行页面即自动枚举。
    """
    import importlib
    import pkgutil
    import strategies as strategies_pkg
    from strategies.registry import REGISTRY
    for info in pkgutil.iter_modules(strategies_pkg.__path__):
        if info.name in ("base", "registry", "__init__"):
            continue
        try:
            importlib.import_module(f"strategies.{info.name}")
        except Exception as exc:  # noqa: BLE001
            _log.warning("策略模块 strategies.%s 导入失败: %s", info.name, exc)
    return list(REGISTRY)


def strategy_by_name(name: str):
    for cls in all_strategies():
        if cls.name == name:
            return cls
    return None


def describe_params(clazz) -> str:
    d = getattr(clazz, "params", {}) or {}
    return "，".join(f"{k}={v}" for k, v in d.items()) or "无参数"


def param_widgets(clazz, key_prefix: str, disabled: bool = False) -> Dict[str, Any]:
    """按策略 __init__ 参数默认值动态生成数字输入控件。

    数值参数：int 用整数步进；float 用浮点步进；
    非数值（str/bool）退化为 text/checkbox。返回用户选择的参数 dict。
    """
    import streamlit as st
    out: Dict[str, Any] = {}
    defaults = dict(getattr(clazz, "params", {}) or {})
    st.caption(f"参数默认：{describe_params(clazz)}")
    for pname, default in defaults.items():
        key = f"{key_prefix}_{clazz.name}_{pname}"
        if isinstance(default, bool):
            out[pname] = st.checkbox(f"`{pname}`", value=bool(default),
                                     key=key, disabled=disabled)
        elif isinstance(default, int):
            out[pname] = st.number_input(
                f"`{pname}` (int)", value=int(default), step=1,
                key=key, disabled=disabled)
        elif isinstance(default, float):
            step = round(abs(default) / 100.0, 6) if default else 0.01
            step = max(step, 1e-6)
            fmt = "%.4f" if abs(default) < 1000 else "%.1f"
            out[pname] = st.number_input(
                f"`{pname}` (float)", value=float(default), step=step,
                format=fmt, key=key, disabled=disabled)
        else:
            out[pname] = st.text_input(f"`{pname}`", value=str(default),
                                       key=key, disabled=disabled)
    return out


def instantiate(clazz, params: Dict[str, Any]):
    """实例化策略；参数非法时抛 ValueError（页面捕获展示）。"""
    try:
        return clazz(**params)
    except TypeError as exc:
        raise ValueError(f"参数不匹配：{exc}") from exc


# --------------------------------------------------------------------------- #
# 模拟盘(Paper)状态持久化
# --------------------------------------------------------------------------- #
def _slug(name: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_\-]", "_", str(name))


def paper_file(name: str) -> Path:
    return PAPER_DIR / f"{_slug(name)}.json"


def paper_names() -> List[str]:
    if not PAPER_DIR.exists():
        return []
    return sorted(p.stem for p in PAPER_DIR.glob("*.json"))


def save_paper(name: str, sim, cfg: Dict[str, Any]) -> Path:
    """把 PaperSimulator + 配置落盘为 data/paper_state/{name}.json。"""
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    envelope = {
        "version": 1,
        "strategy": cfg.get("strategy"),
        "params": cfg.get("params", {}),
        "inst_id": cfg.get("inst_id"),
        "bar": cfg.get("bar", "1H"),
        "initial_cash": cfg.get("initial_cash", 10_000.0),
        "fee_rate": cfg.get("fee_rate", PAPER_FEE_RATE),
        "slippage": cfg.get("slippage", PAPER_SLIPPAGE),
        "started_at": cfg.get("started_at"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "anchor_time": cfg.get("anchor_time"),
        "sim": sim.to_dict(),
    }
    path = paper_file(name)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(envelope, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_paper(name: str) -> Optional[Dict[str, Any]]:
    path = paper_file(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _log.warning("读取 %s 失败: %s", path, exc)
        return None


def delete_paper(name: str) -> bool:
    path = paper_file(name)
    if path.exists():
        path.unlink()
        return True
    return False


def build_sim_from_envelope(env: Dict[str, Any]):
    """从 envelope 还原 PaperSimulator（注入策略对象）。

    兼容两种信封结构：
    - 规范 envelope：顶层含 "sim"（PaperSimulator.to_dict() 结果）；
    - 早期 PM 基线格式：顶层含 "state"（内容同为 to_dict() 结果）。
    无法还原时返回 None（调用方应跳过该文件）。
    """
    from paper.simulator import PaperSimulator
    sim_data = env.get("sim")
    if not isinstance(sim_data, dict) or "inst_id" not in sim_data:
        legacy = env.get("state")
        if isinstance(legacy, dict) and "inst_id" in legacy:
            sim_data = legacy
    if not isinstance(sim_data, dict) or "inst_id" not in sim_data:
        _log.warning("跳过无法解析的模拟盘文件: strategy=%s inst=%s",
                     env.get("strategy"), env.get("inst_id"))
        return None
    clazz = strategy_by_name(env.get("strategy"))
    strategies = []
    if clazz is not None:
        try:
            strategies = [clazz(**env.get("params", {}))]
        except Exception as exc:  # noqa: BLE001
            _log.warning("重建策略 %s 失败: %s", env.get("strategy"), exc)
    return PaperSimulator.from_dict(sim_data, strategies=strategies)


def warm_strategy(sim, df: pd.DataFrame, anchor: pd.Timestamp,
                  include_anchor: bool = True) -> int:
    """用历史 K 线给策略内部状态预热（只喂策略 on_bar，不撮合不开仓）。

    用于“启动/继续”后让 SMA 等带内部缓冲的策略立即可用。
    """
    if df is None or df.empty or not sim.strategies:
        return 0
    sel = df.index <= anchor if include_anchor else df.index < anchor
    n = 0
    for _, bar in df.loc[sel].iterrows():
        sim.strategies[0].on_bar(bar)
        n += 1
    return n


def _row_ts(row) -> pd.Timestamp:
    return pd.Timestamp(row["time"]) if isinstance(row, dict) else pd.Timestamp(row)


def anchor_of(env: Dict[str, Any]) -> Optional[pd.Timestamp]:
    """模拟盘最后处理到的 K 线时间（锚点）：优先取权益曲线末点。"""
    curve = (env.get("sim") or {}).get("equity_curve") or []
    if curve:
        try:
            return pd.Timestamp(curve[-1][0])
        except Exception:  # noqa: BLE001
            pass
    a = env.get("anchor_time")
    return pd.Timestamp(a) if a else None


def equity_of_sim(sim) -> float:
    """当前权益 = cash + quantity × last_close。"""
    close = sim.last_close
    return sim.equity(close)


def open_position_text(sim) -> str:
    pos = sim.position()
    if pos == 0:
        return "空仓"
    side = "多" if pos > 0 else "空"
    qty = abs(sim.quantity)
    entry = sim.entry_price
    entry_s = fmt_money(entry, 6) if entry else "—"
    return f"{side}仓 {qty:.6f} @ {entry_s}"


def day_change(sim) -> Optional[float]:
    """当日(UTC)涨跌：以今日第一笔权益记录为基准。"""
    curve = sim.equity_curve or []
    if len(curve) < 2:
        return None
    now = pd.Timestamp.now(tz="UTC")
    start = pd.Timestamp(now.date(), tz="UTC")
    base = None
    last = None
    for t_str, eq in curve:
        t = pd.Timestamp(t_str)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        last = eq
        if t >= start and base is None:
            base = eq
    if base is None or last is None or base <= 0:
        return None
    return last / base - 1.0


# --------------------------------------------------------------------------- #
# API 连通性测试（Settings 页）
# --------------------------------------------------------------------------- #
def test_account_api() -> Dict[str, str]:
    """调用 get_balance(dry_run=True)：凭据缺失也应安全返回提示文本。"""
    client = OkxClient(proxies=market.get_proxies())
    try:
        result = client.get_balance(dry_run=True)
        return {
            "level": "success",
            "title": "API 连通性 OK（dry_run）",
            "detail": "已检测到 OKX_API_KEY/OKX_SECRET/OKX_PASSPHRASE 环境变量。"
                      f"返回：{result.get('description', '')}。dry_run 模式仅构造签名请求，"
                      "未发送任何真实请求、未读取任何密钥文件。",
        }
    except RuntimeError as exc:
        text = str(exc)
        if "请在 .env 配置" in text:
            return {
                "level": "info",
                "title": "未配置凭据（dry_run）",
                "detail": "未检测到 OKX_API_KEY / OKX_SECRET / OKX_PASSPHRASE 环境变量，"
                          "接口处于 dry_run 安全模式，未发送任何真实请求。"
                          f"提示原文：{text}",
            }
        return {"level": "error", "title": "调用异常", "detail": text}


def env_summary() -> Dict[str, str]:
    """展示当前代理/离线/凭据配置状态（不显示密钥值）。"""
    proxies = market.get_proxies()
    p = next(iter(proxies.values()), None) if proxies else None
    return {
        "proxy": p or "直连（未配置代理）",
        "offline": "是（OKX_MARKET_OFFLINE=1）" if market._offline() else "否",
        "api_key": "已配置" if os.environ.get("OKX_API_KEY", "").strip() else "未配置",
        "secret": "已配置" if os.environ.get("OKX_SECRET", "").strip() else "未配置",
        "passphrase": "已配置" if os.environ.get("OKX_PASSPHRASE", "").strip() else "未配置",
    }
