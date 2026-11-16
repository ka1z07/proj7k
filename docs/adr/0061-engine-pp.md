# 0061. 引擎 pp：osu!mania 的 pp 公式，星级换成引擎星级

- **状态**：proposed（2026-11-16；Kai：「增加一个计算当前引擎难度下 pp 的功能，登录后计算并显示引擎难度算法下的 top 100 plays 和 7k 总 pp」）。不动引擎、不动星级、不动任何标定。
- **背景**：ADR-0041 之后控制台能用玩家自己的 osu! 令牌读官方数据，主页显示官方 7K pp 与排名。官方 pp 建在官方星级上，而本工具的难度是引擎星级（ADR-0017/0018，按锦锦段位标定，高段位比官方高：Stellium RC 12.28 vs Sunny 10.25）。玩家想知道：按引擎的难度，自己最值钱的 100 局是哪些、7K 一共值多少。

## 决定

1. **公式照搬 osu!mania 的 `ManiaPerformanceCalculator`，只换星级这一个输入**（`proj7k.enginepp`）：
   $$pp = 8\cdot\max(\star-0.15,\ 0.05)^{2.2}\cdot\max(0,\ 5\,acc-4)\cdot\bigl(1+0.1\min(1,\ hits/1500)\bigr)\cdot m$$
   - $\star$ 是引擎总星级，**按实际打的那张谱**：DT/HT/自定义速度下是把歌按倍率压缩后的谱（与游玩记录同一个 `ChartStars.rated`）。
   - $acc$ 是 osu! 的 pp 准确率，不是成绩显示的准确率：stable 成绩（带 Classic/`CL`，或 API 给了 `legacy_score_id`）MAX 记 320，lazer 成绩记 305（ppy/osu#25487），其余 300/200/100/50/0，分母是全部判定数。
   - $hits$ 是判定总数（lazer 的长条头尾各判一次，照官方原样用成绩里的统计）。
   - $m$：NF ×0.75、EZ ×0.5。其余 Mod 不乘。
   - 为什么不重新拟一条曲线：Kai 要的是「引擎难度下的 pp」，保留官方的形状，玩家对 pp 的直觉（准确率 80% 以下不给分、长度加成、加权方式）都还成立，差别只来自难度本身；曲线常数是官方按官方星级定的，在引擎星级上不再「校准」，因此引擎 pp 只和引擎 pp 比，页面上明说。
2. **总 pp 是官方加权**：每张谱只算最值钱的一局，排序后第 $i$ 局（从 0 起）乘 $0.95^i$，最多 100 局。官方按 ranked 成绩数给的 bonus pp 不算（官方的分键数 7K pp 也没有它）。
3. **成绩两路来源合并**（`proj7k.dashboard.topplays`）：
   - **osu!**：`GET /api/v2/users/{id}/scores/best?mode=mania`（lazer 成绩格式，`x-api-version`），两页共 200 个，按官方 pp 排；只留 7K mania 原生谱（不要 convert）。谱面文件先用本机 osu!lazer 曲库里的那份（按 MD5），没有就从 `osu.ppy.sh/osu/{beatmap_id}` 下一次，存到控制台输出目录的 `osu_files/`，MD5 对不上才重下。
   - **本机**：osu!lazer 里同一账号（按 user id，没有 id 时按名字）的通关 7K 成绩。osu! 的 best 只到 200 且按官方 pp 排，一张官方星低、引擎星高的谱可能根本进不去；本机成绩把它补回来。同一个成绩两边都有（本机 `online_id` = 线上 score id）时只算一次，标「osu! + 本机」。
   - 改变谱面本身的 Mod（Random、换键数、Invert、Hold Off、Dual Stages、Autoplay 等，`profiler.mods.UNSUPPORTED_ACRONYMS`）的成绩不算：引擎读的是原谱文件。
4. **默认只算 ranked/approved 谱**（官方给 pp 的谱：API `status`，lazer `BeatmapOnlineStatus` 1/2）；页面上可以切到「也算非 ranked 谱面」（loved、qualified、未上架、自制谱、降阶练习谱）。
5. **界面**：控制台（即桌面端窗口）新页「引擎 pp」`/pp`，侧栏「玩家」组，主页登录行加一个入口按钮。未登录时只给登录按钮。页面显示引擎 7K 总 pp、官方 7K pp 对照、计入的谱数与来源，前 100 局每局列引擎星级、引擎 pp、加权后计入的部分与官方 pp。接口 `GET /api/me/enginepp`（`/refresh` 强制重算，`?scope=all`）。
6. **在后台算**：第一次要下载谱面、逐张跑引擎，可能要一两分钟；接口立刻回答上次的结果与进度，页面每 1.5 秒问一次直到算完。结果在内存里，10 分钟后再问会重算；换账号就作废。下载的谱面与无 Mod 的引擎读数持久化（`chart_stars.json` 里以 `osu:<md5>` 为键），变速读数只在内存。

## 后果与已知局限

- **覆盖不全**：osu! API 不给一个玩家的全部成绩，只给 best 200（按官方 pp）。从没上传过、又不在这台电脑上的成绩（例如别的电脑上打的、stable 时代的老成绩但官方 pp 低）看不到。逐谱查询 `/beatmaps/{id}/scores/users/{id}` 能补，但上千张谱要上千次请求，先不做。
- 官方 pp 与引擎 pp 并排显示，便于看哪些谱引擎认为被低估/高估；两者不是一把尺子。
- 云端容器连不上 osu!，真实账号上的结果要在 meow 上验证；测试用假账号与假曲库钉住公式、合并、去重与范围过滤。
- 不进引擎版本号，不需要重同步、重摄入。
