# -*- coding: utf-8 -*-
"""行情数据层：OKX 直连 + 备用公开源降级 + 本地增量缓存。

设计目标（对应 UI 需求）
-----------------------
* ``get_candles_cached(inst_id, bar, limit)`` 返回与 ``okx.OkxClient.get_candles``
  同构的 DataFrame：小写 ohlcv 列 + UTC tz-aware 时间索引，升序。
* 数据源优先级：
    1. OKX（直连 / 代理） —— 权威源，走 okx.OkxClient；
    2. Binance ``api.binance.com/api/v3/klines`` —— 备用源（覆盖全部粒度、
       支持向历史分页）；注意 Binance / US 域名在国内可能被墙/封禁，
       失败会静默降级并写日志；
    3. CoinGecko 日线 ``/coins/{id}/ohlc`` —— 仅 1D 粒度时的次级备用
       （Binance 也不可用时）。
  每一级失败只记录日志与 LAST_CALL 诊断，不影响返回。
* 本地缓存：``data/cache/{inst_id}_{bar}.csv``（data/cache 已被 .gitignore
  排除）。**增量策略**：每次只补“最新缺失段”（缓存末根 K 线之后到当前这根），
  不再全量重拉；当请求条数大于缓存深度时才向历史补拉。文件保留最近
  ``max(limit, 300)`` 根，避免无限膨胀。
* 环境开关 ``OKX_MARKET_OFFLINE=1`` 可完全禁用网络（仅用于离线自检/调试）。

约定
----
* 毫秒时间戳按 K 线起点对齐（UTC）。OKX/Binance 的 1m~1D 起点与 epoch 对齐，
  不同源的同一根 K 线 ts 一致，可安全按索引去重（保留先写入的 OKX 行）。
  1W / 1M 边界跨交易所可能错位，UI 不开放这两个粒度，函数内部也会跳过
  跨源“补洞”以免拼出双份数据。
* 任何源都失败且缓存为空时抛 RuntimeError；缓存存在则返回缓存并给出告警。
"""

from __future__ import annotations

import json
import logging
import os
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from okx.okx_client import OkxClient

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
#: 项目根（data/market.py 位于 <root>/data/ 下）
ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache"
LOGS_DIR = ROOT / "logs"

#: 常用现货交易对（OKX 用 '-' 分隔；对外请求时按各源规则转换）
TRADING_PAIRS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "DOGE-USDT",
    "XRP-USDT", "ADA-USDT", "AVAX-USDT", "LINK-USDT", "DOT-USDT",
]

#: UI 开放展示的粒度（排除 1W/1M 的跨源边界错位问题）
UI_BARS = ["1m", "5m", "15m", "30m", "1H", "4H", "6H", "12H", "1D"]

#: 每根 K 线的毫秒数（1W/1M 仅用于上界粗估）
BAR_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1H": 3_600_000, "2H": 7_200_000,
    "4H": 14_400_000, "6H": 21_600_000, "12H": 43_200_000,
    "1D": 86_400_000, "1W": 604_800_000, "1M": 2_592_000_000,
}

#: OKX bar -> Binance kline interval
_BINANCE_INTERVAL = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1H": "1h", "2H": "2h", "4H": "4h", "6H": "6h", "12H": "12h",
    "1D": "1d", "1W": "1w", "1M": "1M",
}

#: OKX 交易对 -> CoinGecko 币 id（仅内置常用对；其它对无映射则跳过该源）
_COINGECKO_IDS = {
    "BTC-USDT": "bitcoin", "ETH-USDT": "ethereum", "SOL-USDT": "solana",
    "BNB-USDT": "binancecoin", "DOGE-USDT": "dogecoin", "XRP-USDT": "ripple",
    "ADA-USDT": "cardano", "AVAX-USDT": "avalanche-2", "LINK-USDT": "chainlink",
    "DOT-USDT": "polkadot",
}

#: OKX /api/v5/market/tickers 只给这些字段做数值化
_TICKER_NUMERIC = (
    "last", "lastSz", "askPx", "askSz", "bidPx", "bidSz",
    "open24h", "high24h", "low24h", "vol24h", "volCcy24h",
)

