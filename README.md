# 🎹 proj7k

<p align="center">
  <strong>基于第一性原理与视觉/生物力学感知的 osu!mania 7K 客观难度评价与技法解构系统</strong>
</p>

<p align="center">
  <a href="docs/quickstart.md">🚀 5分钟快速上手</a> •
  <a href="docs/user-guide.md">📖 玩家实操指南</a> •
  <a href="docs/faq.md">❓ 常见问题 (FAQ)</a> •
  <a href="CONTEXT.md">📐 领域词汇体系</a>
</p>

---

## 🌟 为什么需要 proj7k？

在传统的下落式音游（特别是 osu!mania 7K）中，官方评级算法常常让玩家困惑：
- **反键与高密长条**：明明难得令人窒息（负空间读谱倒错、微观放手窗口极窄），官方星级却严重低估；
- **纯单轨连打 (Jack)**：微观肌肉疲劳巨大，却被当成普通低密度处理；
- **单纯看总体 NPS / 简单加和**：忽视了双手轮换与单手死扛之间 2 倍生理负荷的巨大天壤之别。

**proj7k** 拒绝黑盒，基于**第一性物理键力时钟**、**微观手型拓扑**与**人类神经应变感知**，带来真正反映玩家实操体验的客观工具链：
- **🎯 8 维技法雷达**：将铺面精准解构为 `Jack`（叠键）、`Tech`（混押）、`Speed`（手速）、`Stream`（切键）与 4 维 `LN`（长条综合/技巧/反键/尾放）；
- **📊 客观 Star Rating**：告别虚高与低估，严密锚定全球权威 Jinjin 7K Dan 考核梯级；
- **🖥️ 实时网页仪表盘**：一边选歌打歌，一边在旁侧屏幕实时展示动态雷达与难度流；
- **🔄 osu!lazer 数据库安全同步**：一键将游戏内失衡星级替换为客观星级，并提供随时一键无痕还原；
- **🛠️ 自适应降级练习器 (Downscaler)**：打不过神图？智能保留手型骨架，原汁原味降至适合你的专属段位/星级；
- **🩺 回放微观病理诊断 (Player Profiler)**：因果时序判定击键，量化各轨 UR、双手偏载、Jack 疲劳漂移斜率与 LN 释放粘键，严格隔离惊慌鬼键；
- **📈 8 维承压雷达与段位映射**：点对点对齐微观瞬时应变与击键偏差，拟合生理承压拐点输出 8 维 Jinjin Dan 段位；
- **🎯 双教练模式与三阶梯练习包 (Closed-Loop Coaching)**：支持“短板突破”与“长板专精”双策略（严禁段位考题，本地曲库精准召回）；自动截取致死高应变切片（$t_{\text{fatal}}-10\text{s}, +5\text{s}$），闭环生成 Recovery、Bridge、Push 三阶梯练习包（打包为独立 `.osz`）。

---

## ⚡ 5 分钟极速上手

### 1. 准备环境 (30秒)
```bash
git clone https://github.com/ka1z07/proj7k.git
cd proj7k
pip install -r requirements.txt
```

### 2. 体验单曲客观评级与 8 维雷达 (10秒)
直接测试仓库内置的示范谱面：
```bash
PYTHONPATH=src python3 -m proj7k.difficulty docs/sample_7k.osu
```
或者测试你电脑上的任意谱面（把 `.osu` 拖进终端）：
```bash
PYTHONPATH=src python3 -m proj7k.difficulty "/path/to/your/beatmap.osu"
```

**终端输出效果：**
```text
============================================================
 PROJ7K INTRINSIC DIFFICULTY REPORT
============================================================
 Song       : Artist - Song Title [7K Difficulty]
 Notes      : 43 (LN: 4.7%) | NPS: 10.42
------------------------------------------------------------
 ★ Star Rating: 3.61★ (Uncompressed: 3.61★)
 Dominance   : stream (3.58★, Synergy: +0.03★)
------------------------------------------------------------
 8-Dimension Technique Radar:
   Jack       :  2.55★    LN General :  0.00★
   Tech       :  0.00★    LN Tech    :  0.00★
   Speed      :  0.00★    LN Inverse :  0.00★
   Stream     :  3.58★    LN Release :  0.00★
------------------------------------------------------------
 Strain Profile: P90=50.56 | Peak=57.79
============================================================
```

### 3. 打开实时雷达大屏 (10秒)
启动实时仪表盘，并在浏览器中自动打开：
```bash
PYTHONPATH=src python3 -m proj7k.live --open
```
在 osu!lazer 中切换 7K 谱面，网页雷达图将随选歌近乎零延迟动态刷新！

### 4. 一键刷新 osu!lazer 曲库星级 (安全有保障)
退出 osu!lazer 后运行：
```bash
PYTHONPATH=src python3 -m proj7k.sync
```
> [!NOTE]
> 首次运行同步前需执行 `PYTHONPATH=src python3 -m proj7k.sync --setup` 安装 Node.js bridge。  
> 详见 [快速上手文档](docs/quickstart.md)。

> [!TIP]
> 随时想还原官方原始星级？只需一条指令：  
> `PYTHONPATH=src python3 -m proj7k.sync --revert`

