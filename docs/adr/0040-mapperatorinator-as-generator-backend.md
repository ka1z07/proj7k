# 0040. 借用 Mapperatorinator 作 7K 生成器的落键后端

- **状态**：accepted（2026-10-23；Kai 问：「结合当前 proj7k 的 context 和 Mapperatorinator，是否能开发一个铺面生成模型？」，读完方案后：「试试接入 mappertorinator」）。阶段 0 与阶段 1 一起做：先把后端接上，再在 meow 上用它跑试评。LoRA（阶段 2）另等 Kai 点头。
- **背景**：gen-1 到 gen-6（ADR-0027、0030–0033、0039）用的是拼装式模型：节奏 MLP、行掩码 n-gram、长条分类器、段落切分、设计稿，再由引擎闭环到目标星级。Kai 多次觉得谱面僵硬、公式化：段落没有主题，节奏变化少，切叠混合很机械。问题出在结构上：每个小模型只看局部，键形和节奏是分开抽的，谁也没学过「人写谱时整段怎么写」。

## Mapperatorinator 是什么（2026-10-23 读 main 分支源码）

- **作者与许可**：OliBomby，代码 MIT。训练代码来自 osuT5，坐标细化来自 osu-diffusion。
- **模型**：改过的 Whisper（`varwhisper_small_v3`，约 2.19 亿参数，另有 `v32-mini`）。编码器读 128 维 log-mel 频谱，解码器逐个输出事件 token，就是一个「音频 → 谱面事件序列」的翻译模型。窗口 16.4 秒，长歌分窗滑动生成，每窗预填上一窗一半的 token 以保持连贯。
- **mania 原生支持**：四个模式一起训练。mania 的 token 有 `column`（0–17）、`hold_note`、`hold_note_end`、`hold_note_sustain`，时间精度 10 ms，另有节拍细分（snap）token 用于后处理吸附网格。
- **条件 token**：模式、难度（osu! 官方星级分档）、谱师 ID、年份、`keycount`（1–18）、`hold_note_ratio`、`scroll_speed_ratio`、描述标签（osu! 的谱面 tag）、歌长、在 mapset 里的位置。训练时随机把条件替换成「未知」，所以推理时可以只给一部分。`cfg_scale > 1` 时加强谱师和标签条件。
- **训练数据与算力**：默认数据集是 HF 上的 `project-riz/osu-beatmaps`（全部 ranked 图，含音频），作者自报约 5,700 GPU 小时（4060 Ti + 租的 4090）。提供 LoRA 微调配置（`lora_v32.yaml`：r=64，1000 步，建议至少 10 张同风格完整谱），数据用 Mapperator 导出的 `mmrs` 格式，可包含 graveyard/pending 等全部状态。
- **推理**：Python 3.10，PyTorch 2.10。设备自动选 CUDA / MPS（Mac）/ CPU；bf16 和快速解码循环（CUDA graph）只在 NVIDIA 上有，Mac 和 CPU 退回 fp32，会慢很多（作者没给耗时数字，需实测）。
- **已知短板**：作者说 SR 或风格与歌不配时条件会被忽略；不给难度与年份时输出不稳定。社区和 README 的例子几乎都是 std，mania 7K 的质量没有公开评测。

## 三个选项

| | A. 当后端用（零训练 / LoRA） | B. 自己从头训小模型 | C. 只当参考，继续改拼装式生成器 |
|---|---|---|---|
| 能解决「僵硬」吗 | 最有希望：节奏和键形由同一个序列模型一起出，带 16 秒上下文 | 原理同 A，但数据量差两个数量级 | 难，问题是结构性的 |
| 数据 | 零训练不需要；LoRA 用 Kai 曲库 1,220 张带音频的 7K 谱即可起步 | 至少要几万张带音频的 7K 谱：线上库 79,681 张只有 .osu，没有音频，要另外下 .osz | 现有 |
| 算力 | 推理：Mac MPS 或 meow CPU 可试；LoRA：一张 bf16 NVIDIA 卡数小时（租 4090 即可） | 数百到上千 GPU 小时 | 无 |
| 与星级闭环 | 难度 token 是官方 SR，不是我们的星级：多采几份（不同难度 token / 温度 / 种子），用引擎选最接近目标的，再小幅修 | 可直接用我们的星级当条件 | 现成 |

**建议 A**，B 不做；C 的成果（引擎闭环、审稿、分轨、设计稿 skill）留作 A 的外壳。

## 建议的阶段

