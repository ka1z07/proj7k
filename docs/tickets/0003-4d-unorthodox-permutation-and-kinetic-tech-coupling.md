# Ticket 3: 四维非常规排列复杂度与动能乘性技巧重构 (Tech 主导权恢复)

- **ID**: `SPEC-P2.2-03`
- **状态**: `closed`
- **标签**: `ready-for-agent`
- **阻塞依赖 (Blocked by)**: [`docs/tickets/0002-ln-flux-base-and-antiphase-cartesian-product-fix.md`](file:///Users/kz/proj7k/docs/tickets/0002-ln-flux-base-and-antiphase-cartesian-product-fix.md)
- **所属父规格**: [`docs/specs/phase2.2-radar-alignment-and-subdimension-recalibration.md`](file:///Users/kz/proj7k/docs/specs/phase2.2-radar-alignment-and-subdimension-recalibration.md)
- **关联 ADR**: [`docs/adr/0008-continuous-decay-pooling-and-kinetic-unorthodox-technique-coupling.md`](file:///Users/kz/proj7k/docs/adr/0008-continuous-decay-pooling-and-kinetic-unorthodox-technique-coupling.md)

---

## 任务背景

当前 `r_tech` 仅依赖静态音符对密度统计（`gap1_density * 2.0 + adj_density * 0.8`），数值被死锁在 $1.5 \sim 8.0$ 之间，且完全脱离打击时基与速度。在 30 NPS 极限高速下，相邻剪切与指间拮抗无法获得动能放大，导致 Tech 维度在全部段位中被 Speed/Stream 完全碾压，Regular Tech 标杆谱的主导识别率为 0.0%。社区公认的非常规切/叠/速与复节奏技巧难度完全无法体现。

---

## 实施范围 (Scope)

1. **提取节奏与拍点混乱度 (Rhythmic Irregularity)**：
   在 [`src/proj7k/features.py`](file:///Users/kz/proj7k/src/proj7k/features.py) 中，提取拍点细分变异熵（Snap Variance Entropy，表征 1/3、1/4、1/6、12 分音符混切与切分音）与时钟抖动率（Micro-timing Jerk）。
2. **合成四维非常规度算子 $\Omega_{\text{irreg}}$**：
   在 [`src/proj7k/radar.py`](file:///Users/kz/proj7k/src/proj7k/radar.py) 中，将流向紊乱度（Tortuosity）、括号/剪切度（Bracket/Shear）、空间转移熵（Spatial Transition Entropy）与节奏混乱度合成为无量纲乘性算子 $\Omega_{\text{irreg}} \ge 1.0$。
3. **实现动能乘性技巧耦合 (Kinetic Technique Coupling)**：
   提取物理底座动能 $K_{\text{base}} = \max(r_{\text{stream\_raw}}, r_{\text{speed\_raw}}, r_{\text{jack\_raw}})$，并构建耦合方程：
   $$r_{\text{tech}} = K_{\text{base}} \times (\Omega_{\text{irreg}} - 1.0)^\gamma \times \lambda_{\text{tech}}$$
   同步将该逻辑适配至 `r_ln_tech`。
4. **验证 Tech 标杆主导性与规整谱面抗漂移性**：
   验证 Regular Tech 标杆谱（`Isometry`, `Rengoku`, `Toki`, `Wanderflux`）正确成为 `tech` 主导，而规整双流谱面（`Fox4-Raize`, `Triumphal Return`）因 $\Omega_{\text{irreg}} \approx 1.0$ 保持 `stream` 主导。

---

## 验收条件 (Acceptance Criteria)

- [x] `Regular Tech` 标杆曲目成功以 `tech` 为主导维度，且分值随段位严格单调上升。
- [x] `LN Tech` 标杆曲目成功以 `ln_tech` 为主导维度。
- [x] 规整切键谱面（`Regular Stream`）不发生语义漂移，维持 `stream` 主导与 0 逆序数。
- [x] 全量 120 曲跑批单调性守卫全绿（`tests/test_120_benchmark_guard.py`：Mean Spearman $\rho \ge 0.98$, Mean Kendall $\tau \ge 0.94$, Inversions $\le 20$）。
