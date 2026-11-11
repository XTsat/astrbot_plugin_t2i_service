# 插件自带模板

把随插件一起发布的转图模板放在这个目录里（`*.html`）。面板「T2i 模板」栏会把它们列出来，来源标注为**插件**，与 AstrBot 自带的（标注「内置」）和用户自己建的（标注「自定义」）区分开。

约定：

- **文件名就是模板名**：AstrBot 配置里的模板标识、面板卡片、`data/t2i_templates/` 下的同名副本都用它，所以只能用字母、数字、下划线和短横线，需以字母或数字开头，最长 64 个字符。
- **同名的以用户副本为准**：用户在面板里编辑插件模板后，副本落在 AstrBot 的 `data/t2i_templates/` 下，优先级是「用户副本 > 插件自带 > AstrBot 内置」。
- **卡片信息来自模板自己**：`<title>` 作卡片标题，`<meta name="description">` 作卡片说明；用户也可以另设显示名覆盖标题。
- **资源放 `assets/` 下，模板里按 `assets/文件名` 引用**：渲染跑的是 `file://` 临时文件，相对路径指不到插件目录，所以插件在**渲染前**把 `assets/xxx` 换成 data URI（按文件 mtime 缓存）；面板编辑器里看到的始终是 `assets/xxx` 这样的引用，不会被 base64 灌满。换图只要替换 `assets/` 里的文件；用户自建模板也可以在自己模板旁边放一个 `assets/` 目录。
- **要跑的脚本也放进 `assets/`**：只有图片之外的资源同样会被内联，脚本还能按 `text/javascript` 声明——服务器上常常没有外网，把 `marked` 这类库指向 CDN 会静默失败，正文直接空白。两套日记模板的 Markdown 渲染器就是这么带的（`assets/markdown.js`）。
- **渲染变量**：`{{ text }}` 是消息正文，`{{ version }}` 是插件版本；参考 AstrBot 自带模板（`astrbot/core/utils/t2i/template/`）的写法。**除这两个变量外不要写别的**——AstrBot 不会传，会被渲染成空字符串。
- **改了要生效**：用户在面板点「应用」后，内容被写进本插件的槽位模板；插件更新模板内容后同样点一次「重新应用」就能换到新版本。

面板里删不掉这里发布的模板（属于插件资产），只能删用户目录里的同名副本。

## 当前发布的两套模板

| 文件 | 卡片标题 | 版式 | 预览图 |
|---|---|---|---|
| `atrifeed_diary.html` | 今日の手記 · ATRI | **旧版**手记页：日期卡、摘要卡、正文纸页 | [`docs/assets/plugin-template-atrifeed-diary.png`](../../docs/assets/plugin-template-atrifeed-diary.png) |
| `atrifeed_diary_legacy.html` | 今日の日記 · ATRI | **新版**日记本：绑带、日期角标、印章与别针 | [`docs/assets/plugin-template-atrifeed-diary-legacy.png`](../../docs/assets/plugin-template-atrifeed-diary-legacy.png) |

> 文件名沿用上游（`diary.html` / `diary_legacy.html`），但**上游那个 `diary_legacy.html` 才是新版版式**，别按文件名猜新旧。

面板上这两个模板的卡片和「应用」弹窗都会标出它们的许可证（`<meta name="license" content="AGPL-3.0">`，卡片上是那枚 AGPL-3.0 小标签）；卡片下方的出处行来自 `<meta name="origin">`，写成可点链接靠 `<meta name="origin-url" content="https://…">`（只放行 http/https）：

