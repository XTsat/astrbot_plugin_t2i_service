"""文转图服务面板「模板」栏的后端 Web API。

与 ``font_web.py`` 同一套插件页面桥：页面用 ``window.AstrBotPluginView`` 调用，
注册的路由带插件名前缀，页面里的 endpoint 不带。模板本身在
``t2i_plugin/templates.py`` 里管理，这里只把它包成三个接口：

* ``templates/list``     —— 模板清单 + 槽位状态；
* ``templates/preview``  —— 渲染（或直接返回缓存的）预览图；
* ``templates/preview_draft`` —— 渲染编辑器里还没保存的正文（不写盘、不进缓存）；
* ``templates/apply``    —— 把模板写进槽位并写 AstrBot 的 t2i 配置；
* ``templates/create``   —— 新建用户模板（空白 / 复制现有 / 粘贴 HTML）；
* ``templates/save``     —— 覆盖保存用户模板的内容；
* ``templates/read``     —— 取模板原文给页面编辑器预填；
* ``templates/delete``   —— 删除用户自建模板（内置模板删不了）；
* ``templates/sample_save`` / ``templates/sample_reset`` —— 改预览用的样例文本 / 恢复默认。

写 AstrBot 主配置的动作留在 ``main.py``：它才拿得到 ``Context``。
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from pathlib import Path

from astrbot.api import logger

from t2i_plugin.templates import TemplateGallery

try:
    from astrbot.api.web import error_response, json_response, request

    WEB_API_AVAILABLE = True
except Exception:  # noqa: BLE001
    # 老版本 AstrBot：没有统一的 Web API 门面。页面因此不可用，但插件要能加载。
    error_response = None  # type: ignore[assignment]
    json_response = None  # type: ignore[assignment]
    request = None  # type: ignore[assignment]
    WEB_API_AVAILABLE = False


# 草稿预览的正文上限：页面把整段 HTML 塞进请求体，正常模板只有几十 KB，
# 挡住误贴进来的超大内容，免得一次渲染把并发全占住
MAX_DRAFT_CHARS = 512 * 1024


class TemplateWebApi:
    """把模板清单 / 预览 / 应用暴露成页面可调用的接口。"""

    def __init__(
        self,
        plugin_name: str,
        gallery: TemplateGallery,
        *,
        preview: Callable[[str], Awaitable[tuple[bool, str]]],
        preview_draft: Callable[[str], Awaitable[tuple[bool, str]]],
        apply: Callable[[str], Awaitable[dict[str, object]]],
        state: Callable[[], Awaitable[dict[str, object]]],
        font_need: Callable[[str], dict[str, object]] | None = None,
    ) -> None:
        self.plugin_name = plugin_name
        self.gallery = gallery
        self.preview = preview
        self.preview_draft = preview_draft
        self.apply_template = apply
        self.state = state
        self.font_need = font_need
        self.registered: list[str] = []

    # ------------------------------------------------------------ 注册

    def register(self, context: object) -> list[str]:
        """注册全部路由；返回注册成功的路径，便于状态里展示。"""
        register = getattr(context, "register_web_api", None)
        if not WEB_API_AVAILABLE or not callable(register):
            logger.warning("[t2i] 当前 AstrBot 版本不支持插件页面 Web API，模板页面不可用")
            return []
        routes: list[tuple[str, Callable[[], Awaitable[object]], list[str], str]] = [
            (
                f"/{self.plugin_name}/templates/list",
                self.page_list,
                ["GET"],
                "模板清单",
            ),
            (
                f"/{self.plugin_name}/templates/preview",
                self.page_preview,
                ["POST"],
                "模板预览图",
            ),
            (
                f"/{self.plugin_name}/templates/preview_draft",
                self.page_preview_draft,
                ["POST"],
                "编辑器草稿预览图",
            ),
            (
                f"/{self.plugin_name}/templates/apply",
                self.page_apply,
                ["POST"],
                "应用模板",
            ),
            (
                f"/{self.plugin_name}/templates/create",
                self.page_create,
                ["POST"],
                "新建模板",
            ),
            (
                f"/{self.plugin_name}/templates/save",
                self.page_save,
                ["POST"],
                "保存模板",
            ),
            (
                f"/{self.plugin_name}/templates/read",
                self.page_read,
                ["POST"],
                "读取模板原文",
            ),
            (
                f"/{self.plugin_name}/templates/delete",
                self.page_delete,
                ["POST"],
                "删除模板",
            ),
            (
                f"/{self.plugin_name}/templates/sample_save",
                self.page_sample_save,
                ["POST"],
                "保存预览样例文本",
            ),
            (
                f"/{self.plugin_name}/templates/sample_reset",
                self.page_sample_reset,
                ["POST"],
                "恢复默认样例文本",
            ),
        ]
        for route, handler, methods, desc in routes:
            try:
                register(route, handler, methods, desc)
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响其它路由
                logger.warning(f"[t2i] 注册模板接口失败 {route}：{exc}")
                continue
            self.registered.append(route)
        logger.info(f"[t2i] 模板接口已注册 {len(self.registered)} 条")
        return list(self.registered)

    # ------------------------------------------------------------ 响应工具

    def _ok(self, payload: dict[str, object]) -> object:
        if json_response is None:  # pragma: no cover - 注册阶段已拦下
            return payload
        return json_response(payload)

    def _fail(self, message: str, status_code: int = 400) -> object:
        if error_response is None:  # pragma: no cover
            return {"status": "error", "message": message}
        return error_response(message, status_code=status_code)

    # ------------------------------------------------------------ 接口实现

    async def page_list(self) -> object:
        try:
            templates = await asyncio.to_thread(self.gallery.list_templates)
            if self.font_need is not None:
                for item in templates:
                    item["fonts"] = self.font_need(str(item.get("name") or ""))
            slot = await self.state()
        except Exception as exc:  # noqa: BLE001 - 页面不该因为磁盘异常白屏
            logger.warning(f"[t2i] 读取模板清单失败：{exc}")
            return self._fail(f"读取模板清单失败：{exc}", status_code=500)
        sample = await asyncio.to_thread(self.gallery.sample_payload)
        return self._ok(
            {
                "templates": templates,
                "slot": slot,
                "sample": sample,
                "page_api": True,
            }
        )

    async def page_preview(self) -> object:
        payload = await request.json(default={})
        name = str(payload.get("name") or "").strip()
        if not name:
            return self._fail("缺少模板名")
        refresh = bool(payload.get("refresh"))
        if not refresh:
            cached = await asyncio.to_thread(self.gallery.cached_preview, name)
            if cached is not None:
                data = await asyncio.to_thread(_read_b64, cached)
                if data:
                    return self._ok({"name": name, "image": _data_url(data), "cached": True})
        ok, detail = await self.preview(name)
        if not ok:
            return self._fail(detail, status_code=500)
        try:
            stored = await asyncio.to_thread(
                self.gallery.store_preview, name, Path(detail)
            )
        except OSError as exc:
            return self._fail(f"预览图写入失败：{exc}", status_code=500)
        data = await asyncio.to_thread(_read_b64, stored)
        if not data:
            return self._fail("预览图读取失败", status_code=500)
        return self._ok({"name": name, "image": _data_url(data), "cached": False})

    async def page_preview_draft(self) -> object:
        """渲染编辑器里尚未保存的正文：只出图，不写模板文件也不进预览缓存。

        编辑时反复看效果会调用很多次，缓存按模板内容哈希命名，草稿没有名字可用，
        存进去只会变成一堆没人回收的孤儿图。
        """
        payload = await request.json(default={})
        content = str(payload.get("content") or "")
        if not content.strip():
            return self._fail("模板内容不能为空")
        if len(content) > MAX_DRAFT_CHARS:
            return self._fail(
                f"模板内容太长（超过 {MAX_DRAFT_CHARS // 1024} KB），先在编辑器里精简一下"
            )
        ok, detail = await self.preview_draft(content)
        if not ok:
            return self._fail(detail, status_code=500)
        data = await asyncio.to_thread(_read_b64, Path(detail))
        if not data:
            return self._fail("预览图读取失败", status_code=500)
        return self._ok({"image": _data_url(data), "cached": False})

    async def page_apply(self) -> object:
        payload = await request.json(default={})
        name = str(payload.get("name") or "").strip()
        if not name:
            return self._fail("缺少模板名")
        try:
            result = await self.apply_template(name)
        except (ValueError, OSError, FileNotFoundError) as exc:
            return self._fail(str(exc))
        return self._ok(result)

    async def page_create(self) -> object:
        payload = await request.json(default={})
        name = str(payload.get("name") or "").strip()
        if not name:
            return self._fail("缺少模板名")
        try:
            item = await asyncio.to_thread(
                self.gallery.create,
                name,
                source=str(payload.get("source") or "").strip(),
                content=str(payload.get("content") or ""),
                title=str(payload.get("title") or ""),
            )
        except FileExistsError as exc:
            return self._fail(str(exc), status_code=409)
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            return self._fail(str(exc))
        logger.info(f"[t2i] 已新建模板 {name}")
        return self._ok({"template": item, "created": True})

    async def page_save(self) -> object:
        payload = await request.json(default={})
        name = str(payload.get("name") or "").strip()
        if not name:
            return self._fail("缺少模板名")
        title = payload.get("title")
        try:
            item = await asyncio.to_thread(
                self.gallery.save,
                name,
                str(payload.get("content") or ""),
                title=None if title is None else str(title),
            )
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            return self._fail(str(exc))
        logger.info(f"[t2i] 已保存模板 {name}")
        return self._ok({"template": item, "created": False})

    async def page_read(self) -> object:
        payload = await request.json(default={})
        name = str(payload.get("name") or "").strip()
        if not name:
            return self._fail("缺少模板名")
        try:
            content = await asyncio.to_thread(self.gallery.read, name)
        except FileNotFoundError as exc:
            return self._fail(str(exc), status_code=404)
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            return self._fail(str(exc))
        return self._ok({"name": name, "content": content})

    async def page_sample_save(self) -> object:
        payload = await request.json(default={})
        try:
            sample = await asyncio.to_thread(
                self.gallery.save_sample_text, str(payload.get("text") or "")
            )
        except (ValueError, OSError) as exc:
            return self._fail(str(exc))
        logger.info("[t2i] 已更新预览样例文本")
        return self._ok({"sample": sample})

    async def page_sample_reset(self) -> object:
        try:
            sample = await asyncio.to_thread(self.gallery.reset_sample_text)
        except OSError as exc:  # pragma: no cover - 删除失败极少见，仍要回给页面
            return self._fail(f"恢复默认失败：{exc}", status_code=500)
        logger.info("[t2i] 预览样例文本已恢复默认")
        return self._ok({"sample": sample})

    async def page_delete(self) -> object:
        payload = await request.json(default={})
        name = str(payload.get("name") or "").strip()
        if not name:
            return self._fail("缺少模板名")
        try:
            result = await asyncio.to_thread(self.gallery.delete, name)
        except FileNotFoundError as exc:
            return self._fail(str(exc), status_code=404)
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            return self._fail(str(exc))
        logger.info(f"[t2i] 已删除模板 {name}")
        return self._ok(result)


def _data_url(data: str) -> str:
    return f"data:image/jpeg;base64,{data}"


def _read_b64(path: Path) -> str:
    try:
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as exc:
        logger.warning(f"[t2i] 读取模板预览图失败：{exc}")
        return ""
