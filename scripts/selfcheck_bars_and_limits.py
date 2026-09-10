# -*- coding: utf-8 -*-
"""自测：K 线收盘判定 + 实盘下单限额（2026-09-11 修复项）

运行（用项目 .venv）：
    cd C:\\Users\\wty0131\\okx_system
    .venv\\Scripts\\python scripts\\selfcheck_bars_and_limits.py

覆盖两个修复：
  1. **未收盘的「半根 K 线」**：OKX candles 会连同正在形成的当前 K 线一起返回
     （confirm="0"）。原 get_candles 把 confirm 解析出来后在列裁剪时丢弃，
     调用方无从判断；模拟盘据此记账会污染净值，并把锚点推进到该 bar，
     导致其最终数据此后被永久跳过。
  2. **实盘限额可被绕过**：原上限判定写作 `est_usdt is not None and ...`，
     行情不可用时 est_usdt=None → 两道金额上限被整体跳过，无上限订单被放行；
     且当日累计只存 session_state，刷新即归零。

全部离线（不联网）；OKX 客户端用桩函数替换 _public_get。
末尾打印 PASS；任一步失败抛异常并退出非 0。
"""

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from data.market import BAR_MS, drop_unclosed_bars, is_bar_closed  # noqa: E402
from okx.okx_client import OkxClient  # noqa: E402
from app.trade_limits import (  # noqa: E402
    add_daily_used, check_order_limits, load_daily_used,
)

SEP = "=" * 78


def _row(ts_ms: int, close: float, confirm: str):
    """构造一行 OKX candles（字段序见 okx_client._CANDLE_FIELDS）"""
    return [str(ts_ms), "1", "1", "1", str(close), "1", "1", "1", confirm]


def _client_with(rows):
    c = OkxClient(timeout=1.0)
    c._public_get = lambda path, params: rows      # 桩：不发网络请求
    return c


def _check(n, total, title, fn):
    fn()
    print(f"[{n}/{total}] {title} OK")


# --------------------------------------------------------------------------- #
# 1. 按时间判定 K 线是否收盘
# --------------------------------------------------------------------------- #

def test_is_bar_closed():
    base = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
    ts = pd.Timestamp(base)
    now_edge = (base + timedelta(hours=1)).timestamp()

    # 周期结束的瞬间即算已收盘（ts + bar_ms <= now）
    assert is_bar_closed(ts, "1H", now=now_edge) is True, "周期结束瞬间应判为已收盘"
    assert is_bar_closed(ts, "1H", now=now_edge - 1) is False, "周期未满不应判为已收盘"
    assert is_bar_closed(ts, "1D", now=now_edge) is False, "1D 周期在 1 小时后远未结束"
    assert is_bar_closed(ts, "1D",
                         now=(base + timedelta(days=1)).timestamp()) is True
    # 未知粒度不阻断
    assert is_bar_closed(ts, "7X", now=now_edge) is True, "未知粒度不应阻断"


def test_drop_unclosed_bars():
    idx = pd.DatetimeIndex([datetime(2026, 9, 11, h, 0, tzinfo=timezone.utc)
                            for h in (8, 9, 10)])
    df = pd.DataFrame({"open": [1.0, 2.0, 3.0], "high": [1.0, 2.0, 3.0],
                       "low": [1.0, 2.0, 3.0], "close": [1.0, 2.0, 3.0],
                       "vol": [1.0, 1.0, 1.0]}, index=idx)

    # now = 10:30 → 最后一根（10:00 的 1H）未收盘
    now = datetime(2026, 9, 11, 10, 30, tzinfo=timezone.utc).timestamp()
    out = drop_unclosed_bars(df, "1H", now=now)
    assert len(out) == 2, f"应丢弃未收盘的最后一根，实际剩 {len(out)} 根"
    assert out.index[-1].hour == 9, "保留的最后应为一根已收盘的 bar"

    # now = 11:00 → 全部已收盘，原样返回
    now2 = datetime(2026, 9, 11, 11, 0, tzinfo=timezone.utc).timestamp()
    out2 = drop_unclosed_bars(df, "1H", now=now2)
    assert len(out2) == 3, f"全部收盘时不应丢弃，实际 {len(out2)} 根"
    assert out2 is df, "全部收盘时应原样返回同一对象（不做多余拷贝）"

    # 空表安全
    assert drop_unclosed_bars(pd.DataFrame(), "1H", now=now).empty


# --------------------------------------------------------------------------- #
# 2. OKX 客户端 drop_unclosed 参数（桩掉 HTTP）
# --------------------------------------------------------------------------- #

