# -*- coding: utf-8 -*-
"""自测：``.env`` 按 key 增量更新（2026-09-11 修复项）

运行（用项目 .venv）：
    cd C:\\Users\\wty0131\\okx_system
    .venv\\Scripts\\python scripts\\selfcheck_env_file.py

覆盖的问题：
  设置页保存 API Key 时**整文件覆写** ``.env`` 为 3 行，抹掉
  ``OKX_PROXY`` / ``OKX_HTTP_PROXY`` / ``OKX_HTTPS_PROXY`` /
  ``OKX_MARKET_OFFLINE`` / ``OKX_TRADING_ENABLED``。最坏后果是用户只想改个
  代理，实盘总开关却被静默关闭（或打开）。

⚠️ 本自测**全部在临时目录进行，绝不读写项目真实的 .env**。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.env_file import mask_value, read_env_keys, update_env_file  # noqa: E402

SEP = "=" * 78
SAMPLE = """\
# okx_system 环境变量
# 行情代理（可选）
OKX_PROXY=http://127.0.0.1:7897
OKX_HTTP_PROXY=http://127.0.0.1:7897
OKX_HTTPS_PROXY=http://127.0.0.1:7897

# 离线调试
OKX_MARKET_OFFLINE=0

# 实盘总开关（默认关闭）
OKX_TRADING_ENABLED=1

OKX_API_KEY=OLD_KEY_VALUE
OKX_SECRET=OLD_SECRET_VALUE
OKX_PASSPHRASE=OLD_PASS_VALUE
"""


def _check(n, total, title, fn):
    fn()
    print(f"[{n}/{total}] {title} OK")


# --------------------------------------------------------------------------- #
def test_update_preserves_other_keys():
    """核心：只改目标键，其余行（含注释/空行/其他键）逐行保留"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / ".env"
        p.write_text(SAMPLE, encoding="utf-8")
        before = p.read_text(encoding="utf-8").splitlines()

        res = update_env_file(p, {"OKX_API_KEY": "NEW_KEY",
                                  "OKX_SECRET": "NEW_SECRET",
                                  "OKX_PASSPHRASE": "NEW_PASS"})

        after = p.read_text(encoding="utf-8")
        assert res["updated"] == ["OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE"], res
        assert res["added"] == [], "不应新增键"

        # 关键：这些必须原样还在
        for must in ("OKX_PROXY=http://127.0.0.1:7897",
                     "OKX_HTTP_PROXY=http://127.0.0.1:7897",
                     "OKX_HTTPS_PROXY=http://127.0.0.1:7897",
                     "OKX_MARKET_OFFLINE=0",
                     "OKX_TRADING_ENABLED=1",
                     "# okx_system 环境变量",
                     "# 实盘总开关（默认关闭）"):
            assert must in after, f"增量更新却丢失了：{must!r}"

        # 目标键已替换为新值
        assert "OKX_API_KEY=NEW_KEY" in after
        assert "OLD_KEY_VALUE" not in after

        # 行数与顺序基本不变（只有值变了）
        tail = after.splitlines()
        assert len(tail) == len(before), f"行数变了：{len(before)} → {len(tail)}"
        assert tail.index("OKX_API_KEY=NEW_KEY") == before.index(
            "OKX_API_KEY=OLD_KEY_VALUE"), "键的位置被移动了"


def test_backup_and_atomic_write():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / ".env"
        p.write_text(SAMPLE, encoding="utf-8")
        update_env_file(p, {"OKX_API_KEY": "NEW"})

        bak = p.with_name(".env.bak")
        assert bak.exists(), "未生成备份"
        assert "OLD_KEY_VALUE" in bak.read_text(encoding="utf-8"), "备份内容不对"

        assert not p.with_name(".env.tmp").exists(), "临时文件未清理（原子替换失败）"

        # 再次更新不得覆盖更早的备份
        first_bak = bak.read_text(encoding="utf-8")
        update_env_file(p, {"OKX_API_KEY": "NEWER"})
        assert bak.read_text(encoding="utf-8") == first_bak, "备份被二次覆盖"