### 5. 诊断玩家回放并生成靶向降阶练习包 (15秒)
分析 `.osr` 回放文件，诊断微观病理并针对断连/致死段落直接生成 Recovery、Bridge、Push 三阶梯专属 `.osz` 练习包：
```bash
PYTHONPATH=src python3 -m proj7k.profiler -r "replay.osr" -b "beatmap.osu" --recommend --bundle
```
**终端输出效果：**
- 诊断各轨击键偏差均值与 UR、左右手偏载比例、连叠漂移疲劳告警；
- 点对点对齐 8 维瞬时应变与击键偏差，输出有效承压上限与 8 维 Jinjin Dan 段位雷达；
- 运用双教练策略（短板突破 / 长板专精），扫描本地已装曲库（严格排除段位考题）推荐针对性练习谱；
- 自动截取致死高应变切片（$-10\text{s}, +5\text{s}$），并在 `./practice_bundles/` 生成独立三阶梯练习谱面及 `.osz` 包。

### 6. 查询玩家宏观技能画像与历史趋势 (5秒)
查询近 30 天滑动窗口竞技状态（Recent Rolling Form）并对比全历史巅峰（All-Time Peak）：
```bash
PYTHONPATH=src python3 -m proj7k.profiler -p "YourUsername" --horizon-days 30 --recommend
```

### 7. 导入 osu!lazer 历史回放 (一次性)
将本地 osu!lazer 中的历史 7K 对局批量导入本地档案库，作为宏观画像的历史样本：
```bash
PYTHONPATH=src python3 -m proj7k.profiler --import-replays --player "YourUsername"
```

**要点：**
- 按**回放指纹**（物理回放文件哈希）幂等去重，重复执行不会产生重复样本；
- 遵守脏数据清洗规则（ADR-0012）：剔除时长 <30 秒或完成度 <50% 的重试对局，但**保留中途暴毙对局的致死前高应变样本**；
- 可选 `--import-limit N` 限制单次处理量，`--realm` 指定非默认的 `client.realm` 路径。

> ⚠️ **方向区分**：`--import-replays` 是**读入**方向（lazer → 本地档案库）；反向的 `--sync-lazer` 属于降阶器与 `proj7k.sync`，用于将星级标签与衍生练习谱**写入** lazer 数据库。两者方向相反，不可混用。

---

## 🎛️ 玩家常用命令速查

| 使用场景 | 命令示例 | 说明 |
| :--- | :--- | :--- |
| **评测单曲** | `PYTHONPATH=src python3 -m proj7k.difficulty <谱面.osu>` | 打印 8 维技法与星级简报 |
| **输出 JSON** | `PYTHONPATH=src python3 -m proj7k.difficulty <谱面.osu> --json` | 导出结构化数据供程序调用 |
| **实时大屏** | `PYTHONPATH=src python3 -m proj7k.live --open` | 启动 Web 实时雷达监控 |
| **同步 Lazer** | `PYTHONPATH=src python3 -m proj7k.sync` | 批量将客观星级写入 lazer 数据库 |
| **还原 Lazer** | `PYTHONPATH=src python3 -m proj7k.sync --revert` | 彻底还原为官方原始星级 |
| **降级练习** | `PYTHONPATH=src python3 -m proj7k.downscaler -i "图.osu" -d "7th" -o ./out` | 降级到指定 Jinjin 段位 |
| **指定星级降级** | `PYTHONPATH=src python3 -m proj7k.downscaler -i "图.osu" -s 6.5 -o ./out` | 降级到指定连续星级 |
| **回放微观诊断** | `PYTHONPATH=src python3 -m proj7k.profiler -r "play.osr" -b "map.osu"` | 诊断离散度、双手偏载、Jack漂移与8维承压段位 |
| **双教练智能推荐** | `PYTHONPATH=src python3 -m proj7k.profiler -r "play.osr" -b "map.osu" --recommend` | 推荐本地曲库练习谱（短板突破/长板专精，严格排除考题） |
| **致死降阶练习包** | `PYTHONPATH=src python3 -m proj7k.profiler -r "play.osr" -b "map.osu" --bundle` | 自动切出致死高应变切片并导出 Recovery/Bridge/Push 三阶梯 .osz |
| **近期30天画像** | `PYTHONPATH=src python3 -m proj7k.profiler -p "Username" --horizon-days 30` | 聚合近 30 天竞技状态并对比全历史巅峰 |
| **全历史巅峰画像** | `PYTHONPATH=src python3 -m proj7k.profiler -p "Username" --all-time` | 查看全历史极限承压雷达与各技法巅峰 |
| **批量对局回放入库** | `PYTHONPATH=src python3 -m proj7k.profiler --batch-dir ./replays --beatmap-dir ./beatmaps [--player "Username"]` | 批量过滤重试脏数据并持久化到本地 SQLite 档案库（可限定单一玩家） |
| **Lazer 回放导入** | `PYTHONPATH=src python3 -m proj7k.profiler --import-replays --player "Username"` | 从本地 osu!lazer 存储提取该玩家历史 7K 回放，按回放指纹幂等入库 |

---

## 🛡️ 安全与数据保障承诺

1. **纯本地离线计算**：所有分析与数据库写入全部在您的个人电脑本地完成，绝不上报或截取任何账号凭据。
2. **零封号风险**：仅修改客户端离线缓存，与多人联机/排位防作弊系统完全正交解耦。
3. **自动防灾备份**：任何写入操作均先建立 `client.realm.backup_*` 副本。

---

## 📚 深入阅读与文档导航

- 📘 [快速上手与环境配置 (Quickstart)](docs/quickstart.md)：全平台环境安装、依赖与别名配置。
- 📙 [玩家完整实操指南 (User Guide)](docs/user-guide.md)：8 维雷达指标深度剖析、大屏交互、以及 Downscaler 高阶参数。
- 📕 [常见问题解答 (FAQ)](docs/faq.md)：疑难排查与还原攻略。
- 📐 [领域模型词汇表 (CONTEXT.md)](CONTEXT.md)：深入了解 7k-VSDL、反相位对偶、动作时钟与第一性原理物理定义。
