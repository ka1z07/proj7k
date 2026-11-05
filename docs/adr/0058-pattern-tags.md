# 0058. 特征标签：谱面「是什么」，与难度分开

- **状态**：accepted（2026-11-03；新模块 `src/proj7k/patterns.py`，不在 `engine/` 包内、不进引擎版本号；目录 `pattern_json`、看板谱面页、直播帧、`python3 -m proj7k.difficulty` 都带上它）。
- **背景**：Kai 要「除了技法难度以外，判断每个图的特征标签，比如 bracket、dense chordstream、delay、dump、shield、coordination 等」。引擎的八个技法星级说的是谱面对技法的**要求**（ADR-0056），不说谱面由什么构成；玩家、目录检索、生成器和教练用的是社区的模式词汇。

## 决策

1. **定义照社区，写在 `TAG_DEFINITIONS` 里，一行一条。** 来源：osu! wiki 的 osu!mania 术语表与 ranking criteria（anchor、bracket、chordstream、grace、jack、minijack、release、roll、shield、split jumptrill、stream、trill）、7K/4K 段位串与常见 mapping guide 用法（longjack、chordjack、jumpstream、dense chordstream、burst、polyrhythm、hybrid、inverse）。delay 与 coordination 社区没有正式定义，这里定为：长条头/尾跨列错开不到半拍格；一次按下与另一列的放开相距不到四分之一拍格。共 23 个标签。
2. **全部对「本地拍格」读。** 拍格 `unit` 是该节拍行前后一秒内行间隔的 70 分位（不是中位数：装饰音与爆发夹在拍格之间，会把中位数拉低），所以「流」是相隔一个拍格的连续行，不问 BPM；「装饰音」是比拍格近得多、又不在爆发里的行。叠键要求不慢于拍格（1.15 容差），爆发要求 ≤ 0.6 拍格且连续四行。
3. **每个标签是节拍行的份额**（`shares[tag]` = 带该标签的按压行 / 全部按压行；release 是孤立尾行 / 按压行，封顶 1），`labels` 是份额 ≥ `LABEL_SHARE = 0.08` 的标签、份额大的在前。份额是「谱面有多少是这个」，不是难度：一个标签永远不是星级。
4. **复合节奏看两个滞后。** 3 对 2、4 对 3 交错起来，相邻行间隔是二分的（1/6 行夹在 1/4 行之间读成 1/2 + 1/2），隔一行的间隔才是 3/2，所以相邻与隔一行两个滞后都按拍格分类，非二分类（1/3、2/3、3/4、4/3、3/2）占两秒窗口四分之一以上、且二分类也占四分之一以上，才算。
5. **dump 不做。** dump 的定义是「音符与歌不对应」，没有音频就判不了；试过「连续 16 秒拍格填满」的代理，在 Speed/Stream 池上读 0.5–0.85，只是在量「长段不停」。要做得在有音频的地方（目录、看板）对齐 `generator/onset.py` 的起始点，单独一个 ADR。
6. **放的位置。** 目录 `charts.pattern_features` 多一项 `tags`（`labels` + `shares`，存进 `pattern_json`，引擎版本变更或为空时刷新）；直播帧与看板谱面页多一项 `patterns`（谱面页「特征标签」卡片：标签 + 份额，悬停是定义）；目录谱面页「特征标签」面板同样；`proj7k.difficulty` 的 JSON 多 `patterns`，文本摘要多一行 `Patterns`。中英繁三份名字与定义在两份 `i18n.js` 里。

## 实测（120 张标杆谱，每池前几名标签）

- Jack 池：jack 0.29–0.60、minijack、chordjack、longjack（高档）；Tech 池：polyrhythm 0.25–0.45（10th/Gamma/Zenith 最高）、burst、stream；Speed 池：stream 0.35–0.68、stair；Stream 池：chordstream 0.25–0.38、jumpstream；LN General/Tech：hybrid 0.5–0.95、coordination 0.3–0.6；LN Inverse：inverse 0.4–0.97、release；LN Release：coordination 0.5–0.76、release、delay。
- 合成谱钉住每个标签（`tests/test_patterns.py`）：纯交互不读楼梯、装饰音不读爆发、3 对 2 读复合节奏、纯 1/4 流不读复合节奏、空谱无标签。

## 后果

- 标签是启发式，阈值（0.08 份额、0.4/0.6 拍格、半/四分之一拍格）是约定不是拟合；改定义不碰引擎、不碰星级指纹。
- 生成器的「动机模仿」（ADR-0055）与教练可以直接用 `labels` 说人话；目录检索按标签筛选是下一步。