def test_okx_drop_unclosed():
    base_ms = 1_700_000_000_000
    # OKX 返回**倒序**（最新在前）；最后一根 confirm="0" 表示仍在形成
    rows = [
        _row(base_ms + 3600_000, 102.0, "0"),   # 最新，未收盘
        _row(base_ms, 101.0, "1"),
        _row(base_ms - 3600_000, 100.0, "1"),
    ]
    c = _client_with(rows)

    kept = c.get_candles("BTC-USDT", bar="1H", limit=10)
    assert len(kept) == 3, f"默认应保留全部（含未收盘），实际 {len(kept)}"
    assert kept.index.is_monotonic_increasing, "返回必须升序"

    dropped = c.get_candles("BTC-USDT", bar="1H", limit=10, drop_unclosed=True)
    assert len(dropped) == 2, f"drop_unclosed=True 应丢弃未收盘的最后一根，实际 {len(dropped)}"
    # 被丢的必须是「最新」的那根（排序后位于末尾），而不是最早那根
    assert dropped["close"].iloc[-1] == 101.0, (
        f"被丢弃的应是 close=102 的最新未收盘 bar，实际末根 close="
        f"{dropped['close'].iloc[-1]}"
    )
    assert list(dropped.columns) == ["open", "high", "low", "close", "vol"], \
        "返回列不得因本次改动而变化"

    # 最新一根已收盘 → 两种模式结果一致
    rows2 = [_row(base_ms + 3600_000, 102.0, "1")] + rows[1:]
    c2 = _client_with(rows2)
    assert len(c2.get_candles("BTC-USDT", bar="1H", limit=10)) == 3
    assert len(c2.get_candles("BTC-USDT", bar="1H", limit=10,
                              drop_unclosed=True)) == 3, "已收盘时不应丢弃"


# --------------------------------------------------------------------------- #
# 3. 当日累计下单金额落盘
# --------------------------------------------------------------------------- #

def test_daily_usage_persistence():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "trade_usage.json"

        assert load_daily_used(p) == 0.0, "文件不存在时应为 0"

        assert add_daily_used(10.0, p) == 10.0
        assert load_daily_used(p) == 10.0, "写入后应可读回（刷新页面不清零）"
        assert add_daily_used(7.5, p) == 17.5, "应累加"
        assert load_daily_used(p) == 17.5

        # 跨日归零
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        assert load_daily_used(p, now=tomorrow) == 0.0, "跨日应归零"

        # 文件损坏不抛异常
        p.write_text("{ 不是合法 json", encoding="utf-8")
        assert load_daily_used(p) == 0.0, "文件损坏时应安全返回 0"


# --------------------------------------------------------------------------- #
# 4. 实盘限额闸门（fail-closed）
# --------------------------------------------------------------------------- #

def _gate(est, used=0.0, cap1=10.0, cap2=30.0, ack=True, word="CONFIRM",
          preview=False):
    return check_order_limits(est_usdt=est, used_today=used, cap_single=cap1,
                              cap_day=cap2, ack=ack, confirm_word=word,
                              preview_only=preview)


def test_order_limits_fail_closed():
    # 核心：无法估算金额必须拦截（原实现会放行）
    assert _gate(None) is not None, "est_usdt=None 必须拦截（fail-closed）"
    assert _gate(0.0) is not None, "est_usdt=0 必须拦截"
    assert _gate(-5.0) is not None, "est_usdt 为负必须拦截"
    assert "无法估算" in _gate(None), "应给出可理解的拦截原因"

    # 正常限额
    assert _gate(5.0) is None, "5 USDT 在 10 上限内应放行"
    assert _gate(10.0) is None, "恰好等于上限应放行"
    assert _gate(10.01) is not None, "超过单笔上限应拦截"
    assert _gate(5.0, used=26.0) is not None, "5+26=31 超过单日 30 应拦截"
    assert _gate(4.0, used=26.0) is None, "4+26=30 恰好等于单日上限应放行"

    # 二次确认
    assert _gate(5.0, ack=False) is not None, "未勾选确认应拦截"
    assert _gate(5.0, word="confirm") is None, "CONFIRM 应大小写不敏感"
    assert _gate(5.0, word="") is not None, "未输入 CONFIRM 应拦截"

    # 仅预览不受限额约束（不发送真实订单）
    assert _gate(None, preview=True) is None, "仅预览应放行"
    assert _gate(99999.0, preview=True) is None, "仅预览不受金额上限约束"


def test_old_limit_logic_would_pass_unbounded_order():
    """回归证据：旧判定在 est_usdt=None 时会放行 —— 证明上面的断言有牙齿"""
    def old_gate(est_usdt, used, cap_single, cap_day, ack, word):
        if est_usdt is not None and est_usdt > cap_single:
            return "超过单笔上限"
        elif est_usdt is not None and used + est_usdt > cap_day:
            return "超过单日上限"
        elif (not ack) or word.strip().upper() != "CONFIRM":
            return "需二次确认"
        return None

    old = old_gate(None, 0.0, 10.0, 30.0, True, "CONFIRM")
    new = _gate(None)
    assert old is None, "回归证据不成立：旧判定本应放行"
    assert new is not None, "新判定必须拦截"
    print(f"      旧判定 est_usdt=None → {old!r}（放行，无上限）；"
          f"新判定 → 拦截 ✔")


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print(SEP)
    checks = [
        ("K 线收盘判定：按时间推算（含边界/未知粒度）", test_is_bar_closed),
        ("drop_unclosed_bars：只丢末尾未收盘且不改列", test_drop_unclosed_bars),
        ("OkxClient 新增 drop_unclosed（丢弃最新未收盘 bar）", test_okx_drop_unclosed),
        ("当日累计下单金额：落盘/累加/跨日归零/损坏容错",
         test_daily_usage_persistence),
        ("实盘限额 fail-closed：无法估算金额必须拦截", test_order_limits_fail_closed),
        ("回归证据：旧判定会放行无上限订单",
         test_old_limit_logic_would_pass_unbounded_order),
    ]
    for i, (title, fn) in enumerate(checks, 1):
        _check(i, len(checks), title, fn)
    print(SEP)
    print(f"BAR_MS 粒度数：{len(BAR_MS)}")
    print("PASS：K 线收盘判定与实盘限额自测全部通过 ✔")
