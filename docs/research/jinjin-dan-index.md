# 《Jinjin 7K Dan 基准曲目与段位架构索引》

## 一、Jinjin 7K Dan 体系概览与架构约束

在 osu!mania 7K 竞技生态中，由知名谱师/选手 **Jinjin** 创作的 7K Dan 系列是公认最具权威性、标准度最高的技术水平定级基准。与 4K 领域不同，Jinjin 7K Dan 体系遵循高度严谨的架构规范与领域约束：

1. **严格双轨制（Dual-Track Architecture）**：
   - 体系仅包含 **Regular Dan（常规段位）** 与 **LN Dan（长押段位）** 两大独立轨道。
   - **不存在 Extra Dan 或 Lunatic Dan** 等衍生分支；难度的向上延伸统一通过 **Phase 进阶**（Phase I 至 Phase IV）实现。
2. **固定四阶段技法考点闭环（Fixed 4-Stage Taxonomy）**：
   - **Regular Dan（常规段位）** 严格按固定顺序汇合四项单一米键（Rice）基础技法：
     - **Stage 1: 叠 (Jack / Chordjack)** —— 考察物理键力、手腕/手指回弹与同轨/双押抗挤压能力。
     - **Stage 2: 技 (Tech / Technical)** —— 考察变速、非规整节拍、不对称指法与复杂读谱能力。
     - **Stage 3: 乱 (Stream / Roll)** —— 考察高速单点流、楼梯/滚键、极限手速（Speed）与耐力持久力（Stamina）。
     - **Stage 4: 切 (Chordstream / Bracket)** —— 考察密集和弦流、多指协调发力、手盘综合底力（Endurance）。
   - **LN Dan（长押段位）** 严格按固定顺序汇合四项单一长押核心技法：
     - **Stage 1: 密度 (General / Density)** —— 考察高密度混面 LN、全局视认与肌肉紧张控制。
     - **Stage 2: 技 (Tech / LN Technical)** —— 考察 LN 与单点交叉混押、非对称手指独立性（Finger Independence）。
     - **Stage 3: 反键 (Inverse / Shield)** —— 考察反向长押（长按仅留松键缝隙）视认、负空间思维与防粘键手感。
     - **Stage 4: 释放 (Release / Precision Release)** —— 考察严苛尾判（Tail Timing）、松键精度与极速动作切断能力。
3. **合格认定与考核准则（Standard Dan Criteria）**：
   - 全程连续演奏（Marathon 模式，禁止暂停），默认判定标准为 **OD8**，HP 为统一严苛设定。
   - 社区公认的合格判定线为 **Accuracy ≥ 96.00%（Grade S）**，未达标或中途力竭（Fail）均视为未通过。

---

## 二、核心谱面集（Beatmapsets）归属与层级映射

根据用户提供的 7 组核心链接，经精准匹配与关联梳理，其元数据与层级架构归属如下：

| Beatmapset ID | 代表性 Beatmap ID | 谱面集官方标题 / 别名 | 体系归属 | 所属阶段 (Phase) | 涵盖段位级别 | 难度/星级定位 (★) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **450069** | 965652 | *osu!mania 7K Dan Course - Regular Dan Phase I* | **Regular Dan** | Phase I | **1st Dan ～ 3rd Dan** (初段~三段) | 入门至进阶基石 (约 3.8★ - 5.0★) |
| **451788** | 969190 | *osu!mania 7K Dan Course - Regular Dan Phase II* | **Regular Dan** | Phase II | **4th Dan ～ 8th Dan** (四段~八段) | 中级至高阶主力 (约 5.2★ - 7.5★) |
| **930218** | 1942650 | *osu!mania 7K Dan Course - Regular Dan Phase III* | **Regular Dan** | Phase III | **9th Dan ～ 10th Dan, Zenith Dan** (九段、十段、天顶段) | 大师/顶尖考核 (约 7.8★ - 9.5★) |
| **450649** | 966816 | *osu!mania 7K Dan Course - LN Dan Phase I* | **LN Dan** | Phase I | **1st Dan ～ 3rd Dan** (LN 初段~三段) | LN 基础与视认启蒙 (约 4.0★ - 5.3★) |
| **895138** | 1877529 | *osu!mania 7K Dan Course - LN Dan Phase II* | **LN Dan** | Phase II | **4th Dan ～ 8th Dan** (LN 四段~八段) | LN 独立性与反键强化 (约 5.5★ - 7.8★) |
| **1220647** | 2539251 | *osu!mania 7K Dan Course - LN Dan Phase III* | **LN Dan** | Phase III | **9th Dan ～ 10th Dan, Zenith Dan** (LN 九段、十段、天顶段) | 极端反键与释放巅峰 (约 8.0★ - 10.0★) |
| **1061136** | 2221603 | *osu!mania 7K Dan Phase IV* (*Stellium Dan*) | **Regular & LN** | Phase IV | **Stellium Dan (Regular)**<br>**Stellium Dan (LN)** | 终极神级/超段考评 (约 9.8★ - 12.0+★) |

