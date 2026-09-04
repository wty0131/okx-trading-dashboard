# -*- coding: utf-8 -*-
"""OKX API v5 轻量客户端（纯 requests 实现，不依赖任何第三方交易所 SDK）。

功能划分
--------
* 公开行情：K 线(candles)、最新价(ticker)，均支持可选 proxies 参数
  （本机代理示例 127.0.0.1:7897，仅行情类接口可用）。
* 私有接口：按 OKX v5 规范实现 HMAC-SHA256 签名(sign_request)，
  账户余额 / 持仓方法已写好签名与请求构造，但默认 dry_run=True 不实际
  发送请求；只有显式传 dry_run=False 才会真正执行。

安全约定
--------
* 本模块从不落盘、从不打印任何密钥。
* 凭据一律从环境变量读取：OKX_API_KEY / OKX_SECRET / OKX_PASSPHRASE，
  缺失时私有方法抛错提示"请在 .env 配置"（.env 本身不在此模块处理）。

参考：https://www.okx.com/docs-v5/  （签名规范章节）
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

import pandas as pd
import requests

# OKX 公开行情基础地址
BASE_URL = "https://www.okx.com"

# 私有接口鉴权头名
_H_KEY = "OK-ACCESS-KEY"
_H_SIGN = "OK-ACCESS-SIGN"
_H_TS = "OK-ACCESS-TIMESTAMP"
_H_PASS = "OK-ACCESS-PASSPHRASE"

# K 线支持的粒度（本层校验白名单）
ALLOWED_BARS = {
    "1m", "3m", "5m", "15m", "30m",
    "1H", "2H", "4H", "6H", "12H",
    "1D", "1W", "1M",
}

# K 线单次最大条数（OKX v5 限制为 300）
MAX_CANDLES_LIMIT = 300

# candles 返回字段顺序：ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm
_CANDLE_FIELDS = [
    "ts", "open", "high", "low", "close", "vol",
    "volCcy", "volCcyQuote", "confirm",
]


def _timestamp_iso() -> str:
    """生成 OKX v5 要求的 ISO8601 时间戳（毫秒精度，UTC，Z 结尾）。"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S") + f".{now.microsecond // 1000:03d}Z"


def sign_request(
    method: str,
    path: str,
    query: str = "",
    body: str = "",
    api_key: str = "",
    secret: str = "",
    passphrase: str = "",
) -> Dict[str, str]:
    """按 OKX v5 规范生成签名请求头（纯函数，不读环境变量、不发请求）。

    签名规则：prehash = timestamp + method + requestPath + body
    * GET   : requestPath 需带 query（"?" 后的原始串），body 为空；
    * POST  : requestPath 不带 query，body 为 JSON 字符串。
    然后 HMAC-SHA256(key=secret, msg=prehash) -> base64。

    参数
    ----
    method     : "GET" / "POST" 等（大小写不敏感）
    path       : 形如 "/api/v5/market/candles" 的请求路径
    query      : GET 的查询串（不含 "?"，如 "instId=BTC-USDT&bar=1H"）
    body       : POST 的请求体（JSON 字符串）
    api_key / secret / passphrase : 直接传入的凭据（也可由 OkxClient 从环境变量读取）

    返回
    ----
    dict：可直接附加到请求上的 OKX 鉴权请求头。
    """
    ts = _timestamp_iso()
    method_u = method.upper()
    request_path = f"{path}?{query}" if query else path
    prehash = f"{ts}{method_u}{request_path}{body}"
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")
    return {
        _H_KEY: api_key,
        _H_SIGN: signature,
        _H_TS: ts,
        _H_PASS: passphrase,
        "Content-Type": "application/json",
    }


