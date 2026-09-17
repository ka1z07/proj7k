# proj7k: 玩家高频常见问题 (FAQ)

整理了 osu!mania 7K 玩家在使用 proj7k 工具链时最常见的问题与解决方案。

---

## 1. 基础运行与环境问题

### Q: 运行命令提示 `ModuleNotFoundError: No module named 'proj7k'`？
**原因**：Python 没有将 `src` 目录加入搜索路径。  
**解决**：在命令前加上 `PYTHONPATH=src`，例如：
```bash
PYTHONPATH=src python3 -m proj7k.difficulty docs/sample_7k.osu
```
若使用的是 Windows PowerShell，可先执行：
```powershell
$env:PYTHONPATH="src"
python -m proj7k.difficulty docs\sample_7k.osu
```
或参考 [快速上手文档](quickstart.md#方式-b配置-shell-别名macos--linux) 配置系统全局别名。

---

### Q: 提示 `ModuleNotFoundError: No module named 'numpy'` 或 `scipy`？
**原因**：未安装必要的高性能计算科学库。  
**解决**：执行以下命令安装依赖：
```bash
pip install -r requirements.txt
```

---

## 2. 数据库同步 (`proj7k.sync`) 问题

### Q: 同步时报错：`Resource temporarily unavailable` 或 `Realm is locked by another process`？
**原因**：osu!lazer 正在运行中，其游戏引擎独占锁定了 `client.realm` 数据库文件。  
**解决**：
1. 请先**完全退出 osu!lazer**；
2. 重新运行 `PYTHONPATH=src python3 -m proj7k.sync`；
3. 同步完成后再启动 osu!lazer。

---

### Q: 修改数据库会导致 osu! 官方封号吗？
**解答**：**绝对不会。**
1. `client.realm` 是存储在您个人电脑上的本地 SQLite/Realm 客户端缓存，记录了谱面元数据与本地离线计算的难度星级；
2. proj7k 的数据库同步是**纯粹的本地离线读写**，既不拦截网络流量，也不伪造游戏分数，更不上报任何修改数据；
3. 即使 osu! 官方客户端进行云端校验或重新计算曲库，也只会覆盖掉本地缓存，完全不存在触发封号机制的风险。

---

### Q: 我想恢复 osu!lazer 官方原始的星级，怎么做？
**解答**：只需执行还原命令：
```bash
PYTHONPATH=src python3 -m proj7k.sync --revert
```
或者，由于系统每次写入前都会在对应目录下自动备份原始文件（例如 `client.realm.backup_*`），您也可以直接将备份文件重命名还原。

---

### Q: 找不到 osu!lazer 默认安装路径怎么办？
**解答**：如果您的 osu!lazer 数据目录迁移到了自定义磁盘路径，您可以通过 `--realm` 手动指定：
```bash
PYTHONPATH=src python3 -m proj7k.sync --realm "/你的自定义路径/client.realm"
```

---

## 3. 实时雷达大屏 (`proj7k.live`) 问题

### Q: 打开网页大屏后，为什么雷达图一片空白/未显示选歌？
**排查步骤**：
1. 确认已开启 osu!lazer 并在 7K 谱面之间进行切换；
2. 如果 osu!lazer 在游戏设置中关闭了日志输出，可能导致无法监听切换。可尝试在控制台检查是否有新日志输出；
3. 若端口 7770 被其他应用程序占用，可通过参数更换端口：
   ```bash
   PYTHONPATH=src python3 -m proj7k.live --port 8888 --open
   ```

---

## 4. 降级练习器 (`proj7k.downscaler`) 问题

### Q: 什么是 Jinjin Dan 段位代号（如 `7th`, `Stellium`, `04th`）？
**解答**：
Jinjin 7K Dan 是目前全球 osu!mania 7K 圈内最公认、最严谨的段位考核体系：
- **Regular（常规米键段位）**：`01st`, `02nd`, `03rd`, ..., `10th`，以及顶尖神级 `Alpha`, `Beta`, `Gamma`, `Delta`。
- **LN（长条专项段位）**：`01st`, `02nd`, ..., `10th`，以及顶尖考核 `Stellium`。

在执行 `proj7k.downscaler -d <tier>` 时，可以直接传入对应段位名称（例如 `-d "7th"` 或 `-d "Stellium"`），算法会自动将其映射为该段位的权威考核应变基线。
