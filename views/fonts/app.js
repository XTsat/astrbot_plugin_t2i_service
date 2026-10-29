// 渲染字体页：一个页面分栏切换——「概览」（统计 / 任务 / 上传 / 链接下载 / 预览）
// 与对接栏位（对接项目的模板整套 + 单家族清单）。栏位按 DOM 里的 tab/panel 配对
// 现读，加栏位只改 index.html；各栏共用同一次清单请求，切换只做显隐，栏位状态记在
// 地址 hash 里（#upstream 可直接打开第二栏）。
//
// 主题与语言都由 AstrBot 注入的桥决定：桥把 data-theme 写到 <html> 上（本页只用
// CSS 变量跟随，不自己判断深浅色），文案统一走 t() 取插件 i18n 的 pages.fonts.*。

const $ = (id) => document.getElementById(id);

const els = {
  boot: $("boot"),
  app: $("app"),
  chips: $("chips"),
  loadError: $("load-error"),
  loadErrorText: $("load-error-text"),
  btnRetry: $("btn-retry"),
  stats: $("stats"),
  warnDisabled: $("warn-disabled"),
  warnApi: $("warn-api"),
  urlForm: $("url-form"),
  urlInput: $("url-input"),
  nameInput: $("name-input"),
  btnUpload: $("btn-upload"),
  fileInput: $("file-input"),
  installed: $("installed"),
  installedCount: $("installed-count"),
  specimenPicker: $("specimen-picker"),
  specimenScale: $("specimen-scale"),
  specimenCaption: $("specimen-caption"),
  specimenBox: $("specimen-box"),
  specimenImg: $("specimen-img"),
  templates: $("templates"),
  families: $("families"),
  onlyMissing: $("only-missing"),
  toast: $("toast"),
  btnRefresh: $("btn-refresh"),
  btnSpecimen: $("btn-specimen"),
  btnAll: $("btn-all"),
  upstreamTabs: $("upstream-tabs"),
  projectPanel: $("panel-project"),
};

// 栏位不写死：从 DOM 里按 tab/panel 配对现读，加栏位只动 index.html
const tabs = [];

const state = {
  data: null,
  job: null,
  busy: false,
  pollTimer: 0,
  tab: "",
  lastError: "",
  // 预览自检里手动点选的家族名，空集合表示「全部已装字体」
  specimenPicked: new Set(),
  // 本次下载任务覆盖的家族 key 与模板 key：进度要显示在点下载的那张卡里
  jobKeys: new Set(),
  jobTemplate: "",
  jobSig: "",
};

// ---------------------------------------------------------------- 桥与 i18n

let sdk = null;

/** 取 AstrBot 注入的桥；注入可能晚于本模块解析，所以每次读 window 兜底。 */
function bridge() {
  if (window.AstrBotPluginView) sdk = window.AstrBotPluginView;
  return sdk;
}

/** 等桥注入并完成 ready()；拿不到桥时返回 null。 */
async function waitForBridge() {
  for (let attempt = 0; attempt < 40 && !bridge(); attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const api = bridge();
  if (!api) return null;
  try {
    await api.ready();
  } catch (error) {
    // ready() 失败通常只是父窗口上下文还没到，接口调用仍然可用
    console.warn("plugin view bridge ready failed", error);
  }
  return api;
}

let context = { locale: "zh-CN", isDark: false };

/**
 * 取页面文案。key 相对 pages.fonts；桥按插件 i18n 的绝对路径查表，缺项返回 fallback。
 * vars 用于替换文案里的 {name} 占位。
 */
function t(key, fallback = "", vars = {}) {
  const api = bridge();
  let text = fallback;
  if (api && typeof api.t === "function") {
    const value = api.t(`pages.fonts.${key}`, fallback);
    if (typeof value === "string" && value) text = value;
  }
  return String(text).replace(/\{(\w+)\}/g, (match, name) =>
    name in vars ? String(vars[name]) : match,
  );
}

/** 把带 data-i18n* 标记的静态节点按当前语言重写一遍。 */
function applyI18n() {
  for (const el of document.querySelectorAll("[data-i18n]")) {
    el.textContent = t(el.dataset.i18n, el.textContent.trim());
  }
  for (const el of document.querySelectorAll("[data-i18n-html]")) {
    el.innerHTML = t(el.dataset.i18nHtml, el.innerHTML.trim());
  }
  for (const el of document.querySelectorAll("[data-i18n-placeholder]")) {
    el.placeholder = t(el.dataset.i18nPlaceholder, el.placeholder);
  }
  for (const el of document.querySelectorAll("[data-i18n-title]")) {
    el.title = t(el.dataset.i18nTitle, el.title);
  }
  for (const el of document.querySelectorAll("[data-i18n-alt]")) {
    el.alt = t(el.dataset.i18nAlt, el.alt);
  }
  for (const el of document.querySelectorAll("[data-i18n-aria-label]")) {
    el.setAttribute(
      "aria-label",
      t(el.dataset.i18nAriaLabel, el.getAttribute("aria-label") || ""),
    );
  }
  document.title = t("title", "渲染字体");
}

/** 语言变了要重写静态文案，动态区块（统计、清单）跟着重渲染一次。 */
function syncContext(next) {
  if (!next || typeof next !== "object") return;
  const localeChanged =
    typeof next.locale === "string" && next.locale !== context.locale;
  context = { ...context, ...next };
  if (context.locale) document.documentElement.lang = context.locale;
  if (localeChanged) {
    applyI18n();
    if (state.data) renderAll();
  }
}

/** 没有桥（直接打开本页调试）时没有 data-theme，按系统偏好兜底。 */
function applyLocalThemeFallback() {
  if (document.documentElement.dataset.theme) return;
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

// ---------------------------------------------------------------- 基础工具

let toastTimer = 0;

function toast(message, kind = "") {
  els.toast.textContent = message;
  els.toast.className = `toast ${kind}`;
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    els.toast.hidden = true;
  }, kind === "err" ? 6000 : 3200);
}