class OkxClient:
    """OKX API v5 轻量客户端。

    proxies 形如 {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}，
    仅用于公开行情请求；私有(账户)接口请勿经公网代理调用。
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        proxies: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.proxies = proxies or {}
        self.timeout = timeout
        self.session = requests.Session()

    # ------------------------------------------------------------------ #
    # 私有：HTTP 层与鉴权
    # ------------------------------------------------------------------ #
    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """执行一次请求并统一校验 OKX 返回结构。"""
        url = self.base_url + path
        resp = self.session.request(
            method,
            url,
            params=params,
            headers=headers,
            json=json_body if method.upper() == "POST" else None,
            proxies=(self.proxies or None),
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"OKX HTTP {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        if payload.get("code") != "0":
            raise RuntimeError(
                f"OKX 接口错误 code={payload.get('code')} msg={payload.get('msg')} "
                f"({method} {path})"
            )
        return payload

    def _public_get(self, path: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """公开 GET，返回 data 列表。"""
        payload = self._request("GET", path, params=params)
        return payload.get("data", [])

    # ------------------------------------------------------------------ #
    # 公开行情
    # ------------------------------------------------------------------ #
    def get_candles(
        self,
        inst_id: str,
        bar: str = "1H",
        limit: int = 100,
        after: Optional[str] = None,
        before: Optional[str] = None,
    ) -> pd.DataFrame:
        """获取 K 线（candles），封装为 pandas DataFrame。

        返回列：open/high/low/close/vol（小写），时间索引为 UTC（tz-aware），
        升序排列（OKX 原始返回为倒序）。
        支持可选代理：本客户端初始化时传 proxies 即可。

        参数
        ----
        inst_id : 如 "BTC-USDT"
        bar     : 粒度，常用 1H / 4H / 1D（白名单见 ALLOWED_BARS）
        limit   : 条数，1~300（超出自动截断到 300）
        after/before : 分页游标（毫秒时间戳字符串），一般用不到
        """
        if bar not in ALLOWED_BARS:
            raise ValueError(f"不支持的 K 线粒度 bar={bar!r}，可选：{sorted(ALLOWED_BARS)}")
        limit = max(1, min(int(limit), MAX_CANDLES_LIMIT))
        params: Dict[str, Any] = {"instId": inst_id, "bar": bar, "limit": limit}
        if after is not None:
            params["after"] = after
        if before is not None:
            params["before"] = before

        raw = self._public_get("/api/v5/market/candles", params)
        if not raw:
            return pd.DataFrame(columns=["open", "high", "low", "close", "vol"])

        df = pd.DataFrame(raw, columns=_CANDLE_FIELDS)
        # OKX 数值均为字符串，逐列转 float
        for col in ("open", "high", "low", "close", "vol"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        # 时间戳：毫秒 -> UTC 时间索引；OKX 返回倒序，转升序
        df["time"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
        df = (
            df.set_index("time")
            .sort_index()
            .loc[:, ["open", "high", "low", "close", "vol"]]
        )
        df.index.name = "time"
        return df

    def get_ticker(self, inst_id: str) -> Dict[str, Any]:
        """获取最新行情（ticker），返回单条 dict。"""
        raw = self._public_get(
            "/api/v5/market/ticker", {"instId": inst_id}
        )
        if not raw:
            raise RuntimeError(f"OKX 无 {inst_id} 的 ticker 数据")
        item = dict(raw[0])
        # 可数值化的字段转 float，方便后续使用
        for key in ("last", "lastSz", "askPx", "askSz", "bidPx", "bidSz",
                    "open24h", "high24h", "low24h", "vol24h", "volCcy24h"):
            if key in item and item[key] not in (None, ""):
                try:
                    item[key] = float(item[key])
                except ValueError:
                    pass
        return item

    # ------------------------------------------------------------------ #
    # 私有：凭据与签名请求
    # ------------------------------------------------------------------ #
    @staticmethod
    def _require_credentials() -> Dict[str, str]:
        """从环境变量读取凭据；缺失即抛错并提示在 .env 配置。"""
        api_key = os.environ.get("OKX_API_KEY", "").strip()
        secret = os.environ.get("OKX_SECRET", "").strip()
        passphrase = os.environ.get("OKX_PASSPHRASE", "").strip()
        missing = [
            name for name, val in
            (("OKX_API_KEY", api_key), ("OKX_SECRET", secret),
             ("OKX_PASSPHRASE", passphrase))
            if not val
        ]
        if missing:
            raise RuntimeError(
                "缺少环境变量 " + ", ".join(missing)
                + "，请在 .env 配置后（OKX_API_KEY/OKX_SECRET/OKX_PASSPHRASE）再调用"
            )
        return {"api_key": api_key, "secret": secret, "passphrase": passphrase}

    def _signed_get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        dry_run: bool = True,
        description: str = "",
    ) -> Dict[str, Any]:
        """构造并（可选）执行一次签名 GET 请求。

        dry_run=True  （默认）：只构造签名请求，不发送，返回请求描述；
        dry_run=False        ：真正发送到 OKX 私有接口。
        无论哪种模式都要求环境变量凭据已配置。
        """
        creds = self._require_credentials()
        params = params or {}
        # query 按键排序后 URL 编码，保证签名与发送内容一致
        query = urlencode(
            [(k, str(v)) for k, v in sorted(params.items())]
        )
        headers = sign_request(
            "GET", path, query=query,
            api_key=creds["api_key"], secret=creds["secret"],
            passphrase=creds["passphrase"],
        )
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")

        if dry_run:
            # 占位返回：描述请求内容，不实际执行（也不落盘任何内容）
            return {
                "dry_run": True,
                "description": description or path,
                "method": "GET",
                "url": url,
                "params": params,
                "headers": headers,  # 含签名，仅内存调试用，请勿打印/落盘
            }

        payload = self._request("GET", path, params=params, headers=headers)
        return payload

    # ------------------------------------------------------------------ #
    # 私有：账户接口（默认 dry_run，不实际执行）
    # ------------------------------------------------------------------ #
    def get_balance(self, ccy: Optional[str] = None, dry_run: bool = True) -> Dict[str, Any]:
        """查询账户余额。

        默认 dry_run=True：只返回请求描述占位，不实际执行；
        传 dry_run=False 才会真正请求（需先配置好 OKX_API_KEY 等环境变量）。
        """
        params: Dict[str, Any] = {}
        if ccy:
            params["ccy"] = ccy  # 不传则查全部币种
        return self._signed_get(
            "/api/v5/account/balance", params, dry_run=dry_run,
            description="查询账户余额（GET /api/v5/account/balance）",
        )

    def get_positions(self, inst_id: Optional[str] = None, dry_run: bool = True) -> Dict[str, Any]:
        """查询持仓。

        默认 dry_run=True：只返回请求描述占位，不实际执行；
        传 dry_run=False 才会真正请求（需先配置好 OKX_API_KEY 等环境变量）。
        """
        params: Dict[str, Any] = {}
        if inst_id:
            params["instId"] = inst_id  # 不传则查全部持仓
        return self._signed_get(
            "/api/v5/account/positions", params, dry_run=dry_run,
            description="查询持仓（GET /api/v5/account/positions）",
        )
