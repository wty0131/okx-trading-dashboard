# -*- coding: utf-8 -*-
"""账号连通测试（只读）：直连、禁代理、白名单 IP。PM 2026-09-04"""
import os, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 从 .env 载入（不引入 dotenv 依赖）
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

from okx.okx_client import OkxClient

client = OkxClient(proxies={}, timeout=20)  # 直连，不走任何代理
client.session.trust_env = False            # 忽略系统代理环境变量

print("== 连通性：直连 OKX 私有接口（GET /api/v5/account/balance）==")
try:
    payload = client.get_balance(dry_run=False)
    data = payload.get("data") or []
    if not data:
        print("OK 但 data 为空（账户可能无资产或无权限）")
    else:
        acc = data[0]
        total_eq = acc.get("totalEq")
        n_ccy = len(acc.get("details") or [])
        print(f"连接成功 ✅  totalEq(总权益≈USDT)={total_eq}  币种数={n_ccy}")
        # 只显示非零币种（脱敏：金额正常展示给本人）
        for d in (acc.get("details") or [])[:15]:
            eq = d.get("eq")
            try:
                if float(eq or 0) != 0:
                    print(f"   {d.get('ccy')}: eq={eq} avail={d.get('availEq')}")
            except Exception:
                pass
except Exception as e:
    print(f"调用失败: {type(e).__name__}: {e}")
    print("若为 code=50110/50111 -> IP 不在白名单或出口非 你的 key 白名单出口 IP（需关代理或用白名单网络直连）")
    print("若为 code=50113/50102 -> 权限不足或凭据/签名问题")
