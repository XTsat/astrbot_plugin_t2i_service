"""核心层日志出口的回归测试。

历史事故：``_CurrentLogger.warning(msg, exc_info=True)`` 只接受位置参数，抛
``TypeError``，把一次渲染失败变成了 aiohttp 的裸 500，整条失败路径一行日志都没有。
这些用例锁住「任何 stdlib 风格的日志调用都不能反噬业务」。
"""

from __future__ import annotations

import logging

from t2i_core import logger as core_logger


class RecordingLogger:
    """记录收到的调用，模拟宿主注入的 logger（任何 logger 都是这几个方法）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def debug(self, message: str, *args, **kwargs) -> None:
        self.calls.append(("debug", message, kwargs))

    def info(self, message: str, *args, **kwargs) -> None:
        self.calls.append(("info", message, kwargs))

    def warning(self, message: str, *args, **kwargs) -> None:
        self.calls.append(("warning", message, kwargs))

    def error(self, message: str, *args, **kwargs) -> None:
        self.calls.append(("error", message, kwargs))

    def exception(self, message: str, *args, **kwargs) -> None:
        self.calls.append(("exception", message, kwargs))


def setup_function() -> None:
    core_logger.reset_logger()


def teardown_function() -> None:
    core_logger.reset_logger()


def test_exc_info_keyword_does_not_raise() -> None:
    logger = core_logger.get_logger()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        # 事故现场就是这一行：关键字参数曾让代理层抛 TypeError
        logger.warning("渲染失败", exc_info=True)


def test_prefixed_logger_accepts_stdio_style_arguments() -> None:
    inner = RecordingLogger()
    logger = core_logger.PrefixedLogger(inner, "[t2i] ")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logger.error("渲染失败", exc_info=True)

    level, message, kwargs = inner.calls[-1]
    assert level == "error"
    assert message.startswith("[t2i] 渲染失败")
    assert kwargs["exc_info"] is True


def test_message_and_extra_positional_args_are_joined() -> None:
    inner = RecordingLogger()
    core_logger.set_logger(inner)
    core_logger.get_logger().info("渲染 %s 完成", "a.html")

    _, message, _ = inner.calls[-1]
    assert message == "渲染 %s 完成 a.html"


def test_set_logger_routes_core_records_to_host() -> None:
    inner = RecordingLogger()
    core_logger.set_logger(inner)
    core_logger.get_logger().warning("宿主接管")

    assert inner.calls == [("warning", "宿主接管", {})]


def test_stdlib_logger_target_gets_traceback() -> None:
    """宿主是 stdlib Logger 时，exc_info 必须真的传下去而不是被丢掉。"""
    stdlib_logger = logging.getLogger("t2i-test-host")
    stdlib_logger.setLevel(logging.DEBUG)

    class Capture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    handler = Capture()
    stdlib_logger.addHandler(handler)
    core_logger.set_logger(stdlib_logger)
    try:
        raise ValueError("打不开页面")
    except ValueError:
        core_logger.get_logger().warning("渲染失败", exc_info=True)
    finally:
        stdlib_logger.removeHandler(handler)

    assert handler.records
    assert handler.records[-1].exc_info is not None


def test_default_outlet_writes_to_stderr(capsys) -> None:
    """没人注入时（独立服务形态）核心层日志写到 stderr，而不是被静默丢掉。"""
    core_logger.get_logger().warning("独立服务形态的兜底出口")

    captured = capsys.readouterr()
    assert "[t2i] 独立服务形态的兜底出口" in captured.err


def test_host_logger_without_exc_info_support_still_logs() -> None:
    """宿主方法不认 exc_info 关键字时必须退回纯文本，日志调用不能反噬业务。"""

    class StrictLogger:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def warning(self, message: str) -> None:
            self.messages.append(message)

    inner = StrictLogger()
    core_logger.set_logger(inner)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        core_logger.get_logger().warning("渲染失败", exc_info=True)

    assert inner.messages == ["渲染失败"]
