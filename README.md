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
- **🛠️ 自适应降级练习器 (Downscaler)**：打不过神图？智能保留手型骨架，原汁原味降至适合你的专属段位/星级。

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
