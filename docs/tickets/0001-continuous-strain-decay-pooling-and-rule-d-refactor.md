# Ticket 1: 连续应变衰减池化与 Rule D 抑制重构 (Regular Jack 1st 治愈)

- **ID**: `SPEC-P2.2-01`
- **状态**: `closed`
- **标签**: `ready-for-agent`
- **阻塞依赖 (Blocked by)**: 无（首顺位任务）
- **所属父规格**: [`docs/specs/phase2.2-radar-alignment-and-subdimension-recalibration.md`](file:///Users/kz/proj7k/docs/specs/phase2.2-radar-alignment-and-subdimension-recalibration.md)
- **关联 ADR**: [`docs/adr/0008-continuous-decay-pooling-and-kinetic-unorthodox-technique-coupling.md`](file:///Users/kz/proj7k/docs/adr/0008-continuous-decay-pooling-and-kinetic-unorthodox-technique-coupling.md)

---

## 任务背景

在 `Regular Jack 1st`（`dai - dir [Hard]`）中，谱面整体以流式切键为主体，但核心技术考点是 41 处 16 分音符（$\Delta t = 127\text{ms}, \Delta k = 1$）二连叠与多押和弦起手。当前代码通过 `jack_strain_total / duration_s` 做整曲均摊，将局部爆发稀释至 $1.82$，再由 Rule D（`jack_ratio < 0.13`）扣除 $r_{\text{stream}} \times 0.40 = 5.0$，直接归零为 **0.00★**。

---

## 实施范围 (Scope)

1. **构建连续衰减累积模型与分位数池化**：
   在 [`src/proj7k/radar.py`](file:///Users/kz/proj7k/src/proj7k/radar.py) 中实现通用的连续应变衰减累积算子：
   $$S(t) = S(t - \Delta t) \cdot e^{-\Delta t / \tau} + \Delta S$$
   废除除以整曲时长 `duration_s` 的线性均摊，改为通过加权分位数提取局域强度：
   $$r_{\text{jack}} = 0.70 \cdot \text{P90}(S(t)) + 0.30 \cdot \text{Top5\%Mean}(S(t))$$
2. **重构交叉抑制 Rule D**：
   废除硬编码的 `jack_ratio < 0.13` 判定。仅当全谱最大连续复击长度 $L_{\max} < 2$ 且局部 Jack 峰值应变低于底噪门限时才执行切键杂音扣除。

---

## 验收条件 (Acceptance Criteria)

- [x] `tests/test_stream_vs_jack_diagnosis.py` 新增对 `dai - dir [Hard]` 的断言：`radar.jack > 3.0★` 且属于合理 1st 段位区间（$3.2★ \sim 4.2★$）。（实测：`3.53★`）
- [x] `SAMBAJACK` (10th) 与 `Identity: Jack` (Gamma) 保持纯 Jack 主导，Stream 分值维持为 $0.00★$。（实测：SAMBAJACK `jack=7.10★, stream=0.00★`；Identity: Jack `jack=7.73★, stream=0.00★`）
- [x] 120 标杆曲全局单调性守卫全绿（`tests/test_120_benchmark_guard.py`：Mean Spearman $\rho \ge 0.98$, Mean Kendall $\tau \ge 0.94$, Inversions $\le 20$）。（实测：$\rho = 0.9888, \tau = 0.9548$, Inversions = 14）
