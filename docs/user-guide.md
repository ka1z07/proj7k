# proj7k: 玩家实操指南 (User Guide)

欢迎使用 **proj7k** —— 基于第一性原理与生物力学感知的 osu!mania 7K 客观难度与技法解构系统。

本指南详细介绍工具链面向玩家的四大核心功能：
1. [单曲 8 维技法雷达与客观星级测评](#1-单曲客观星级与-8-维雷达测评)
2. [osu!lazer 实时联动与网页雷达大屏](#2-osulazer-实时联动与网页雷达大屏)
3. [osu!lazer 数据库星级安全同步与一键还原](#3-osulazer-曲库星级同步与还原)
4. [智能降级练习图生成器 (Downscaler)](#4-智能降级练习图生成器-downscaler)

---

## 1. 单曲客观星级与 8 维雷达测评

告别官方算法对特定技法（如纯单叠、反键、高速长条）的虚高或严重低估，proj7k 从物理击键动作时钟和认知负荷出发，给出真实的 8 维技法解构。

### 基本命令
```bash
PYTHONPATH=src python3 -m proj7k.difficulty <.osu文件路径>
```

### 快速拖拽使用技巧
在 macOS 终端或 Windows 命令行中，输入 `PYTHONPATH=src python3 -m proj7k.difficulty `（末尾带空格），然后**直接把 .osu 谱面文件从文件管理器中拖进终端窗口**，按回车即可。

### 输出报告字段详解

```text
============================================================
 PROJ7K INTRINSIC DIFFICULTY REPORT
============================================================
 Song       : Artist - Title [Difficulty]
 Creator    : Mapper
 Notes      : 3240 (LN: 34.2%) | NPS: 24.80
------------------------------------------------------------
 ★ Star Rating: 8.42★ (Uncompressed: 8.78★)
 Dominance   : ln_inverse (8.35★, Synergy: +0.07★)
------------------------------------------------------------
 8-Dimension Technique Radar:
   Jack       :  5.20★    LN General :  7.10★
   Tech       :  6.45★    LN Tech    :  6.80★
   Speed      :  6.10★    LN Inverse :  8.35★
   Stream     :  7.02★    LN Release :  7.90★
------------------------------------------------------------
 Strain Profile: P90=115.40 | Peak=142.10
============================================================
```

#### 8 维技法解读
- **Regular（米键常规）四维**：
  - **Jack（停滞原位叠）**：单轨高频回弹连打与多押弦叠（Chordjack）物理疲劳负荷。
  - **Tech（技巧混押）**：非对称切键、指间独立性抠空（`[gap:1]`）与手型剧烈相变。
  - **Speed（极限瞬时手速）**：极小时间窗口（$\Delta t < 110\text{ms}$）内的超高频爆发神经应变。
  - **Stream（流式切键）**：多轨空间换取回弹时间的均匀高速滚动流。
- **LN（长条）四维**：
  - **LN General（长条综合密度）**：长条占比、背景锁指与有效通量。
  - **LN Tech（长条技巧混押）**：锁指状态下的活动指非对称独立点打。
  - **LN Inverse（反键）**：高密长条背景压满下的“负空间图-底倒错”与微观动作时钟极限。
  - **LN Release（尾放精度）**：长条尾部释放时序精度与级联断连阻抗。

> [!TIP]
> 如果您需要将评测结果对接到第三方前端、Discord Bot 或个人主页，可追加 `--json` 参数以获取纯 JSON 数据输出：
> ```bash
> PYTHONPATH=src python3 -m proj7k.difficulty <路径> --json
> ```

---

## 2. osu!lazer 实时联动与网页雷达大屏

一边打歌/选歌，一边在旁侧屏幕或浏览器中实时看到当前曲目的 8 维雷达、实时应变走势（Strain Chart）与段位对标。

### 启动命令
```bash
PYTHONPATH=src python3 -m proj7k.live --open
```

### 功能特点
1. **自动弹窗**：`--open` 会在默认浏览器自动打开 `http://127.0.0.1:7770` 仪表盘。
2. **实时选歌监听**：工具会自动挂载监听 osu!lazer 的运行时状态，当您在游戏选歌界面切换任意 7K 谱面时，网页雷达几乎零延迟同步刷新。
3. **实时游玩应变流 (Live Stream)**：进入游玩状态后，时间轴将随当前播放进度平滑滚动，并高亮展示当前小节的瞬时手速与主导技法。

---

## 3. osu!lazer 曲库星级同步与还原

将本地 osu!lazer 庞大曲库中所有 7K 谱面的官方失衡星级，批量覆写为 proj7k 第一性原理客观星级。

> [!IMPORTANT]
> ### 🛡️ 玩家安全承诺与运行保障
> 1. **纯本地离线操作**：仅修改本地 `client.realm` 中的客户端难度缓存，**绝不上报官方服务器**，绝不触碰任何网络凭据或账号数据。
> 2. **自动备份机制**：每次同步前，系统会自动创建带时间戳的完整备份文件（`client.realm.backup_*`）。
> 3. **随时一键还原**：只要执行 `--revert`，即可立刻抹除所有修改，彻底恢复官方原始星级。

### 操作步骤

#### Step 1: 准备工作
确保 osu!lazer 客户端**已完全退出**（避免数据库文件被游戏独占锁定）。

#### Step 2: 执行增量同步
```bash
PYTHONPATH=src python3 -m proj7k.sync
```
- 程序会自动在 macOS (`~/Library/Application Support/osu`)、Windows (`%APPDATA%/osu`) 或 Linux 标准路径下寻址您的曲库与数据库。
- 采用双层缓存与多进程并发提取，首次同步后建立缓存，后续新增图秒级处理。

#### Step 3: 一键彻底还原（可选）
如果您想恢复 osu!lazer 原汁原味的官方初始评级：
```bash
PYTHONPATH=src python3 -m proj7k.sync --revert
```

#### 进阶后台守护进程（自动跟随模式）
如果您希望在开着游戏时，自动在后台监听空闲时段同步新下载的图：
```bash
PYTHONPATH=src python3 -m proj7k.sync --daemon
```

---

## 4. 智能降级练习图生成器 (Downscaler)

### 痛点场景
想练高难神图（如 10★ 极限反键、240BPM 极速弦叠、或 Jinjin 段位合格考核图），但原图太难直接暴毙？
传统的 HT/Half-Time 减速 Mod 会破坏音乐韵律与打击节奏感；而网上随意减键的练习图往往粗暴删键，破坏了原曲的手型设计。

**proj7k Downscaler** 采用**自适应生理拓扑守恒降级算法**：
- 在严格保留原图双手交互韵律、抠空手型拓扑与重音骨架的前提下；
- 智能稀疏微观爆发点，平滑衰减应变；
- 精准收敛至指定的目标 Jinjin 段位或连续星级。

### 场景 A：降级到指定的 Jinjin 段位
例如将一张 10★ 极限图降级为 7th 段位练习版本：
```bash
PYTHONPATH=src python3 -m proj7k.downscaler -i "神图.osu" -d "7th" -o ./practice
```

### 场景 B：降级到指定目标星级 (Star Rating)
例如降级到 6.5★：
```bash
PYTHONPATH=src python3 -m proj7k.downscaler -i "神图.osu" -s 6.5 -o ./practice
```

### 场景 C：批量生成整套练习梯级
针对一个练习文件夹内的所有高难图，一键输出对应段位套件：
```bash
PYTHONPATH=src python3 -m proj7k.downscaler -i ./hard_maps/ -d "04th" -o ./dan4_practice/
```

生成后的 `.osu` 文件直接双击或拖入 osu! 即可开始畅快击打！
