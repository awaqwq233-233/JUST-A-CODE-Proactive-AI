"""固定版本 Gateway SDK；旧 :9060 客户端和自动启动器已移除。"""
from importlib import import_module


def __getattr__(name):
    """按需导入 Gateway，不加载旧音频、检测或模型运行栈。"""
    if name not in {"GatewayClient", "GatewayCallbacks"}:
        raise AttributeError(name)
    value = getattr(import_module(".gateway_client", __name__), name)
    globals()[name] = value
    return value


__all__ = ["GatewayClient", "GatewayCallbacks"]