0. **零训练试跑（不改仓库）**：在 Mac（MPS）或 meow 上建独立的 Python 3.10 环境装 Mapperatorinator v32，`gamemode=3, keycount=7`，给难度、`hold_note_ratio` 和年份，对 gen-4 起用的 40 首留出歌各出一张，跑 `proj7k.generator.evaluate` 同一套指标（星级、rhythm_repeat、trill/jack/key_repeat、density_cv）并和 gen-6 并排比，挑几张给 Kai 试玩。注意：v32 训练用了全部 ranked 图，留出歌如果本身是 ranked 曲，模型可能见过原谱，对比要另挑没上过 ranked 的歌（例如 Kai 自己的音频）。**这一步的结论决定后面做不做。**
1. **接成生成器的可选后端**：`proj7k.generator --backend mapperatorinator`，用子进程调用独立环境（我们是 Python 3.14，它要 3.10）。星级闭环：一次采 N 份候选，引擎打分取最接近目标的；差得多就调难度 token 再采。产物照旧走 `BeatmapSetID:-1` 与审稿表。面板里像分轨一样做成勾选后才下载的实验选项，默认行为不变。
2. **7K LoRA 微调**：用 Kai 曲库（可再加从线上库挑的高质量 ranked/loved 7K .osz）导成 `mmrs` 数据集，租一张 NVIDIA 卡训 LoRA，只套在 `gamemode=3` 的检查点上。可以按风格分几个 LoRA（rice / LN / Kai 喜欢的谱师）。
3. **（可选）设计稿接到条件上**：分窗生成时每窗的条件 token 可以不同，理论上可让设计稿逐段控制难度、长条比例和描述标签，代替现在 gen-5 的规则落键。需要改它的推理循环，先验证可行性再说。

## 后果与风险

- 引擎、星级指纹、护栏都不变；只多一个可选后端。
- 依赖重：PyTorch + 约 2 亿参数权重，玩家安装包不默认带，和 Demucs 一样勾选才装。
- meow 的显卡未知、CPU 常年约 68% 占用，CPU 推理可能每首几分钟以上；Mac 走 MPS 可能更现实。训练一定要外租或另找 NVIDIA 卡。
- mania 7K 的生成质量没有公开数据，阶段 0 可能得出「不如 gen-6」，那就停在这里，只把结论记进本 ADR。
- 训练数据含他人音频与谱面：仅供自用；把微调权重公开发布前要另议。

## 实现（2026-10-23）

- `proj7k.generator.mapperatorinator`：固定在 Mapperatorinator 提交 `0e2b0e3`、配置 `v32`。`install` 把它克隆到 `~/.cache/proj7k/mapperatorinator/repo`（环境变量 `PROJ7K_MAPPERATORINATOR` 可改位置），用 uv 建 Python 3.10 的 `.venv`，PyTorch 用 `--torch-backend auto` 按本机显卡选版本，桌面 UI 的依赖不装。`run_model` 在子进程里跑它的 `inference.py`（`gamemode=3 keycount=7`）。
- 星级：第一次的难度条件取目标星级，之后每次乘以「目标 / 引擎星级」（每次最多 ×1.6 或 ÷1.6，限制在 0.5–12），种子逐次加一，落进容差就停，否则取最接近的一次并警告。默认最多 4 次（`--model-runs`）。
- 命令行：`python3 -m proj7k.generator --backend mapperatorinator`；`--ln` 不给时长条比例由模型自己定，给了就作为它的 `hold_note_ratio` 条件；`--model-arg` 透传其他条件（如 `descriptors`、`mapper_id`）。模型自己测 timing，参考谱面只借用音频与元数据；设计稿、分轨、按段技法对它不生效，会给出提示。
- 面板「谱面生成」：「实验：Mapperatorinator 模型落键」勾选项，第一次勾选时安装。
- 评测：`python3 -m proj7k.generator.evaluate --backend mapperatorinator --skip-ranked <catalog.db>`，按线上谱面库跳过 ranked/loved/qualified 的谱（模型训练时见过）。

## 提速（2026-10-23，Kai：「有办法加速 mappertorinator 生成吗？」）

在 meow（RTX 5060 Ti 16 GB）上用两首歌（2:25 / 2:58）实测单次生成（秒）：默认 197 / 191，其中加载模型约 16 秒、timing 约 8 秒，写音符 160–170 秒，SV 5–7 秒；去掉 SV 194 / 176，谱面不变；加大窗口步长 172 / 206，召回下降，不用；所有窗口一起写（`parallel=true`）98 / 89，写音符一步降到约 37 秒，但它自己测 timing 反而从约 7 秒变成约 40 秒，长条变少、星级略低；借用人类谱的 timing 117 / 139；两个进程同时跑每张只省约 10%，GPU 已经满了。

- 不再写 SV（`output_type=[TIMING,MAP]`）。
- 借用参考谱面 timing 时（面板默认），把参考谱面去掉在线 ID 后交给它读 timing（`in_context=[TIMING]`），只写音符。不去掉 ID 的话它会把那张谱的谱师当成风格条件，训练时见过的谱还可能被照抄。
- 「快速」选项（`--model-fast`，面板勾选项）开启 `parallel=true`，默认关闭：省一半时间，但手感需要 Kai 试过再决定要不要默认开。
- 少跑几次：第一次的难度条件取目标 ×1.15，结果高出目标不超过 1.5★ 时，用降难器纯删音符降到目标（先保技法，不行再放开）。多数情况一次就够，不必重写 2–4 次。
