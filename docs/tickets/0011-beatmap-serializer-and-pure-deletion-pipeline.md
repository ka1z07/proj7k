# Ticket 11: 谱面反向序列化器与纯音符剔除管道原型

- **Issue**: [#27](https://github.com/ka1z07/proj7k/issues/27)
- **ID**: `SPEC-P5.1-01`
- **状态**: `closed`
- **标签**: `ready-for-agent`
- **阻塞依赖 (Blocked by)**: None (首顺位任务，可立即启动)
- **所属父规格**: [`docs/specs/phase5.1-beatmap-downscaler-and-practice-generator.md`](file:///Users/kz/proj7k/docs/specs/phase5.1-beatmap-downscaler-and-practice-generator.md)
- **关联 ADR**: [`docs/adr/0011-closed-loop-strain-downscaler-and-technique-preservation.md`](file:///Users/kz/proj7k/docs/adr/0011-closed-loop-strain-downscaler-and-technique-preservation.md)

---

## What to build

构建 `.osu` 格式反向序列化工具与纯音符剔除降阶管道原型。能够读取一个 7K `.osu` 文件，对指定的音符执行纯音符剔除变异（Pure Deletion Mutation：米键直接移除，长音符头身尾一体完整移除），并反向格式化写出为合规的 `.osu` 文件。
生成的衍生谱面需自动更新元数据中的 Version（格式为 `[P-{TargetDan}] {OriginalVersion}`）与 Tags（追加 `proj7k_downscaled target_{TargetDan} orig_md5_{OriginalMD5[:8]}`），且导出的文件经 `parse_osu_7k` 往返重读（Round-trip）100% 格式合规无损。

---

## Acceptance criteria

- [x] 实现反向序列化器，能将 `Beatmap7K` 数据结构完整写回标准 `.osu` 格式（包含 General, Metadata, Difficulty, TimingPoints, HitObjects）；
- [x] 实现纯音符剔除变异原子逻辑，确保长条剔除时头身尾一体移除，图元闭集无残留；
- [x] 导出衍生谱面并写入全息追溯元数据（Version 与 Tags）；
- [x] 编写端到端单元测试，验证输入高难谱面执行抽稀后，写出并重读解析出的 HitObjects 与时间戳严格吻合。

