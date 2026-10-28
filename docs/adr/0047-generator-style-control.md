# 0047. 让用户控制生成谱的风格：技法配比、模仿谱师、分段重写与锚定

- **状态**：proposed（2026-10-28；Kai：「考虑如何允许用户控制铺面生成的风格，比如每种技法的难度配比，可以模仿哪个谱师的风格，生成铺面后如何进行修改，针对不满意的段落如何重新生成或者针对该段落为每种技法作难度分配，或者由用户锚定哪些段落需要保持键形一致」）。等 Kai 过目、选定先做哪一阶段。不训练 LoRA；要训练另议。
- **背景**：ADR-0046 之后所有谱都由 Mapperatorinator 落键。现在用户能控制的只有目标星级、长条比例、种子和「快速」，一次生成就是整首歌一锤子买卖：不满意只能换种子整首重来，好的段落也跟着丢。

## 能用的零件（读固定提交 `0e2b0e3` 的源码，v32 配置）

1. **整次生成的条件 token**（`configs/train/v32.yaml` 打开了 `add_mapper_token`、`add_descriptors`）：
   - `mapper_id`：谱师的 osu! 用户 ID。模型只认识训练集（2024 年前的 ranked 图）里的谱师，名单在模型的 tokenizer（`mapper_idx`，随权重下载）里；不认识的 ID 等于「未知谱师」。
   - `descriptors` / `negative_descriptors`：osu! 官方谱面标签。mania 相关的有 `style/jumpstream`、`style/handstream`、`style/quadstream`、`style/chordjack`、`style/longjack`、`skillset/speedjack`、`skillset/wristjack`、`style/chordstream`、`skillset/tech`、`tech/technical hybrid`、`style/generic hybrid`、`style/mixed rice`、`style/LN density`、`style/LN release`、`style/LN coordination`、`style/LN mixed`、`gimmick/LN inverse`、`style/N+1`、`style/dump`、`tap/stamina`、`tech/complex snap`、`rhythm/simple`、`rhythm/repetition` 等（`datasets/tags.json`）。
   - `cfg_scale`：大于 1 时加强谱师和标签的作用（无分类器引导，耗时约翻倍）；负标签只在 `cfg_scale > 1` 时生效，且个数要和正标签相同。
   - `difficulty`（官方星级刻度）、`hold_note_ratio`、`year`。
2. **局部重写**：`beatmap_path` + `add_to_beatmap=true` + `start_time` / `end_time`，只重写这一段，读这段前后已有的音符当上下文。ADR-0040 的补写空段已经在用，所以「这一段换个写法」在技术上已经通了；每段可以用不同的条件（难度、标签、谱师、长条、种子）。
3. **引擎**：八个池的星级，对整首或任意时间窗都能算（`proj7k.window`），降难器能纯删音符降到目标。乐句复用（ADR-0045）已经能把一段原样或镜像地抄到另一段。

## 还不知道的（先在 meow 上量，再决定界面）

- **标签对 7K 管不管用。** 社区用例几乎全是 std；README 说条件和歌不配时会被忽略。打算的实验：3–4 首非 ranked 的歌 × 每个 mania 标签 × 2 个种子 × `cfg_scale` 1 / 2，量八个池星级与键形统计（`chartstats`）相对无标签基线的偏移。只有量得出稳定效果的标签才放进界面，并据此填「池 → 标签」对照表。约 2–3 小时 GPU。
- **模型认识哪些 7K 谱师。** 读 tokenizer 的 `mapper_idx`，和线上谱面库（catalog）里 7K 谱的谱师 ID 取交集，得到「可模仿谱师」名单与每人的谱数、常见段位与主技法。再挑几位风格鲜明的谱师各生成几张，看引擎八池分布是否向这位谱师的人谱靠拢。

## 提议的设计

### 1. 谱面工程：生成不再是一次性的

每次生成在输出目录旁多写一个 `<谱面名>.p7kgen.json`：音频、timing、目标、整首的风格条件、**段落表**（每段起止小节、状态、该段条件、锚定关系）和每一版 `.osu` 的历史。后续所有操作（重写某段、锁定、锚定、读回编辑）都是在这个工程上出新的一版，旧版保留，可以退回。`.osz` 照旧 `BeatmapSetID:-1`，难度名带版本号，导进 lazer 不会覆盖上一版。

段落表默认按乐句复用的切法（两小节一句）合并成 8 或 16 小节的段，听起来一样的段标成一组；用户可以拖动边界、合并、拆开。

### 2. 每种技法的难度配比（整首或单段）

