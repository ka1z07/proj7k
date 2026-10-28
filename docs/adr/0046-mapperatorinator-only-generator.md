# 0046. 谱面生成全面改用 Mapperatorinator

- **状态**：accepted（2026-10-28；Kai：「将铺面生成模型全面转移到 mapperatorinator，去掉 demuc 分轨和每段一主技法」）。取代 ADR-0027 的落键部分（起音网格、自动测速、选点与配键）、ADR-0030、0031、0032、0033、0039；ADR-0040 的 Mapperatorinator 从「可选后端」变成唯一的落键方式。ADR-0045（乐句复用、分段接力）不变。风格控制另见 ADR-0047（提案）。
- **背景**：gen-1 到 gen-6 是拼装式的：节奏 MLP、行掩码 n-gram、长条分类器、段落切分、Demucs 分轨、设计稿与「每段一种主技法」，再由引擎闭环到目标星级。Kai 一再觉得它僵硬、公式化。ADR-0040 起接入的 Mapperatorinator 听着音乐把节奏和键位一起写，经 PR #118–#131 补上了提速、空段补写、合法化与乐句复用，已经是 Kai 实际在用的那条路。

## 决定

1. **只剩一个落键方式**：`proj7k.generator` 的每次生成都交给 Mapperatorinator（`generator/mapperatorinator.py`），引擎打分、多次生成取最接近目标的一张、高出目标时用降难器删到目标（ADR-0040「提速」）。命令行去掉 `--backend`、`--stems`、`--brief`、`--auto-design`；面板去掉「Demucs 分轨」「每段一种主技法」「Mapperatorinator 模型落键」三个勾选项，第一次生成时自动安装模型。
2. **删掉的模块**：`stems`（Demucs）、`brief` 与 `technique_prior.json`（设计稿与按段主技法）、`analyze` 与 `review`（chart-design skill 的分析与审稿；skill 一并删除）、`rhythm`、`patterns`、`holds`、`phrases`、`structure`、`tempo`，以及三个模型文件（`rhythm_model`、`pattern_model`、`hold_model`）和训练它们的四个工具脚本。`GENERATOR_VERSION` 记为 `gen-7`。
3. **留下的模块**：`audio`、`onset`、`grid`（乐句复用要读音频、按拍切句，ADR-0045）、`legal`、`reuse`、`mapperatorinator(_patch)`，以及 `evaluate`（只评 Mapperatorinator：准确率、召回、星级误差、它写的 BPM 对不对、键形统计，`--skip-ranked` 跳过它训练时见过的谱）。键形统计从 `patterns` 移到 `chartstats`。
4. **timing**：借用参考谱面时照旧把去掉在线 ID 的参考谱交给它读 timing。新增：手动给的 BPM 与 offset 不再被忽略，写成一张只有 timing 的空谱交给它读（`timing_chart`），结果的 timing 来源记为「手动指定」；都没有时它自己测（「模型测得」）。proj7k 自己的测速（`tempo`）随之删除。
5. **长条**：「长条比例」直接作为它的 `hold_note_ratio` 条件；「反键」改名「通篇长条」（`hold_note_ratio=1`）：模型没有反键这个概念，旧的反键是 proj7k 自己的长条模型做的。

## 后果

- 生成必须有 Mapperatorinator 的环境（Python 3.10 + PyTorch，数 GB），实际上只在有 NVIDIA 显卡的机器（meow）上可用；没有显卡的机器能装能跑，但很慢。云端容器连不上 Hugging Face，测试一律用替身模型。
- 引擎、星级指纹、护栏都不变：这次只动生成器。
- 测试：原 `test_generator.py` 里针对节奏、键形、长条、分轨、设计稿的用例随模块删除；留下音频解码、起音与拍网格（乐句复用的基础）；生成闭环、命令行、面板与评测都由 `test_generator_mapperatorinator.py` 的替身模型覆盖。
