---
name: chart-design
description: Design and generate an osu!mania 7K chart for a song the way a mapper does: read the song's sections and layers, write a design brief (each section's role, lead layer, primary technique and key pattern, accents, density, long notes), generate from it, then review and revise. Use when asked to 设计/写一张谱, chart a song with intent, or improve a generated chart's musicality.
---

# 谱师 skill：先写设计稿，再落键，再审稿（ADR-0033）

你是谱师。生成器负责落键（对拍、星级闭环），你负责「这首歌每一段写什么」。你听不到音频，读的是分析命令算出的数字和图片。

## 0. 准备

- 需要：歌（osu!lazer 里的谱面 MD5 / 谱面 ID / .osu 路径，或音频文件）、目标段位或星级、Kai 的特别要求（比如「这首写反键」）。
- 在有这首歌音频的机器上跑（Kai 的机器 meow：从 Remote Control 会话，worktree 里，用主 checkout 的 `.venv`，`PYTHONPATH=src`）。只读 osu! 曲库；不导入、不加 `--open`，除非 Kai 要求。
- 工作目录：`~/.cache/proj7k/design/<歌名短名>/`。

## 1. 分析

```bash
python -m proj7k.generator.analyze --timing-from <MD5 或 .osu> --stems --out ~/.cache/proj7k/design/<slug>
```

（只有音频时用 `--audio x.mp3`，可加 `--bpm/--offset`。首次 `--stems` 会安装 Demucs，分轨在 CPU 上约 0.3–0.5 倍实时，之后读缓存。）

读这三样：

- `analysis.png`（用 Read 打开看）：上面是频谱图，竖线是生成器切的段落（`S<序号> b<起始小节> <主跟层>`），下面四条曲线是鼓、贝斯、人声、其他的起音强度。看哪里鼓进来、哪里只剩人声或铺底、哪里是 drop。
- `analysis.json`：`sections`（小节范围、能量 0–1、`sounds_like` 哪些段听起来一样、`sustain` 延音程度、`stem_scores` 各轨得分与自动选的 `lead`）、`bars`（每小节起始时间、响度、各频段与各轨起音）、`technique_prior`（人写谱的技法配比，若有）。
- `brief.json`：按生成器的段落预填好的设计稿模板。

## 2. 写设计稿

改 `brief.json`。段落可以合并、拆分或挪边界（以 4 小节为单位最自然），小节从 0 数、含结束小节，段落不能重叠。每段：

| 字段 | 写什么 |
|---|---|
| `role` | `intro` 前奏、`verse` 主歌、`build` 铺垫、`chorus`/`drop` 高潮、`break` 间奏、`bridge` 过渡、`outro` 尾奏 |
| `lead` | 主跟层：`drums`、`bass`、`vocals`、`other`。谱跟这一层的节奏写 |
| `motive` | 一句话：这段的音乐动机和你要突出的东西（给审稿和 Kai 看） |
| `intensity` | 0–1，这段相对其他段的密度。高潮 0.8–1，主歌 0.4–0.6，前奏间奏 0.1–0.3 |
| `technique` | 主技法，引擎八个池之一：`rc_jack` 叠、`rc_tech` 技巧、`rc_speed` 速度、`rc_stamina` 和弦串、`ln_general`、`ln_tech`、`ln_inverse` 反键、`ln_release` 放手 |
| `pattern` | 主键形：`stairs` 楼梯、`split` 切、`trill` 颤、`jumptrill` 跳颤、`jack` 单叠、`chordjack` 跳叠、`chordstream` 和弦串、`handstream` 手流、`free` 不限。省略时按技法给默认 |
| `accent` | 最多一种点缀：`{"pattern": "jumptrill", "at": "phrase_end"}`；`at` 可为 `phrase_end`（每 4 小节最后一拍）、`phrase_start`（每 4 小节第一拍）或小节列表 |
| `ln` | 长条：`{"ratio": 0–1, "kind": "general" / "release" / "inverse"}`。长条技法不写时有默认 |

顶层 `ln_ratio` 是没写 `ln` 的段的长条比例（米谱写 0）。

写的原则：

- 一张谱只用两三种主技法，不要每段换一种；`technique_prior` 给出人写谱一张谱通常用几种、哪些常一起出现、高潮常用什么。
- 每段一个主键形，生成器会让它占七成以上的行；点缀只在标出的位置。
- 听起来一样的段（`sounds_like`）用同一个技法与键形，回来时可以加强 `intensity`。
- 最难的段放在高潮；前奏、间奏要明显轻。
- 主跟层没动静的地方要留白：`intensity` 低，或者干脆让这段很短。
- 单键键形（楼梯、切、颤、单叠）的段只出单键；和弦键形（跳颤、跳叠、和弦串、手流）至少两押。

## 3. 生成

```bash
python -m proj7k.generator --timing-from <MD5 或 .osu> --target-dan <段位> --stems \
  --brief ~/.cache/proj7k/design/<slug>/brief.json --output-dir ~/.cache/proj7k/design/<slug>/out
```

（`--target-sr 6.5` 也可以；`--seed` 换配键。）

## 4. 审稿（最多三轮）

```bash
python -m proj7k.generator.review --chart <生成的 .osu> --brief <brief.json> \
  --analysis <analysis.json> --target <目标星级> --out ~/.cache/proj7k/design/<slug>/review
```

读 `review.md` 和 `preview.png`（每段前 4 小节并排）。六项检查：

1. 节奏相同的小节键形一致（镜像、平移算一致）；
2. 每个高潮段都不比其他段容易（最弱的高潮也要压过主歌、铺垫）；
3. 每段密度跟主跟层的起音走，没声音的小节没有键；
4. 段落交界不别手；
5. 主键形七成、点缀只在标记处；
6. 星级在目标 ±0.1 内。

不通过就改设计稿（不是改代码）：比如第 2 条不过，降低那段的 `intensity` 或换轻一点的技法；第 3 条不过，检查 `lead` 是否选对、或把没声音的小节拆成单独的低强度段。改完回到第 3 步。三轮后还不过，把剩下的问题如实告诉 Kai。

## 5. 交付

告诉 Kai：`.osz` 路径、星级、每段一句话的设计（小节、功能、主跟层、技法 / 键形），审稿结果，以及你觉得还不对的地方。设计稿 `brief.json` 留在工作目录，Kai 可以直接改了再生成。
