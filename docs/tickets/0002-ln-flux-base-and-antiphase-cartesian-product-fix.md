# Ticket 2: LN 通量基座确立与反相笛卡尔积解构 (LN 对齐与 Inverse 恢复)

- **ID**: `SPEC-P2.2-02`
- **状态**: `closed`
- **标签**: `ready-for-agent`
- **阻塞依赖 (Blocked by)**: [`docs/tickets/0001-continuous-strain-decay-pooling-and-rule-d-refactor.md`](file:///Users/kz/proj7k/docs/tickets/0001-continuous-strain-decay-pooling-and-rule-d-refactor.md)
- **所属父规格**: [`docs/specs/phase2.2-radar-alignment-and-subdimension-recalibration.md`](file:///Users/kz/proj7k/docs/specs/phase2.2-radar-alignment-and-subdimension-recalibration.md)
- **关联 ADR**: [`docs/adr/0008-continuous-decay-pooling-and-kinetic-unorthodox-technique-coupling.md`](file:///Users/kz/proj7k/docs/adr/0008-continuous-decay-pooling-and-kinetic-unorthodox-technique-coupling.md)

---

## 任务背景

在全量 60 首 LN 标杆曲目中，有高达 49 首（81.7%）被判定为 `ln_release` 主导。根因在于 `features.py` 中 `antiphase_count` 采用 $O(R \times P)$ 嵌套双循环导致多押换点时反相事件二次方爆炸，使得 `r_ln_rel` 高达 $50 \sim 100$，严重压制了代表长条物理时空占据通量的 `ln_general`，并抢夺了高段位 `ln_inverse` 的主导权。

---

## 实施范围 (Scope)

1. **消除反相笛卡尔积**：
   在 [`src/proj7k/features.py`](file:///Users/kz/proj7k/src/proj7k/features.py) 中，将 `antiphase_count` 修正为每帧物理独占反相匹配数 $\min(|R|, |P|)$。
2. **重塑 `ln_general` 物理通量基座**：
   在 [`src/proj7k/radar.py`](file:///Users/kz/proj7k/src/proj7k/radar.py) 中建立基于持握比、有效 NPS 与多轨长条并发度的权威物理基座公式。
3. **重新校准 `ln_release`**：
   将其定位降格为抬手微观精度修饰项，量纲收敛至 $20 \sim 35$，仅在纯跳音抬指谱中超越 General。
4. **验证高段位 `ln_inverse` 自然恢复**：
   Release 虚高消除后，检验 8th ~ Stellium 反键谱面是否自然恢复 `ln_inverse` 主导。

---

## 验收条件 (Acceptance Criteria)

- [x] `LN General` 15 首标杆谱面的主导维度彻底逆转，以 `ln_general` 为主导。
- [x] `LN Inverse` 高段位谱面（8th ~ Stellium，包括 Stellium Gram - Nibelungen）恢复 `dominant == 'ln_inverse'`。
- [x] 全量 120 曲跑批单调性守卫全绿（`tests/test_120_benchmark_guard.py`：Mean Spearman $\rho \ge 0.98$, Mean Kendall $\tau \ge 0.94$, Inversions $\le 20$）。
