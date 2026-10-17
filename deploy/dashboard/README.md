# 看板自动更新（Windows）

让本机的看板（`proj7k.dashboard`，端口 7770）始终跑主目录 `main` 的最新代码：

- **git 钩子**：主目录里任何让 `main` 前进的操作（`git pull`、`merge`、`pull --rebase`、切回 `main`）结束后立刻重启看板。
  只认主工作目录；共用钩子的 worktree 不会触发。
- **计划任务** `proj7k dashboard autoupdate`：每 5 分钟 `git fetch origin main`，在主目录处于 `main`、已跟踪文件没有未提交改动、
  本地没有分叉时快进，然后重启看板。以当前账号运行，只在登录时跑，不保存密码。

两者都只调用 `restart-services.ps1 -DashboardOnly`，**从不重启谱面库网站**（7780；那是线上部署，另行手动）。

看板的任务只存在内存里，重启会丢掉正在跑的任务。所以有任务排队或运行时（`/api/jobs` 里 `queued`/`running`）只记下
「待重启」（`%USERPROFILE%\.cache\proj7k\dashboard-restart-pending`），等下一次计划任务发现空闲再重启。

## 安装 / 卸载

在仓库根目录的 PowerShell 里：

```powershell
.\deploy\dashboard\install-autoupdate.ps1                 # 可加 -IntervalMinutes 10
.\deploy\dashboard\install-autoupdate.ps1 -Uninstall
```

重复运行没有副作用；已存在的、不是它写的同名钩子不会被覆盖。

## 排查

日志：`%USERPROFILE%\.cache\proj7k\logs\autoupdate.log`（每次快进、重启、推迟及原因）；看板自己的日志仍是同目录的 `dashboard.log`。
手动跑一次：`.\deploy\dashboard\autoupdate.ps1 -Pull`。
