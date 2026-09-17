# proj7k: 快速上手与环境配置 (Quickstart)

本文档面向 **osu!mania 7K 玩家**，提供开箱即用的环境准备与基础配置指南。

---

## 1. 系统要求与环境准备

| 项目 | 最低要求 | 推荐配置 | 说明 |
| :--- | :--- | :--- | :--- |
| **操作系统** | macOS / Windows 10+ / Linux | macOS / Windows 11 | 全平台兼容 |
| **Python** | Python 3.11+ | Python 3.11 或 3.12 | 核心评级与雷达计算引擎 |
| **Node.js** | Node.js 18+ (LTS) | Node.js 20+ | **仅在使用 `proj7k.sync` 同步 lazer 数据库时需要** |
| **游戏客户端** | osu!lazer | 最新公开版 osu!lazer | 支持自动曲库检测与实时监听 |

---

## 2. 依赖安装

### 第一步：克隆或下载本仓库
```bash
git clone https://github.com/ka1z07/proj7k.git
cd proj7k
```

### 第二步：安装 Python 依赖
```bash
pip install -r requirements.txt
```
> [!NOTE]
> `requirements.txt` 仅包含少量高性能科学计算依赖（`numpy`, `scipy`）与异步通信库（`websockets`），体积轻巧，无冗余开销。

---

## 3. 设置环境变量（便捷运行）

为了在任何子目录下都可以直接调用 `proj7k` 模块，建议在运行命令时指定 `PYTHONPATH=src`，或者将其写入 shell 别名。

### 方式 A：临时指定（推荐新手）
```bash
PYTHONPATH=src python3 -m proj7k.difficulty <谱面路径>
```

### 方式 B：配置 Shell 别名（macOS / Linux）
将以下别名写入您的 `~/.zshrc` 或 `~/.bashrc`：
```bash
alias proj7k="PYTHONPATH=/path/to/proj7k/src python3 -m proj7k"
```
配置完成后即可直接运行：
```bash
proj7k.difficulty docs/sample_7k.osu
```

### 方式 C：Windows PowerShell 用户
```powershell
$env:PYTHONPATH="src"
python -m proj7k.difficulty docs\sample_7k.osu
```

---

## 4. 初始化 Node.js Bridge（仅 osu!lazer 数据库同步玩家）

如果您打算使用 `proj7k.sync` 将客观星级直接刷入本地 osu!lazer 游戏客户端，需要初始化 Realm 数据库通信桥：

```bash
PYTHONPATH=src python3 -m proj7k.sync --setup
```

该命令会自动检测本地 Node.js 环境，并构建 `tools/lazer-bridge` 所需的 Realm 驱动。

> [!TIP]
> 如果您**不需要**修改 lazer 客户端内的曲库星级，仅想：
> 1. 评测单个 `.osu` 谱面难度；
> 2. 打开网页看实时雷达大屏；
> 3. 生成降级练习图；
> 
> 则**完全无需**安装 Node.js 或执行 `--setup`。

---

## 5. 验证安装

运行内置示例验证安装是否成功：
```bash
PYTHONPATH=src python3 -m proj7k.difficulty docs/sample_7k.osu
```
如果终端输出如下 8 维技法雷达与星级报表，说明环境配置完全正常：
```text
============================================================
 PROJ7K INTRINSIC DIFFICULTY REPORT
============================================================
 Song       : proj7k Benchmark Team - Cyber Stream & Chordjack Demonstration
 ★ Star Rating: 3.61★
 Dominance   : stream (3.58★, Synergy: +0.03★)
 8-Dimension Technique Radar:
   Jack       :  2.55★    LN General :  0.00★
   Stream     :  3.58★    LN Release :  0.00★
============================================================
```

接下来，请前往 [玩家完整实操手册](user-guide.md) 开始探索四大核心功能！
