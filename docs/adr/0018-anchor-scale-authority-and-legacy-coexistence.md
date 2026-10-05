# 0018. 锚点标尺的权威、闸门形状与旧引擎共存期 (Anchor-Scale Authority, Gate Shape & Legacy Coexistence)

- **状态**：accepted（2026-10-02；与 ADR-0017 同批对齐，实施在分支 `engine/spec-v0.2`）。
- **日期**：2026-10-02
- **背景**：
  ADR-0017 换掉引擎之后，三件事要有单一的归属：星数标尺由谁说了算、闸门拦什么、旧引擎在被替换期间的边界。旧标尺 `dan.CANONICAL_DAN_SR`（0th 3.2 … 10th 7.4 … Stellium 10.5）是旧引擎自己校出来的；新标尺锚点来自项目方的共识（RC 0th ≈ 3.5、5th ≈ 5.5、8th 6.5–7、10th ≈ 8、Zenith > 10；LN 另有一组），在新引擎上 RC 段中位数是 3.40 / 5.74 / 7.85 / 11.05，两把尺差一大截。

## 决策内容

1. **标尺的权威是锚点文件**（`prototypes/prototype_spec_v01_anchors.json` 的生产对应物），星数只从它换算。旧的绝对技法星带闸门（`TECHNIQUE_BAND`，每池 ≥ 13/15 落在 `CANONICAL_DAN_SR` 的 8% 内）退役：新引擎在其下只有 55/120 在带内，而保留它等于让旧引擎给新引擎打分。保留 0th / 5th / 10th / Stellium 四档的锚点中位数区间，重新基线化。
2. **段位表由引擎派生**：`estimate_canonical_dan` 读一张数据文件，内容取新引擎十五档 RC 段中位星数（0th 3.40、1st 3.94、2nd 4.42、3rd 5.10、4th 5.45、5th 5.74、6th 6.04、7th 6.55、8th 6.96、9th 7.40、10th 7.85、Gamma 8.44、Azimuth 9.30、Zenith 10.00、Stellium 11.05；严格单调，2nd 到 3rd 的间隔 0.68 是已知形状，写入测试）。这张表在重基线化那一步生成，不与引擎改动同提交。旧表改名为 `LEGACY_*`，只给仍读旧星数的消费者。
3. **闸门形状**：总星（`star_rating`）每池闸门（τ ≥ .88、ρ ≥ .95、违规 ≤ 4）阈值不变，新引擎八池全过；阶梯级闸门（`LADDER_*`）阈值不变，新引擎通过。八个技法阶梯改为**逐池棘轮**，上限取当前实测值，明确记为「已知缺陷的上限」，不是验收；`ln_release` 在旧的技法闸门下（τ .829）不过，棘轮据实记录。T1 的 12 处倒挂另开工单记账。
4. **共存期边界**（C、D 组留在旧引擎）：旧模块原地不动，不搬进 `legacy` 包，只在 `CONTEXT.md` 与本 ADR 里标成「旧引擎，待删」。profiler 与 downscaler 整条链（含 `coach` 的星数、段位与 `p90_strain` 目标）留在旧星数与 `LEGACY_*` 表上，自己内部自洽，其 CLI 输出标注 "legacy SR"。live 一帧里的星数、段位、雷达来自新引擎，应变画布与 `tech_breakdown` 放在帧里单独的 `legacy` 键下并标注。旧机制的测试原样保留，等 C、D 重做完后与旧模块一起删。
5. **注入的接入时机**：`lazer/daemon` 与 `lazer/annotator` 作为最后一个独立提交接入，之前先加 `--dry-run`，报告将改写多少张以及标签新旧名对照，由项目方看过再放行。新引擎的版本令牌沿用「模块 AST 摘要 + 参数字典」的办法。
6. **落点与验收**：新引擎建在 `src/proj7k/engine/`（包名为占位），拆成 `events`、`demand`、`solver`、`attribution`、`scale`、`evaluate`、`params`。移植的金标准是冻结的 `prototypes/prototype_spec_v01_engine.results.json`，120 张谱的 `D`、`D_k`、`π_k` 逐值一致，容差 1e-9。

## 后果

- 旧引擎的字面量登记册、驱动阶梯闸门等测试在旧模块存活期间继续有效；新引擎的常数家是 `engine/params`，不套用旧登记册。
- 技法标签名随改名变化（例如 `stream` 变 `rc_stamina`），会影响游戏内按标签筛选与收藏夹；是否保留旧标签名作别名，在 `--dry-run` 报告后由项目方决定。
