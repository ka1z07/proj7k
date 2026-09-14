# 标杆案例审问与逆向归纳标准化作业程序 (Case Interrogation & Reverse Induction SOP)

## 1. 概述与适用范围

本 SOP 规范了智能体或领域研究员在面对任意一张新 7K 铺面时，如何**从客观物理切片出发，借助 7k-VSDL 与人类直觉分析框架，逆向提炼其内在认知/生理阻力，并将其准确锚定至 Jinjin 7K Dan 权威基准**的标准作业流程。

---

## 2. 标准六步审问流程 (The 6-Step Interrogation SOP)

```
[Step 1: 全景扫描与物理时钟锚定]
       │
       ▼
[Step 2: 7k-VSDL 微观状态与多押手型标注]
       │
       ▼
[Step 3: 原子连贯窗口划分与 4 轴客观几何定型]
       │
       ▼
[Step 4: 宏观多声部图谱与物理负荷计算]
       │
       ▼
[Step 5: 四维直觉阻力交叉审问 (Checklist)]
       │
       ▼
[Step 6: Jinjin Dan 基准对齐与模式库归纳]
```

---

### Step 1: 全景扫描与物理时钟锚定 (Panoramic Scan & Clock Anchoring)
1. **切片图像生成**：
   调用切片工具生成包含时间刻度与小节线的高清垂直图像：
   ```bash
   PYTHONPATH=src python3 -m proj7k.slicer --osu <chart.osu> -m <start_measure>-<end_measure> -o <slice.png>
   ```
2. **物理基准提取**：
   - 提取段落基准 BPM、每拍毫秒数 $\Delta t_{\text{beat}}$、16 分音符时钟间隔 $\Delta t_{1/16}$；
   - 统计总音符数、时长、LN 数量占比（%）与宏观平均 NPS。

---

### Step 2: 7k-VSDL 微观状态与多押手型标注 (VSDL Primitive & Chord Annotation)
1. **自动生成 7k-VSDL 矩阵**：
   调用分析器生成自底向上（Bottom-to-Top）矩阵：
   ```bash
   PYTHONPATH=src python3 -m proj7k.analyzer --osu <chart.osu> -m <start_measure>-<end_measure>
   ```
2. **图元与手型审定**：
   - 检查并验证矩阵中的 6 元状态（`.`、`o`、`⎵`、`|`、`⎴`、`x`）；
   - 提取所有瞬间多押（Chord）的手型拓扑签名，精确标定：
     - `L{..}[adj]` / `R{..}[adj]`（相邻并指）
     - `L{..}[gap:1]` / `R{..}[gap:1]`（抠空中指，高腱鞘张力）
     - `L{..}[full]` / `R{..}[full]`（三指全砸）

---

### Step 3: 原子连贯窗口划分与 4 轴客观几何定型 (Atomic Motif Slicing)
1. **边界截断检测**：依据 4 类边界事件划分 0.5 拍至 2 拍的原子切片（$M_1, M_2, \dots$）：
   - 流向是否发生单向流折返？
   - 节拍量化网格是否发生突变（如 16 分转 24 分三连音）？
   - 双手协同是否发生解耦（互锁转独立）？
   - 自由度约束是否发生突变（LN 突然锁轨或全释放）？
2. **4 轴客观张量打标**：
   - 空间轨迹（Flow-Mono / Oscillation / Convergent / Dispersed）
   - 并发与密度波形（Uniform / Periodic-Chords / Repetition / Spike）
   - 两手协同（Unilateral / Interlocking / Split-Independent / Space-Attraction）
   - 自由度约束（Free / Static-Lock / Release-Articulate / Antiphase）

---

### Step 4: 宏观多声部图谱与物理负荷计算 (Macro Polyphonic Synthesis)
1. **代数算子组合**：
   使用时序过渡算子 `->` 与空间解耦并联算子 `||` 构建宏观 DAG 表达式：
   $$\text{Macro} = (M_1 \xrightarrow{[\text{transition}]} M_2) \parallel M_{\text{Layer2}}$$
2. **客观负荷指标量化**：
   - **平均锁指数**：`mean_locked_fingers`（0~7 根，揭示 LN Inverse 压迫度）；
   - **反相位对偶率**：`antiphase_count`（同毫秒同拍抬按事件频度）；
   - **双手负荷比**：统计 Left : Space : Right 的敲击分配比例。

---

### Step 5: 四维直觉阻力交叉审问 (Cross-Interrogation via Intuition Framework)
对照《人类玩家直觉分析框架》进行结构化过筛排查：

- [ ] **Q1 (视认阻力)**：
  - 空间组块是否失调？中心轨 Space 是否破坏了视线对称锚定？
  - 是否存在负空间图-底倒错（常态压满、寻找松手缝隙）？
  - 是否存在视觉外推预测失效的密度骤变点？
- [ ] **Q2 (腱鞘与发力)**：
  - 是否频繁出现无名指与食指并发、中指悬空的 `[gap:1]` 抠空张力型？
  - 在长条持续按住（Hold `|`）时，其余自由手指是否被要求以超高频打点（自由度压制）？
  - 发力杠杆是否由指关节被迫转移至手腕与前臂？马拉松耐力是否跨越 90 秒乳酸衰减阈值？
- [ ] **Q3 (容错瓶颈)**：
  - 尾判释放时间窗是否极窄？是否存在“一指早放导致整排断连”的级联崩溃风险？
  - 双手空间跨度是否过大导致手指产生邻轨物理漂移？
- [ ] **Q4 (复合畸变)**：
  - 多个单项技法叠加时，是否存在非线性难度乘性膨胀（如高速流 $\otimes$ 锁指、多押 $\otimes$ 反相位）？

---

### Step 6: Jinjin Dan 基准对齐与模式库归纳 (Jinjin Dan Calibration & Pattern Induction)
1. **段位基准对齐**：
   对比 `docs/research/jinjin-dan-index.md` 中的 8 大单一练习套件与 14 级段位序列：
   - 本段落的核心技法属于哪个单项维度？（Stream, Chordstream, Jack, Tech, LN Regular, LN Inverse 等）
   - 其物理冲击量与微操复杂度等价于 Jinjin Dan 的第几段位（如 7th Dan、10th Dan、Stellium）？
2. **沉淀结构化归纳卡片 (Induction Card)**：
   将审问结论格式化输出并归档，作为后续通用规则的推导基石。

---

## 3. 标准化归纳输出卡片模板 (Standard Induction Card)

```markdown
### 案例审问与归纳卡片：[铺面名称] [版本]
- **审问切片**：Measure XX.X ~ XX.X (XXXXms ~ XXXXms)
- **物理参数**：BPM XXX | NPS XX.X | LN 占比 XX% | 平均锁指 X.XX/7
- **7k-VSDL 宏观结构**：`M1 ->[trans] (ML || MR)`
- **核心生理/认知阻力**：
  1. [视认] ...
  2. [腱鞘] ... (高频出现 L{3,1}[gap:1])
  3. [容错] ...
- **Jinjin Dan 对齐基准**：
  - 等价单项套件：[如 LN Inverse Practice / Chordstream Practice]
  - 估算基准段位：[如 11th Dan]
- **泛化提炼规则**：
  - "当 7K 铺面在锁指 >= 3 的背景下，连续引入 [gap:1] 抠空多押时，其实际生理疲劳度呈现指数级而非线性增长。"
```
