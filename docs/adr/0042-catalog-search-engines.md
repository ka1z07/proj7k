# 0042. 谱面库给搜索引擎看的东西

- **状态**：accepted（2026-10-23；扩展 ADR-0026、ADR-0038）。
- **背景**：Kai 问怎么给 <https://7k.kaiz.dev/> 做搜索优化。谱面页、谱师页原本是同一份静态 HTML，标题都是「谱面 · proj7k 7K 谱面库」，正文全由 `catalog.js` 调 API 画出来。Google 会执行脚本，但排队慢、不保证每页都执行；Bing、百度和 Discord/QQ/X 的链接预览基本只读 HTML。站点也没有 `robots.txt` 与 sitemap，不存在的谱面 ID 照样返回 200。

## 决策内容

1. **服务端写每页自己的 `<head>`**（`catalog/seo.py`）：标题、description、canonical、Open Graph（谱面页用 osu! 的封面图，谱师页用头像）、面包屑 JSON-LD；首页再带 `WebSite`。canonical 不带查询串，`/beatmapsets?q=…` 这类变体都归到列表页。
2. **服务端写一段纯 HTML 摘要**（`<section id="ssr">`）：谱面页是曲名、谱师、状态与各难度的官方/引擎星级和段位；谱师页是数量、星级中位数、长条占比和风格标签。脚本画好页面（或判定找不到）后删掉它，所以用户看到的页面不变。
3. **找不到就是 404**：不存在的谱面组或谱师返回 404 与 `noindex`，页面仍由脚本显示「找不到」。
4. **`robots.txt` 只挡 `/oauth/`**：`/api/` 不能挡，Google 渲染页面时要调它，挡了只会看到脚本的「找不到」。
5. **sitemap**：`/sitemap.xml` 是索引，下分 `pages`、`mappers` 与 `sets-N`（每份最多 4 万条，留在 5 万上限之下；`lastmod` 取该组 `updated_at`）。只列 osu! 有 ID 的谱面组。
6. **地址来自 `serve --public-url`**（默认 `https://7k.kaiz.dev`），与 OAuth 回调同一个参数。

## 后果

- 页面 HTML 不再是纯静态：谱面页与谱师页每次请求查一次库（谱师用已缓存的画像），仍按 `STATIC_MAX_AGE_S` 缓存。
- 语言仍只在浏览器里切换（ADR-0038），搜索引擎只看到简体中文；英文/繁体要被收录，得给它们独立网址并加 hreflang，留待以后。
- 站点验证（Google Search Console、Bing Webmaster Tools）和提交 sitemap 在网站之外，由 Kai 在 Cloudflare DNS 加 TXT 记录完成，见 `deploy/catalog/README.md`。
