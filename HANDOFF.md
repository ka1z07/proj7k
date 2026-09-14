# proj7k 会话交接备忘录 (Session Handoff)

> **生成时间**：2026-09-14 23:02 (UTC+8)  
> **当前分支**：`main`（Commit: [`6778112`](https://github.com/ka1z07/proj7k/commit/6778112)）  
> **工作区状态**：Clean working tree（已通过 73 项全量测试，覆盖率 92%）

---

## 1. 当前里程碑与工单状态 (Issue Tracker State)

本仓库遵循 GitHub Issues 驱动与五角色分流标签规范（见 [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md)）。

### 父规格工单：Issue #8
- **标题**：`Spec: Jinjin 7K Dan 自动化全量批量验证、梯级单调性评估与技法特征提炼系统`
- **状态**：**OPEN**（待执行真实曲库端到端跑批验收后结项）
- **核心规格**：[`docs/specs/jinjin-dan-automated-benchmark-and-monotonicity-system.md`](docs/specs/jinjin-dan-automated-benchmark-and-monotonicity-system.md)

### 已交付并关闭的子工单序列（全部已闭环）
1. **[#9](https://github.com/ka1z07/proj7k/issues/9) 端到端最小基准批处理流水线与骨架验证**：
   - 实现基准清单解析、单曲特征提取聚合、摘要报表输出与错误隔离（`FAILED_INGESTION`）。
2. **[#10](https://github.com/ka1z07/proj7k/issues/10) 生理拓扑与微观约束特征抽取器垂直扩展**：
   - 实现物理轨距约束（`gap:0` / `gap:1`）、单手局部负荷密度、双轨微反（Antiphase）、同轨连击（Jack）微观特征。
3. **[#11](https://github.com/ka1z07/proj7k/issues/11) 梯级单调性评估与异象违例诊断引擎**：
   - 实现 Kendall's $\tau$、Spearman's $\rho$ 单调性系数、邻级阶梯跃迁表 $\Delta_{i \to i+1}$、局部倒挂识别与断崖式跳跃预警。
4. **[#12](https://github.com/ka1z07/proj7k/issues/12) 反键 BPM 缩放律非线性门控算子**：
   - 贯彻 ADR-0006，基于动作时钟窗口实现低速认知区硬截断与高速机械区指数惩罚门控（`apply_inverse_bpm_scaling`）。
5. **[#13](https://github.com/ka1z07/proj7k/issues/13) 8 大专项特征蒸馏与正交基线指纹库**：
   - 针对 8 大专项提炼 13 维特征空间质心向量、Top 3 主导特征排序、专项间马氏/正交分离度矩阵，输出标准 Ground Truth 结构。
6. **[#14](https://github.com/ka1z07/proj7k/issues/14) 全量 112+ 标杆曲目实证跑批、双层缓存与 CI 单调性守卫**：
   - 递归曲库扫描与 Mode 3/7K 模式校验绑定；
   - 基于内容 SHA-256 + 算法版本的 AST 与特征双层持久化缓存（带内存上限淘汰）；
   - 多进程并行调度与轻量级数据聚合；
   - 跨平台确定性特征校验和（`compute_feature_checksum`）；
   - CI 单调性守卫套件（`evaluate_monotonicity_guard` / `--guard`）。

---

## 2. 核心架构与模块清单

```text
src/proj7k/
├── __init__.py         # 顶层公开 API 集中导出
├── parser.py           # .osu 纯文本解析器 (HitObject, TimingPoint, Beatmap7K)
├── slicer.py           # 时间窗口切片与多轨并发手型切片器
├── features.py         # 13 维生理拓扑与时空特征抽取引擎 (BeatmapFeatures)
├── scaling.py          # 反键 BPM 动作时钟缩放律与门控算子 (ADR-0006)
├── monotonicity.py     # 梯级单调性评估引擎与异象诊断器 (Kendall's tau / Spearman's rho)
├── distillation.py     # 8 大专项特征蒸馏、质心指纹库与正交分离度矩阵
├── assets.py           # 本地曲库递归扫描器与权威索引路径自动绑定器
├── cache.py            # AST 与特征张量双层持久化缓存系统 (TwoLayerCache)
├── checksum.py         # 确定性跨平台特征校验和计算器 (SHA-256)
├── guard.py            # CI 单调性守卫套件与回归阻断器 (MonotonicityGuard)
└── batch.py            # 多进程并行跑批总调度器与 CLI 主入口
```

---

## 3. 测试与验证基准 (Testing Baseline)

- **环境要求**：Python 3.14.0，使用 `PYTHONPATH=src` 运行。
- **全量测试命令**：
  ```bash
  PYTHONPATH=src pytest -v
  ```
  当前状态：**73 passed in 0.30s**, 行覆盖率 92%。
- **CLI 守卫防御验证**：
  ```bash
  PYTHONPATH=src python3 -m proj7k.batch --manifest docs/research/structured_index.json --guard
  ```
  在本地曲库缺失时，正确退出码 1 阻断 CI。

---

## 4. 新对话接手后的行动指南 (Immediate Action Plan)

新对话开启后，Agent 可以直接从以下路线切入：

### 路线 A：端到端真实曲库实证跑批（最高测试接缝）
若用户提供本地 osu!mania 歌曲目录（例如 `/path/to/osu!/Songs`）：
1. 运行带 4 进程加速的全量跑批：
   ```bash
   PYTHONPATH=src python3 -m proj7k.batch \
     --manifest docs/research/structured_index.json \
     --library-dir "/path/to/osu!/Songs" \
     --cache-dir .cache/proj7k \
     -j 4 -v \
     --output reports/batch_report.json \
     --ground-truth-output docs/research/distilled_ground_truth.json \
     --guard
   ```
2. 验证：
   - 120/120 标杆曲目成功解析并绑定（0 摄入失败）；
   - 二次执行命中 Layer 2 缓存（耗时骤降至 <100ms）；
   - 8 大专项单调性全部达标（无关键倒挂）；
   - 产生固定的 `feature_checksum` 并校验。
3. 若发现倒挂异常：按 `/diagnosing-bugs` 流程提取最小切片分析，判断是“选曲历史局限”还是“算法盲区”。
4. 实证通过后关闭父工单 #8：
   ```bash
   gh issue close 8 --comment "全量 112+ 标杆曲目实证跑批完成，双层缓存、单调性守卫与基准指纹库全部达标。"
   ```

### 路线 B：启动 Phase 2（核心计算引擎与综合难度拟合）
若暂无外部曲库直接开展下一步开发：
1. 主工单 [#2](https://github.com/ka1z07/proj7k/issues/2) 进入下一阶段——综合难度拟合（Star Rating / pp 算法建模）；
2. 依据 [`docs/methodology/om7k-intrinsic-difficulty-specification.md`](docs/methodology/om7k-intrinsic-difficulty-specification.md) 和 [`docs/future-roadmap.md`](docs/future-roadmap.md)；
3. 遵循主流程发起 `/grill-with-docs` 或 `/to-spec`，拆解 Phase 2 实施规格书。
