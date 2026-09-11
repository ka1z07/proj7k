# 《Jinjin 7K Dan 基准曲目与段位架构索引》

## 一、Jinjin 7K Dan 体系概览与作者规范

在 osu!mania 7K 生态中，由 **Jinjin** 制作的 7K Dan 体系是公认用于评估 7K 技术等级的核心基准。

### 1. 段位分类与通过标准（作者官方定义）

> "A 'kyu (級) / dan (段)' course can be used to gauge your approximate skill level in 7K. The Dan courses are separated into three types, Normal Kyu, Regular (Insane) Dan, and LN (Insane) Dan."

根据段位作者原文规范，通过各阶段考核的基准规则如下：
- **演奏要求**：必须在无暂停（without pauses）的情况下完成全曲演奏；仅允许在谱面之间的休息段（breaks in between maps）进行暂停。
- **准确率与评级门槛**：
  - **Normal Kyu (級)**：$\ge 95.00\%$ (Grade S)
  - **Regular (Insane) Dan (常规段位)**：$\ge 96.00\%$
  - **LN (Insane) Dan (长押段位)**：$\ge 95.00\%$ (Grade S)

### 2. 架构约束与进阶序列

1. **双轨进阶制**：
   - 核心考核分为 **Regular Dan** 与 **LN Dan**。
   - 不存在 Extra Dan 或 Lunatic Dan 补充包。难度的向上延伸通过 **Phase I 至 Phase IV** 进行阶梯进阶。
   - **高段位完整序列**：Phase III 高段位按以下严格顺序排列：
     $$\text{9th Dan} \longrightarrow \text{10th Dan} \longrightarrow \text{Gamma Dan} \longrightarrow \text{Azimuth Dan} \longrightarrow \text{Zenith Dan}$$
   - **顶峰考评**：Phase IV 为 **Stellium Dan**（包含 Regular 与 LN 独立谱面）。
2. **四阶段单一技法考点闭环**：
   - **Regular Dan** 固定四阶段技法序列：
     $$\text{Stage 1: 叠 (Jack / Chordjack)} \longrightarrow \text{Stage 2: 技 (Tech / Technical)} \longrightarrow \text{Stage 3: 乱 (Stream / Roll)} \longrightarrow \text{Stage 4: 切 (Chordstream / Bracket)}$$
   - **LN Dan** 固定四阶段长押技法序列：
     $$\text{Stage 1: 密度 (General / Density)} \longrightarrow \text{Stage 2: 技 (Tech / LN Technical)} \longrightarrow \text{Stage 3: 反键 (Inverse / Shield)} \longrightarrow \text{Stage 4: 释放 (Release / Precision Release)}$$

---

## 二、核心谱面集（Beatmapsets）归属与层级映射

| Beatmapset ID | 代表性 Beatmap ID | 谱面集官方标题 | 体系归属 | 所属阶段 (Phase) | 涵盖段位级别 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **450069** | 965652 | *osu!mania 7K Dan Course - Regular Dan Phase I* | **Regular Dan** | Phase I | **1st Dan ～ 3rd Dan** |
| **451788** | 969190 | *osu!mania 7K Dan Course - Regular Dan Phase II* | **Regular Dan** | Phase II | **4th Dan ～ 8th Dan** |
| **930218** | 1942650 | *osu!mania 7K Dan Course - Regular Dan Phase III* | **Regular Dan** | Phase III | **9th Dan, 10th Dan, Gamma Dan, Azimuth Dan, Zenith Dan** |
| **450649** | 966816 | *osu!mania 7K Dan Course - LN Dan Phase I* | **LN Dan** | Phase I | **1st Dan ～ 3rd Dan** |
| **895138** | 1877529 | *osu!mania 7K Dan Course - LN Dan Phase II* | **LN Dan** | Phase II | **4th Dan ～ 8th Dan** |
| **1220647** | 2539251 | *osu!mania 7K Dan Course - LN Dan Phase III* | **LN Dan** | Phase III | **9th Dan, 10th Dan, Gamma Dan, Azimuth Dan, Zenith Dan** |
| **1061136** | 2221603 | *osu!mania 7K Dan Phase IV* (*Stellium Dan*) | **Regular & LN** | Phase IV | **Stellium Dan (Regular), Stellium Dan (LN)** |

---

## 三、Marathon 谱面与单一技法谱面集的关系

1. **Marathon 谱面**：
   - 将选定的 4 首曲目连缀成一张 8~11 分钟的长程谱面，考察玩家在长程演奏中的稳定性与耐力分配。
2. **单一技法谱面集**：
   - 将各段位拆分为独立的单个 Stage 练习谱面，按对应技法（Regular: 叠、技、乱、切；LN: 密度、技、反键、释放）归类，供玩家单独练习。

---

## 四、各段位基准曲目索引（客观数据表）

