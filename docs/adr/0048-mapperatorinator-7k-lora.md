# 0048. 用 7K 谱给 Mapperatorinator 训练 LoRA

- **状态**：rejected for now（这次 1000 步的 LoRA 不启用，见「结果」；最初 2026-10-29，Kai 在「要不要在 meow 上训练 7K 长条 LoRA？」上选了「训练 7K 通用 LoRA」：米谱和长条谱一起训）。训练与对比在 meow 上进行，结果补在下面；效果不好就不启用。
- **背景**：ADR-0047 阶段 2 与之后的诊断：风格标签推不动 7K 谱；在长条歌上模型每行只写一个音符（人谱 2.6 个），难度条件从 3 到 12 都只有每秒 4–6 个音符，7 以上还退化出空段。Mapperatorinator 的训练集是 2024 年前的 ranked 图，各模式混在一起，高密度 7K 长条（多在段位包、loved、未 rank 图里）很少。改代码挑不出模型不会写的东西，要让模型见过这类谱。

## 做法

- **数据**（`tools/export_lora_dataset.py`）：Kai 的 osu!lazer 曲库里所有 7K 谱（任何状态），导出成 Mapperatorinator 的 mmrs 格式（每个谱面集一个文件夹：音频硬链接、各难度 .osu，加 metadata.parquet）。去掉 proj7k / 模型写的谱（生成谱、练习谱、降难谱）、少于 200 个音符的谱，以及测量用的留出谱（Exterminate [FLN]、Fluorite [Hard]、Viyella's Scream [Judgement (edit)]）。难度条件用 osu! 官方星级，和模型训练时一致：先查线上谱面库（按校验和），再查 proj7k 写星级之前 lazer 的原值（备份），最后才用 lazer 当前值。谱师 ID 从谱面库的用户表按名字对上，对不上就是「未知谱师」。按谱面集取 5% 做验证集。
- **训练**（`tools/train_lora.py`）：用生成器装好的固定提交（`~/.cache/proj7k/mapperatorinator`）里的 `lora_v32` 配置，改成 mania 检查点（`pretrained_gamemode=3`，LoRA 只在 `gamemode=3` 上加载）、只读 mania 数据、不做变速增强（数据只有 1.0 倍的星级）、Windows 上不用 torch.compile 与 flash attention（sdpa）、不连 wandb。先跑几步看能不能跑、一步多久，再定总步数，在 meow 的 RTX 5060 Ti 上控制在几个小时内。
- **验收**：同 ADR-0047 的诊断工具（`tools/mapperatorinator_difficulty.py --model-arg lora_path=…`），留出的三首歌上比基础模型与 LoRA：每行音符数、每秒音符、引擎星级随难度条件是否上升、空段比例，再加 ADR-0045 的连贯性指标。长条谱的密度要明显上来、米谱不变差，才接进生成器。
- **接进生成器**（验收通过后）：一个开关 / 设置项，把 `lora_path` 传给模型；长条难度封顶（ADR-0047 `LN_MAX_DIFFICULTY`）按 LoRA 的表现重定。

## 在 meow 上试跑（2026-10-29）与改用 UCL 的 GPU 工作站

- 导出：1,194 张谱、657 个谱面集（624 训练 / 33 验证），米谱 660、长条为主 188；星级 1–10+ 都有，6–8 最多。复制要 3.3 GB（音频 3.1 GB）。一个谱面集里用了别的音频的难度（合集、剪辑版）单独成集，因为 Mapperatorinator 每集只读一个音频。
- 试跑（4–8 步）都能训练、写出检查点（LoRA 本身约 43 MB）。每步 32 个序列时，微批 4 约 5.8 秒一步；微批 8 约 19 秒一步，16 GB 显存基本吃满，Kai 的电脑卡到没法用，停掉了（Kai：「先把训练停掉，太卡了」）。
- 改到 Kai 学校的远程 GPU 工作站（UCL CS，RTX 4090 24 GB，Rocky Linux，浏览器远程桌面，一次最长订 3 天，`/scratch0/$USER` 在会话结束时清空）：`tools/pack_lora_job.py` 在 meow 上把数据集和代码打成一个 tar，Kai 传到学校的 home；在工作站上解压到 scratch，运行 `tools/lora_remote.sh`（装 uv、两个 Python 环境与模型，训练，每 5 分钟把 LoRA 与日志拷回 home）。训完把 `~/proj7k-lora-out`（几百 MB 以内）拿回 meow 做对比。
- 2026-10-29 在 beachcomber（RTX 4090）上开跑：meow 经 knuckles 跳板用密钥登录，tar 传到 home；工作站能连 pypi 与 huggingface，脚本自己装好 uv 和两个 Python 环境。batch 8 × grad_acc 4 每步约 1.5 秒、显存 16.5 / 24 GB，1000 步约 25 分钟（meow 上每步 19 秒）。两处坑已修进仓库：脚本须是 LF 换行（打包时强制，`.gitattributes` 里 `*.sh eol=lf`）；工作站登录 shell 是 tcsh，tmux 里要 `bash -c "…"` 启动。

## 结果（2026-10-29，beachcomber）

