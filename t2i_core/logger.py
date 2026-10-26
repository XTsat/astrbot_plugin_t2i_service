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

# loguru 没有 ``exception`` 这个级别名，它用 ``opt(exception=...)`` 打 ERROR。
_LOGURU_LEVEL = {
    "debug": "DEBUG",
    "info": "INFO",
    "warning": "WARNING",
    "error": "ERROR",
    "exception": "ERROR",
}

_MISSING = object()


def _render_text(message: object, args: tuple[Any, ...]) -> str:
    """把 loguru 的 ``{}`` 与 logging 的 ``%s`` 两种参数风格拼成一条字符串。

    核心层消息都已在调用处拼好字符串，这里把额外参数用空格兜底拼接，
    避免宿主是 logging.Logger 时报格式错误。
    """
    if not args:
        return str(message)
    return str(message) + " " + " ".join(str(arg) for arg in args)


def _emit(inner: Any, level: str, text: str, kwargs: dict[str, Any]) -> None:
    """把一条日志投递给宿主 logger，并消化 stdlib 风格的关键字参数。

    为什么必须消化而不能透传：宿主可能是 loguru（插件模式注入 AstrBot logger），
    也可能是 stdlib logging。前者会静默忽略 ``exc_info``（堆栈直接丢掉），后者
    要求 ``exc_info``/``extra`` 只能作关键字参数。代理层原先只接受位置参数，
    于是 ``logger.warning(msg, exc_info=True)`` 抛 TypeError，把一次渲染失败变成
    aiohttp 的裸 500。这里统一收口：带堆栈的写入口走 ``opt(exception=...)`` 或
    ``exc_info=``，其余关键字直接忽略，保证日志调用永远不会反噬业务。
    """
    exc_info = kwargs.get("exc_info", _MISSING)
    if exc_info is _MISSING:
        exc_info = kwargs.get("exception", _MISSING)
    if exc_info is not _MISSING and exc_info is not False and exc_info is not None:
        opt = getattr(inner, "opt", None)
        if callable(opt):  # loguru：只有 opt(exception=...) 才带堆栈
            opt(exception=exc_info).log(_LOGURU_LEVEL.get(level, level.upper()), text)
            return
        method = getattr(inner, level, None)
        if method is not None:
            if level == "exception":
                method(text)
            else:
                method(text, exc_info=exc_info)
            return

    getattr(inner, level)(text)


class PrefixedLogger:
    """在每条日志前追加固定前缀（如 ``[t2i]``），并保持常用方法签名。"""

    __slots__ = ("_inner", "_prefix")

    def __init__(self, inner: Any, prefix: str) -> None:
        self._inner = inner
        self._prefix = prefix

    def _log(
        self,
        level: str,
        message: object,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        _emit(self._inner, level, f"{self._prefix}{_render_text(message, args)}", kwargs)

    def debug(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._log("debug", message, args, kwargs)

    def info(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._log("info", message, args, kwargs)

    def warning(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._log("warning", message, args, kwargs)

    def error(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._log("error", message, args, kwargs)

    def exception(self, message: object, *args: Any, **kwargs: Any) -> None:
        # logging.Logger.exception 需要在 except 块内调用才能带堆栈；
        # loguru 的 opt(exception=...) 无此限制，两者都由 _emit 统一处理。
        self._log("exception", message, args, kwargs)


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

    def _log(
        self,
        level: str,
        message: object,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        _emit(self._target(), level, _render_text(message, args), kwargs)

    def debug(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._log("debug", message, args, kwargs)

    def info(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._log("info", message, args, kwargs)

    def warning(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._log("warning", message, args, kwargs)

    def error(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._log("error", message, args, kwargs)

    def exception(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._log("exception", message, args, kwargs)


_proxy = _CurrentLogger()


def get_logger() -> Any:
    """返回当前生效的 logger 代理；核心层统一从这里取用。"""
    return _proxy
