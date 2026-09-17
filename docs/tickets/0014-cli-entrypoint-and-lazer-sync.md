# Ticket 14: 本地工具链 CLI 入口与 Lazer Bridge 练习收藏夹自动联动

- **Issue**: [#30](https://github.com/ka1z07/proj7k/issues/30)
- **ID**: `SPEC-P5.1-04`
- **状态**: `closed`
- **标签**: `ready-for-agent`
- **阻塞依赖 (Blocked by)**: [#29](https://github.com/ka1z07/proj7k/issues/29)
- **所属父规格**: [`docs/specs/phase5.1-beatmap-downscaler-and-practice-generator.md`](file:///Users/kz/proj7k/docs/specs/phase5.1-beatmap-downscaler-and-practice-generator.md)
- **关联 ADR**: [`docs/adr/0011-closed-loop-strain-downscaler-and-technique-preservation.md`](file:///Users/kz/proj7k/docs/adr/0011-closed-loop-strain-downscaler-and-technique-preservation.md)

---

## What to build

提供用户端到端可交互的 CLI 与客户端联动链路：
1. 暴露 `python3 -m proj7k.downscaler --input <path> --target-dan <dan> [--target-sr <sr>] [--sync-lazer]`；
2. 终端输出降阶前后 8 维雷达、峰值应变、削减比例与双手平衡比的彩色格式化卡片；
3. 若开启 `--sync-lazer`，复用 `proj7k.sync` 的 Node Realm Bridge 与 `SafeFlushWindow` 安全刷盘窗，自动为衍生谱面计算离散星级桶标签（`Binned Skill Tag`），一键安全注入 osu!lazer 的 `7K Practice` 专属游戏内收藏夹。

---

## Acceptance criteria

- [x] 实现 `proj7k.downscaler` 命令行入口，支持输入单谱面或曲库目录并批量降阶；
- [x] 终端以表格和雷达文本格式输出降阶前后的关键特征与技法对比；
- [x] 整合 `--sync-lazer`，调用 Realm Bridge 与 SafeFlushWindow 将衍生谱面打标并写入 `7K Practice` 收藏夹；
- [x] 编写端到端 CLI 调用测试，验证参数解析、文件写入与刷盘联动正常。
