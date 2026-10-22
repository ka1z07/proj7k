# 0038. 线上谱面库的三种语言

- **状态**：accepted（2026-10-22；扩展 ADR-0026，沿用 ADR-0035 的做法）。
- **背景**：Kai 要给线上谱面库（<https://7k.kaiz.dev/>）加语言选项。README 已有 English / 简体中文 / 繁體中文三版，网站只有简体中文。

## 决策内容

1. **简体中文仍是源文字，英文与繁体各一张对照表。** 同 ADR-0035：`catalog/static/i18n.js` 里的 `EN` 与 `HANT` 把每句简体映射过去；脚本文字一律 `tr("中文 {x}", { x })`，静态 HTML 的文字、`title` / `placeholder` / `alt` / `aria-label`、页面标题和 description 在加载后按整句查表替换。
2. **繁体由工具生成。** `tools/catalog_hant.py` 用 OpenCC 的台湾用语（s2twp）把 `EN` 的每个键转成繁体，再套脚本里的少量改写（储存库、捷径、标竿），重写 `HANT`。OpenCC 只是开发时的工具，不进运行时与 CI 依赖。
3. **语言怎么定**：第一次访问按浏览器语言（`zh-TW/HK/MO/Hant` → 繁体，其它 `zh*` → 简体，其它 → 英文）；顶栏右侧「简 / 繁 / EN」切换，存在 `localStorage`（`p7k.lang`）后重载页面。全在浏览器里做：服务端返回的 HTML 与 API 不随语言变化，所以 Cloudflare 缓存与内容哈希版本（PR #89）照旧。
4. **服务端生成的文字带模板。** 谱师风格标签原本在服务端拼成中文；现在每个标签另带 `label_t` / `why_t`（`mappers.TAG_TEXT` 里的中文模板）与 `vars`，页面翻译模板再填值。`label` / `why` 仍是拼好的简体，旧客户端不受影响。
5. **测试兜底**：`tests/test_catalog_i18n.py` 断言每个页面、`catalog.js` 与 `TAG_TEXT` 里的中文都在两张表里，英文里没有中文、繁体里没有常见简体字，占位符一致，脚本里的中文都包在 `tr()` 里。

## 后果

- 加新文字要同时加英文，再跑一次 `tools/catalog_hant.py`。
- 谱面数据（标题、艺术家、谱师名、标签）与技法、段位名不翻译。
- 首页「三步开始」顺带改成现在的安装方式（release 的 zip + `install.cmd` / `install.sh`），不再提 `dashboard.cmd` 与手动 `pip install`。
