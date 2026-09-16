# Ticket 5: 7K 谱面元数据注记器与双轴收藏夹编排器

- **ID**: `SPEC-P2.3-02`
- **状态**: `closed`
- **标签**: `ready-for-agent`
- **阻塞依赖 (Blocked by)**: `SPEC-P2.3-01`
- **所属父规格**: [`docs/specs/phase2.3-lazer-realm-ingestion-and-sync-daemon.md`](file:///Users/kz/proj7k/docs/specs/phase2.3-lazer-realm-ingestion-and-sync-daemon.md)
- **关联 ADR**: [`docs/adr/0009-lazer-realm-non-destructive-ingestion-and-node-bridge.md`](file:///Users/kz/proj7k/docs/adr/0009-lazer-realm-non-destructive-ingestion-and-node-bridge.md)

---

## 任务背景

为了在游戏内直观呈现评级结果并支持搜索与分类，需对计算出的 `IntrinsicDifficultyResult` 进行元数据注记映射：包含难度名后缀注入与幂等清洗、离散星级桶 Tags 合成，以及 12 个双轴分类收藏夹的编排。

---

## 实施范围 (Scope)

1. **难度名后缀幂等注入与剥离 (`src/proj7k/lazer/annotator.py`)**：
   - 提取基础难度名（剥离旧后缀）：
     `INJECTED_SUFFIX_PATTERN = re.compile(r"\s*\(\d+\.\d+★\s+[A-Za-z_]+\)$")`
   - 格式化新难度名：`f"{base_name} ({star_rating:.2f}★ {dominant_title})"`；
   - 验证对未注记、已注记、多次连续注记的幂等等价性。
2. **离散星级桶标签生成**：
   - 生成 `dominant_{dominant_tech.lower()}`；
   - 生成 `{dominant_tech.lower()}_{int(star_rating)}★`（如 `jack_6★`）；
   - 在保留已有 Tags 的基础上无损追加，并去重。
3. **双轴矩阵收藏夹编排 (Biaxial Collections)**：
   - 8 大技法专项：`7K Jack`, `7K Tech`, `7K Speed`, `7K Stream`, `7K LN General`, `7K LN Tech`, `7K LN Inverse`, `7K LN Release`；
   - 4 档能力阶梯：
     - `7K Tier: 00th-03rd (1★-4★)` ($SR < 4.0$)
     - `7K Tier: 04th-06th (4★-6★)` ($4.0 \le SR < 6.0$)
     - `7K Tier: 07th-09th (6★-8★)` ($6.0 \le SR < 8.0$)
     - `7K Tier: 10th+ (8★+)` ($SR \ge 8.0$)
   - 为每首谱面计算应归属的 Collection 映射表。

---

## 验收条件 (Acceptance Criteria)

- [x] `strip_injected_suffix` 与 `format_injected_difficulty_name` 通过全量幂等属性测试（包括特殊字符与边界测试）；
- [x] Tags 注入函数正确生成离散桶且不破坏谱面原有 tag 词法；
- [x] 双轴收藏夹生成器对任意 7k 谱面均输出恰好 2 个目标收藏夹（1 个专项 + 1 个阶梯）；
- [x] 单元测试覆盖率 $\ge 95\%$（实测 100%）。

