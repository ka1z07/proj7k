# 0043. 控制台任务按资源并行，并证明不会把数据库写坏

- **状态**：accepted（2026-10-23；Kai 问「不同 tools 的 jobs 可以并行运行吗？」，看完方案后说「可以试试，做完方案必须详细测试，以及形式化证明当前设计不存在将数据库写坏」）。替代 ADR-0024 第 3 条的「单个工作线程按提交顺序执行」。
- **背景**：ADR-0024 让所有任务排在一条队列里，一次跑一个，理由是同步和降阶的「加入收藏夹」都会写 lazer 库，串行就不会撞车。代价是一个 Mapperatorinator 生成任务（4–16 分钟，ADR-0040）会挡住后面所有降难、回放诊断和导入，而它们和生成根本不碰同一份数据。

## 决策

1. **资源。** 共有四种（`proj7k.claims`）：
   - `lazer`：osu!lazer 曲库，包括 client.realm、proj7k 给它做的备份（快照、原始状态 `lazer_backup_state.json`）、收藏夹，以及游戏进程本身（关闭、启动）。
   - `profiler`：profiler 数据库（`~/.proj7k/profiler.db`），以及写在它旁边的回放查看器和练习包。
   - `downscaler`：降难的输出文件夹。
   - `generator`：生成器用的 GPU（Mapperatorinator、Demucs）、它们的安装目录和缓存，以及生成器的输出文件夹。
2. **每种任务提交时声明要占用的资源**（`actions.JOB_RESOURCES`、`actions.job_resources`）：

   | 任务 | 占用 |
   |---|---|
   | `sync_setup`、`sync_once`（包括试运行，它会写评估缓存）、`sync_revert`、`lazer_control` | `lazer` |
   | `downscale` | `downscaler`；勾了「写入 lazer」（`sync_lazer`）时再加 `lazer` |
   | `generate` | `generator` |
   | `profile_replay`、`profile_player`、`profile_habits`、`import_replays` | `profiler` |
   | 表里没有的任务 | 全部四种（新加的任务在登记之前只能单独跑） |

   读 lazer 库不算占用：bridge 每次读都以只读方式打开 Realm。
3. **调度规则**（`jobs.Scheduler`，没有自己的线程，所有调用都在 `JobManager` 的一把锁里）。排队中的任务 j 满足以下三条时开始运行：(a) 正在运行的任务都没有占用 j 的任何资源；(b) 排在 j 前面、还在等待的任务都不需要 j 的任何资源；(c) 正在运行的任务少于 `MAX_PARALLEL_JOBS`（4）个。所以共用资源的任务一个一个跑，按提交顺序开始；不共用资源的任务同时跑。一个任务只会挡住排在它后面、和它共用资源的任务。
4. **写入的地方检查声明**（`claims.require`）。每个写共享存储的函数在动手之前先调用 `require(资源)`：如果当前线程正在跑一个任务，而这个任务没有声明这项资源，就抛出 `ClaimError`，什么都不写。不在任务线程上（命令行、测试、控制台自己的读线程）时，这个调用什么都不做。检查点有：
   - lazer：`RealmBridgeClient.apply_batch_update`、`revert_batch`，`LazerBackupManager.create_realm_snapshot`、`record_original_states`、`clear_backup_states`，`LazerClient.close`、`start`（`restart` 先 close 再 start）。
   - profiler：`ProfilerStorage.save_snapshot`、`delete_replay`。
5. **bridge 安装加锁。** 每次调用 bridge 都会先检查 Node 依赖装好没有，没装就跑 `npm install`。任务和控制台的曲库读线程可能同时发现没装，所以安装放在一把进程内的锁里，并在锁里再检查一次。这样只会装一次，两个 `npm install` 不会同时写同一个 node_modules。
6. 没有资源函数的 `JobManager` 让所有任务都占用同一个 `SERIAL` 资源，行为和以前一样，一次跑一个。

## 证明：两个任务不会同时写同一份数据

### 模型

- 资源集合 𝓡 = {lazer, profiler, downscaler, generator}。任务 j 有声明 C(j) ⊆ 𝓡，在提交时由 `job_resources(kind, params)` 算出，之后不再改变。
- 任务 j 在运行中写到的存储集合记为 W(j) ⊆ 𝓡。写 client.realm、它的备份或收藏夹，或者关闭、启动游戏，都算写 lazer；写 profiler.db 算写 profiler。
- 调度器的状态是一个等待队列 Q（有序，元素是 (id, C)）和一个运行集合 R（id → C）。改变状态的操作有：enqueue、remove（取消排队中的任务）、finish（运行结束，成功、失败或停止都算）、start_ready。
- 执行：`JobManager` 只在持有 `_lock` 时改调度器（submit、cancel、_dispatch、_run 的 finally、shutdown）。因此所有操作构成一条全序序列，下面的不变式只需要对每个操作逐一验证。

**定理。** 在任一时刻，如果任务 j ≠ k 都在执行它们的 action，那么 W(j) ∩ W(k) = ∅。