> 注：以下列表仅记录谱面客观数据（Stage、曲名、艺术家、BPM）。移除所有非官方的推测性主观评语。

### 1. Regular Dan（常规段位：叠 ➔ 技 ➔ 乱 ➔ 切）

#### 【Phase I】（Set: 450069）
- **1st Dan**
  - Stage 1【叠】：*Canon (Rock ver.)* / JerryC | BPM: 130~150
  - Stage 2【技】：*Xepher* / Tatsh | BPM: 170
  - Stage 3【乱】：*Evans* / SOUND HOLIC feat. Nana Takahashi | BPM: 185
  - Stage 4【切】：*Second Heaven* / Ryu☆ | BPM: 149
- **2nd Dan**
  - Stage 1【叠】：*Red Like Roses part II* / Jeff Williams | BPM: 130
  - Stage 2【技】：*quell~the seventh slave~* / DJ Mass MAD Izm* | BPM: 148
  - Stage 3【乱】：*The Sampling Paradise* / Mamonis | BPM: 150
  - Stage 4【切】：*SigSig* / kors k | BPM: 179
- **3rd Dan**
  - Stage 1【叠】：*Blast* / LeaF | BPM: 150
  - Stage 2【技】：*Doppelganger* / LeaF | BPM: 137~274
  - Stage 3【乱】：*Altale* / Sakuzyo | BPM: 83~110
  - Stage 4【切】：*Far east nightbird* / 猫叉Master | BPM: 162

#### 【Phase II】（Set: 451788）
- **4th Dan**
  - Stage 1【叠】：*chipstream* / t+pazolite | BPM: 165
  - Stage 2【技】：*Bad Apple!! (REDALiCE Remix)* / Alstroemeria Records | BPM: 175
  - Stage 3【乱】：*Xevel* / Tatsh | BPM: 178
  - Stage 4【切】：*Valkyrie dimension* / Spriggan | BPM: 190
- **5th Dan**
  - Stage 1【叠】：*End Time* / Cres | BPM: 170
  - Stage 2【技】：*AA* / Amuro & Killer | BPM: 154
  - Stage 3【乱】：*Ascension to Heaven* / xi | BPM: 200
  - Stage 4【切】：*Elemental Creation* / kors k & dj TAKA | BPM: 212
- **6th Dan**
  - Stage 1【叠】：*Anguish* / Halv | BPM: 160
  - Stage 2【技】：*Chronomia* / Sakuzyo | BPM: 190
  - Stage 3【乱】：*FREEDOM DiVE* / xi | BPM: 222
  - Stage 4【切】：*Plan 8* / Ryu☆ | BPM: 212
- **7th Dan**
  - Stage 1【叠】：*Jackpot* / void | BPM: 175
  - Stage 2【技】：*Evans (Autobahn Remix)* / DJ TAKA | BPM: 185
  - Stage 3【乱】：*Everlasting Message* / ぺのれり | BPM: 230
  - Stage 4【切】：*perditus†paradisus* / iconoclasm | BPM: 216
- **8th Dan**
  - Stage 1【叠】：*Brain Power* / NOMA | BPM: 170
  - Stage 2【技】：*Evans (VIP)* / DJ TAKA | BPM: 195
  - Stage 3【乱】：*Bangin' Burst* / かめりあ | BPM: 234
  - Stage 4【切】：*Confiserie* / L.E.D. vs S-C-U | BPM: 210

#### 【Phase III】（Set: 930218）
- **9th Dan**
  - Stage 1【叠】：*THE DETONATOR* / teranoid feat. MC RUE | BPM: 180
  - Stage 2【技】：*G e n g a o z o* / -45 | BPM: 155
  - Stage 3【乱】：*Halcyon* / xi | BPM: 191
  - Stage 4【切】：*VALLIS-NERIA* / DJ YOSHITAKA | BPM: 185
- **10th Dan**
  - Stage 1【叠】：*Blue Army* / void | BPM: 180
  - Stage 2【技】：*Cold Breath* / Morimori Atsushi | BPM: 200
  - Stage 3【乱】：*Blue Planet* / beatMARIO | BPM: 240
  - Stage 4【切】：*Dreadnought* / master groove circle | BPM: 220
- **Gamma Dan**
  - Stage 1【叠】：*Angeline* / xi | BPM: 185
  - Stage 2【技】：*HAELEQUIN* / orange lounge | BPM: 172
  - Stage 3【乱】：*Blue Zenith* / xi | BPM: 200
  - Stage 4【切】：*Stellium* / lapix | BPM: 215
- **Azimuth Dan**
  - Stage 1【叠】：待补齐（源自 930218 内对应难度曲目）
  - Stage 2【技】：待补齐
  - Stage 3【乱】：待补齐
  - Stage 4【切】：待补齐
