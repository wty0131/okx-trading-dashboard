# -*- coding: utf-8 -*-
"""策略注册表（先留空列表）。

具体策略模块通过 @register 装饰器注册：
    from strategies.registry import register

    @register
    class MyStrategy(Strategy): ...

REGISTRY 初始为空——只有导入对应策略模块后才会被填充，
界面/工厂可用 available() 枚举。
"""

#: 已注册策略类列表（初始为空，导入具体策略模块后填充）
REGISTRY: list = []


def register(cls):
    """把策略类登记进 REGISTRY（作为类装饰器使用）。"""
    if cls not in REGISTRY:
        REGISTRY.append(cls)
    return cls


def available() -> list:
    """返回已注册策略的 name 列表。"""
    return [c.name for c in REGISTRY]