**许可（重要）**：这两个文件改编自 [astrbot_plugin_atrifeed](https://github.com/Heximiao/astrbot_plugin_atrifeed)（作者 Heximiao，**AGPL-3.0**）的 `template/diary/`，是它的衍生作品，**按 AGPL-3.0 使用与分发**，与本插件其余部分（Apache-2.0）的许可相互独立；文件头的来源声明不要删，`NOTICE` 里也记了这一条。

**改动原则：只换「变量从哪来」，样式照原样。** 两套模板的 `<style>` 与原文件逐行比对，只有 `font-family` 的值和那句 `font-size`（原本是模板变量，改成固定默认值、再由脚本按长度覆盖）不同；选择器、尺寸、配色、排版规则一律没动。**唯一的排版例外是两端对齐**：原模板对正文一律 `text-align: justify`，日记体纯中文好看，但消息里混进英文单词、JSON 或报错日志时，浏览器为了撑满行宽会把空格拉成大片空白，所以正文渲染完会逐块判定（`p` / `li` / 标题 / 引用 / 表格单元格 / 代码块），拉丁字母数字达到中日文一半的块改回左对齐。对齐方式写在模板里的 `var TEXT_ALIGN = "auto";` 这一行（`auto` 逐块判定，`left` / `center` / `right` / `justify` 强制一种），用户在面板「T2i 模板 → 编辑」里用下拉框改，保存时由面板改写这行标记——模板拿不到插件配置（AstrBot 真实渲染只传 `text` / `version`），所以只能落在 HTML 自己身上；改完在卡片上点「重新应用」才会用到出图里。具体改动：

- **正文**：`{{ content }}` → `{{ text }}`（同样转义），再由模板自带的 `assets/markdown.js` 在出图时渲染成 HTML——消息正文里的 `**粗体**`、标题、列表、引用、代码块、表格都会按 Markdown 出效果，渲染器离线可用、不依赖 CDN；渲染器没加载出来时退回纯文本，不会只剩一张空纸；
- **日期 / 星期 / 字数 / 字号**：原模板由插件预计算，这里由页面末尾的脚本按渲染时刻与正文长度现算（日期 `2026年11月2日`；星期新版用日文 `月曜日`、旧版用 `星期一`；字号分档与上游一致：新版 26/23/21/19/17、旧版 31/27/24/21/18）；
- **天气**：上游来自当天消息的情绪分析（`src/diary/service.py` 的 `_weather()`），五档 `晴 / 多云 / 阴 / 雨 / 多云转晴`，图标与日文显示在 `renderer.py` 的表里；AstrBot 拿不到这份数据，所以**按时间分桶**：每 `templates.refresh_minutes` 分钟（默认 15）换一档，桶内反复出图保持一致，跨桶自动换（面板里的预览渲染走 `preview` 变量，保持每次随机，方便逐档看效果）；同一张卡片里两处天气始终一致；标签写成「日文 + 小号罗马字」，其中多云用 `薄曇り / USUGUMORI`、阴用 `曇り / KUMORI` 区分；
- **心情卡照原样还原**：配图是上游写死的两张（好心情 `pic/pictorial/my_atri1/emoji1.jpg`、坏心情 `pic/emoji/tired/atri_tired1.jpg`，`阴 / 雨` 判为坏心情，并给卡加 `mood-bad` 阴影）；右侧文案取上游 `renderer.py` 的 `ATRI_ONE_LINERS`（6 句，如「今日も高性能です！」「海风正好，适合记录今天。」），每次随机一句；
- **左右立绘也保留**：上游正文两侧那两张半透明立绘（`pic/lihui/shop/atri6.png` 与 `pic/lihui/atri2.png`），位置、尺寸、透明度、渐变遮罩仍走原模板的 `.entry-art` / `.entry-illustration` 规则；
- **图片怎么放**：上面四张图都收在 `assets/` 下（`mood-good.jpg` / `mood-bad.jpg` / `art-left.webp` / `art-right.webp`，已裁切压缩），模板里按 `assets/文件名` 引用，插件读模板时内联成 data URI——渲染跑的是 `file://` 临时文件，相对路径指不到插件目录。换图只要替换文件；
- **页脚**：右下角只写一行 `astrbot_plugin_t2i_service`（渲染者），不带链接、不写「由 … 渲染」；版式出处只在面板卡片的「来源」行交代。面板卡片只在「模板变量」下面写一行「版式源自 Heximiao / astrbot_plugin_atrifeed」，许可与渲染者不在卡片上重复（许可在文件头与 `NOTICE`，渲染署名在出图页脚）。

### 字体

原模板用的是 Windows / macOS 自带字体：`Yu Mincho`（日文明朝）、`Yu Gothic`（日文黑体）、`MS Mincho`、`楷体 STKaiti / KaiTi`、`FangSong`、`Microsoft YaHei`。服务器上通常一个都没有，所以每处 `font-family` 都写成三级链：

1. **原版字体名**（真装了就用它，字形最接近原作）；
2. **插件面板可下载的替代字体**：`Noto Serif JP`（替 Yu Mincho）、`Noto Sans JP`（替 Yu Gothic）、`霞鹜文楷 LXGW WenKai`（替楷体）；
3. **系统自带 CJK 字体**（`Noto Serif/Sans CJK SC` 等）——没下载就落到这一级，字形会变，但不会缺字或豆腐块。

应用模板时的提示会按上面的顺序说明：先说这套模板原本用的是 `Yu Mincho` / `楷体` / `仿宋` 这些 Windows / macOS 自带字体（服务器上多数没有），再说本机也没装可替代的开放字体；服务器上已经有原版字体时不会再弹。

下载入口在「文转图服务面板 → 对接项目 → T2i 模板 → 日记卡（本插件模板）」，一次装齐 `Noto Serif JP`（13.6 MB）/ `Noto Sans JP`（9.6 MB）/ `霞鹜文楷 Regular+Medium`（49.3 MB）；只想装其中一个家族，去「概览 → 单个字体家族」点它自己那颗按钮。

- **霞鹜文楷的等宽版（Mono）单独一件**（24.8 MB）：它只影响代码块，日记模板用不上，所以没有并进日记卡这套；要装可以在家族清单里单独点。
- **原版字体不提供下载**：`Yu Mincho`、`Yu Gothic`、`楷体`、`FangSong`、`Microsoft YaHei` 都是微软/苹果的商业字体，随包分发有版权问题。三种变通：
  1. 服务器上本来就装了 → 模板字体栈里它们排第一，直接用上；
  2. 手上有授权字体文件 → 在「概览」上传，效果同上；
  3. **什么都不用做也有近似字形**：插件写的 fontconfig 里给这些商业字名加了兜底规则（`Yu Mincho → Noto Serif JP`、`Yu Gothic → Noto Sans JP`、`KaiTi/楷体 → LXGW WenKai`、`Microsoft YaHei → Noto Sans SC`），只要装了对应的开放字体，**任何**模板里写这些原始字名都能拿到相近的字形——不只是本插件这两套。
- 字体功能整体可以在配置里关掉（`fonts.enabled`），关掉后就是纯降级，应用模板时也不会再弹提示。

直接把原文件拷进 `data/t2i_templates/` 是渲不出东西的：那些变量 AstrBot 不传，卡片只会剩一个空壳。

> 预览小提示：面板里那块「预览用样例文本」是 Markdown，两套日记模板会把它按 Markdown 渲染出来；换成别的模板（比如 AstrBot 自带的）时，卡片预览显示成什么样取决于那套模板自己怎么处理正文。
