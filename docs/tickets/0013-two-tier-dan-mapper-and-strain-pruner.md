# Ticket 13: 双层级联段位映射与闭环时序应变剪枝器

- **Issue**: [#29](https://github.com/ka1z07/proj7k/issues/29)
- **ID**: `SPEC-P5.1-03`
- **状态**: `open`
- **标签**: `ready-for-agent`
- **阻塞依赖 (Blocked by)**: [#28](https://github.com/ka1z07/proj7k/issues/28)
- **所属父规格**: [`docs/specs/phase5.1-beatmap-downscaler-and-practice-generator.md`](file:///Users/kz/proj7k/docs/specs/phase5.1-beatmap-downscaler-and-practice-generator.md)
- **关联 ADR**: [`docs/adr/0011-closed-loop-strain-downscaler-and-technique-preservation.md`](file:///Users/kz/proj7k/docs/adr/0011-closed-loop-strain-downscaler-and-technique-preservation.md)

---

## What to build

构建核心降阶数学优化引擎：
1. `TwoTierDanMapper`：解析目标段位，优先从 `distilled_ground_truth.json` 提取主导专项的目标应变阈值 $S_{\text{target}}$ 与雷达质心，兜底采用权威星级标尺插值；
2. `WindowedPeakBatchPruner`：基于 `proj7k.strain` 双手时序应变曲线定位超标 Peak Windows，在窗口内排除骨架音符并结合双手失衡惩罚批量平抑峰值，多轮迭代快速收敛；
3. `DualGateValidator`：重评 8 维雷达与专项特征，强制核验雷达余弦相似度 $\ge 0.80$ 且主导技法第 1 顺位守恒，严防图种本质退化。

---

## Acceptance criteria

- [ ] 实现 `TwoTierDanMapper`，支持标准段位名称与自定义星级解析，映射为应变阈值与雷达目标；
- [ ] 实现 `WindowedPeakBatchPruner`，能够定位超标峰值区间，多轮迭代分批剪枝直至全局应变达到目标；
- [ ] 实现 `DualGateValidator`，核验雷达余弦相似度与主导技法第 1 顺位，违例时回滚或惩罚；
- [ ] 编写端到端单元测试，验证高难 Chordjack 与 Stream 合成图降阶至目标段位，且主导技法严格守恒。