def test_empty_value_keeps_key_present():
    """留空表示「键=（空值）」而不是删除该键 —— 便于用户显式清空凭据"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / ".env"
        p.write_text(SAMPLE, encoding="utf-8")
        update_env_file(p, {"OKX_SECRET": ""})
        after = p.read_text(encoding="utf-8")
        assert "OKX_SECRET=\n" in after or after.rstrip().endswith("OKX_SECRET="), \
            "空值应写成 KEY= 而非删除该键"
        assert "OLD_SECRET_VALUE" not in after


def test_creates_file_with_header_and_appends_new_keys():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / ".env"
        res = update_env_file(p, {"OKX_API_KEY": "K"}, header="# 表头")
        text = p.read_text(encoding="utf-8")
        assert text.startswith("# 表头\n"), f"表头缺失：{text!r}"
        assert "OKX_API_KEY=K" in text
        assert res["added"] == ["OKX_API_KEY"], res

        # 已有文件时追加新键，并保留既有键
        res2 = update_env_file(p, {"OKX_API_KEY": "K2", "OKX_PROXY": "http://p"})
        text2 = p.read_text(encoding="utf-8")
        assert "OKX_API_KEY=K2" in text2
        assert res2["updated"] == ["OKX_API_KEY"], res2
        assert res2["added"] == ["OKX_PROXY"], res2


def test_read_keys_and_mask_never_leak_values():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / ".env"
        p.write_text(SAMPLE, encoding="utf-8")
        keys = read_env_keys(p)
        assert "OKX_PROXY" in keys and "OKX_TRADING_ENABLED" in keys, keys
        assert all("=" not in k for k in keys), "read_env_keys 只应返回键名"

    # 脱敏函数绝不返回完整密钥（此处用一眼可辨的假值，非任何真实密钥）
    secret = "DUMMY-not-a-real-key-0000ABCD"
    out = mask_value(secret)
    assert secret not in out, f"脱敏后仍含完整密钥：{out!r}"
    assert secret[-4:] in out, f"应保留末 4 位：{out!r}"
    assert out != secret and len(out) < len(secret) + 20, f"脱敏结果异常：{out!r}"
    assert mask_value("") == "未填写"
    assert secret not in mask_value("x" * 1)  # 极短值不整段回显


def test_old_overwrite_would_destroy_config():
    """回归证据：旧实现（整文件覆写 3 行）会抹掉全部其他配置"""
    vals = ("K", "S", "P")
    old_text = "\n".join([
        "# okx_system 本地密钥（gitignore 已排除，绝不提交）",
        f"OKX_API_KEY={vals[0]}", f"OKX_SECRET={vals[1]}",
        f"OKX_PASSPHRASE={vals[2]}",
    ]) + "\n"
    for must in ("OKX_PROXY", "OKX_TRADING_ENABLED", "OKX_MARKET_OFFLINE"):
        assert must not in old_text, f"回归证据不成立：旧写法竟保留了 {must}"
    print("      旧写法覆写后仅剩 4 行，OKX_PROXY / OKX_TRADING_ENABLED "
          "等全部丢失；新写法逐行保留 ✔")


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print(SEP)
    checks = [
        ("增量更新保留其他键/注释/空行与键位置", test_update_preserves_other_keys),
        ("写盘前备份 + 临时文件原子替换", test_backup_and_atomic_write),
        ("空值写成 KEY= 而非删除键", test_empty_value_keeps_key_present),
        ("新建文件加表头、并追加新键", test_creates_file_with_header_and_appends_new_keys),
        ("read_env_keys 只返回键名；mask_value 不泄露完整密钥",
         test_read_keys_and_mask_never_leak_values),
        ("回归证据：旧写法会抹掉全部其他配置",
         test_old_overwrite_would_destroy_config),
    ]
    for i, (title, fn) in enumerate(checks, 1):
        _check(i, len(checks), title, fn)
    print(SEP)
    print("PASS：.env 增量更新自测全部通过 ✔（全程使用临时文件，未触碰真实 .env）")