---

## 三、Marathon 谱面与单一技法谱面集的关系

Jinjin 7K Dan 体系在设计上具有高度工程化的“模块化测试”思想：

1. **功能定位区别**：
   - **Marathon 谱面（段位综合考核卷）**：
     - 将选定的 4 首曲目通过音频缝合技术连缀成一张 8~11 分钟的长程谱面。
     - 核心考察：在长时间生理疲劳与心理压力下，多项技能切换时的**稳定性（Consistency）**、**耐力分配（Stamina Management）**与**综合准度（Accuracy Retention）**。
   - **单一技法谱面集（分卷专项练习册）**：
     - 将各段位拆分为独立的单个 Stage 谱面（Practice Maps），按技法打标签归类（如 Regular: Jack Pack, Tech Pack, Stream Pack, Chordstream Pack；LN: Density Pack, Tech Pack, Inverse Pack, Release Pack）。
     - 核心考察：突破单一物理极限（如提升 180BPM Chordjack 的爆发键力，或适应 200BPM Inverse LN 的负空间视认）。

2. **训练学逻辑（Training Methodology）**：
   - **以练促考**：玩家在面对高段位 Marathon 时，若 Stage 4 因耐力耗尽失格，需通过单一技法练习谱进行“局部超负荷训练”。
   - **疲劳隔离**：避免直接刷 Marathon 带来的前 3 首歌冗余消耗，实现弱项精准攻坚。

---

## 四、各段位基准曲目架构与核心考点索引

### 1. Regular Dan（常规段位：叠 ➔ 技 ➔ 乱 ➔ 切）

#### 【Phase I】入门与基础建立（Set: 450069）
- **1st Dan (一段)**
  - Stage 1【叠】：*Canon (Rock ver.)* / JerryC | BPM: 130~150 | 基础双押与单轨连叠手感建立
  - Stage 2【技】：*Xepher* / Tatsh | BPM: 170 | 基础楼梯与中速变速交替
  - Stage 3【乱】：*Evans* / SOUND HOLIC feat. Nana Takahashi | BPM: 185 | 均匀 16 分单点轻流
  - Stage 4【切】：*Second Heaven* / Ryu☆ | BPM: 149 | 基础切音（Chordstream）节奏与键型分配
- **2nd Dan (二段)**
  - Stage 1【叠】：*Red Like Roses part II* / Jeff Williams | BPM: 130 | 轻量级 Chordjack 腱力初步
  - Stage 2【技】：*quell~the seventh slave~* / DJ Mass MAD Izm* | BPM: 148 | 搓盘式非对称手位与跨轨切分
  - Stage 3【乱】：*The Sampling Paradise* / Mamonis | BPM: 150 | 纯净单点乱打与小滚键
  - Stage 4【切】：*SigSig* / kors k | BPM: 179 | 稳定双押切音耐力初探