由下面的引理 1（运行中的任务声明互不相交）和引理 2（W(j) ⊆ C(j)）可得：W(j) ∩ W(k) ⊆ C(j) ∩ C(k) = ∅。∎

### 引理 1（互斥）：任一时刻，R 中任两个任务的声明互不相交；正在执行 action 的任务都在 R 中。

*第一部分，用不变式 I：R 中的声明两两不相交。*
- 初始时 R = ∅，I 成立。
- enqueue、remove 不改 R。finish 只从 R 中删除元素，两两不相交的集合删掉一个仍然两两不相交。
- start_ready：设 busy₀ = ⋃R。函数按队列顺序扫描，busy 一开始是 busy₀，每扫过一个任务（不管它开没开始）就并上它的声明。任务 j 只在 C(j) ∩ busy = ∅ 时加入 R。这时 busy 包含原来 R 里所有任务的声明，也包含这次已经加入 R 的所有任务的声明，所以 C(j) 和它们都不相交。I 保持。

*第二部分。* 只有 `_dispatch` 会把 `_run` 交给线程池，交出去的只有 `start_ready` 刚放进 R 的任务（状态在锁里改成 running）。`_run` 在 action 返回或抛出之后，才在 finally 里调用 finish。所以从 action 开始执行到它结束，这个任务一直在 R 中。取消一个任务时，它要么还在 Q 里，被 remove 并标成 cancelled，从此不会开始；要么已经在 R 里，只会收到停止标志。这两种情况都在同一把锁里判定，不存在「已取消但又开始运行」的交错。∎

### 引理 2（写入必在声明之内）：W(j) ⊆ C(j)。

1. **写操作的入口是固定的几处**（由测试从源码逐项审计，见下面「机械检查」第 2 部分）：
   - client.realm 只有 bridge.js 的 `handleUpdateBatch` 和 `handleRevertBatch` 会以可写方式打开（`readOnly: false`，`realm.write`）；其他处理函数打开的 Realm 都是 `readOnly: true`。能触发这两个处理函数的只有 `update-batch`、`revert-batch` 两条命令，Python 里只有 `apply_batch_update` 和 `revert_batch` 会发送它们；包里没有其他代码运行 bridge.js，也没有人调用 `_run_command`。
   - lazer 的备份文件只有 `LazerBackupManager` 里的函数会写，凡是复制、删除、替换或以写模式打开文件的函数，都先调用 `require(LAZER)`。
   - 关闭或启动游戏只能通过 `LazerClient.close` / `start`。
   - profiler.db 只由 `ProfilerStorage` 打开（包里另外两处 sqlite 连接是线上谱面库自己的数据库，以及生成器评测以 immutable 方式读它）。数据写语句（INSERT、DELETE、UPDATE、REPLACE）只出现在 `save_snapshot`、`delete_replay` 和建表相关的三个函数里。建表函数的情况见下面的「前提」。
2. 上面每个入口都先调用 `require(r)`。action 在 `claims.holding(C(j))` 里执行，声明记在当前线程上。如果 r ∉ C(j)，`require` 在写之前就抛出异常，写入不会发生。
3. **action 的代码都在任务自己的线程上运行。** 包里除了 `jobs.py`、`player.py` 和 `batch.py`（批量评测，不经过控制台），没有代码创建线程或线程池（有测试检查），所以 action 不会把写入交给别的线程，绕过声明。
4. **声明和实际行为用的是同一份参数。** 提交后，参数只有上传文件的那几项会变：执行时由文件名换成任务文件夹里的路径。路径里一定有路径分隔符，`_flag` 永远把它读成「未设置」。所以执行时如果 `sync_lazer` 为真，提交时它一定也为真，C(j) 里已经有 lazer。`job_resources` 与 action 用同一个 `_flag` 读这个开关（有测试逐值对照）。

由 1–4：任务 j 每写一次存储 r，r 都在 C(j) 里。∎

### 引理 3（顺序）：共用资源的两个任务，按提交顺序开始。

设 j 比 k 先提交，C(j) ∩ C(k) ≠ ∅，并且 j 没有在开始前被取消。在 k 开始的那次 start_ready 里，如果 j 还在队列中，它排在 k 前面，扫到 k 时 busy 已经包含 C(j)，所以 k 不会开始。因此 k 开始时，j 已经开始了。∎

### 引理 4（不会卡死）：如果每个 action 都会在有限时间内结束，每个排队的任务最终都会开始。

- 每次操作之后，管理器都会调用 start_ready。调用之后，如果 Q 非空，那么 R 也非空：否则 busy₀ = ∅，运行数是 0，队首任务可以开始，与「它还在排队」矛盾。
- 对队列中的位置做归纳。任务 k 只会被两类任务挡住：R 中的任务，以及 Q 中排在它前面的任务。后来提交的任务排在它后面，挡不住它（规则 (b) 只看前面的任务）。R 中的任务在有限时间内结束。按归纳假设，前面的任务也会在有限时间内开始，并随后结束。等它们都开始并结束以后，k 没有任何阻挡，在下一次 start_ready 时开始。∎

