# 0048. 用 7K 谱给 Mapperatorinator 训练 LoRA

- **状态**：proposed（2026-10-29，Kai 在「要不要在 meow 上训练 7K 长条 LoRA？」上选了「训练 7K 通用 LoRA」：米谱和长条谱一起训）。训练与对比在 meow 上进行，结果补在下面；效果不好就不启用。
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

## 风险

- 曲库只有几千张 7K 谱，可能过拟合到 Kai 收藏的风格；用验证集损失和留出歌盯着，步数宁少勿多。
- 用了模型已经见过的 ranked 图，帮助有限；真正的新信息来自段位包、loved 和未 rank 的谱。
- 训练期间 meow 的显卡被占满，dashboard 的生成任务会很慢。