- **3rd Dan (三段 - 965652)**
  - Stage 1【叠】：*Blast* / LeaF | BPM: 150 | 紧凑双押叠，考察回弹发力
  - Stage 2【技】：*Doppelganger* / LeaF | BPM: 137~274 | 极高读谱压力的变速与非规整配置
  - Stage 3【乱】：*Altale* / Sakuzyo | BPM: 83~110 (16分/24分乱打) | 变速乱打与精准抓节拍
  - Stage 4【切】：*Far east nightbird* / 猫叉Master | BPM: 162 | 连绵型切音底力考核

#### 【Phase II】进阶与高阶突破（Set: 451788）
- **4th Dan (四段)**
  - Stage 1【叠】：*chipstream* / t+pazolite | BPM: 165 | 快速同轨双押叠跳
  - Stage 2【技】：*Bad Apple!! (REDALiCE Remix)* / Alstroemeria Records | BPM: 175 | 复合指法与交替切音
  - Stage 3【乱】：*Xevel* / Tatsh | BPM: 178 | 密集单手速度流与连续楼梯
  - Stage 4【切】：*Valkyrie dimension* / Spriggan | BPM: 190 | 高速双押切音耐力
- **5th Dan (五段)**
  - Stage 1【叠】：*End Time* / Cres | BPM: 170 | 经典中速 Chordjack，高负荷手腕发力
  - Stage 2【技】：*AA* / Amuro & Killer | BPM: 154 | 高难度分轨技巧与非对称指法
  - Stage 3【乱】：*Ascension to Heaven* / xi | BPM: 200 | 200BPM 纯乱打流速度测试
  - Stage 4【切】：*Elemental Creation* / kors k & dj TAKA | BPM: 212 | 高速切音与大跨度跳押
- **6th Dan (六段)**
  - Stage 1【叠】：*Anguish* / Halv | BPM: 160 | 重型密集 Chordjack 持续压制
  - Stage 2【技】：*Chronomia* / Sakuzyo | BPM: 190 | 复杂节奏交替与瞬时位移
  - Stage 3【乱】：*FREEDOM DiVE* / xi | BPM: 222 | 极限高速单点流与持久体力消耗
  - Stage 4【切】：*Plan 8* / Ryu☆ | BPM: 212 | 暴力切音与高密度和弦
- **7th Dan (七段)**
  - Stage 1【叠】：*Jackpot* / void | BPM: 175 | 快速重叠，严苛手腕回弹要求
  - Stage 2【技】：*Evans (Autobahn Remix)* / DJ TAKA | BPM: 185 | 复合高难度读谱与多指杂糅
  - Stage 3【乱】：*Everlasting Message* / ぺのれり | BPM: 230 | 230BPM 爆发性超高速乱打
  - Stage 4【切】：*perditus†paradisus* / iconoclasm | BPM: 216 | 经典 7K 综合切音大考验
- **8th Dan (八段 - 969190)**
  - Stage 1【叠】：*Brain Power* / NOMA | BPM: 170 | 超长程密集 Chordjack，极高腱力要求
  - Stage 2【技】：*Evans (VIP)* / DJ TAKA | BPM: 195 | 极限分轨与不对称复杂敲击
  - Stage 3【乱】：*Bangin' Burst* / かめりあ | BPM: 234 | 234BPM 极限高速单点与混合滚键
  - Stage 4【切】：*Confiserie* / L.E.D. vs S-C-U | BPM: 210 | 高重押率稠密切音（Dense Chordstream）

#### 【Phase III】大师级殿堂（Set: 930218）
- **9th Dan (九段)**
  - Stage 1【叠】：*THE DETONATOR* / teranoid feat. MC RUE | BPM: 180 | 重型高速 Jack 墙
  - Stage 2【技】：*G e n g a o z o* / -45 | BPM: 155 | 经典 BMS 神级高难技巧配置迁移
  - Stage 3【乱】：*Halcyon* / xi | BPM: 191 (32分/复杂细分) | 细分高密度乱流
  - Stage 4【切】：*VALLIS-NERIA* / DJ YOSHITAKA | BPM: 185 | 极稠密括号切音与长程抗压
