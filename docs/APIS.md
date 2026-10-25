# AstrBot 插件 API 速查（生命周期 / 命令事件 / 主动发消息 / 配置）

基准源码：`/vol1/@appdata/deepseek.harness/home/AstrBot`，v4.29.1。行号均为 AstrBot 源码行号。

---

## 1. 生命周期：`initialize()` / `terminate()`

### 1.1 插件如何被实例化

- `class Star(CommandParserMixin, PluginKVStoreMixin)`（`astrbot/core/star/base.py:18`），构造函数 `def __init__(self, context: Context, config: dict | None = None)`（`base.py:27`）；加载器会传入插件自己的 `AstrBotConfig`，并注入专用 logger `self.logger`（`base.py:33-52`）。
- **插件类只要继承 `Star` 就会被自动登记，不需要任何装饰器**：`Star.__init_subclass__` 在**类定义时**（import 阶段）就把子类写进 `star_map` / `star_registry`（`astrbot/core/star/base.py:53-64`，按 `cls.__module__` 建索引）。v3.5.19+ 起如此。
- `@register(...)` / `register_star(...)` **已废弃**：源码里明确标注 `[DEPRECATED] 该装饰器已废弃，将在未来版本中移除`（`astrbot/core/star/register/star.py:17`），调用时还会打运行时警告（同文件 `:42`）。`@register` 只是 `register_star` 的别名。
- 插件名、展示名、作者、版本、`astrbot_version` 等元数据来自插件根目录的 `metadata.yaml`（加载器解析，见 `astrbot/core/star/star_manager.py:556-563` 一带），**不是**来自装饰器参数。`metadata.yaml` 的 `name` 同时决定插件数据目录名（`data/plugin_data/<name>`）。

### 1.2 加载顺序与 `initialize()`

`PluginManager.load()` 中（`astrbot/core/star/star_manager.py`）：

1. 导入模块 → `@filter.command` 等装饰器在 **import 时**就注册到 `star_handlers_registry`（`astrbot/core/star/register/star_handler.py:70-71`）。
2. 构造 Star 实例、写入 `star_registry`/`star_map`、注入插件配置。
3. 全部注册完成后：`await metadata.star_cls.initialize()`（**`star_manager.py:1424`**，仅当类定义了 `initialize`；已被 `await`，插件不写 `async` 会直接报错）。
4. 触发 `OnPluginLoadedEvent` 钩子（`:1426-1437`）。

**要求与注意**

- `initialize()` **可以 await 长任务**：没有超时保护，它就是被 `await` 的。但它运行在插件加载流程里，**会阻塞加载**（AstrBot 启动时的插件加载、以及 WebUI 的插件 reload 请求都会一起等）。耗时初始化建议 `asyncio.create_task(...)` 后台跑或加超时，别在里面 sleep/等网络。
- **异常后果**：`initialize()` 抛出的任何异常都被外层 `except BaseException as e:` 捕获（`star_manager.py:1430`），日志打印 `----- Failed to load plugin {root_dir_name} -----`（`:1440`）并写入 `failed_plugin_dict`，随后 `_cleanup_plugin_state()` 清理注册状态。**插件等于加载失败**，命令与 Web API 都不生效，直到 reload/重启。
  → 所以 `main.py:110` 把 `_register_web_apis()` 包在 `try/except` 里、把可能失败的外部动作（清理孤儿进程、启动服务）放在 `initialize` 中是正确做法；但要注意 `server.start()` 若很慢，会拖慢加载。
- 判断“加载完成”：`OnPluginLoadedEvent` 钩子（`@filter.on_plugin_loaded`）。

### 1.3 卸载 / reload 与 `terminate()`

`_terminate_plugin()`（**`star_manager.py:1932-1968`**）：

