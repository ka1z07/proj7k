# NOTICE

proj7k 的源代码以 [MIT 许可证](LICENSE) 发布。下面这些内容不是 proj7k 的作品，不在 MIT 许可范围内。
The proj7k source code is MIT licensed ([LICENSE](LICENSE)). The items below are not proj7k's own work and are not
covered by that license.

## 谱面内容 / Beatmap content

- `tests/fixtures/benchmark_corpus.json.gz`、`tests/fixtures/practice_corpus.json.gz`：用来标定和回归测试引擎的谱面
  `.osu` 文本。每张谱面的版权归它的谱师所有，只为难度研究和测试收录。
  The `.osu` text of the charts used to calibrate and regression-test the engine. Each chart belongs to its mapper;
  it is included for difficulty research and testing only.
- `docs/research/`：Jinjin 7K 段位表与相关谱面的索引和统计。段位体系归其作者所有。
  Indexes and statistics of the Jinjin 7K dan courses and their charts. The dan courses belong to their authors.
- `src/proj7k/generator/*_model.json.gz`：从谱面中统计、训练出的模型参数，不包含任何谱面或音频本身。
  Model parameters counted and trained from charts; they contain no chart or audio.

谱师如果不希望自己的谱面出现在这里，请提 [Issue](https://github.com/ka1z07/proj7k/issues)，我们会移除。
Mappers who want their chart removed: please open an issue.

## osu!

proj7k 是第三方工具，与 ppy Pty Ltd 无关，也没有得到其认可。osu! 是 ppy Pty Ltd 的商标。proj7k 不包含 osu! 的代码、
皮肤、音效或其他素材；谱面库里的封面和谱面信息在浏览时直接从 osu! 官网加载。
proj7k is an unofficial third-party tool, not affiliated with or endorsed by ppy Pty Ltd. "osu!" is a trademark of
ppy Pty Ltd. proj7k contains no osu! code, skins, sounds or other assets; covers and beatmap information in the
catalog are loaded from the osu! website when viewed.

## 依赖 / Dependencies

proj7k 不随附第三方代码，下列依赖在安装时从各自的来源下载，按各自的许可证使用：
proj7k bundles no third-party code; these are downloaded at install time and used under their own licenses:

| 依赖 / Dependency | 用途 / Used for | 许可证 / License |
| :--- | :--- | :--- |
| NumPy, SciPy | 难度引擎 / engine | BSD-3-Clause |
| websockets | 本地控制台 / dashboard | BSD-3-Clause |
| Pillow | 图片 / images | MIT-CMU (HPND) |
| pyminiaudio | 音频解码 / audio decoding | MIT |
| Realm JavaScript (`realm`) | 读写 osu!lazer 数据库 / osu!lazer database | Apache-2.0 |
| Tauri 2 及其插件 / and plugins | 桌面窗口 / desktop window | MIT OR Apache-2.0 |
| Demucs, PyTorch（可选，勾选分轨时才安装 / optional） | 分轨 / stem separation | MIT; BSD-3-Clause |

桌面窗口的发布版（`proj7k-windows.exe`、`proj7k-macos.zip`）由 Tauri 及其约 450 个 Rust 依赖编译而成（版本见
`desktop/tauri/src-tauri/Cargo.lock`）。它们绝大多数是 MIT 或 Apache-2.0；另有 `cssparser`、`cssparser-macros`、
`selectors`、`dtoa-short`、`option-ext` 为 MPL-2.0，ICU4X 系列（`icu_*`、`zerovec` 等）为 Unicode-3.0。这些依赖都
未经修改，源代码可在 <https://crates.io/> 按 Cargo.lock 中的版本获取。
The released desktop window builds are compiled from Tauri and about 450 Rust crates (versions in
`desktop/tauri/src-tauri/Cargo.lock`), almost all MIT or Apache-2.0. `cssparser`, `cssparser-macros`, `selectors`,
`dtoa-short` and `option-ext` are MPL-2.0 and the ICU4X crates (`icu_*`, `zerovec`, ...) are Unicode-3.0. None of them
is modified; their source is available on <https://crates.io/> at the versions in Cargo.lock.
