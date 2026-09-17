# Ticket 12: 复合骨架锚点保护与双手通量动态平衡引擎

- **Issue**: [#28](https://github.com/ka1z07/proj7k/issues/28)
- **ID**: `SPEC-P5.1-02`
- **状态**: `open`
- **标签**: `ready-for-agent`
- **阻塞依赖 (Blocked by)**: [#27](https://github.com/ka1z07/proj7k/issues/27)
- **所属父规格**: [`docs/specs/phase5.1-beatmap-downscaler-and-practice-generator.md`](file:///Users/kz/proj7k/docs/specs/phase5.1-beatmap-downscaler-and-practice-generator.md)
- **关联 ADR**: [`docs/adr/0011-closed-loop-strain-downscaler-and-technique-preservation.md`](file:///Users/kz/proj7k/docs/adr/0011-closed-loop-strain-downscaler-and-technique-preservation.md)

---

## What to build

在音符修剪中施加两大物理与音乐刚性不变量：
1. `MetricSkeletonDetector`：解析 TimingPoints 拍网，保护 1/1 小节强拍（Downbeat）底座不被删空，且多押和弦（Chord $\ge 2$）只削减押数不整拍抹空；
2. `BimanualFluxBalancer`：计算左右手（L3-L1 vs R1-R3，中心轨 S 均摊）打击通量比，引入失衡惩罚乘子，自适应平衡双手负荷，防止单手过度削减导致生理肌肉代偿，确保两手通量比稳定在 $[45\%, 55\%]$。
给定任意候选修剪集，能自动过滤拦截骨架音符，并自适应输出两手平衡的有效修剪集。

---

## Acceptance criteria

- [ ] 实现 `MetricSkeletonDetector`，基于拍号和拍长精准计算音符拍位，对 1/1 强拍及多押最后 1 个音符建立不可变保护；
- [ ] 实现 `BimanualFluxBalancer`，实时计算两手击键通量，输出非对称惩罚因子；
- [ ] 在施加骨架过滤与双手平衡后，验证高难谱面不会出现突兀小节空白，且两手通量比收敛在 $[45\%, 55\%]$ 内；
- [ ] 编写单元测试，使用非对称偏载谱面验证失衡惩罚能够自适应优先修剪高负荷手。
