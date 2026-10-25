"""核心层统一日志出口：默认 loguru，允许宿主（插件模式）注入自己的 logger。

独立服务形态不注入时，行为与直接 ``from loguru import logger`` 完全一致；
插件模式在启动时调用 :func:`set_logger` 把 AstrBot 的插件 logger 注入进来，
这样核心层的渲染/清理日志会归到插件名下，而不是散落在 ``t2i_core.*``。

注入对象只要求具备 ``debug/info/warning/error/exception`` 方法——
``logging.Logger`` 与 loguru 的 ``Logger`` 都满足，因此本模块不引入任何
AstrBot 依赖。
"""

from __future__ import annotations

from typing import Any

from loguru import logger as _loguru_logger


class PrefixedLogger:
    """在每条日志前追加固定前缀（如 ``[t2i]``），并保持常用方法签名。

    loguru 的参数风格（``logger.info("x {}", v)``）与 logging 的懒格式化
    （``logger.info("x %s", v)``）不兼容；核心层消息都已在调用处拼好字符串，
    这里把额外参数用空格兜底拼接，避免宿主是 logging.Logger 时报格式错误。
    """

    __slots__ = ("_inner", "_prefix")

    def __init__(self, inner: Any, prefix: str) -> None:
        self._inner = inner
        self._prefix = prefix

    def _fmt(self, message: object, args: tuple[Any, ...]) -> str:
        text = str(message)
        if args:
            text = text + " " + " ".join(str(a) for a in args)
        return f"{self._prefix}{text}"

    def debug(self, message: object, *args: Any) -> None:
        self._inner.debug(self._fmt(message, args))

    def info(self, message: object, *args: Any) -> None:
        self._inner.info(self._fmt(message, args))

    def warning(self, message: object, *args: Any) -> None:
        self._inner.warning(self._fmt(message, args))

    def error(self, message: object, *args: Any) -> None:
        self._inner.error(self._fmt(message, args))

    def exception(self, message: object, *args: Any) -> None:
        # logging.Logger.exception 需要在 except 块内调用才能带堆栈；
        # loguru 的 opt(exception=True) 无此限制，统一走 exception 即可。
        self._inner.exception(self._fmt(message, args))


_logger: Any = _loguru_logger


def set_logger(host_logger: Any, prefix: str = "") -> None:
    """把宿主 logger 注入核心层，插件模式在初始化时调用一次。"""
    global _logger
    _logger = PrefixedLogger(host_logger, prefix) if prefix else host_logger


def reset_logger() -> None:
    """恢复默认 loguru logger（插件卸载、测试隔离时使用）。"""
    global _logger
    _logger = _loguru_logger


class _CurrentLogger:
    """动态转发到当前生效 logger 的代理。

    核心层模块在导入时执行 ``logger = get_logger()``，拿到的是本代理而不是
    快照；这样插件在导入之后才注入宿主 logger 也能立即生效。
    """

    def _target(self) -> Any:
        return _logger

    def debug(self, message: object, *args: Any) -> None:
        self._target().debug(message, *args)

    def info(self, message: object, *args: Any) -> None:
        self._target().info(message, *args)

    def warning(self, message: object, *args: Any) -> None:
        self._target().warning(message, *args)

    def error(self, message: object, *args: Any) -> None:
        self._target().error(message, *args)

    def exception(self, message: object, *args: Any) -> None:
        self._target().exception(message, *args)


_proxy = _CurrentLogger()


def get_logger() -> Any:
    """返回当前生效的 logger 代理；核心层模块统一从这里取用。"""
    return _proxy
