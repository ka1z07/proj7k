# proj7k 会话交接备忘录 (Session Handoff)

> **生成时间**：2026-09-14 23:12 (UTC+8)  
> **当前分支**：`main`（Commit: [`18644a2`](https://github.com/ka1z07/proj7k/commit/18644a2)）  
> **工作区状态**：Clean working tree（已通过 74 项全量测试，覆盖率 92%）

---

## 1. 当前里程碑与工单状态 (Issue Tracker State)

本仓库遵循 GitHub Issues 驱动与五角色分流标签规范（见 [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md)）。

### 父规格工单：Issue #8
- **标题**：`Spec: Jinjin 7K Dan 自动化全量批量验证、梯级单调性评估与技法特征提炼系统`
- **状态**：**CLOSED**（已通过本地 osu!lazer 真实曲库 120/120 端到端实证跑批验收结项）
- **核心规格**：[`docs/specs/jinjin-dan-automated-benchmark-and-monotonicity-system.md`](docs/specs/jinjin-dan-automated-benchmark-and-monotonicity-system.md)
- **实证交付物**：
  - 8 大专项全量蒸馏基准：[`docs/research/distilled_ground_truth.json`](docs/research/distilled_ground_truth.json)
  - 确定性特征校验和：`sha256:c75ad8f0c2cad0ac3fdfe7cac3fe24534c289ec8de84c9b45f6f42595f1d992b`
  - 专项间全局正交分离度得分：`0.6613`，互信息量：`0.3387`
  - 梯级单调性：全 8 大专项 `peak_4m_nps` 秩相关系数 Spearman's $\rho \ge 0.960$（最高 Stream 达 $0.991$）
  - 反键缩放律实证：LN Inverse 经 ADR-0006 动作时钟非线性门控后，Kendall's $\tau$ 由未校准的 $-0.0769$ 跃升至 $+0.3846$，Stellium 难度收敛至 $34.16$，低速认知盲区硬截断生效。

### 已交付并关闭的子工单序列（全部已闭环）
1. **[#9](https://github.com/ka1z07/proj7k/issues/9) 端到端最小基准批处理流水线与骨架验证**：已闭环。
2. **[#10](https://github.com/ka1z07/proj7k/issues/10) 生理拓扑与微观约束特征抽取器垂直扩展**：已闭环。
3. **[#11](https://github.com/ka1z07/proj7k/issues/11) 梯级单调性评估与异象违例诊断引擎**：已闭环。
4. **[#12](https://github.com/ka1z07/proj7k/issues/12) 反键 BPM 缩放律非线性门控算子**：已闭环。
5. **[#13](https://github.com/ka1z07/proj7k/issues/13) 8 大专项特征蒸馏与正交基线指纹库**：已闭环。
6. **[#14](https://github.com/ka1z07/proj7k/issues/14) 全量 112+ 标杆曲目实证跑批、双层缓存与 CI 单调性守卫**：已闭环。

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
### 路线 A：端到端真实曲库实证跑批（已全部完成验收 ✅）
- **曲库定位**：发现并绑定本地 `~/Library/Application Support/osu/files`（osu!lazer 内容寻址文件存储，39,900+ 文件）。
- **执行命令**：
  ```bash
  PYTHONPATH=src python3 -m proj7k.batch \
    --manifest docs/research/structured_index.json \
    --library-dir "$HOME/Library/Application Support/osu/files" \
    --cache-dir .cache/proj7k \
    -j 4 -v \
    --output reports/batch_report.json \
    --ground-truth-output docs/research/distilled_ground_truth.json \
    --guard \
    --guard-metric peak_4m_nps \
    --guard-max-violations 4 \
    --expected-checksum "sha256:c75ad8f0c2cad0ac3fdfe7cac3fe24534c289ec8de84c9b45f6f42595f1d992b"
  ```
- **验收结论**：
  1. 120/120 标杆曲目全部解析并绑定（0 摄入失败）；
  2. 二次执行全部命中 Layer 2 缓存（120 feature hits, 0 misses）；
  3. 8 大专项 `peak_4m_nps` 秩相关系数全部 $\ge 0.960$；
  4. 确定性特征校验和：`sha256:c75ad8f0c2cad0ac3fdfe7cac3fe24534c289ec8de84c9b45f6f42595f1d992b`；
  5. 专项指纹库成功导出至 `docs/research/distilled_ground_truth.json`；
  6. 父工单 #8 已正式关闭。

### 路线 B：启动 Phase 2（核心计算引擎与综合难度拟合）
父工单 #8 验收关闭后，正式承接 Wayfinder 主工单 [#2](https://github.com/ka1z07/proj7k/issues/2) 进入下一阶段：
1. **输入基准**：基于本次跑批实证产出的 120 首标杆曲目特征库与 `docs/research/distilled_ground_truth.json`；
2. **核心课题**：
   - 物理键力时序与读谱认知双层难度数学建模；
   - 8 大专项多维技法雷达标量综合权重拟合（推翻官方单一失衡 SR/pp，建立与 Jinjin Dan 段位严格单调锚定的固有难度评级 Star Rating / pp 算法）；
   - 针对长条（LN）混押与反键的非线性动作时钟损失函数校准。
3. **下一步行动**：
   - 依据 [`docs/methodology/om7k-intrinsic-difficulty-specification.md`](docs/methodology/om7k-intrinsic-difficulty-specification.md) 和 [`docs/future-roadmap.md`](docs/future-roadmap.md)；
   - 编制 Phase 2 综合难度算法建模规格书（可拆解为新规格工单与子任务序列）。
