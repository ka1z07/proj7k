# 0002. 以 Jinjin 7K Dan 为单一技法难度的唯一权威诠释基准

om7k 难度重构引擎必须能体现铺面在不同技法层面上的具体难度，且单一技法的难度评判以 Jinjin's osu!mania 7K Dan 为唯一权威基准。

## 决策背景

不同 VSRG（如 BMS、O2Jam、Malody）因按键物理机制与谱面风格差异，对相同键形有着不同的难度诠释（Interpretation），直接套用其他音游标准不适用于 om7k 社群。Jinjin 7K Dan 是 om7k 社群对于单一技法难度梯度的共识体现。

同时明确：Jinjin 7K Dan 体系在架构上遵循严格双轨制，仅由 **Regular Dan（常规段位）** 与 **LN Dan（长押段位）** 两大体系组成，不存在 Extra 或 Lunatic 衍生分支；体系按 Phase I 至 Phase IV 进行阶梯进阶：
- **Regular Dan 闭环考点**：Stage 1 叠 (Jack) ➔ Stage 2 技 (Tech) ➔ Stage 3 乱 (Stream) ➔ Stage 4 切 (Chordstream)
- **LN Dan 闭环考点**：Stage 1 密度 (Density) ➔ Stage 2 技 (Tech) ➔ Stage 3 反键 (Inverse) ➔ Stage 4 释放 (Release)

## 方案取舍

- **被拒绝的方案**：引入 BMS 发狂/Stella 表、4K Reform Dan 概念或 O2Jam 等级作为复合参考体系。
- **采纳的方案**：以 Jinjin 7K Dan 的 Marathon 综合谱面与对应拆分的单一技法练习集作为构建多维难度度量衡与对齐经验的唯一基线。

## 架构影响

后续所有针对单一技法维度的定级映射、案例审问切片坐标、以及难度引擎的校准测试集，均以 Jinjin Dan 为基准坐标系展开。