- **Zenith Dan**
  - Stage 1【叠】：待补齐
  - Stage 2【技】：待补齐
  - Stage 3【乱】：待补齐
  - Stage 4【切】：待补齐

#### 【Phase IV】（Set: 1061136）
- **Stellium Dan (Regular)**：Jinjin 定制的巅峰考评（Beatmap ID: 2221603）

---

### 2. LN Dan（长押段位：密度 ➔ 技 ➔ 反键 ➔ 释放）

#### 【Phase I】（Set: 450649）
- **1st Dan**
  - Stage 1【密度】：*Sakura Reflection* / Ryu☆ | BPM: 180
  - Stage 2【技】：*Flower* / DJ YOSHITAKA | BPM: 173
  - Stage 3【反键】：*Smooth Operator* / Big Daddy | BPM: 135
  - Stage 4【释放】：*Air* / SHIKI | BPM: 178
- **2nd Dan**
  - Stage 1【密度】：*Evans* / DJ TAKA | BPM: 185
  - Stage 2【技】：*SigSig* / kors k | BPM: 179
  - Stage 3【反键】：*She is my wife* / SUPER STAR 満-MITSURU- | BPM: 145
  - Stage 4【释放】：*L99* / TaQ | BPM: 155
- **3rd Dan**
  - Stage 1【密度】：*Kanata* / LeaF | BPM: 160
  - Stage 2【技】：*Doppelganger (LN)* / LeaF | BPM: 137
  - Stage 3【反键】：*Brain Power (Inverse)* / NOMA | BPM: 170
  - Stage 4【释放】：*Altale (LN)* / Sakuzyo | BPM: 83~110

#### 【Phase II】（Set: 895138）
- **4th Dan**
  - Stage 1【密度】：*405nm* / Another Infinity | BPM: 176
  - Stage 2【技】：*CROSS FIRE* / Ryu☆ | BPM: 180
  - Stage 3【反键】：*Overdrive* / Nanahira | BPM: 165
  - Stage 4【释放】：*Garakuta Doll Play* / t+pazolite | BPM: 256
- **5th Dan**
  - Stage 1【密度】：*Blue Army (LN)* / void | BPM: 180
  - Stage 2【技】：*Sound Chimera* / Laur | BPM: 200
  - Stage 3【反键】：*Cyberozar* / Sakuzyo | BPM: 180
  - Stage 4【释放】：*Lunatic Sounds* / Lunatic Sounds | BPM: 190
- **6th Dan**
  - Stage 1【密度】：*VALLIS-NERIA (LN)* / DJ YOSHITAKA | BPM: 185
  - Stage 2【技】：*Chronomia (LN)* / Sakuzyo | BPM: 190
  - Stage 3【反键】：*Anguish (Inverse)* / Halv | BPM: 170
  - Stage 4【释放】：*FREEDOM DiVE (Release)* / xi | BPM: 222
- **7th Dan**
  - Stage 1【密度】：*Plan 8 (LN)* / Ryu☆ | BPM: 212
  - Stage 2【技】：*Cross Time* / lapix | BPM: 180
  - Stage 3【反键】：*Jackpot (Inverse)* / void | BPM: 175
  - Stage 4【释放】：*Ascension to Heaven (LN)* / xi | BPM: 200
- **8th Dan**
  - Stage 1【密度】：*Confiserie (LN)* / L.E.D. vs S-C-U | BPM: 210
  - Stage 2【技】：*Evans (LN Tech)* / DJ TAKA | BPM: 195
  - Stage 3【反键】：*High-voltage (Inverse)* / LeaF | BPM: 190
  - Stage 4【释放】：*Bangin' Burst (Release)* / かめりあ | BPM: 234

#### 【Phase III】（Set: 1220647）
- **9th Dan**
  - Stage 1【密度】：*Dreadnought (LN)* / master groove circle | BPM: 220
  - Stage 2【技】：*HAELEQUIN (Extended LN)* / orange lounge | BPM: 172
  - Stage 3【反键】：*THE DETONATOR (Inverse)* / teranoid | BPM: 180
  - Stage 4【释放】：*Halcyon (Release)* / xi | BPM: 191
- **10th Dan**
  - Stage 1【密度】：*Blue Planet (LN)* / beatMARIO | BPM: 240
  - Stage 2【技】：*Cold Breath (LN Tech)* / Morimori Atsushi | BPM: 200
  - Stage 3【反键】：*G e n g a o z o (Inverse)* / -45 | BPM: 155
  - Stage 4【释放】：*Blue Zenith (Release)* / xi | BPM: 200
- **Gamma Dan / Azimuth Dan / Zenith Dan**
  - 具体 Stage 曲目待按 1220647 内难度分段校准补充

#### 【Phase IV】（Set: 1061136）
- **Stellium Dan (LN)**：Jinjin 顶级长押试炼
