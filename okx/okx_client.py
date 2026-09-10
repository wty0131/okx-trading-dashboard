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
import json
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
        raw_body: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行一次请求并统一校验 OKX 返回结构。

        raw_body：已序列化好的 JSON 字符串。交易类 POST 必须用它——
        保证"参与签名的字符串"与"实际发送的字符串"完全一致。
        """
        url = self.base_url + path
        kwargs: Dict[str, Any] = dict(
            params=params,
            headers=headers,
            proxies=(self.proxies or None),
            timeout=self.timeout,
        )
        if raw_body is not None:
            kwargs["data"] = raw_body.encode("utf-8")
        elif method.upper() == "POST":
            kwargs["json"] = json_body
        resp = self.session.request(method, url, **kwargs)
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

    # ------------------------------------------------------------------ #
    # 私有：签名 POST（交易类接口；默认 dry_run 不发送）
    # ------------------------------------------------------------------ #
    def _signed_post(
        self,
        path: str,
        body: Dict[str, Any],
        dry_run: bool = True,
        description: str = "",
    ) -> Dict[str, Any]:
        """构造并（可选）执行一次签名 POST。

        dry_run=True（默认）：只返回"将要发送什么"的描述，绝不发送；
        dry_run=False         ：真实发送（需凭据 + 出口 IP 命中白名单 +
                                 该 API Key 具备对应权限）。签名与发送使用
                                 同一份序列化字符串，避免签名不一致。
        """
        creds = self._require_credentials()
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        headers = sign_request(
            "POST", path, body=raw,
            api_key=creds["api_key"], secret=creds["secret"],
            passphrase=creds["passphrase"],
        )
        if dry_run:
            return {
                "dry_run": True,
                "description": description or path,
                "method": "POST",
                "url": f"{self.base_url}{path}",
                "body": body,
                # headers 含签名，仅内存调试用，请勿打印/落盘
            }
        payload = self._request("POST", path, headers=headers, raw_body=raw)
        return payload

    def place_order(
        self,
        inst_id: str,
        side: str,
        sz: float,
        ord_type: str = "market",
        px: Optional[float] = None,
        td_mode: str = "cash",
        tgt_ccy: Optional[str] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """下单（默认 dry_run 不发送）。

        inst_id : 如 "BTC-USDT"
        side    : "buy" / "sell"
        sz      : 数量；市价买入时可配合 tgt_ccy="quote_ccy" 表示"按计价币金额下单"
        ord_type: "market"（市价）/ "limit"（限价，需 px）
        """
        side = side.lower()
        ord_type = ord_type.lower()
        if side not in ("buy", "sell"):
            raise ValueError("side 只能是 buy / sell")
        if ord_type not in ("market", "limit"):
            raise ValueError("ord_type 只能是 market / limit")
        body: Dict[str, Any] = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": ord_type,
            "sz": str(sz),
        }
        if ord_type == "limit":
            if px is None or px <= 0:
                raise ValueError("限价单必须给出正的 px")
            body["px"] = str(px)
        if tgt_ccy:
            body["tgtCcy"] = tgt_ccy
        return self._signed_post(
            "/api/v5/trade/order", body, dry_run=dry_run,
            description=f"下单 {side} {sz} {inst_id} ({ord_type})",
        )

    def cancel_order(
        self,
        inst_id: str,
        ord_id: Optional[str] = None,
        cl_ord_id: Optional[str] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """撤单（需 ordId 或 clOrdId 之一；默认 dry_run 不发送）。"""
        if not ord_id and not cl_ord_id:
            raise ValueError("必须提供 ord_id 或 cl_ord_id")
        body: Dict[str, Any] = {"instId": inst_id}
        if ord_id:
            body["ordId"] = str(ord_id)
        if cl_ord_id:
            body["clOrdId"] = str(cl_ord_id)
        return self._signed_post(
            "/api/v5/trade/cancel-order", body, dry_run=dry_run,
            description=f"撤单 {inst_id} ordId={ord_id or cl_ord_id}",
        )

    def get_pending_orders(self, inst_id: Optional[str] = None,
                           dry_run: bool = False) -> Dict[str, Any]:
        """查询未成交挂单（只读 GET；dry_run 默认 False 便于展示）。"""
        params: Dict[str, Any] = {}
        if inst_id:
            params["instId"] = inst_id
        return self._signed_get(
            "/api/v5/trade/orders-pending", params, dry_run=dry_run,
            description="查询挂单（GET /api/v5/trade/orders-pending）",
        )