- 插件未启用（`activated == False`）或没有 `star_cls` 时直接跳过（`:1943-1949`）。
- **优先 `__del__`**：如果插件类在自己的 `__dict__` 里定义了 `__del__`，则在线程池里执行 `__del__`（同步），并且 **`terminate()` 不会被调用**（`elif` 分支，`:1965-1966`）。想让 `async terminate()` 生效，**不要**定义 `__del__`。
- 否则 `await star_metadata.star_cls.terminate()`（`:1966`）。
- 之后触发 `OnPluginUnloadedEvent`（`:1970+`）。
- 调用方（reload：`star_manager.py:1044-1045`、`:1497-1498`；卸载：`:1917`、`:2110` 等）用 `try/except` 包住 `_terminate_plugin()`，异常只记 `logger.warning`（**不会**让卸载失败），但这意味着 terminate 里出错 = 资源泄漏，需自己保证清理。
- `reload(specified_plugin_name=None)`（`:1018`）会先 terminate 再重新 load；reload 期间有 `self._pm_lock` 互斥。

**结论**：`terminate()` 应当是快速、幂等、自己兜异常的清理函数（取消 task、关浏览器/子进程、关文件），不要指望调用方能帮你处理错误。

---

## 2. `@filter.command` 与事件 API

### 2.1 `filter.command` 是什么

- `astrbot/api/event/filter/__init__.py:12` 把 `register_command` 重导出为 `command` → 定义在 `astrbot/core/star/register/star_handler.py:75`。
- 签名：`register_command(command_name=None, sub_command=None, alias: set | None = None, **kwargs)`；`kwargs` 会转成 handler 注册参数（`:104-113`）。
- 指令解析、参数注入由 `CommandFilter`（`astrbot/core/star/filter/command.py:31`）完成：它用 `inspect.signature(..., eval_str=True)` 读取 handler 第 3 个往后的参数名与类型（`:62-80`），运行时以 **关键字参数** 形式传给 handler（`astrbot/core/pipeline/process_stage/method/star_request.py:74`：`call_handler(event, handler.handler, **params)`）。
  - 所以 handler 形如 `async def foo(self, event: AstrMessageEvent, text: str = "", count: int = 1)`，`text`/`count` 会被指令参数自动填充；类型注解用于解析（支持 `int/float/bool/str`，`GreedyStr` 吃剩余全部文本，见 `command.py:17-18,22-29`）。
- 裸指令（`@filter.command("t2i")`）；子指令用 `@filter.command_group` + `@group.command(...)`。

### 2.2 handler 的返回值约定

`call_handler()`（`astrbot/core/pipeline/context_utils.py:12-75`）同时支持 **async 生成器**和 **协程**：

- `async def handler(...)` 里 `yield event.plain_result("...")` → `event.set_result(ret)`，把结果交给下游发送（`context_utils.py:47-60`）；`yield None` 表示“继续后续阶段”。
- 直接 `return event.plain_result("...")` 也支持（`:68-73`）——**但 `return` 后不能再 `yield`**，二者只能选一种风格。
- 返回其它类型会被当作“无结果”透传。
- 结果类型：`MessageEventResult` 或 `CommandResult`（`astrbot/core/message/message_event_result.py`）。

常用结果构造器（`astrbot/core/platform/astr_message_event.py`）：

| API | 位置 | 说明 |
| --- | --- | --- |
| `event.plain_result(text)` | `:455` | 纯文本 |
| `event.image_result(url_or_path)` | `:459` | `http(s)` 开头走 `url_image`，否则当作本地路径 `file_image` |
| `event.make_result()` | `:440` | 链式构建：`.message()` / `.url_image()` / `.file_image()` |
| `event.chain_result([...])` | `:469` | 自定义消息链（组件列表） |
| `await event.send(MessageChain)` | `:534` | **立即发送**，绕过结果收集；注意 `unified_msg_origin`/`session`（`:157`） |

- `MessageChain` 从公开路径导入：`from astrbot.api.event import MessageChain`（`astrbot/api/event/__init__.py:1-15`，与 `AstrMessageEvent`/`MessageEventResult` 一起导出）。`astrbot.core.message.message_event_result.MessageChain` 是内部路径，能用但不保证兼容。

---

## 3. `context.send_message()`：主动发消息

`Context.send_message(session, message_chain)`（**`astrbot/core/star/context.py:655-680`**）：

