# 连续应变衰减池化、LN通量基座与动能耦合非常规技巧模型

- **状态**：accepted
- **日期**：2026-09-16
- **背景**：针对 Phase 2.1 标杆雷达实证中发现的三个深层物理失真：
  1. 局部二连叠（如 `dai - dir [Hard]`）因全局时间均摊稀释及 Rule D 粗暴扣除导致 Jack 评级为 0.00★；
  2. 反相（Antiphase）双重循环笛卡尔积导致 `ln_release` 数值虚高 2~3 倍，反客为主压制了代表长条物理密度的 `ln_general` 与高段位 `ln_inverse`；
  3. 技巧（Tech）依赖静态密度统计导致数值死锁在个位数，与速度脱节，无法体现社区公认的“非常规排列”难度。

## 决策内容

1. **全技能连续应变衰减与分位数池化 (Continuous Strain Decay-Accumulation & Quantile Pooling)**：
   - 拒绝按技能硬编码不同窗口大小，统一物理建模：任何技能在不同谱面中均可表现为瞬间爆发（Burst）或长时持久（Sustained）。
   - 采用连续衰减累积模型：
     $$S(t) = S(t - \Delta t) \cdot e^{-\Delta t / \tau} + \Delta S$$
     每个技能事件根据其微观物理强度（如 Jack 的 $s_{\text{factor}} \cdot W(L) \cdot C_{\text{chord}}$）向连续曲线注入冲激 $\Delta S$。
   - 废除整曲时长线性平摊（$/ \text{duration\_s}$），采用加权分位数提取全曲有效强度：
     $$r_{\text{skill}} = 0.70 \cdot \text{P90}(S(t)) + 0.30 \cdot \text{Top5\%Mean}(S(t))$$
     使 `dai - dir` 等局域致死二连叠群免受全局垃圾时间稀释。
   - **重构 Rule D 抑制**：废除基于 `jack_ratio < 0.13` 的生硬线性扣除，仅当谱面全曲最大连叠 $L_{\max} < 2$ 且 Jack 局部峰值应变低于底噪门限时方认定为切键杂音。

2. **LN 物理通量基座与反相笛卡尔积解构**：
   - **笛卡尔积修复**：修复 `features.py` 中 `antiphase_count` 的嵌套双重循环 $O(R \times P)$ 漏洞，改为每物理时间戳（Tick）的独占反相触键数 $O(\min(R, P))$。
   - **通量基座确立**：`ln_general` 作为衡量长条空间占据与持握通量的核心底层基准：
     $$r_{\text{ln\_gen}} \propto \text{hold\_ratio} \times \text{NPS} \times \text{ConcurrentHolds}$$
   - **Release 定位重塑**：`ln_release` 作为表征抬手时基窗口精度与反相复杂性的微观修饰项，仅在纯跳音释放谱面中超越 General。
   - **奥卡姆剃刀验证 Inverse**：优先依靠消除 Release 虚高，让高段位 `ln_inverse` 自然恢复主导地位。

3. **对齐社区共识的“技（Tech）”四维非常规度量**：
   - 将“技”严格定义为**非常规排列的难度（Unorthodox Permutation Difficulty）**，由四大正交分量无量纲合成 $\Omega_{\text{irreg}} \ge 1.0$：
     1. **流向紊乱度 (Tortuosity / Reversals)**：连续击键行进方向高频反转率（锯齿、折角流造成的屈伸肌拮抗）；
     2. **括号与手内剪切度 (Shear & Bracket Ratio)**：单手内非相邻手指的跨度离散度与括号拓扑相变；
     3. **轨位转移空间熵 (Spatial Transition Entropy)**：7 轨间转移概率矩阵的信息熵（表征乱轨跨跳）；
     4. **节奏与拍点混乱度 (Rhythmic Irregularity)**：由拍点细分变异熵（1/3、1/4、1/6、12 分混切与切分音）与击键时基抖动率（Micro-timing Jerk）构成。
     $$\Omega_{\text{irreg}} = 1.0 + w_1 \cdot \text{Tortuosity} + w_2 \cdot \text{Bracket} + w_3 \cdot \text{SpatialEntropy} + w_4 \cdot \text{RhythmicEntropy}$$

4. **动能乘性技巧耦合模型 (Kinetic Technique Coupling)**：
   - 技不能脱离物理载体独立存在。从物理流中提取主导底座动能 $K_{\text{base}} = \max(r_{\text{stream\_raw}}, r_{\text{speed\_raw}}, r_{\text{jack\_raw}})$。
   - 技巧强度随底座动能非线性放大：
     $$r_{\text{tech}} = K_{\text{base}} \times (\Omega_{\text{irreg}} - 1.0)^\gamma \times \lambda_{\text{tech}}$$
   - 当谱面排列极端扭曲恶心或节奏极度混乱时，其技巧负荷将反超基础物理动能，在雷达中脱颖而出成为 `dominant == 'tech'`。

## 权衡与取舍 (Considered Options & Trade-offs)

- **放弃按技能定制固定窗口（如 1s vs 4s）**：每个技能均存在爆发型（Burst）与耐力型（Sustained）两种形态，固定时间切片会人为削弱某种形态。连续应变衰减结合分位数池化能够零截断失真地同时兼顾峰值与高原。
- **放弃静态音符对计数计算 Tech**：静态密度与速度完全脱节，无法体现 30 NPS 下的高速肌腱绞杀。动能乘性耦合既保证低速不膨胀，又保证高速高难下技巧的主导地位。
- **放弃为人造反键引入生硬特判抑制**：坚持奥卡姆剃刀，先消除反相笛卡尔积 BUG 导致的 Release 虚高，保持物理模型的自洽与简洁。