界面给引擎的八个池（米：Jack、Tech、Speed、Stamina；长条：LN General、LN Tech、LN Inverse、LN Release）各一个滑块：「少 / 照常 / 多」，高级模式可填每个池的目标星级。实现：

- 「多」的池换成对应的正标签，「少」的池换成负标签（`cfg_scale` 取实验得出的值）。对照表来自上面的实验，起点猜测：Jack ↔ `style/chordjack`、`skillset/speedjack`、`skillset/wristjack`；Tech ↔ `skillset/tech`、`tech/technical hybrid`、`tech/complex snap`；Speed ↔ `style/jumpstream`、`style/handstream`；Stamina ↔ `tap/stamina`、`style/quadstream`；LN General ↔ `style/LN density`；LN Tech ↔ `style/LN coordination`；LN Inverse ↔ `gimmick/LN inverse`；LN Release ↔ `style/LN release`。
- 每次生成后用引擎的八池星级打分：分数 = 总星级与目标的差 + 各池与配比的加权差。多跑几次取最好，和现在按总星级取最好是同一个循环，只是目标从一个数变成一个向量。
- 降难时按配比挑要保的技法（降难器已有「保留技法」模式，扩展成按池加权）。
- 诚实的边界：模型只能被「推」向某种写法，不能保证某个池恰好几星；做不到时报告实际八池星级与差距。

### 3. 模仿谱师

一个可搜索的下拉框，只列模型认识的 7K 谱师（见上），每人显示谱数、常见段位、主技法。选中后把 `mapper_id` 交给模型，`cfg_scale` 默认调高。整首或单段都能选（例如副歌模仿 A、主歌模仿 B）。不认识的谱师不让选，避免「选了等于没选」。真正学一位谱师（LoRA，用他的 10 张以上 7K 谱微调）效果会强得多，但要训练，另等 Kai 点头。

### 4. 生成后怎么改

- **大改用 osu!lazer 编辑器**：proj7k 不做逐键编辑器，lazer 的编辑器比我们能做的好。导入后在 lazer 里改，回到面板点「从 lazer 读回」：按工程记下的谱面 ID 找到 lazer 里改过的那一版，读回成工程的新一版；被改动过的小节自动标成「手改」并锁定，之后的重写、降难、乐句复用都不碰它们。
- **段落级操作在面板里做**：在时间轴上选段，可以重写、锁定、抄另一段（原样 / 镜像 / 平移）、清空、退回上一版。时间轴每段显示密度、八池星级条和预览图（现有的切片渲染）。

### 5. 重写不满意的段落

选中一段或几段，给这段单独的条件：目标星级、八池配比、长条比例、谱师、种子；一次出 K 个候选（默认 3），每个候选用局部重写生成，按「该段八池星级与配比的差 + 与前后段接缝处的突变」打分排序。用户看预览挑一个，或者都不要再来一轮。换上后重算整首星级，偏离总目标时提示，并可选择让未锁定的段微调回去。

### 6. 锚定：哪些段必须键形一致

- **锁定**：这段一个音符都不许动（重写、降难、乐句复用都跳过）。
- **锚定到另一段**：把 B 段标成「同 A」或「镜像 A」，A 之后每次变化（重写、读回编辑）都自动同步到 B。按拍对齐复制，用乐句复用现有的复制函数；两段小节数不同或节奏差得多时照样复制，但提示。
- 自动的乐句复用（ADR-0045）变成「建议的锚定」显示在时间轴上，用户可以确认或拆开。

## 建议的阶段

1. **工程 + 段落时间轴 + 分段重写 + 锁定与锚定**（不依赖实验，零件都已验证过）：命令行 `python3 -m proj7k.generator.project`（新建、重写某段、锁定、锚定、导出），面板「谱面生成」下加时间轴。降难器要能跳过锁定的音符。
2. **meow 实验**：标签效果与可模仿谱师名单，结论写回本 ADR。
3. **技法配比**：按实验结果做八池滑块（整首与单段）。
4. **模仿谱师**：下拉框（整首与单段）。
5. **lazer 编辑读回**：找回改过的那一版、标出手改小节。

## 后果与风险

- 引擎、星级指纹、护栏不变。
- 每重写一段就是一次模型调用（加载约 16 秒 + 这段的长度），K 个候选就是 K 次；开 `cfg_scale` 再翻倍。meow 上一段 16 小节约半分钟到一分钟一个候选。
- 标签和谱师条件可能对 7K 很弱，那第 3、4 阶段就缩成「能推多少推多少」，界面要说清楚。
- 新界面文字都走 `tr()` 与 `i18n.js`。