let armedKey = "";
let armedButton = null;
let armedTimer = 0;

/**
 * 插件页跑在 sandbox 属性没有 allow-modals 的 iframe 里，window.confirm() 会被浏览器
 * 直接忽略并返回 false，删除与批量下载因此永远走不下去；这里改成两步点按：第一次点
 * 进入待确认（按钮变红 + 提示），4 秒内再点一次才执行。
 */
function armConfirm(button, key, hint) {
  if (armedButton === button && armedKey === key) {
    clearTimeout(armedTimer);
    armedKey = "";
    armedTimer = 0;
    armedButton.classList.remove("armed");
    armedButton = null;
    return true;
  }
  if (armedButton) armedButton.classList.remove("armed");
  clearTimeout(armedTimer);
  armedKey = key;
  armedButton = button;
  button.classList.add("armed");
  toast(hint);
  armedTimer = setTimeout(() => {
    if (armedButton) armedButton.classList.remove("armed");
    armedButton = null;
    armedKey = "";
    armedTimer = 0;
  }, 4000);
  return false;
}

function escapeHtml(text) {
  return String(text ?? "").replace(
    /[&<>"']/g,
    (ch) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[ch],
  );
}

function errorText(error) {
  if (error && typeof error === "object") {
    return error.message || error.error || "";
  }
  return typeof error === "string" ? error : "";
}

function fail(error) {
  state.lastError =
    errorText(error) || t("error.generic", "请求失败，请查看 AstrBot 日志");
  toast(state.lastError, "err");
  return null;
}

async function callBridge(method, endpoint, payload) {
  const api = bridge();
  if (!api || typeof api.apiGet !== "function") {
    state.lastError = t(
      "error.noBridge",
      "没有检测到 AstrBot 插件页面桥，请从 WebUI 的插件页面打开本页。",
    );
    return null;
  }
  try {
    const send = method === "get" ? api.apiGet : api.apiPost;
    return await send.call(api, endpoint, payload);
  } catch (error) {
    return fail(error);
  }
}

async function apiGet(endpoint, params) {
  return callBridge("get", endpoint, params || {});
}

async function apiPost(endpoint, body) {
  return callBridge("post", endpoint, body || {});
}

// ---------------------------------------------------------------- 栏位切换

function collectTabs() {
  tabs.length = 0;
  const container = $("page-tabs");
  if (!container) return;
  for (const tab of container.querySelectorAll('[role="tab"][aria-controls]')) {
    const panel = $(tab.getAttribute("aria-controls"));
    if (!panel) continue;
    // 栏位名取 tab 的 id 去掉 tab- 前缀，正好是 hash 与 data-goto 里写的值
    const id = (tab.id || panel.id).replace(/^(tab|panel)-/, "");
    tabs.push({ id, tab, panel });
  }
  state.tab = tabs.length ? tabs[0].id : "";
}

function activateTab(name, { focus = false, syncHash = true } = {}) {
  if (!tabs.length) return;
  const target = tabs.find((entry) => entry.id === name) || tabs[0];
  state.tab = target.id;
  for (const entry of tabs) {
    const active = entry.tab === target.tab;
    entry.tab.setAttribute("aria-selected", active ? "true" : "false");
    entry.tab.tabIndex = active ? 0 : -1;
    entry.panel.hidden = !active;
  }
  if (focus) target.tab.focus();
  if (syncHash && window.location.hash !== `#${target.id}`) {
    // replaceState 而不是改 hash：切栏不该往浏览器历史里塞一堆条目
    window.history.replaceState(null, "", `#${target.id}`);
  }
}

function bindTabs() {
  for (const [index, entry] of tabs.entries()) {
    entry.tab.addEventListener("click", () => activateTab(entry.id));
    entry.tab.addEventListener("keydown", (event) => {
      let next = index;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else return;
      event.preventDefault();
      activateTab(tabs[next].id, { focus: true });
    });
  }
  for (const button of document.querySelectorAll("button[data-goto]")) {
    button.addEventListener("click", () => activateTab(button.dataset.goto));
  }
  window.addEventListener("hashchange", () => {
    activateTab(window.location.hash.replace("#", ""));
  });
}

// ---------------------------------------------------------------- 数据加载

function setRefreshBusy(busy) {
  els.btnRefresh.disabled = busy;
  els.btnRefresh.classList.toggle("loading", busy);
  const label = els.btnRefresh.querySelector("[data-i18n]");
  if (label) {
    label.textContent = busy
      ? t("action.refreshing", "刷新中…")
      : t("action.refresh", "重新加载字体");
  }
}

function showLoadError(message) {
  els.loadErrorText.textContent =
    message || t("error.generic", "请求失败，请查看 AstrBot 日志");
  els.loadError.hidden = false;
}

async function load() {
  setRefreshBusy(true);
  try {
    const data = await apiGet("fonts/list");
    if (!data) {
      // 请求失败走错误条；接口本身不存在（旧 AstrBot）走版本横幅
      if (state.lastError) showLoadError(state.lastError);
      else els.warnApi.hidden = false;
      return;
    }
    state.lastError = "";
    els.loadError.hidden = true;
    state.data = data;
    state.job = data.status && data.status.job ? data.status.job : state.job;
    els.boot.hidden = true;
    els.app.hidden = false;
    renderAll();
  } finally {
    setRefreshBusy(false);
  }
}

/** 对接项目的二级栏位：一个上游项目一格，标签直接用项目名，避免顶层栏名随项目变长。 */
function renderProjectTabs(upstream) {
  const container = els.upstreamTabs;
  if (!container) return;
  const project = upstream && upstream.project ? upstream.project : "";
  if (!project) {
    // 没有上游项目数据时整块收起，不显示一个点不动的空栏位
    container.innerHTML = "";
    if (els.projectPanel) els.projectPanel.hidden = true;
    return;
  }
  container.innerHTML = `<button
    class="subtab"
    id="subtab-project"
    type="button"
    role="tab"
    aria-controls="panel-project"
    aria-selected="true"
    tabindex="0"
  >${escapeHtml(shortProjectName(project) || project)}</button>`;
  if (els.projectPanel) els.projectPanel.hidden = false;
}

function renderAll() {
  if (!state.data) return;
  const { status, enabled } = state.data;
  els.warnDisabled.hidden = enabled !== false;
  els.warnApi.hidden = true;

  const upstream = state.data.catalog.upstream;
  renderProjectTabs(upstream);
  const link = document.querySelector("[data-upstream-link]");
  if (link && upstream) {
    link.href = upstream.doc || "#";
    link.textContent = upstream.project || t("upstream.linkFallback", "对接项目");
  }

  renderChips(status);
  renderStats(status);
  renderInstalled(state.data.installed);
  renderSpecimenPicker();
  renderTemplates(state.data.catalog.templates);
  renderFamilies(state.data.catalog.families, status);
  renderJob(state.job);
}

/** 顶部状态 chip：字体是否已生效、进行中的任务。 */
function renderChips(status) {
  const enabled = state.data.enabled !== false;
  // 开关与接管合成一个 chip：用户关心的是「自定义字体此刻有没有生效」
  const configHint = t("chip.configHint", "{path} · FONTCONFIG_FILE={env}", {
    path: status.config_path || "-",
    env: status.env_value || t("chip.configUnset", "（未设置）"),
  });
  const stateChip = !enabled
    ? { cls: "warn", text: t("chip.state.off", "字体功能已关闭") }
    : status.env_active
      ? { cls: "ok", text: t("chip.state.on", "字体已生效"), title: configHint }
      : {
          cls: "warn",
          text: t("chip.state.pending", "字体未生效，下次渲染自动生效"),
          title: configHint,
        };
  const items = [stateChip];
  if (state.job && state.job.status === "running") {
    items.push({
      cls: "warn",
      text: t("chip.job", "任务 {done}/{total}", {
        done: state.job.done,
        total: state.job.total,
      }),
    });
  }
  els.chips.innerHTML = items
    .map(
      (item) =>
        `<span class="chip ${item.cls}"${item.title ? ` title="${escapeHtml(item.title)}"` : ""}>${escapeHtml(item.text)}</span>`,
    )
    .join("");
}

function renderStats(status) {
  const items = [
    {
      k: t("stat.files", "字体文件"),
      v: t("value.files", "{count} 个 · {size}", {
        count: status.files,
        size: status.total_text,
      }),
    },
    {
      k: t("stat.fccache", "fc-cache"),
      v: status.fc_cache ? t("value.available", "可用") : t("value.unavailable", "不可用"),
      cls: status.fc_cache ? "ok" : "warn",
    },
    { k: t("stat.limit", "单个文件上限"), v: `${status.upload_limit_mb} MB` },
    { k: t("stat.dir", "字体目录"), v: status.dir },
  ];
  els.stats.innerHTML = items
    .map(
      (item) => `<div class="stat">
        <div class="k">${escapeHtml(item.k)}</div>
        <div class="v ${item.cls || ""}">${escapeHtml(item.v)}</div>
      </div>`,
    )
    .join("");
}

/** 已装家族的选择条：点选后只对比这几个，没选就是全部。 */
function renderSpecimenPicker() {
  const names = [];
  for (const item of state.data.installed) {
    for (const name of item.families || []) {
      const text = String(name).trim();
      if (text && !names.includes(text)) names.push(text);
    }
  }
  names.sort((a, b) => a.localeCompare(b));
  // 字体被删掉后，残留的选中项要一起清掉
  for (const name of [...state.specimenPicked]) {
    if (!names.includes(name)) state.specimenPicked.delete(name);
  }
  els.specimenPicker.hidden = names.length === 0;
  els.specimenPicker.innerHTML = names
    .map((name) => {
      const on = state.specimenPicked.has(name) ? "true" : "false";
      return `<button type="button" class="pick" data-pick="${escapeHtml(name)}" aria-pressed="${on}">${escapeHtml(name)}</button>`;
    })
    .join("");
}

function renderInstalled(installed) {
  els.installedCount.textContent = installed.length
    ? t("manage.installed.count", "{count} 个文件 · {size}", {
        count: installed.length,
        size: state.data.status.total_text,
      })
    : "";
  if (!installed.length) {
    els.installed.innerHTML = `<div class="empty">${escapeHtml(
      t("manage.installed.empty", "字体目录还是空的，先上传或按链接下载一个字体吧。"),
    )}</div>`;
    return;
  }
  const unknown = t("manage.installed.unknownFamily", "未识别家族");
  const groups = new Map();
  for (const item of installed) {
    const names = item.families && item.families.length ? item.families : [unknown];
    const key = names[0];
    if (!groups.has(key)) groups.set(key, { names, items: [] });
    groups.get(key).items.push(item);
  }
  els.installed.innerHTML = [...groups.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([name, group]) => {
      const total = group.items.reduce((sum, item) => sum + (Number(item.size) || 0), 0);
      // 真实字体动辄十来个别名（含语言变体），全列出来会把卡片撑爆，只显示前两个
      const aliases = group.names.slice(1).filter((alias) => alias !== name);
      const shownAliases = aliases.slice(0, 2);
      const files = group.items
        .map(
          (item) => `<div class="file-line">
          <span class="fname" title="${escapeHtml(item.filename)}">${escapeHtml(item.filename)}</span>
          <span class="fmeta">${escapeHtml(item.size_text)}</span>
          <button class="danger" data-path="${escapeHtml(item.path)}">${escapeHtml(
            t("manage.installed.delete", "删除"),
          )}</button>
        </div>`,
        )
        .join("");
      return `<div class="tile">
        <div class="name">${escapeHtml(name)}<span class="pill">${escapeHtml(
          t("manage.installed.fileCount", "{count} 个文件", { count: group.items.length }),
        )}</span></div>
        <div class="meta">${escapeHtml(
          t("manage.installed.sizeTotal", "共 {size}", { size: humanSize(total) }),
        )}</div>
        ${
          aliases.length
            ? `<div class="meta">${escapeHtml(
                aliases.length > shownAliases.length
                  ? t("manage.installed.aliasesMore", "另有名称 {list} 等 {count} 个", {
                      list: shownAliases.join(t("list.separator", "、")),
                      count: aliases.length,
                    })
                  : t("manage.installed.aliases", "另有名称 {list}", {
                      list: shownAliases.join(t("list.separator", "、")),
                    }),
              )}</div>`
            : ""
        }
        <div class="files">${files}</div>
      </div>`;
    })
    .join("");
}

// 与后端 human_mb 保持一致：小于 1 MB 用 KB，避免小字体全变成 0.0 MB
function humanSize(bytes) {
  if (!bytes) return "0 KB";
  if (bytes < 1024 * 1024) {
    const kb = bytes / 1024;
    return kb < 10 ? `${kb.toFixed(1)} KB` : `${kb.toFixed(0)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 上游项目名的括号部分就是好认的短名：`astrbot_plugin_x（群聊日常分析）` → `群聊日常分析`。 */
function shortProjectName(project) {
  if (!project) return "";
  const text = String(project);
  const match = text.match(/[（(]([^）)]+)[）)]/);
  // 没有括号注释时退回插件名本身，去掉 astrbot_plugin_ 前缀至少还能认出来。
  return (match ? match[1] : text.replace(/^astrbot_plugin_/, "").replace(/_/g, " ")).trim();
}

function familyName(key) {
  const family = state.data.catalog.families.find((item) => item.key === key);
  return family ? family.name : key;
}

/** 家族 key 对应的已安装文件路径：后端只支持按文件删除，批量删除要自己挑出文件。 */
function installedPathsFor(keys) {
  const families = state.data.catalog.families;
  const wantedKeys = new Set();
  const wantedFiles = new Set();
  const wantedNames = new Set();
  for (const key of keys) {
    wantedKeys.add(String(key));
    const family = families.find((item) => item.key === key);
    if (!family) continue;
    // 下载时按家族建子目录（目录名就是 key），根目录里的则靠文件名/家族名兜底
    for (const file of family.files || []) wantedFiles.add(String(file));
    // 清单里的名字带括号注释（「LXGW WenKai（霞鹜文楷）」），字体文件自称的名字没有
    const bare = String(family.name || "").split(/[（(]/)[0].trim();
    if (bare) wantedNames.add(bare);
  }
  return (state.data.installed || [])
    .filter(
      (item) =>
        wantedKeys.has(String(item.group || "")) ||
        wantedFiles.has(String(item.filename || "")) ||
        (item.families || []).some((name) => wantedNames.has(String(name).trim())),
    )
    .map((item) => item.path);
}

/** 这些家族里仍被别的「装齐」模板需要的那些：删掉会让对方缺字体，跳过不删。 */
function familiesInUseElsewhere(keys, skipTemplateKey) {
  const templates = (state.data.catalog.templates || []).filter(
    (tpl) =>
      tpl.key !== skipTemplateKey && tpl.installed && !(tpl.missing || []).length,
  );
  const used = new Map();
  for (const key of keys) {
    const owners = templates
      .filter((tpl) => (tpl.families || []).includes(key))
      .map((tpl) => tpl.name_en || tpl.name || tpl.key);
    if (owners.length) used.set(key, owners);
  }
  return used;
}

/** 被牵连的其它模板名（去重），用在「删掉后它们会缺字体」的提示里。 */
function describeOwners(inUse) {
  const names = [];
  for (const owners of inUse.values()) {
    for (const owner of owners) {
      if (!names.includes(owner)) names.push(owner);
    }
  }
  return names.join(t("list.separator", "、"));
}

// 模块级删除和家族级删除的确认文案不同：后者已经在家族清单里，不需要再指路
const SHARED_HINTS = {
  "toast.armDeleteShared":
    "再点一次删除「{name}」的 {count} 个字体文件；{affected} 还在用，删掉后它们会缺字体。只想删其中某个字体的话，到下面「单个字体家族」里手动删掉 {shared}",
  "toast.armDeleteFamilyShared":
    "再点一次删除「{name}」的 {count} 个字体文件；{affected} 还在用，删掉后它们会缺字体",
};

/**
 * 删除某个家族（或某个模板涉及的家族）的已装文件。
 * 共用只提醒不拦：点名要删就照删，确认提示里说明会让哪些模板缺字体；
 * 模板级删除还会指路到下面的家族清单，免得为了一个字体删掉整套。
 */
async function removeFamilies(
  keys,
  label,
  button,
  inUse = new Map(),
  sharedHintKey = "toast.armDeleteShared",
) {
  const paths = installedPathsFor(keys);
  if (!paths.length) {
    toast(t("toast.nothingDelete", "这个家族还没有安装字体文件，无需删除"), "warn");
    return;
  }
  const affected = describeOwners(inUse);
  const shared = keys.filter((key) => inUse.has(key)).map(familyName).join(t("list.separator", "、"));
  const armed = armConfirm(
    button,
    `family:${keys.join(",")}`,
    affected
      ? t(sharedHintKey, SHARED_HINTS[sharedHintKey] || "", {
          name: label,
          count: paths.length,
          affected,
          shared,
        })
      : t("toast.armDeleteMany", "再点一次删除「{name}」的 {count} 个字体文件", {
          name: label,
          count: paths.length,
        }),
  );
  if (!armed) return;
  setBusy(true);
  let removed = 0;
  for (const path of paths) {
    const data = await apiPost("fonts/delete", { path });
    if (data) removed += 1;
  }
  setBusy(false);
  if (removed) {
    toast(t("toast.deletedMany", "已删除 {count} 个字体文件", { count: removed }), "ok");
  }
  await load();
}

// 对接项目的模板标识本身是英文（它自己的配置里也填这些名字），标题以英文为准，
// 中文界面再补一个译名，避免用户在两边的叫法对不上
function templateTitle(tpl) {
  const title = escapeHtml(tpl.name_en || tpl.name || tpl.key);
  const isZh = String(context.locale || "").toLowerCase().startsWith("zh");
  if (!isZh || !tpl.name || tpl.name === (tpl.name_en || "")) return title;
  return `${title}<span class="sub">${escapeHtml(tpl.name)}</span>`;
}

function renderTemplates(templates) {
  const tiles = (templates || []).map((tpl) => {
    const missing = tpl.missing || [];
    // 装齐了同样要把用到的家族名列出来，否则只剩「全部 N 个家族」看不出装了什么
    const missingText = tpl.installed
      ? t("template.installedAll", "全部 {count} 个家族已安装：{list}", {
          count: tpl.family_names.length,
          list: (tpl.family_names || []).join(t("list.separator", "、")),
        })
      : t("template.missing", "缺少：{list}", {
          list: missing.map(familyName).join(t("list.separator", "、")),
        });
    const installedAll = tpl.installed && !missing.length;
    // 删除只清「已装、且没被其它装齐的模板占用」的家族：部分装好的模板也可能有可删的部分，
    // 所以缺字体的模板会同时出现「下载」（补缺）与「删除」（清掉自己独占的部分）
    const removable = (tpl.families || []).filter((key) => {
      const family = state.data.catalog.families.find((item) => item.key === key);
      return Boolean(family && family.installed);
    });
    const downloading =
      jobActive() &&
      (state.jobTemplate === tpl.key ||
        (tpl.families || []).some((key) => state.jobKeys.has(key)));
    const actions = [];
    if (!downloading) {
      if (!installedAll) {
        actions.push(`<button class="primary" data-template="${escapeHtml(tpl.key)}" ${
          state.busy ? "disabled" : ""
        }>${escapeHtml(t("template.download", "下载"))}</button>`);
      }
      if (removable.length) {
        actions.push(`<button class="danger" data-template-delete="${escapeHtml(tpl.key)}" ${
          state.busy ? "disabled" : ""
        }>${escapeHtml(t("template.actionRemove", "删除"))}</button>`);
      }
    }
    const action = actions.join("");
    return `<div class="tile">
      <div class="head">
        <div class="name">${templateTitle(tpl)}</div>
        <div class="acts">${action}</div>
      </div>
      <div class="meta">${escapeHtml(missingText)}</div>
      <div class="meta">${escapeHtml(
        t("template.approx", "约 {mb} MB · {note}", {
          mb: tpl.approx_mb,
          note: tpl.note || "",
        }),
      )}</div>
      ${downloading ? jobProgressHtml() : ""}
    </div>`;
  });

  els.templates.innerHTML = tiles.join("");
}

function renderFamilies(families, status) {
  void status;
  const onlyMissing = els.onlyMissing.checked;
  const list = onlyMissing ? families.filter((family) => !family.installed) : families;
  if (!list.length) {
    els.families.innerHTML = `<div class="empty">${escapeHtml(
      t("families.empty", "没有未安装的字体家族。"),
    )}</div>`;
    return;
  }
  const separator = t("list.separator", "、");
  els.families.innerHTML = list
    .map((family) => {
      const downloading = jobActive() && state.jobKeys.has(family.key);
      // 单个家族是用户直接点名删的，共用只提醒不拦（模板批量删才跳过共用的部分）
      const action = downloading
        ? ""
        : family.installed
          ? `<button class="danger" data-family-delete="${escapeHtml(family.key)}" ${
              state.busy ? "disabled" : ""
            }>${escapeHtml(t("family.actionRemove", "删除"))}</button>`
          : `<button class="primary" data-family="${escapeHtml(family.key)}" ${
              state.busy ? "disabled" : ""
            }>${escapeHtml(t("family.actionDownload", "下载"))}</button>`;
      return `<div class="tile">
        <div class="head">
          <div class="name">${escapeHtml(family.name)}</div>
          <div class="acts">${action}</div>
        </div>
        <div class="meta">${escapeHtml(family.note || "")}</div>
        <div class="meta">${escapeHtml(
          t("family.meta", "字重 {weights} · 约 {mb} MB", {
            weights: family.weights || "-",
            mb: family.approx_mb,
          }),
        )}</div>
        <div class="meta">${escapeHtml((family.files || []).join(separator))}</div>
        ${downloading ? jobProgressHtml() : ""}
      </div>`;
    })
    .join("");
}

/** 任务是否还没结束：后端新任务先落 idle，后台协程起来才置 running，两者都算「在跑」。 */
function jobActive() {
  const status = state.job ? String(state.job.status) : "";
  return Boolean(status) && status !== "done" && status !== "failed";
}

/** 当前任务进度（0-100）。 */
function jobPercent() {
  const job = state.job;
  if (!job || job.total <= 0) return 0;
  return Math.min(100, Math.round((job.done / job.total) * 100));
}

/** 卡片里的行内进度条：下载就在点它的那张卡上出进度。 */
function jobProgressHtml() {
  const percent = jobPercent();
  const width = jobActive() ? Math.max(4, percent) : percent;
  const text = state.job && state.job.total > 0 ? `${percent}%` : t("job.preparing", "准备中");
  return `<div class="tile-job" role="progressbar" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100">
    <div class="bar"><div class="fill" style="width:${width}%"></div></div>
    <span>${escapeHtml(text)}</span>
  </div>`;
}

/** 哪些卡该显示进度条变了才重排清单；没变就只更新百分比（避免列表抖动）。 */
function syncTileProgress() {
  const signature = jobActive()
    ? `${Array.from(state.jobKeys).sort().join(",")}|${state.jobTemplate}`
    : "";
  if (signature !== state.jobSig) {
    state.jobSig = signature;
    if (state.data) {
      renderTemplates(state.data.catalog.templates);
      renderFamilies(state.data.catalog.families, state.data.status);
    }
  }
  updateTileProgress();
}

/** 轮询刷新时只改已有的进度条，不重排整份清单（免得列表抖动、丢焦点）。 */
function updateTileProgress() {
  const percent = jobPercent();
  const width = jobActive() ? Math.max(4, percent) : percent;
  const text = state.job && state.job.total > 0 ? `${percent}%` : t("job.preparing", "准备中");
  for (const bar of document.querySelectorAll(".tile-job")) {
    bar.setAttribute("aria-valuenow", String(percent));
    const fill = bar.querySelector(".fill");
    if (fill) fill.style.width = `${width}%`;
    const label = bar.querySelector("span");
    if (label) label.textContent = text;
  }
}

function renderJob(job) {
  state.job = job || null;
  const running = jobActive();
  const percent = job && job.total > 0 ? Math.min(100, (job.done / job.total) * 100) : 0;
  for (const card of document.querySelectorAll(".job-card")) {
    card.hidden = !job;
    if (!job) continue;
    const status = card.querySelector(".job-status");
    const fill = card.querySelector(".fill");
    const log = card.querySelector(".job-log");
    card.querySelector(".job-label").textContent =
      job.label || t("job.label", "字体任务");
    if (status) {
      status.textContent = t("job.status", "{state} · {done}/{total} · {elapsed}s", {
        state: t(`job.state.${job.status}`, job.status),
        done: job.done,
        total: job.total,
        elapsed: job.elapsed,
      });
    }
    if (fill) fill.style.width = `${running ? Math.max(4, percent) : percent}%`;
    card.querySelector(".job-msg").textContent = job.message || "";
    if (log) {
      log.textContent = (job.log || []).join("\n");
      log.scrollTop = log.scrollHeight;
    }
  }
  // 任务结束（或压根没有任务）才撤掉卡里的进度，恢复成下载/删除按钮
  const finished = !job || ["done", "failed"].includes(String(job.status));
  if (finished && (state.jobKeys.size || state.jobTemplate)) {
    state.jobKeys = new Set();
    state.jobTemplate = "";
  }
  syncTileProgress();
  if (state.data) renderChips(state.data.status);
}

// ---------------------------------------------------------------- 任务轮询

function stopPolling() {
  clearTimeout(state.pollTimer);
  state.pollTimer = 0;
}

function schedulePoll() {
  stopPolling();
  state.pollTimer = setTimeout(pollJob, 1200);
}

async function pollJob() {
  const data = await apiGet("fonts/job");
  if (!data) return;
  const job = data.job;
  renderJob(job);
  // 后端新任务先落 idle、后台协程起来才置 running，只认 running 会在第一次轮询就误判结束
  if (jobActive()) {
    schedulePoll();
    return;
  }
  // 任务结束：刷新整个清单（已装状态、占用空间都变了）
  setBusy(false);
  await load();
}

function setBusy(busy) {
  state.busy = busy;
  const selector =
    "button[data-path], button[data-template], button[data-template-delete], button[data-family], button[data-family-delete]";
  for (const button of document.querySelectorAll(selector)) {
    button.disabled = busy;
  }
  els.btnAll.disabled = busy;
}

async function startJob(endpoint, body, okMessage, marks) {
  if (state.busy) {
    toast(t("toast.busy", "已有任务在进行中，请等待完成"), "err");
    return;
  }
  // 先记下这次下载覆盖哪些卡，任务一开跑就能在对应的卡里出进度
  state.jobKeys = new Set((marks && marks.keys) || []);
  state.jobTemplate = (marks && marks.template) || "";
  const data = await apiPost(endpoint, body);
  if (!data) {
    state.jobKeys = new Set();
    state.jobTemplate = "";
    return;
  }
  setBusy(true);
  renderJob(data.job);
  toast(okMessage || t("toast.started", "任务已开始"));
  schedulePoll();
}

// ---------------------------------------------------------------- 交互

function bindInstalled() {
  els.installed.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button || !button.dataset.path) return;
    const path = button.dataset.path;
    if (!armConfirm(button, `file:${path}`, t("toast.armDelete", "再点一次确认删除"))) return;
    button.disabled = true;
    const data = await apiPost("fonts/delete", { path });
    if (!data) {
      button.disabled = false;
      return;
    }
    toast(t("toast.deleted", "已删除"), "ok");
    await load();
  });
}

function bindForms() {
  els.urlForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const url = els.urlInput.value.trim();
    if (!url) return;
    const body = { url, filename: els.nameInput.value.trim() };
    await startJob(
      "fonts/download-url",
      body,
      t("toast.urlStarted", "已开始按链接下载"),
    );
    els.urlInput.value = "";
    els.nameInput.value = "";
  });

  // 上传做成单个按钮：点它打开文件选择，选完立刻上传，少一次「先选后点上传」的来回
  els.btnUpload.addEventListener("click", () => {
    if (els.fileInput) els.fileInput.click();
  });

  els.fileInput.addEventListener("change", async () => {
    const file = els.fileInput.files && els.fileInput.files[0];
    if (!file) return;
    await uploadFile(file);
    // 清空以便重复选同一个文件时仍能触发 change
    els.fileInput.value = "";
  });
}

/**
 * 上传单个字体文件并提示结果。
 * @param {File} file 用户选中的字体文件。
 */
async function uploadFile(file) {
  const api = bridge();
  if (!api || typeof api.upload !== "function") {
    toast(
      t("toast.uploadUnsupported", "当前 AstrBot 版本不支持页面上传，请用链接下载"),
      "err",
    );
    return;
  }
  els.btnUpload.disabled = true;
  const original = els.btnUpload.textContent;
  els.btnUpload.textContent = t("manage.upload.busy", "上传中…");
  try {
    const result = await api.upload("fonts/upload", file);
    const names = (result && result.file && result.file.families) || [];
    toast(
      names.length
        ? t("toast.uploaded", "已上传：{names}", { names: names.join(" · ") })
        : t(
            "toast.uploadNoFamily",
            "已上传，但没读到字体家族名，请检查文件是否有效",
          ),
      names.length ? "ok" : "err",
    );
    await load();
  } catch (error) {
    fail(error);
  } finally {
    els.btnUpload.disabled = false;
    els.btnUpload.textContent = original;
  }
}

function bindGrids() {
  els.templates.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    const template = button.dataset.template;
    if (template) {
      const tpl = state.data.catalog.templates.find((item) => item.key === template);
      await startJob(
        "fonts/download",
        { template },
        t("toast.templateStarted", "已开始下载模板字体"),
        { template, keys: tpl ? tpl.families : [] },
      );
      return;
    }
    const templateDelete = button.dataset.templateDelete;
    if (templateDelete) {
      const tpl = state.data.catalog.templates.find(
        (item) => item.key === templateDelete,
      );
      const families = tpl ? tpl.families : [];
      await removeFamilies(
        families,
        tpl ? tpl.name_en || tpl.name : templateDelete,
        button,
        familiesInUseElsewhere(families, tpl ? tpl.key : ""),
      );
      return;
    }
    const keys = button.dataset.keys;
    if (keys) {
      const list = keys.split(",").filter(Boolean);
      await startJob(
        "fonts/download",
        { keys: list },
        t("toast.keysStarted", "已开始下载列表里的字体"),
        { keys: list },
      );
    }
  });

  els.families.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.familyDelete) {
      const key = button.dataset.familyDelete;
      await removeFamilies(
        [key],
        familyName(key),
        button,
        familiesInUseElsewhere([key], ""),
        "toast.armDeleteFamilyShared",
      );
      return;
    }
    if (!button.dataset.family) return;
    await startJob(
      "fonts/download",
      { keys: [button.dataset.family] },
      t("toast.familyStarted", "已开始下载该字体"),
      { keys: [button.dataset.family] },
    );
  });

  els.onlyMissing.addEventListener("change", () => {
    if (state.data) {
      renderFamilies(state.data.catalog.families, state.data.status);
    }
  });
}

function bindActions() {
  els.btnRetry.addEventListener("click", () => {
    els.loadError.hidden = true;
    state.lastError = "";
    load();
  });

  els.btnRefresh.addEventListener("click", async () => {
    setRefreshBusy(true);
    const data = await apiPost("fonts/refresh", {});
    setRefreshBusy(false);
    if (!data) return;
    toast(t("toast.refreshed", "字体配置已重写，渲染进程会按需重启"), "ok");
    await load();
  });

  els.specimenPicker.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-pick]");
    if (!button) return;
    const name = button.dataset.pick || "";
    if (!name) return;
    const on = !state.specimenPicked.has(name);
    if (on) state.specimenPicked.add(name);
    else state.specimenPicked.delete(name);
    button.setAttribute("aria-pressed", on ? "true" : "false");
  });

  els.btnSpecimen.addEventListener("click", async () => {
    els.btnSpecimen.disabled = true;
    els.btnSpecimen.textContent = t("manage.specimen.busy", "渲染中…");
    const picked = [...state.specimenPicked];
    const payload = picked.length ? { families: picked } : {};
    // 清晰度由渲染器的像素比等级决定：越高越清晰、图片也越大
    const scale = els.specimenScale ? els.specimenScale.value : "";
    if (scale) payload.scale = scale;
    const data = await apiPost("fonts/specimen", payload);
    els.btnSpecimen.disabled = false;
    els.btnSpecimen.textContent = t("manage.specimen.action", "渲染预览图");
    if (!data) return;
    els.specimenImg.src = data.image;
    els.specimenBox.hidden = false;
    const used = Array.isArray(data.families) ? data.families : [];
    els.specimenCaption.hidden = used.length === 0;
    els.specimenCaption.textContent = used.length
      ? t("manage.specimen.caption", "本次对比 {count} 个家族：{list}", {
          count: used.length,
          list: used.join(t("list.separator", "、")),
        })
      : "";
  });

  els.btnAll.addEventListener("click", async () => {
    if (!state.data) return;
    const keys = state.data.catalog.families
      .filter((family) => !family.installed)
      .map((family) => family.key);
    if (!keys.length) {
      toast(t("toast.allInstalled", "所有字体家族都已安装"));
      return;
    }
    const armed = armConfirm(
      els.btnAll,
      "download:all",
      t("toast.armDownload", "再点一次开始下载 {count} 个字体家族", {
        count: keys.length,
      }),
    );
    if (!armed) return;
    await startJob(
      "fonts/download",
      { keys },
      t("toast.allStarted", "已开始下载全部字体"),
      { keys },
    );
  });
}

// ---------------------------------------------------------------- 启动

async function boot() {
  const api = await waitForBridge();
  if (!api) {
    applyLocalThemeFallback();
    els.boot.textContent = t(
      "error.noBridge",
      "没有检测到 AstrBot 插件页面桥，请从 WebUI 的插件页面打开本页。",
    );
    return;
  }
  syncContext(typeof api.getContext === "function" ? api.getContext() : null);
  if (typeof api.onContext === "function") api.onContext(syncContext);
  applyI18n();
  collectTabs();
  bindTabs();
  activateTab(window.location.hash.replace("#", ""), { syncHash: false });
  bindInstalled();
  bindForms();
  bindGrids();
  bindActions();
  await load();
  const current = state.data && state.data.status && state.data.status.job;
  if (current && current.status === "running") {
    setBusy(true);
    schedulePoll();
  }
}

boot();