注意：引理 4 依赖于 action 会结束。运行中的任务目前还是不能中途停掉（ADR-0024 以来一直如此），卡住的任务会一直挡住和它共用资源的后续任务，但不影响其他资源上的任务。

### 机械检查（`tests/test_dashboard_scheduler.py`）

1. **穷举状态空间，检查的是真实的 `Scheduler` 类，不是另写的模型。** 从空状态开始做广度优先搜索。每一步做一个操作：提交一个声明为任意非空子集的任务、结束任一运行中的任务，或取消任一排队中的任务，然后调用 start_ready。状态按（队列中声明的序列，运行集合中声明的多重集）归并。调度器的行为只取决于这两样东西，与任务编号无关，所以归并不会漏掉情况。在每个可达状态上检查：运行中的声明两两不相交（安全）；运行数不超过上限（容量）；新开始的任务前面没有和它共用资源、仍在等待的任务（顺序）；每个等待中的任务确实被容量、运行中的任务或前面的任务挡住（不空转）；Q 非空时 R 非空（无死锁）；不再提交新任务时，依次结束运行中的任务能清空队列（活性）。
   - 默认规模：3 种资源（7 种声明），队列最多 4 个，上限 1/2/3：分别有 19,608 / 29,598 / 30,389 个状态，约 11 万到 20 万次转移，全部通过。
   - 2026-10-23 另外跑过更大的规模：3 种资源、队列最多 5 个（约 21 万个状态，150 多万次转移，上限 1–4），以及 4 种资源、队列最多 4 个、上限 1 / 2 / 3（813,616 / 1,918,485 / 2,285,275 个状态，共 3,300 多万次转移），全部通过。设置 `PROJ7K_DEEP_MODEL_CHECK=1` 可以重跑 4 种资源的规模。
2. **写入入口审计**（引理 2 第 1、3 点）：解析 bridge.js 的处理函数、bridge.py / backup.py / storage.py / client.py 的语法树，以及全包的 sqlite 连接和线程创建。新增一个绕开 `require` 的写入入口，测试就会失败。
3. **运行时检查**：声明不对的任务写 profiler.db，任务失败，数据库保持为空；声明正确时写入成功。持有其他三种资源时调用每个 lazer 写入入口，都会抛出 `ClaimError`，不会产生快照或备份文件。
4. **真实线程**：三个不同资源的任务在一个 Barrier 上会合（说明它们真的同时在跑）；同一资源的任务峰值并发为 1，并按提交顺序执行；80 个随机声明的任务混合随机失败与取消，检查没有两个任务同时占用同一资源、共用资源的任务按提交顺序开始、并发峰值大于 1 且不超过上限；取消一个等待中的任务后，被它挡住的任务会开始；并行任务的日志和界面语言不会串到别的任务；关机时排队中的任务标成已取消；四个线程同时要求安装 bridge 时，只会跑一次 npm。

### 前提与不在范围内的情况

- **osu!lazer 本身。** 游戏运行时自己会写 client.realm。proj7k 的写入照旧走已有的安全写入窗口（`lazer.lock.SafeFlushWindow`），游戏占着数据库锁时不写，这一部分与本 ADR 无关、没有改动。本 ADR 证明的是 proj7k 的任务之间不会撞车。
- **读。** 读不加锁：bridge 以只读方式打开 Realm，读的一方不会改动文件，最坏情况是读到写入之前的数据或读取失败。控制台自己的读线程（玩家主页、曲库）从 ADR-0034 起就一直和任务并行，没有变化。
- **profiler.db 的建表语句。** 每次打开连接（包括只读的页面）都会执行 `CREATE TABLE/INDEX IF NOT EXISTS`、设置 WAL，并在唯一索引第一次建立前做一次去重。这些操作是幂等的，各自在 SQLite 事务里完成，并发打开时由 SQLite 自己的文件锁排队，不会互相破坏。快照表的 `replay_hash` 有唯一索引，`save_snapshot` 用 `ON CONFLICT DO NOTHING`，即使有两个写入方也不会产生重复的回放。
- **另一个进程。** 同时开命令行（例如 `proj7k.sync`）或第二个控制台，不在本证明的范围内，和以前一样。
- **CPU。** 降难、诊断和生成可以同时跑，会争 CPU。生成的主要负载在 GPU 上，影响有限。

## 后果

- 生成谱面时可以同时降难、诊断回放、导入回放。只有占用相同资源的任务才会排队，「任务」页上显示为排队中。任务数据里多了 `resources` 字段。
- 新加一种任务时，要在 `JOB_RESOURCES` 里登记它的声明（有测试检查每种任务都已登记）；不登记的任务会占用全部资源、单独运行。新加一个写共享存储的函数时，要先调用 `claims.require`，否则审计测试会失败。
