# 0037. 桌面端：Tauri 自有窗口、osu!lazer 风格、动画

- **状态**：accepted（2026-10-22；修订 ADR-0034 的窗口、ADR-0036 的视觉）。
- **背景**：ADR-0036 合并后，Kai 说桌面端风格仍不好看，很不满意“浏览器窗口”式的展示（Edge `--app`），也缺少动画。Kai 选了 Tauri 作为窗口外壳、osu!lazer 作为视觉方向。

## 决策内容

1. **窗口是 `desktop/tauri` 的 Tauri 2 程序**，只做一件事：开一个窗口，加载已经在 7770 端口运行的控制台（`--url`）。Windows / Linux 上是无边框窗口，标题栏由控制台外壳自己画（拖动、双击最大化、最小化 / 最大化 / 关闭按钮，通过 `window.__TAURI__`）；macOS 保留原生红绿灯，叠在外壳标题栏上（`TitleBarStyle::Overlay`）。单实例（再点一次快捷方式就把已开的窗口拿到前面），记住窗口大小和位置。`target=_blank` 的链接里，控制台自己的页面（任务写出的回放查看器）在应用内的查看器层打开：盖住侧栏和页面、保留标题栏，「← 返回」或 Esc 关闭（关闭后卸载页面，音乐随之停止），「在浏览器中打开」转交系统浏览器；其他网站（osu! 官网、线上曲库）交给系统浏览器打开。（2026-10-22 修订：此前回放查看器也走系统浏览器。）权限只开放给 `http://127.0.0.1:*` / `localhost:*`（`capabilities/default.json`）。
2. **控制台照旧不在窗口进程里**：`python -m proj7k.desktop` 仍负责找到或启动控制台，然后优先打开 Tauri 窗口，没装时依次退回 pywebview、Edge/Chrome `--app`、默认浏览器。所以自动更新重启控制台不受影响，页面也随控制台更新，窗口程序只有改 `desktop/tauri` 时才需要更新。
3. **构建与安装**：`.github/workflows/desktop.yml` 在 Windows / macOS 上构建（图标由 `proj7k.desktop.icon` 画，不入库）；合并到 main 后替换 `desktop-latest` 预发布里的 `proj7k-windows.exe` / `proj7k-macos.zip`。`deploy/desktop/install-tauri.ps1`（装到 `%LOCALAPPDATA%\proj7k\desktop\proj7k.exe`，顺带刷新快捷方式图标）和 `install-tauri.sh`（装到 `~/Applications/proj7k.app`，去掉隔离标记）用 `gh release download` 下载，因为仓库是私有的。
4. **osu!lazer 风格**：外壳背后是游戏当前谱面（没有就用最近一局）的封面，模糊、压暗，换谱时淡入淡出；上面缓慢上升 osu! 的三角形（画布约 30 fps，窗口失去焦点就停，玩游戏时不占 CPU）。页面在外壳里是透明的，卡片半透明、偏紫的深色，标题前有粉色小竖条；谱面卡片是 lazer 的谱面面板样式，封面铺满、文字压在下方渐变上。粉色 `#ff66ab` 是唯一的强调色，天蓝 `#66ccff` 用于数据。
5. **动画**：页面切换淡入上滑，详情面板从右侧滑入，页面内容第一次填充时依次升起（之后从游戏切回来刷新数据时不再重播），主页数字从 0 滚到目标值，进度曲线画出，技法条长出，悬停时卡片浮起、封面轻微放大，加载时转圈。页面切换时旧页淡出、新页淡入交叉进行，侧栏的选中底色滑到新的一项，详情面板和查看器关闭时也有退场动画；回放查看器换成同一套 lazer 配色，面板依次升起。系统设置“减少动态效果”时全部关闭。

## 后果

- 第一次需要在每台机器上运行一次 `install-tauri.ps1` / `.sh`（需要已登录的 `gh`）；没装时一切照旧用 Edge 窗口。
- 实时雷达页（`live/static/index.html`，也是 OBS 叠加层）仍是自己的不透明深蓝风格，留待单独统一。
- 未签名：Windows SmartScreen / macOS Gatekeeper 第一次打开可能提示，`install-tauri.sh` 已去掉隔离标记。
