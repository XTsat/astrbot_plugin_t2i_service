"""核心层统一日志出口：不依赖任何日志库，由宿主注入。

上架规范要求插件只能用 ``astrbot.api`` 的 logger；而核心层还要被独立服务
（FastAPI）复用，不能 import ``astrbot``（见 AGENTS.md 2.2）。两者的折中就是
「核心层只留一个可注入的出口」：

* 插件模式：``main.py`` 在 ``initialize()`` 里把 ``astrbot.api.logger`` 注入进来，
  核心层的渲染 / 清理日志因此和插件层共用宿主的 logger 与 ``[t2i]`` 前缀；
* 独立服务：没人注入时落到内置的 :class:`StderrLogger`，``docker logs`` 照样看得到；
* 注入对象只要求具备 ``debug/info/warning/error/exception`` 方法，不绑定任何具体库。
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from typing import Any

PREFIX = "[t2i] "

_MISSING = object()


def _render_text(message: object, args: tuple[Any, ...]) -> str:
    """把消息与额外参数拼成一条字符串。

    核心层消息都已在调用处拼好，这里把多余参数用空格兜底拼上，避免宿主按
    ``%s`` / ``{}`` 重新格式化时报错。
    """
    if not args:
        return str(message)
    return str(message) + " " + " ".join(str(arg) for arg in args)


class StderrLogger:
    """内置兜底出口：直接写 stderr，供独立服务形态在没有宿主注入时使用。

    刻意不引入任何日志库——插件形态由宿主注入 logger，这里只需要「别把日志丢掉」。
    """

    __slots__ = ()

    def _write(self, level: str, text: str, with_trace: bool) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stream = sys.stderr
        try:
            stream.write(f"{stamp} | {level:<5} | {text}\n")
            if with_trace and sys.exc_info()[0] is not None:
                stream.write(traceback.format_exc())
            stream.flush()
        except (OSError, ValueError):  # stderr 被重定向或已关闭时不能反噬业务
            pass

    def debug(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._write("DEBUG", _render_text(message, args), False)

    def info(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._write("INFO", _render_text(message, args), False)

    def warning(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._write("WARN", _render_text(message, args), False)

    def error(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._write("ERROR", _render_text(message, args), True)

    def exception(self, message: object, *args: Any, **kwargs: Any) -> None:
        self._write("ERROR", _render_text(message, args), True)


def _emit(inner: Any, level: str, text: str, kwargs: dict[str, Any]) -> None:
    """把一条日志投递给宿主 logger，并消化 stdlib 风格的关键字参数。

    为什么必须消化而不能透传：``logger.warning(msg, exc_info=True)`` 是核心层
    的常见写法，宿主的 logger 未必接受这个关键字（历史上就是它抛 TypeError，
    把一次渲染失败变成 aiohttp 的裸 500）。这里统一收口：带堆栈的调用尽量按
    ``exc_info=`` 传下去，宿主不认就退回不带堆栈的调用，保证日志调用不会反噬业务。
    """
    method = getattr(inner, level, None)
    if not callable(method):
        method = getattr(inner, "error", None)
    if not callable(method):
        return

    exc_info = kwargs.get("exc_info", _MISSING)
    if exc_info is _MISSING:
        exc_info = kwargs.get("exception", _MISSING)
    wants_trace = (
        exc_info is not _MISSING and exc_info is not None and exc_info is not False
    )

    # exception() 自带当前堆栈，重传 exc_info 只会让宿主再拼一次。
    if wants_trace and level != "exception":
        try:
            method(text, exc_info=exc_info)
            return
        except TypeError:  # 宿主签名不认 exc_info：退化成纯文本，日志本身不能失败
            pass
    method(text)


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
        self._log("exception", message, args, kwargs)


_fallback = StderrLogger()


def _default_logger() -> PrefixedLogger:
    """没有宿主时的出口：stderr + 与插件层一致的 ``[t2i]`` 前缀。"""
    return PrefixedLogger(_fallback, PREFIX)


_logger: Any = _default_logger()


def set_logger(host_logger: Any, prefix: str = "") -> None:
    """把宿主 logger 注入核心层；插件模式在 ``initialize()`` 里调用一次。"""
    global _logger
    _logger = PrefixedLogger(host_logger, prefix) if prefix else host_logger


def reset_logger() -> None:
    """恢复内置出口（插件卸载、测试隔离时使用）。"""
    global _logger
    _logger = _default_logger()


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