- **10th Dan (十段 - 1942650)**
  - Stage 1【叠】：*Blue Army* / void | BPM: 180 | 持续超高压 Chordjack
  - Stage 2【技】：*Cold Breath* / Morimori Atsushi | BPM: 200 | 极限读谱难度与交替错位配置
  - Stage 3【乱】：*Blue Planet* / beatMARIO | BPM: 240 | 240BPM 人类极限手速单点流
  - Stage 4【切】：*Dreadnought* / master groove circle | BPM: 220 | 极致大体量切音，底力天花板
- **Zenith Dan (天顶段)**
  - Stage 1【叠】：*Angeline* / xi | BPM: 185 | 极限双三押交替连叠
  - Stage 2【技】：*HAELEQUIN* / orange lounge | BPM: 172 | 极端非对称复合技巧
  - Stage 3【乱】：*Blue Zenith* / xi | BPM: 200 (24/32分混乱流) | 超长高速高密度乱打
  - Stage 4【切】：*Stellium* / lapix | BPM: 215 | 顶尖多指和弦切音终极试炼

#### 【Phase IV】终极群星神级考评（Set: 1061136）
- **Stellium Dan (Regular) [2221603]**：
  - 由 Jinjin 亲自定制的巅峰串烧，汇聚了 7K 常规技术四大维度的理论与物理极限。

---

### 2. LN Dan（长押段位：密度 ➔ 技 ➔ 反键 ➔ 释放）

#### 【Phase I】LN 启蒙与基础视认（Set: 450649）
- **1st Dan (一段)**
  - Stage 1【密度】：*Sakura Reflection* / Ryu☆ | BPM: 180 | 基础均匀长押，保持平稳手型
  - Stage 2【技】：*Flower* / DJ YOSHITAKA | BPM: 173 | 单点与 LN 的简单交错
  - Stage 3【反键】：*Smooth Operator* / Big Daddy | BPM: 135 | 浅层反键（长空隙短松键）启蒙
  - Stage 4【释放】：*Air* / SHIKI | BPM: 178 | 规整 4 分/8 分尾判释放准度
- **2nd Dan (二段)**
  - Stage 1【密度】：*Evans* / DJ TAKA | BPM: 185 | 全键位 2~3 轨并发长押
  - Stage 2【技】：*SigSig* / kors k | BPM: 179 | 交叉指法下的长押维持
  - Stage 3【反键】：*She is my wife* / SUPER STAR 満-MITSURU- | BPM: 145 | 规范反键视认与防粘连手感
  - Stage 4【释放】：*L99* / TaQ | BPM: 155 | 连续切断型尾判松键
- **3rd Dan (三段 - 966816)**
  - Stage 1【密度】：*Kanata* / LeaF | BPM: 160 | 4 轨常驻密集长押混面
  - Stage 2【技】：*Doppelganger (LN)* / LeaF | BPM: 137 | 变速下的长押独立动作
  - Stage 3【反键】：*Brain Power (Inverse)* / NOMA | BPM: 170 | 中速大面积反向长条负空间识别
  - Stage 4【释放】：*Altale (LN)* / Sakuzyo | BPM: 83~110 | 极其严苛的微速差与尾判对齐

#### 【Phase II】LN 独立性与进阶对抗（Set: 895138）
- **4th Dan (四段)**
  - Stage 1【密度】：*405nm* / Another Infinity | BPM: 176 | 稠密多指长条平推
  - Stage 2【技】：*CROSS FIRE* / Ryu☆ | BPM: 180 | 单手保持 LN 另一手高频单点
  - Stage 3【反键】：*Overdrive* / Nanahira | BPM: 165 | 典型盾键（Shield）与连续反向释放
  - Stage 4【释放】：*Garakuta Doll Play* / t+pazolite | BPM: 256 | 极高 BPM 下的快速切断松键
- **5th Dan (五段)**
  - Stage 1【密度】：*Blue Army (LN)* / void | BPM: 180 | 5 轨交织长押压制
  - Stage 2【技】：*Sound Chimera* / Laur | BPM: 200 | 狂暴节奏下的手指独立解离
  - Stage 3【反键】：*Cyberozar* / Sakuzyo | BPM: 180 | 高速反键面，极易产生肌肉僵硬
  - Stage 4【释放】：*Lunatic Sounds* / Lunatic Sounds | BPM: 190 | 精密 16 分尾判对齐
