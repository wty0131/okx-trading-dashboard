# -*- coding: utf-8 -*-
"""``.env`` 按 key 增量更新 —— 可独立测试的纯逻辑（不依赖 Streamlit）

背景（2026-09-11 修复）：
  ``app/pages/5_Settings.py`` 保存 API Key 时**整文件覆写** ``.env`` 为 3 行，
  把 ``OKX_PROXY`` / ``OKX_HTTP_PROXY`` / ``OKX_HTTPS_PROXY`` /
  ``OKX_MARKET_OFFLINE`` / ``OKX_TRADING_ENABLED`` 等全部抹掉。
  最坏的连锁反应：用户只是想改个代理，结果**实盘总开关被静默关闭**（或反之），
  且没有任何提示。

  现改为按 key 增量更新：只替换目标键，其余行（注释、空行、其他键与自定义顺序）
  原样保留；写盘前先备份、写盘用「临时文件 + 原子替换」，避免中途崩溃留下半截文件。

安全约定：本模块**绝不打印、不记录、不返回任何密钥值**，只回报被更新的键名。
"""
from __future__ import annotations

import re
from pathlib import Path

#: 匹配 ``KEY=...`` 形式的行（允许前导空白与 ``export `` 前缀）
_KV_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def update_env_file(
    path: Path | str,
    updates: dict[str, str],
    *,
    header: str | None = None,
    backup: bool = True,
) -> dict[str, list[str]]:
    """按键增量更新 ``.env``，**保留其余所有行**。

    Args:
        path:    ``.env`` 路径（不存在则创建）
        updates: {键名: 新值}，空字符串表示写入「键=（留空）」而非删除该键
        header:  文件不存在时写入的首行注释
        backup:  是否先把原文件备份为 ``<name>.bak``（仅备份一次，不覆盖更早的备份）

    Returns:
        {"updated": [...被替换的键...], "added": [...新增的键...],
         "preserved": 保留的原文件行数}

    绝不返回或打印任何值。
    """
    p = Path(path)
    updates = {k: ("" if v is None else str(v)) for k, v in updates.items()}
    pending = dict(updates)

    lines: list[str] = []
    if p.exists():
        if backup:
            bak = p.with_name(p.name + ".bak")
            if not bak.exists():
                try:
                    bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
                except OSError:
                    pass
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []

    out: list[str] = []
    updated: list[str] = []
    kept = 0

    for line in lines:
        m = _KV_RE.match(line)
        key = m.group(1) if m else None
        if key is not None and key in pending:
            out.append(f"{key}={pending.pop(key)}")   # 只替换值，键名保持原样
            updated.append(key)
        else:
            out.append(line)
            kept += 1

    # 文件不存在且需要表头
    if not out and header:
        out.append(header)

    added = sorted(pending.keys())
    for key in added:
        out.append(f"{key}={pending[key]}")

    text = "\n".join(out)
    if text and not text.endswith("\n"):
        text += "\n"

    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)          # 原子替换：崩溃不会留下半截文件

    return {"updated": updated, "added": added, "preserved": kept}


def read_env_keys(path: Path | str) -> list[str]:
    """列出 ``.env`` 中的键名（**只返回键名，不返回值**）"""
    p = Path(path)
    if not p.exists():
        return []
    keys: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        m = _KV_RE.match(line)
        if m and m.group(1) not in keys:
            keys.append(m.group(1))
    return keys


def mask_value(value: str, keep: int = 4) -> str:
    """把密钥脱敏为「已配置(末 N 位 xxxx)」—— 唯一允许出境的展示形式"""
    v = (value or "").strip()
    if not v:
        return "未填写"
    tail = v[-keep:] if len(v) >= keep else "*" * len(v)
    return f"已配置(末{keep}位 {tail})"