```python
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Image   # 组件（或直接构造 MessageChain）

sent = await self.context.send_message(
    event.unified_msg_origin,              # str，或 MessageSesion 对象
    MessageChain([Plain("任务完成")]),
)
```

- `session`：`event.unified_msg_origin`（格式 `platform_name:message_type:session_id`，`astr_message_event.py:157`）或 `event.session`；传字符串时会 `MessageSesion.from_str()` 解析，非法字符串抛 `ValueError("不合法的 session 字符串: ...")`（`context.py:675-678`）。
- 返回 `bool`：**是否找到匹配的平台**（`True` 只代表投递成功，不代表对方已收到）。
- 已知限制：`qq_official`（QQ 官方 API）**不支持**该方法（docstring 明确写出）。
- 典型用途：定时/后台任务结束后主动推送；必须自己保存 `umo`（例如写进插件数据目录或内存映射），因为主动发送时没有 `event` 上下文。
- 若只是回复当前消息，用 `yield event.plain_result(...)` 即可，不需要 `send_message`。

---

## 4. 插件配置 `AstrBotConfig`

- 获取：`self.context.get_config(umo: str | None = None) -> AstrBotConfig`（`context.py:638-655`）；不传 `umo` 返回**默认全局配置** `self._config`，传 `umo` 返回该会话的配置。插件的 `config`（构造参数）就是插件自己的配置对象。
- 读：`AstrBotConfig` 是 `dict` 子类，直接 `core_config["t2i_endpoint"]`；注意可能不存在 → 用 `.get()` 或 `in` 判断（`main.py` 里的写法正确）。
- 写：

```python
core_config["t2i_endpoint"] = endpoint          # 只改内存
core_config.save_config()                        # 同步落盘（会阻塞事件循环）
await core_config.save_config_async()            # 推荐：asyncio.to_thread 落盘
```

- `save_config(replace_config: dict | None = None, *, indent: int = 2) -> None`（**`astrbot/core/config/astrbot_config.py:282`**）：先 `_prepare_config_snapshot()` 取快照与递增 revision，再 `_write_config_snapshot()` 写 JSON（原子替换 + revision 校验）。
- `async save_config_async(...) -> bool`（**`:294`**）：同一套快照逻辑但用 `asyncio.to_thread` 执行，返回“本次快照是否被提交”（更新的快照会取代旧快照）。**在 async handler（比如 Web API handler）里必须用这个**，否则同步写盘会卡住整个 AstrBot 事件循环。
- 语义要点：
  - 只写内存不调用 `save_config*`，重启即丢失；反之 `save_config` 会把 `replace_config` 先 merge 进来再整体落盘。
  - 配置改动**不一定立即生效**：很多组件在启动时读取配置（例如 AstrBot 主进程的 HTML 渲染策略），本插件 `main.py` 的提示（改 `t2i_endpoint` 需重启）符合实际行为。
  - 插件自身的配置 schema 由 `_conf_schema.json` 描述，WebUI 插件配置页据此渲染。

---

## 5. 与本插件（`astrbot_plugin_t2i_service`）的对应关系

- `main.py:110` `async def initialize()`：`_register_web_apis()` 用 `try/except` 包裹（正确，见 §1.2）；随后 `cleanup_orphans` + `server.start()` + 应用端点。
- `main.py` 未定义 `__del__`，`terminate()`（`main.py:137-155`）会被调用 ✔（若哪天加了 `__del__`，`terminate()` 就被跳过——见 §1.3）。它自己 try/except 包住各步清理，符合 §1.3 的建议。
- `main.py` 的 `web_*` handler 都是 `async def` 且返回 `json_response`，符合 §3（`astrbot/api/web.py`）与桥的拆包约定；`apply-endpoint` 里同步调用 `core_config.save_config()`，见 §4 建议改 `save_config_async()`。
- 命令侧（`/t2i`、`/t2i_state` 等）用 `yield event.plain_result(...)` / `event.image_result(...)`，并可用 `event.unified_msg_origin` 配合 `context.send_message` 做后台推送。