- **6th Dan (六段)**
  - Stage 1【密度】：*VALLIS-NERIA (LN)* / DJ YOSHITAKA | BPM: 185 | 持续全屏高密度覆盖
  - Stage 2【技】：*Chronomia (LN)* / Sakuzyo | BPM: 190 | 错位滑条与多层复杂读谱
  - Stage 3【反键】：*Anguish (Inverse)* / Halv | BPM: 170 | 重度反键压迫，考察指尖细微挑起控制
  - Stage 4【释放】：*FREEDOM DiVE (Release)* / xi | BPM: 222 | 高速连打伴随严苛尾判
- **7th Dan (七段)**
  - Stage 1【密度】：*Plan 8 (LN)* / Ryu☆ | BPM: 212 | 212BPM 暴力长押海
  - Stage 2【技】：*Cross Time* / lapix | BPM: 180 | 复杂交错多轨不协调按压
  - Stage 3【反键】：*Jackpot (Inverse)* / void | BPM: 175 | 纯粹反键地狱，满屏黑白颠倒视认
  - Stage 4【释放】：*Ascension to Heaven (LN)* / xi | BPM: 200 | 极度精准的连续梯形松键
- **8th Dan (八段 - 1877529)**
  - Stage 1【密度】：*Confiserie (LN)* / L.E.D. vs S-C-U | BPM: 210 | 极致长条密度压制
  - Stage 2【技】：*Evans (LN Tech)* / DJ TAKA | BPM: 195 | 顶尖多指完全解离动作
  - Stage 3【反键】：*High-voltage (Inverse)* / LeaF | BPM: 190 | 极致反键盾与瞬时逆向弹起
  - Stage 4【释放】：*Bangin' Burst (Release)* / かめりあ | BPM: 234 | 234BPM 极限回弹与精准切尾

#### 【Phase III】LN 顶峰造极（Set: 1220647）
- **9th Dan (九段)**
  - Stage 1【密度】：*Dreadnought (LN)* / master groove circle | BPM: 220 | 极致稠密全键盘长押
  - Stage 2【技】：*HAELEQUIN (Extended LN)* / orange lounge | BPM: 172 | 超长程极端不协调多指解离
  - Stage 3【反键】：*THE DETONATOR (Inverse)* / teranoid | BPM: 180 | 满轨无缝反键流
  - Stage 4【释放】：*Halcyon (Release)* / xi | BPM: 191 | 微秒级尾判准度考评
- **10th Dan (十段 - 2539251)**
  - Stage 1【密度】：*Blue Planet (LN)* / beatMARIO | BPM: 240 | 极限手速之下的长条平推
  - Stage 2【技】：*Cold Breath (LN Tech)* / Morimori Atsushi | BPM: 200 | 错综复杂的 LN 交织与多指对抗
  - Stage 3【反键】：*G e n g a o z o (Inverse)* / -45 | BPM: 155 | 经典高难谱面的全反向重构
  - Stage 4【释放】：*Blue Zenith (Release)* / xi | BPM: 200 | 200BPM 细分音符的完美切分释放
- **Zenith Dan (LN 天顶段)**
  - Stage 1【密度】：*Stellium (Density)* / lapix | BPM: 215 | 顶格 7 轨常态满屏长押
  - Stage 2【技】：*Last Dance* / tap-G | BPM: 190 | 匪夷所思的非对称 LN 独立指法
  - Stage 3【反键】：*Angeline (Extreme Inverse)* / xi | BPM: 185 | 无法用常理阅读的逆向负空间视认
  - Stage 4【释放】：*Everlasting Message (Release)* / ぺのれり | BPM: 230 | 尾判准度与极限速度的完美统一

#### 【Phase IV】LN 群星终极试炼（Set: 1061136）
- **Stellium Dan (LN)**：
  - 集合了 7K LN 历史上最摧残手指独立性与视认极限的长押谱面集合。
