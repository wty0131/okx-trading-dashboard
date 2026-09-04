# 🪙 OKX Trading Dashboard（本地加密交易研究面板）

面向 OKX 的本地加密量化研究面板：**真实行情 → K 线策略 → 历史回测 → 本地前向模拟盘（不碰真实资金）**，纯 Python + Streamlit，行情直连多源自动降级，密钥仅存本机。

> ⚠️ 本项目用于行情展示、策略回测与**本地模拟交易研究**，不构成投资建议；模拟结果不代表实盘表现。

## ✨ 功能

| 页 | 功能 |
|---|---|
| 总览 | 各模拟盘账户权益汇总 + 净值曲线 + 系统状态 |
| 行情 | 10 个主流币实时价 + K 线蜡烛图（MA20/60 + 成交量），UTC 时间 |
| 策略库 | 注册表自动枚举 7 个 K 线策略与参数 |
| 回测 | 交易对 × 周期 × 策略（参数动态调节）→ 收益指标卡 + 净值/回撤图 + 交易明细 + CSV 导出 |
| 模拟盘 | 每策略一张卡：启动（历史预热+空仓快照）/ 更新（逐根前向撮合）/ 重置；真实行情+本地撮合（手续费 0.1% + 滑点 0.05% 假设），状态持久化 |
| 设置 | 行情源/缓存状态、**填写你的 OKX API Key**、API dry-run 连通测试 |

### 策略（K 线驱动，默认参数，全部可调）
SMA 双均线 · EMA+RSI 趋势过滤 · 布林带均值回归 · MACD 趋势 · 唐奇安通道突破 · RSI(2) 超跌反弹 · 动量跟随 —— 均支持做多/离场/做空语义，`strategies/` 新增文件即自动注册。

## 🚀 快速开始

```bash
git clone <本仓库地址>
cd okx-trading-dashboard
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
.venv\Scripts\python -m streamlit run app\Home.py
```

浏览器打开 `http://localhost:8501`（Windows 也可双击项目根 `OKX交易系统.bat`）。

### 首次使用
- **行情/回测/模拟盘开箱即用**，不需要任何 Key（公开行情 + 本地模拟撮合）；
- 网络受限时可配置代理（环境变量 `OKX_PROXY=http://127.0.0.1:7897`）；行情源按 OKX → Binance → CoinGecko 自动降级，并有本地增量缓存（`data/cache/`）。

## 🔑 接入你自己的 OKX 账户（可选、只读优先）
1. OKX → API 管理 → 新建 Key，**只勾「读取」**（后续如需交易再单独评估）；
2. 把该 Key 的出口 IP 加入白名单（你的网络出口 IP；若走代理则以代理出口 IP 为准）；
3. 在「设置」页粘贴 API Key / Secret / Passphrase → 保存到本机 `.env`（或仅本次会话）→ 点 dry-run 测试；
4. 密钥**只存在你自己的电脑**（`.env` 已被 `.gitignore` 排除）：不上传、不入日志、界面不回显（仅显示末 4 位）。

> 账户类接口默认 `dry_run`（构造签名请求但不发送）；本面板不自动下单，实盘需自行二次开发并自担风险。

## 📁 结构

```
okx-trading-dashboard/
├── app/                  # Streamlit 页面（Home + pages/1~5）
├── okx/okx_client.py     # OKX v5 轻量客户端（纯 requests，签名请求；凭据仅读环境变量）
├── data/
│   ├── market.py         # 行情多源降级 + 增量缓存
│   └── cache/            # 行情缓存（git 忽略）
│   └── paper_state*      # 模拟盘状态（git 忽略）
├── strategies/           # 策略库（自动注册）
├── backtest/engine.py    # 向量化回测
├── paper/simulator.py    # 本地前向模拟器
├── scripts/              # 自检与模拟盘跟进脚本
└── OKX交易系统.bat       # Windows 一键启动
```

## 🧪 测试
```bash
.venv\Scripts\python scripts\selfcheck.py               # 基础层自检
.venv\Scripts\python scripts\selfcheck_strategies.py    # 7 策略自检
.venv\Scripts\python scripts\selfcheck_ui.py            # UI 组件自检
.venv\Scripts\python scripts\run_paper_update.py        # 推进 1D/1H 模拟盘
```

## 🔒 隐私与安全
- 本仓库不含任何真实密钥、个人账号信息或网络指纹；`.env`、`data/cache/`、`data/paper_state*/`、日志与模拟盘产物均被 `.gitignore` 排除；
- API 凭据只从环境变量/本机 `.env` 读取，从不写入日志或提交到 git；
- 请勿把 `.env`、截图里的 Key 或你的 IP 白名单信息外传。

## License
当前仓库未声明开源许可证，使用前请联系作者。
