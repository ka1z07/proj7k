# 0023. 删除旧引擎

- **状态**：accepted（2026-10-08；ADR-0018 决策 4 共存期的结束，承接 ADR-0022）。
- **背景**：ADR-0022 之后，`src/proj7k` 里旧模块以外只剩三处还在用旧引擎：单曲命令行 `python3 -m proj7k.difficulty`（README、快速上手、FAQ、玩家指南都以它开头）、标定工具（`benchmark_core` 与 `tools/` 下三个脚本），以及旧机制自己的测试。共存期的理由已经不在。

## 决策内容

1. **删除旧模块**：`radar`、`strain`、`rating`、`calibration`、`benchmark_core`，`tools/technique_star_fit.py`、`tools/calibration_sandbox.py`、`tools/radar_orthogonality_report.py`，以及只测它们的测试（雷达、应变、合成、标定、字面量登记册、驱动阶梯闸门、技法星带、三个技法归类诊断、正交性、LN 判别量等）。CLAUDE.md 记的 2 个预先存在的失败随之消失，基线变为全绿。
2. **`proj7k.difficulty` 保留为命令行，改由新引擎实现。** 命令与 `--json` 不变，玩家文档里的命令照常可用。输出：星级、段位、主导技法、八技能星数、难度场里风险最集中的 3 段（`ChartField.hot_spots`）；JSON 是 `DifficultyProfile.to_dict()` 加元数据、段位、难点与引擎版本。旧的「应变 P90 / 峰值」「Synergy」行没有新引擎对应量，去掉。
3. **随旧引擎走的周边**：`dan.LEGACY_DAN_SR` 与 `legacy_estimate_canonical_dan`；`guard` 里的 `driver_*` 闸门与绝对技法星带 `TECHNIQUE_BAND`；`monotonicity` 的 `driver_` 指标前缀；`physics` 中只有旧引擎读的常数（只留 `DEFAULT_BPM`，回放病理的两个阈值搬进 `profiler/pathology`，数值不变）；`scaling.CALIBRATION_CONSTANTS`（旧方法论指纹的输入）；包顶层的旧符号再导出。
4. **保留**：原始特征 `features`（批量报告的诊断指标、下缩器的质心闸门）、`scaling`、`window`、`slicer`、`analyzer`、`renderer`、`distillation`，它们不读旧引擎。

## 后果

- 引擎版本令牌不变（`8eb43ebd`）：`engine/` 包与 `dan_table.json`、`anchors.json` 都没动，已盖章的谱面不会过期。120 首星级指纹、四档区间、棘轮都不变。
- `prototypes/prototype_feature_strain_identifiability.py` 与 `docs/methodology/` 下几份旧方法论文档引用了已删除的模块或工具；它们是当时的研究记录，按原样保留，要重跑需检出本 ADR 之前的提交。
- 实时面板的「Hold Ratio」此前把已经是百分数的 `hold_pct` 又乘了 100，顺带修正。