- 训练 1000 步：33 首验证谱上 loss 0.489（原模型）→ 0.358，时间位置准确率 0.777 → 0.811，列准确率 0.527 → 0.587。留出的 Exterminate [FLN] 上逐 token loss 0.192 → 0.149。LoRA 确实加载了（84 个注意力矩阵合并后变了约 4%）。
- 但生成的长条谱和原模型一样稀（三首留出歌、难度 4/7/10：要全长条时都只有 2–3.7★，人谱 9★+）。连训练集里的密集长条谱也只写到人谱密度的 1/10–1/4；米谱两者都接近人谱。
- 主因不在模型：Mapperatorinator 的后处理所有列共用一个「未结束的长条」，和弦或重叠的长条互相覆盖，多出来的结尾被丢掉。Exterminate 模型写了 3,064 个音符，.osu 里只剩 1,037 个，每行 ≈1.1 个音符就是这么来的（ADR-0047 的「每行只写一个」诊断也是它）。补丁 v4 改成每列各记一个，同一组 token：Exterminate 2.61★ → 7.50★（13.8 notes/s，人谱 24），Dustwind LN 3.92★ → 7.69★。
- 次要：长条谱里有些窗口开头一两个 token 就结束（提前 EOS），一旦空了，下一窗的上文也是空的，于是连空 30–70 秒（Exterminate 30% 的秒数空，Dustwind 70%）。没撞到 tgt_seq_len，也没折半 prompt。有音符的地方 Exterminate 已有约 19 notes/s。
- 修好后处理后重跑（三首留出歌 × 难度 4/7/官方星级/10 × 长条比例 不指定/1.0 × 2 个种子，96 次）：长条谱从 2–3.7★ 升到 7–7.7★，Exterminate 每秒 14–17 个音符（人谱 24）。LoRA 仍没有帮助，平均略差（不指定长条：原模型 7.24★ / 17.6 notes/s / 空 10%，LoRA 6.76★ / 15.0 / 15%；全长条：6.78 / 14.1 / 15% 对 6.70 / 12.5 / 21%），不指定比例时 LoRA 写的长条也偏少（Fluorite 0.23–0.40，人谱 0.46，原模型 0.55）。
- **决定**：这次的 LoRA 不接进生成器。长条难度封顶（ADR-0047 `LN_MAX_DIFFICULTY`）去掉：修好后难度 7–12 长条谱不再越要越空。剩下的差距是每秒行数偏低（长条谱 5–8，人谱 9.3）、提前 EOS 造成 8–30% 的空白秒、难度 7 以上饱和在 7–7.7★（人谱 9+）。数据在 meow `~/.cache/proj7k/lora/ucl-run1/cmp2`。

## 第二轮（2026-10-29 晚，Kai：「A + B」，brawn RTX 4070 Ti Super 16 GB）

- A：补丁 v5，没写出音符的窗口重抽，最多 `EMPTY_RETRIES`=3 次（空的那次只花几个 token）。用 cmp2 同样的设置比 v4 的空白秒占比与星级。
- B：只用长条为主的谱（`LnShare` ≥ 0.5，`train_lora.py subset`）训更久：`MIN_LN=0.5 RUN=ln1 EVERY=500 lora_remote.sh 2000 4 8`（16 GB 卡用微批 4）。每个检查点在留出歌上和原模型比（都用 v5）。

### 第二轮结果（brawn，2026-10-30 01:10）

- **A（补丁 v5）有效**：同歌同条件各 48 次，不指定长条比例时空白秒占比 10% → 5%，全长条 15% → 9%（Viyella 7/1.0 37% → 18%）。每秒音符和每秒行数都略升，星级不变（差别在种子噪声以内）。难度 7 以上仍饱和在 7–7.7★。
- **B（只用长条谱训练，188 张谱、168 个谱面集，2000 步）**：验证集 loss 在 1000 步最低（0.289），之后轻微过拟合（2000 步约 28 轮）。用 1000 步的检查点，在 Exterminate 和 Fluorite 上与原模型比（都用 v5，各 24 次）：
  - 星级 7.69 对 7.32，全长条 7.77 对 7.07；Exterminate 每个条件都更高（7.9–8.2★，人谱 9.55）。这是第一个在长条谱上把星级推上去的 LoRA。
  - 但星级是靠和弦变厚涨上去的（每行 2.7 个音符，人谱 2.6），不是靠变密：每秒行数 5.7 对 6.9（人谱 9.3）。
  - 不指定比例时把混合谱写成长条谱（Fluorite 长条占比 0.68–0.79，人谱 0.46），空白秒略多（10% 对 8%）。
  - 结论：只适合长条为主的请求，不能当全局模型。检查点在 meow `~/.cache/proj7k/lora/ln1/checkpoint_1/lora`，表格在 `~/.cache/proj7k/lora/cmp3`。

## 风险

- 曲库只有几千张 7K 谱，可能过拟合到 Kai 收藏的风格；用验证集损失和留出歌盯着，步数宁少勿多。
- 用了模型已经见过的 ranked 图，帮助有限；真正的新信息来自段位包、loved 和未 rank 的谱。
- 训练期间 meow 的显卡被占满，dashboard 的生成任务会很慢。