_EMPTY = pd.DataFrame(columns=["open", "high", "low", "close", "vol"])

#: 单次调用内的内存缓存（同 (inst,bar,limit) 短 TTL 内不重复联网）
_MEMO: Dict[tuple, tuple] = {}
_MEMO_TTL = 5.0

#: 最近一次 get_candles_cached 的诊断信息（UI 读取展示）
LAST_CALL: Dict[str, Any] = {}


def _log() -> logging.Logger:
    logger = logging.getLogger("okx_system.market")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(
                LOGS_DIR / "market.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        except OSError:  # 日志目录不可写时静默
            logger.addHandler(logging.NullHandler())
    return logger


def _note(key: str, value: Any):
    LAST_CALL[key] = value


# --------------------------------------------------------------------------- #
# 代理 / 环境
# --------------------------------------------------------------------------- #
def get_proxies() -> Dict[str, str]:
    """读取代理配置（仅行情类请求使用，私有接口勿走公网代理）。

    优先级：OKX_PROXIES(JSON) > OKX_PROXY(单 URL，http/https 同用)
    > OKX_HTTP_PROXY / OKX_HTTPS_PROXY。
    """
    raw = os.environ.get("OKX_PROXIES", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                out = {k: str(v) for k, v in data.items()
                       if k in ("http", "https") and v}
                if out:
                    return out
        except json.JSONDecodeError:
            pass
    single = os.environ.get("OKX_PROXY", "").strip()
    if single:
        return {"http": single, "https": single}
    out: Dict[str, str] = {}
    for key, k in (("OKX_HTTP_PROXY", "http"), ("OKX_HTTPS_PROXY", "https")):
        v = os.environ.get(key, "").strip()
        if v:
            out[k] = v
    if out:
        return out
    # Windows 注册表系统代理兜底（requests 默认读不到注册表代理）
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        if enabled and server:
            url = server if "://" in server else f"http://{server}"
            return {"http": url, "https": url}
    except Exception:  # noqa: BLE001  注册表不可读时静默跳过
        pass
    # 兜底：本机惯例代理端口 7897 若在监听则使用（Vortex/Clash 等）
    try:
        import socket
        with socket.create_connection(("127.0.0.1", 7897), timeout=0.4):
            return {"http": "http://127.0.0.1:7897",
                    "https": "http://127.0.0.1:7897"}
    except Exception:  # noqa: BLE001
        pass
    return out


def _offline() -> bool:
    return os.environ.get("OKX_MARKET_OFFLINE", "0") == "1"


# --------------------------------------------------------------------------- #
# 小工具：HTTP / 帧操作 / 缓存文件
# --------------------------------------------------------------------------- #
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 okx_system-ui")


def _http_json(url: str, params: Dict[str, Any], proxies: Dict[str, str],
               session: Optional[requests.Session] = None,
               timeout: float = 8.0) -> Any:
    """GET JSON，异常统一抛 RuntimeError。"""
    sess = session or requests.Session()
    try:
        resp = sess.get(url, params=params, proxies=proxies or None,
                        headers={"User-Agent": _UA}, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"请求失败 {url}: {exc.__class__.__name__} {exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _to_ms(ts: Any) -> int:
    """Timestamp / datetime / ISO 字符串 / epoch 秒 -> 毫秒 int。"""
    if isinstance(ts, pd.Timestamp):
        return int(ts.timestamp() * 1000)
    if isinstance(ts, datetime):
        return int(ts.timestamp() * 1000)
    if isinstance(ts, str):
        return int(pd.Timestamp(ts, tz="UTC").timestamp() * 1000)
    return int(ts)


def _frame_from_ts_ms(ts_ms, ohlcv: Dict[str, List[float]]) -> pd.DataFrame:
    """由毫秒时间戳数组 + ohlcv 字典构建统一 DataFrame。"""
    idx = pd.to_datetime(pd.Series(ts_ms, dtype="int64"), unit="ms", utc=True)
    df = pd.DataFrame(ohlcv, index=pd.DatetimeIndex(idx))
    df.index.name = "time"
    df = df[~df.index.duplicated(keep="first")].sort_index()
    for col in ("open", "high", "low", "close", "vol"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    return df


def _merge_frames(frames: List[pd.DataFrame]) -> pd.DataFrame:
    """升序合并多个 DataFrame：按索引去重（先到者优先，即 OKX 优先）。"""
    keep = [f for f in frames if f is not None and not f.empty]
    if not keep:
        return _EMPTY.copy()
    if len(keep) == 1:
        df = keep[0].copy()
    else:
        df = pd.concat(keep)
        df = df[~df.index.duplicated(keep="first")]
        df = df.sort_index()
    df.index.name = "time"
    return df


def _trim_tail(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """只保留最近 n 根。"""
    if df.empty or len(df) <= n:
        return df
    return df.iloc[-n:]


# --------------------------------------------------------------------------- #
# 本地缓存
# --------------------------------------------------------------------------- #
def cache_path(inst_id: str, bar: str) -> Path:
    return CACHE_DIR / f"{inst_id}_{bar}.csv"


def _read_cache(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return _EMPTY.copy()
    try:
        raw = pd.read_csv(path)
    except Exception:
        return _EMPTY.copy()  # 文件损坏当作无缓存，下次重建
    if raw.empty or "ts_ms" not in raw.columns:
        return _EMPTY.copy()
    idx = pd.to_datetime(raw["ts_ms"].astype("int64"), unit="ms", utc=True)
    vol = raw["vol"] if "vol" in raw.columns else float("nan")
    df = pd.DataFrame({
        "open": raw["open"].to_numpy(),
        "high": raw["high"].to_numpy(),
        "low": raw["low"].to_numpy(),
        "close": raw["close"].to_numpy(),
        "vol": vol.to_numpy() if hasattr(vol, "to_numpy") else float("nan"),
    })
    df.index = pd.DatetimeIndex(idx)
    df.index.name = "time"
    for col in ("open", "high", "low", "close", "vol"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    return df.sort_index()


def _write_cache(path: Path, df: pd.DataFrame):
    if df.empty:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    # 注意：pandas>=2.2/3.x 的 datetime64 底层单位可能是 us 或 ns，
    # 不能用 asi8//1_000_000 推毫秒；统一用距 epoch 的毫秒换算。
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    ms = ((df.index - epoch) / pd.Timedelta(milliseconds=1)).astype("int64")
    out = pd.DataFrame({
        "ts_ms": ms,
        "open": df["open"], "high": df["high"], "low": df["low"],
        "close": df["close"], "vol": df.get("vol", float("nan")),
    })
    out.to_csv(tmp, index=False)
    os.replace(tmp, path)  # 原子替换，避免写一半


def cache_stats() -> Dict[str, Any]:
    """离线统计 data/cache 状态（Home/Settings 展示用）。"""
    files = sorted(CACHE_DIR.glob("*.csv")) if CACHE_DIR.exists() else []
    total_rows = 0
    total_bytes = 0
    detail = []
    for f in files[-8:]:
        try:
            raw = pd.read_csv(f, usecols=["ts_ms"])
            rows = len(raw)
            ts = raw["ts_ms"].astype("int64")
            total_rows += rows
            total_bytes += f.stat().st_size
            span = (int(ts.max()) - int(ts.min())) / 86_400_000.0
            detail.append({
                "file": f.name, "rows": rows,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "days_span": round(span, 2),
                "mtime": datetime.fromtimestamp(f.stat().st_mtime),
            })
        except Exception:
            continue
    return {"count": len(files), "rows": total_rows, "size_kb": round(total_bytes / 1024, 1),
            "detail": detail}


def clear_cache() -> int:
    """删除 data/cache 下所有 .csv（本程序生成的运行时缓存），返回删除数。"""
    n = 0
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.csv"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n


# --------------------------------------------------------------------------- #
# 各数据源取数
# --------------------------------------------------------------------------- #
def _okx_session(proxies: Dict[str, str]) -> OkxClient:
    return OkxClient(proxies=proxies or None, timeout=8.0)


def _fetch_okx(inst_id: str, bar: str, proxies: Dict[str, str],
               session: Optional[OkxClient] = None) -> pd.DataFrame:
    """OKX 最新 K 线（默认最近 300 根，升序返回）。"""
    client = session or _okx_session(proxies)
    df = client.get_candles(inst_id, bar=bar, limit=300)
    return df if not df.empty else _EMPTY.copy()


def _fetch_okx_older(inst_id: str, bar: str, older_than_ms: int,
                     proxies: Dict[str, str],
                     session: Optional[OkxClient] = None) -> pd.DataFrame:
    """OKX 取比 older_than_ms 更早的一页（≤300 根）。"""
    client = session or _okx_session(proxies)
    df = client.get_candles(inst_id, bar=bar, limit=300,
                            after=str(int(older_than_ms)))
    return df if not df.empty else _EMPTY.copy()


def _binance_symbol(inst_id: str) -> str:
    return inst_id.replace("-", "")


def _fetch_binance_window(inst_id: str, bar: str, proxies: Dict[str, str],
                          end_ms: Optional[int] = None,
                          oldest_ms: Optional[int] = None,
                          limit: int = 1000) -> pd.DataFrame:
    """Binance 向历史分页取 K 线，覆盖 [oldest_ms, end_ms] 区间（尽力而为）。

    Binance klines 单次 ≤1000 根、按 openTime 升序返回；以 endTime 向前翻页。
    返回结果已按升序合并去重；区间超出 Binance 可回溯范围时只返回能取到的部分。
    """
    interval = _BINANCE_INTERVAL.get(bar)
    if interval is None:
        return _EMPTY.copy()
    url = "https://api.binance.com/api/v3/klines"
    proxies = proxies or {}
    parts: List[pd.DataFrame] = []
    page_end = int(end_ms) if end_ms is not None else int(_time.time() * 1000)
    guard = 0
    with requests.Session() as sess:
        while guard < 12:  # 最多 12 页（1m 粒度约 12 小时，已远超 UI 需求）
            guard += 1
            payload = _http_json(
                url,
                {"symbol": _binance_symbol(inst_id), "interval": interval,
                 "limit": 1000, "endTime": page_end},
                proxies, session=sess, timeout=8.0)
            if not isinstance(payload, list) or not payload:
                break
            rows = []
            for k in payload:
                try:
                    ot = int(k[0])
                    rows.append([ot, float(k[1]), float(k[2]), float(k[3]),
                                 float(k[4]), float(k[5])])
                except (TypeError, ValueError, IndexError):
                    continue
            if not rows:
                break
            first_ot = rows[0][0]
            parts.append(_frame_from_ts_ms(
                [r[0] for r in rows],
                {"open": [r[1] for r in rows], "high": [r[2] for r in rows],
                 "low": [r[3] for r in rows], "close": [r[4] for r in rows],
                 "vol": [r[5] for r in rows]}))
            if oldest_ms is not None and first_ot <= int(oldest_ms):
                break
            page_end = first_ot - 1
            _time.sleep(0.15)
    return _merge_frames(parts)


def _fetch_coingecko_daily(inst_id: str, limit: int,
                           proxies: Dict[str, str]) -> pd.DataFrame:
    """CoinGecko 日线 OHLC（仅 1D 备用；无成交量 -> vol=NaN）。

    免费额度有限、限速较严，仅在 Binance 也不可用时触发。
    """
    cid = _COINGECKO_IDS.get(inst_id)
    if not cid:
        return _EMPTY.copy()
    days = max(2, limit)
    url = f"https://api.coingecko.com/api/v3/coins/{cid}/ohlc"
    payload = _http_json(url, {"vs_currency": "usd", "days": days},
                         proxies or {}, timeout=10.0)
    if not isinstance(payload, list) or not payload:
        return _EMPTY.copy()
    rows = []
    for k in payload:
        try:
            rows.append([int(k[0]), float(k[1]), float(k[2]),
                         float(k[3]), float(k[4])])
        except (TypeError, ValueError, IndexError):
            continue
    if not rows:
        return _EMPTY.copy()
    df = _frame_from_ts_ms(
        [r[0] for r in rows],
        {"open": [r[1] for r in rows], "high": [r[2] for r in rows],
         "low": [r[3] for r in rows], "close": [r[4] for r in rows],
         "vol": [float("nan")] * len(rows)})
    return df


# --------------------------------------------------------------------------- #
# 增量合并主流程
# --------------------------------------------------------------------------- #
def _try_okx_newest(inst_id: str, bar: str, proxies: Dict[str, str],
                    want: int, warnings: List[str],
                    used: List[str]) -> pd.DataFrame:
    try:
        client = _okx_session(proxies)
        df = client.get_candles(inst_id, bar=bar, limit=min(300, max(1, want)))
        if not df.empty:
            used.append("okx")
            return df
        warnings.append("OKX 返回空 K 线")
    except Exception as exc:  # noqa: BLE001 - 源失败属预期，记录后降级
        _log().info("OKX 获取 %s %s 失败: %s", inst_id, bar, exc)
        warnings.append(f"OKX 不可用：{str(exc)[:120]}")
    return _EMPTY.copy()


def _fill_holes(inst_id: str, bar: str, df: pd.DataFrame,
                proxies: Dict[str, str], warnings: List[str],
                used: List[str], min_ts: Optional[pd.Timestamp] = None,
                max_ts: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """扫描 df 内部缺口并用备用源补洞（跨源边界风险粒度跳过）。"""
    if bar in ("1W", "1M") or len(df) < 3:
        return df
    bar_ms = BAR_MS[bar]
    lo = _to_ms(df.index[0]) if min_ts is None else _to_ms(min_ts)
    hi = _to_ms(df.index[-1]) if max_ts is None else _to_ms(max_ts)
    gaps: List[tuple] = []
    prev = df.index[0]
    for t in df.index[1:]:
        delta = (t - prev).total_seconds() * 1000.0
        if delta > bar_ms * 1.5:
            gaps.append((_to_ms(prev) + bar_ms, _to_ms(t) - 1))
        prev = t
    if not gaps:
        return df
    pieces = [df]
    for g0, g1 in gaps:
        if g1 < lo or g0 > hi:
            continue
        try:
            part = _fetch_binance_window(inst_id, bar, proxies,
                                         end_ms=g1, oldest_ms=g0)
        except Exception as exc:  # noqa: BLE001
            _log().info("Binance 补洞失败 %s: %s", inst_id, exc)
            warnings.append(f"Binance 补洞失败：{str(exc)[:100]}")
            continue
        if not part.empty:
            used.append("binance")
            pieces.append(part)
    out = _merge_frames(pieces)
    return out


def get_candles_cached(inst_id: str, bar: str = "1H", limit: int = 300,
                       proxies: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    """统一取 K 线入口：OKX -> Binance -> CoinGecko(1D) + 本地增量缓存。

    返回最近 ``limit`` 根 K 线（不足 limit 则返回实际可得），升序，
    列 open/high/low/close/vol，UTC tz-aware 时间索引。诊断写入 LAST_CALL。
    """
    LAST_CALL.clear()
    _cfg = proxies or get_proxies()
    proxies = dict(_cfg) if _cfg else None
    limit = max(2, min(int(limit), 3000))
    if bar not in BAR_MS:
        raise ValueError(f"不支持的 K 线粒度 bar={bar!r}，可选：{sorted(BAR_MS)}")
    bar_ms = BAR_MS[bar]
    key = (inst_id, bar, limit)
    now = _time.time()
    if key in _MEMO:
        hit_at, hit_df = _MEMO[key]
        if now - hit_at < _MEMO_TTL:
            return hit_df.copy()

    warnings: List[str] = []
    used: List[str] = []
    path = cache_path(inst_id, bar)
    merged = _read_cache(path)

    # ---------- 1) 增量：只补最新缺失段 ----------
    if not _offline():
        now_ms = int(now * 1000)
        now_bar_ms = (now_ms // bar_ms) * bar_ms
        if merged.empty:
            # 全新缓存：先拿 OKX 最新（最多 300 根），不足 limit 交给补深
            merged = _try_okx_newest(inst_id, bar, proxies,
                                     min(limit, 300), warnings, used)
        else:
            last_ms = _to_ms(merged.index[-1])
            head_bars = max(0, (now_bar_ms - last_ms) // bar_ms)
            if head_bars > 0:
                want = min(300, head_bars + 3)
                patch = _try_okx_newest(inst_id, bar, proxies, want,
                                        warnings, used)
                if not patch.empty:
                    merged = _merge_frames([merged, patch])
                elif head_bars > 1:
                    # OKX 挂了：用 Binance 把 [缓存末, now] 整段补上
                    try:
                        part = _fetch_binance_window(
                            inst_id, bar, proxies,
                            end_ms=now_bar_ms, oldest_ms=last_ms)
                    except Exception as exc:  # noqa: BLE001
                        _log().info("Binance 补最新失败 %s: %s", inst_id, exc)
                        warnings.append(f"Binance 补最新失败：{str(exc)[:100]}")
                        part = _EMPTY.copy()
                    if not part.empty:
                        used.append("binance")
                        merged = _merge_frames([merged, part])
                # head_bars==1 且 OKX 空：说明刚开盘，暂无新 K 线，无需告警

        # ---------- 2) 深度不足则向历史补 ----------
        need_oldest_ms = now_bar_ms - (limit - 1) * bar_ms
        rounds = 0
        while not merged.empty and len(merged) < limit and rounds < 6:
            oldest_ms = _to_ms(merged.index[0])
            if oldest_ms <= need_oldest_ms:
                break
            rounds += 1
            try:
                older = _fetch_okx_older(inst_id, bar, oldest_ms, proxies)
            except Exception as exc:  # noqa: BLE001
                _log().info("OKX 历史分页失败 %s: %s", inst_id, exc)
                older = _EMPTY.copy()
                warnings.append(f"OKX 历史分页不可用：{str(exc)[:100]}")
            if not older.empty:
                used.append("okx")
                merged = _merge_frames([older, merged])
                if _to_ms(older.index[0]) >= oldest_ms:  # 没往更早推进
                    break
            else:
                break
        if len(merged) < limit:
            try:
                part = _fetch_binance_window(inst_id, bar, proxies,
                                             oldest_ms=need_oldest_ms)
            except Exception as exc:  # noqa: BLE001
                _log().info("Binance 补深失败 %s: %s", inst_id, exc)
                warnings.append(f"Binance 补深失败：{str(exc)[:100]}")
                part = _EMPTY.copy()
            if not part.empty:
                used.append("binance")
                merged = _merge_frames([merged, part])
        if len(merged) < limit and bar == "1D":
            try:
                cg = _fetch_coingecko_daily(inst_id, limit, proxies)
            except Exception as exc:  # noqa: BLE001
                _log().info("CoinGecko 备用失败 %s: %s", inst_id, exc)
                warnings.append(f"CoinGecko 不可用：{str(exc)[:100]}")
                cg = _EMPTY.copy()
            if not cg.empty:
                used.append("coingecko")
                merged = _merge_frames([merged, cg])
        # ---------- 3) 洞检测（尽力补，避免图/回测断裂） ----------
        merged = _fill_holes(inst_id, bar, merged, proxies, warnings, used)
    else:
        warnings.append("离线模式（OKX_MARKET_OFFLINE=1）：未发起任何网络请求")

    if merged.empty:
        _note("ok", False)
        _note("sources_used", used)
        _note("warnings", warnings)
        _note("msg", "所有行情源均不可用且本地无缓存")
        raise RuntimeError(
            f"获取 {inst_id} {bar} 失败：所有行情源不可用且本地无缓存 "
            f"（可用代理设置 OKX_PROXY 后重试）")

    # ---------- 4) 收尾：裁到目标长度并写缓存 ----------
    keep = max(limit, 300)
    merged = merged.sort_index()
    merged = merged[merged["close"].notna()]
    merged = _trim_tail(merged, keep)
    try:
        _write_cache(path, merged)
    except OSError as exc:
        warnings.append(f"缓存写入失败：{exc}")

    out = _trim_tail(merged, limit).copy()
    lag = max(0.0, (now - _to_ms(out.index[-1]) / 1000.0))
    _note("ok", True)
    _note("inst_id", inst_id)
    _note("bar", bar)
    _note("limit", limit)
    _note("rows", int(len(out)))
    _note("sources_used", used or ["cache"])
    _note("warnings", warnings)
    _note("lag_seconds", round(lag, 1))
    _note("last_time", out.index[-1])
    _note("first_time", out.index[0])
    _note("cache_file", str(path))
    if len(out) < limit:
        _note("msg", f"缓存仅 {len(out)} 根（请求 {limit} 根）——历史深度或离线受限")
    else:
        _note("msg", "OK")
    _MEMO[key] = (_time.time(), out.copy())
    return out


# --------------------------------------------------------------------------- #
# Ticker（批量）
# --------------------------------------------------------------------------- #
def get_ticker(inst_id: str, proxies: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """单个最新行情（OKX），失败抛 RuntimeError。"""
    client = _okx_session(dict(proxies or get_proxies()))
    return client.get_ticker(inst_id)


def get_tickers(inst_ids, proxies: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """批量最新行情：优先一次 OKX /market/tickers 拉全量再过滤；
    失败降级为逐对 get_ticker。返回 {inst_id: ticker dict 或 None}。
    """
    inst_ids = list(inst_ids)
    _cfg = proxies or get_proxies()
    proxies = dict(_cfg) if _cfg else None
    wanted = set(inst_ids)
    out: Dict[str, Any] = {i: None for i in inst_ids}
    try:
        payload = _http_json(
            "https://www.okx.com/api/v5/market/tickers",
            {"instType": "SPOT"}, proxies, timeout=8.0)
        data = payload.get("data", []) if isinstance(payload, dict) else []
        for item in data:
            iid = item.get("instId")
            if iid in wanted:
                t = dict(item)
                for key in _TICKER_NUMERIC:
                    v = t.get(key)
                    if v not in (None, ""):
                        try:
                            t[key] = float(v)
                        except (TypeError, ValueError):
                            pass
                out[iid] = t
        return out
    except Exception as exc:  # noqa: BLE001
        _log().info("OKX 批量 tickers 失败，降级逐对: %s", exc)
    client = _okx_session(proxies)
    for iid in inst_ids:
        try:
            out[iid] = client.get_ticker(iid)
        except Exception as exc:  # noqa: BLE001
            _log().info("OKX ticker %s 失败: %s", iid, exc)
    return out


# --------------------------------------------------------------------------- #
# 连通性探测（Settings 页使用；会真实发少量请求）
# --------------------------------------------------------------------------- #
def probe_sources(proxies: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """轻量探测 OKX / Binance / CoinGecko 连通性，返回逐源状态与耗时。"""
    _cfg = proxies or get_proxies()
    proxies = dict(_cfg) if _cfg else None
    result: Dict[str, Any] = {}
    probes = {
        "okx": ("https://www.okx.com/api/v5/market/ticker",
                {"instId": "BTC-USDT"}),
        "binance": ("https://api.binance.com/api/v3/ping", {}),
        "coingecko": ("https://api.coingecko.com/api/v3/ping", {}),
    }
    with requests.Session() as sess:
        for name, (url, params) in probes.items():
            t0 = _time.time()
            try:
                sess.get(url, params=params, proxies=proxies or None,
                         headers={"User-Agent": _UA}, timeout=5.0)
                result[name] = {"ok": True,
                                "ms": round((_time.time() - t0) * 1000, 0)}
            except Exception as exc:  # noqa: BLE001
                result[name] = {"ok": False,
                                "ms": round((_time.time() - t0) * 1000, 0),
                                "error": str(exc)[:120]}
    return result


def source_chain_text() -> str:
    """行情源链路说明（Home / Settings 展示用，纯文本）。"""
    if _offline():
        return "OKX → Binance → CoinGecko(1D) —— 当前处于离线模式（不联网）"
    proxy = get_proxies()
    if proxy:
        hosts = {k.split("://")[0] for k in proxy.values() if "://" in k}
        p = next(iter(proxy.values()))
        return f"OKX(代理 {p}) → Binance → CoinGecko(1D)"
    return "OKX(直连) → Binance → CoinGecko(1D)"
