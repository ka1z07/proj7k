# 领域文档规范（Domain Docs）

工程技能（engineering skills）在探索本仓库代码库时如何阅读和理解领域文档。

## 探索代码库前必读

- 根目录下的 **`CONTEXT.md`**，或
- 根目录下的 **`CONTEXT-MAP.md`**（若存在）：指向每个上下文对应的 `CONTEXT.md`，按需阅读与主题相关的文档。
- **`docs/adr/`**：阅读与即将开展的工作相关的 ADR（架构决策记录）。在多上下文仓库中，还需检查 `src/<context>/docs/adr/` 获取该上下文特定的决策。

若上述文件不存在，**请静默继续**。不要提示其缺失，也不要在初期主动建议创建。`/domain-modeling` 技能（通过 `/grill-with-docs` 和 `/improve-codebase-architecture` 触发）会在术语或决策真正明确时延迟按需创建它们。

## 目录结构

单上下文仓库（绝大多数仓库）：

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-event-sourced-orders.md
│   └── 0002-postgres-for-write-model.md
└── src/
```

多上下文仓库（根目录下存在 `CONTEXT-MAP.md`）：

```
/
├── CONTEXT-MAP.md
├── docs/adr/                          ← 系统级全局决策
└── src/
    ├── ordering/
    │   ├── CONTEXT.md
    │   └── docs/adr/                  ← 上下文专属决策
    └── billing/
        ├── CONTEXT.md
        └── docs/adr/
```

## 统一使用词汇表术语

当你的输出涉及领域概念时（在 Issue 标题、重构建议、假设分析、测试用例命名中），必须使用 `CONTEXT.md` 中定义的术语。切勿使用词汇表明确避免的同义词。

如果你所需的概念尚未收录在词汇表中，这代表一个信号：要么你正在引入项目未使用的术语（请重新考虑），要么确实存在定义缺口（请记下来供 `/domain-modeling` 补充）。

## 明确标注 ADR 冲突

如果你的设计或方案与现有的 ADR 产生冲突，请明确指出而不是静默覆盖：

> _与 ADR-0007 (event-sourced orders) 冲突，但值得重新审议，原因为……_
