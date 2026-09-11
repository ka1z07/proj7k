# Issue Tracker：GitHub

本仓库的需求（Specs）和任务均作为 GitHub Issues 维护。所有操作均通过 `gh` CLI 执行。

## 常用操作规范

- **创建 Issue**：`gh issue create --title "..." --body "..."`。多行内容请使用 heredoc 格式。
- **读取 Issue**：`gh issue view <number> --comments`，通过 `jq` 过滤评论并同时获取标签。
- **列出 Issues**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，配合适用的 `--label` 和 `--state` 过滤。
- **评论 Issue**：`gh issue comment <number> --body "..."`
- **添加 / 移除标签**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **关闭 Issue**：`gh issue close <number> --comment "..."`

仓库信息可由 `git remote -v` 推断；在 Git 仓库内运行时 `gh` 会自动识别。

## Pull Requests 作为分流来源

**PR 是否作为需求来源：否（no）。** _（如果本仓库将外部 PR 视为功能请求，请设为 `yes`；`/triage` 技能会读取此标志。）_

当设为 `yes` 时，PR 与 Issue 遵循相同的标签和状态，使用对应的 `gh pr` 命令：

- **读取 PR**：`gh pr view <number> --comments`，查看 diff 使用 `gh pr diff <number>`。
- **列出待分流的外部 PR**：`gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments`，仅保留 `authorAssociation` 为 `CONTRIBUTOR`、`FIRST_TIME_CONTRIBUTOR` 或 `NONE`（排除 `OWNER`/`MEMBER`/`COLLABORATOR`）。
- **评论 / 标签 / 关闭**：`gh pr comment`、`gh pr edit --add-label`/`--remove-label`、`gh pr close`。

GitHub 在 Issue 和 PR 之间共用编号空间，因此单个 `#42` 可能为两者之一：先通过 `gh pr view 42` 解析，失败则回退至 `gh issue view 42`。

## 当技能提示“发布至 Issue Tracker”（publish to the issue tracker）时

创建一个 GitHub Issue。

## 当技能提示“获取关联工单”（fetch the relevant ticket）时

运行 `gh issue view <number> --comments`。

## 导航 / 任务分解操作（Wayfinding operations）

供 `/wayfinder` 使用。**主图（map）** 是一个带有子任务 Issues 的单个 Issue。

- **主图（Map）**：标记为 `wayfinder:map` 的独立 Issue，内容包含 笔记（Notes） / 既有决策（Decisions-so-far） / 待探索迷雾（Fog）。运行 `gh issue create --label wayfinder:map`。
- **子任务工单（Child ticket）**：作为 GitHub sub-issue 关联到主图（通过 sub-issues 端点的 `gh api`）。若未启用 sub-issues，则在主图正文的任务列表中添加子任务，并在子任务正文顶部注明 `Part of #<map>`。标签格式为：`wayfinder:<type>`（类型包括 `research`/`prototype`/`grilling`/`task`）。认领后，工单将分配给负责推进的开发者。
- **依赖阻塞（Blocking）**：GitHub 的**原生 Issue 依赖关系**，这是平台界面原生可见的规范表现形式。通过 `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>` 添加依赖边，其中 `<blocker-db-id>` 是阻塞工单的数字 **database id**（通过 `gh api repos/<owner>/<repo>/issues/<n> --jq .id` 获取，_不是_ `#number` 或 `node_id`）。GitHub 会返回 `issue_dependencies_summary.blocked_by`（未解决的阻塞项计数，用于实时准入控制）。若不支持原生依赖，则回退为在子工单正文顶部添加 `Blocked by: #<n>, #<n>`。当所有阻塞项全部关闭时，工单解除阻塞。
- **前沿就绪任务查询（Frontier query）**：列出主图的所有未关闭子任务（`gh issue list --state open`，作用域限制在主图的 sub-issues / 任务列表中），排除任何存在未关闭阻塞项（`issue_dependencies_summary.blocked_by > 0`，或 `Blocked by` 包含未关闭 issue）或已有负责人的工单；主图中排列在前的工单优先认领。
- **认领（Claim）**：`gh issue edit <n> --add-assignee @me`，作为当前会话的首次写入操作。
- **完成处理（Resolve）**：`gh issue comment <n> --body "<answer>"`，然后 `gh issue close <n>`，最后在主图的既有决策（Decisions-so-far）中追加上下文指针（简要说明与链接）。
