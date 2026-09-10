# -*- coding: utf-8 -*-
"""实盘下单限额 —— 可独立测试的纯逻辑（不依赖 Streamlit）

为什么单独成模块：
  ``app/pages/6_Trade.py`` 是 Streamlit 页面，**import 即执行渲染**，无法在
  自检脚本里直接导入测试。而资金安全相关的判定（金额上限、fail-closed 行为、
  当日累计持久化）恰恰是最需要测试覆盖的部分，因此抽到这里。

背景（2026-09-11 修复）：
  1. 原实盘页把两道上限写成 ``est_usdt is not None and ...``，一旦行情不可用
     或估算异常，``est_usdt`` 为 None → **两道金额上限被整体跳过**，一笔无上限
     的真实订单会被静默放行。资金安全场景必须 fail-closed。
  2. 当日累计金额只存在 ``st.session_state``，页面一刷新即归零，
     「单日累计上限」实际退化成「单次页面会话上限」。现改为落盘。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

#: 默认落盘路径（相对本模块：app/ -> 项目根 -> data/）
DEFAULT_USAGE_PATH = Path(__file__).resolve().parents[1] / "data" / "trade_usage.json"


def today_str(now: Optional[datetime] = None) -> str:
    """当日日期键（UTC，YYYY-MM-DD）"""
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def load_daily_used(path: Path | str = DEFAULT_USAGE_PATH,
                    now: Optional[datetime] = None) -> float:
    """读取当日已下单金额；跨日、文件缺失或损坏一律返回 0.0"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("date") == today_str(now):
            return max(0.0, float(raw.get("used", 0.0)))
    except Exception:  # noqa: BLE001 —— 任何读取/解析问题都按 0 处理，不阻断页面
        pass
    return 0.0


def add_daily_used(amount: float, path: Path | str = DEFAULT_USAGE_PATH,
                   now: Optional[datetime] = None) -> float:
    """累加当日已下单金额并落盘；返回累加后的总额。

    写盘失败不抛异常（页面继续可用），但会打印告警——上限仍按内存值生效，
    下次刷新会退回磁盘值，属已知取舍。
    """
    total = load_daily_used(path, now) + max(0.0, float(amount))
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"date": today_str(now), "used": total}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 写入当日累计下单金额失败：{exc}")
    return total


def check_order_limits(
    *,
    est_usdt: Optional[float],
    used_today: float,
    cap_single: float,
    cap_day: float,
    ack: bool,
    confirm_word: str,
    preview_only: bool = False,
) -> Optional[str]:
    """实盘下单前的风控闸门。返回 None 表示放行，否则返回拦截原因。

    **fail-closed**：无法估算金额（``est_usdt`` 为 None 或 <= 0）时一律拦截，
    绝不跳过金额上限检查。这是与模拟盘最关键的区别——这里动的是真实资金。
    """
    if preview_only:
        return None

    if est_usdt is None or est_usdt <= 0:
        return ("无法估算本单的 USDT 金额（行情不可用或参数异常），"
                "出于资金安全已拦截。请稍后重试，或改用限价单并填写明确价格。")

    if est_usdt > cap_single:
        return f"单笔金额 ≈{est_usdt:.2f} USDT 超过单笔上限 {cap_single:.2f}"

    if used_today + est_usdt > cap_day:
        return (f"今日累计 ≈{used_today + est_usdt:.2f} USDT "
                f"超过单日上限 {cap_day:.2f}")

    if (not ack) or confirm_word.strip().upper() != "CONFIRM":
        return "需勾选风险确认并输入 CONFIRM"

    return None
