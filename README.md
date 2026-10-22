# proj7k

**English** · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md)

A desktop companion for osu!mania 7K players: see your level and how it is moving, find out what makes a chart hard, turn a chart you can't pass into one you can practise, generate a chart from a song, and find your weak spots from your own replays.

Difficulty is proj7k's own star rating, calibrated to the [Jinjin 7K dan courses](#reading-stars-and-dans): a 5★ chart should be about as hard whether it is a jack chart or an LN chart.

**⬇️ Download the desktop app: [latest release](https://github.com/ka1z07/proj7k/releases/latest)**　·　**🌐 Online chart catalog: <https://7k.kaiz.dev/>** (nothing to install)

---

## Contents

- [Install](#install)
  - [Windows](#windows)
  - [macOS](#macos)
  - [Update and uninstall](#update-and-uninstall)
- [First launch](#first-launch)
- [A tour of the window](#a-tour-of-the-window)
- [Player: Home, History, Library](#player-home-history-library)
- [Tools](#tools)
  - [Live radar](#live-radar)
  - [Replays & profile](#replays--profile)
  - [Downscale](#downscale)
  - [Chart generator](#chart-generator)
  - [Library sync](#library-sync)
- [Online chart catalog](#online-chart-catalog)
- [Reading stars and dans](#reading-stars-and-dans)
- [FAQ](#faq)
- [Command line (advanced)](#command-line-advanced)
- [License](#license)

---

## Install

You need **osu!lazer** (proj7k reads lazer's database and replays; osu!stable is not supported) and Windows 10 / 11 or macOS. Everything runs on your own computer: no login, nothing is uploaded.

### Windows

1. Open the **[latest release](https://github.com/ka1z07/proj7k/releases/latest)** and, under Assets at the bottom, download **Source code (zip)**.
2. Unzip it somewhere you will keep it, e.g. `Documents\proj7k`.
3. Double-click **`install.cmd`** in that folder.

   > [!NOTE]
   > If Windows says "Windows protected your PC", click "More info" → "Run anyway". proj7k is not code-signed, so Windows doesn't recognise it.

4. Wait for the console window to finish (3–10 minutes the first time, depending on your connection). It:
   - installs Python 3.14 and Node.js with Windows' built-in winget if they are missing (you may get one administrator prompt);
   - creates a private Python environment (`.venv`) inside the proj7k folder with everything proj7k needs, without touching any other Python on your computer;
   - downloads the proj7k window and puts a proj7k shortcut in the **Start menu and on the desktop**;
   - opens proj7k.

From then on, open **proj7k** from the Start menu or the desktop.

> [!TIP]
> If it stops with "Python / Node.js was installed but this window cannot see it yet", close the window and double-click `install.cmd` again. Steps already done are skipped.

### macOS

1. Download **Source code (zip)** from the **[latest release](https://github.com/ka1z07/proj7k/releases/latest)** too, and unzip it, e.g. to `~/Documents/proj7k`.
2. Open Terminal, type `sh ` (with a trailing space), drag `install.sh` from the unzipped folder into the Terminal window, and press Return.
   - Python 3.14 and Node.js are installed with [Homebrew](https://brew.sh) if missing; install Homebrew first with the one-line command on its home page if you don't have it.
   - The proj7k window goes to `~/Applications/proj7k.app`.
3. From then on, double-click **`proj7k.command`** in the folder to open proj7k.

### Update and uninstall

**Update**: download the new release's zip, unzip it **over** the old folder, and run `install.cmd` (`install.sh` on a Mac) again. With git, `git pull` and then run it again. Your profile, replay analysis and settings live in your user folder (`~/.proj7k`, `~/.cache/proj7k`) and survive updates.

> [!TIP]
> After an update, run "Replays & profile → Import lazer replays" again so your old replays are recalculated by the new version.

**Uninstall**: delete the proj7k folder, the Start-menu and desktop shortcuts, and `%LOCALAPPDATA%\proj7k` (`~/Applications/proj7k.app` on a Mac). To remove your data too, delete `.proj7k` and `.cache\proj7k` in your user folder. If you used Library sync, click "Restore official stars" before uninstalling.

---

## First launch

1. **osu!lazer** can be open or closed: proj7k only reads its database, and can read it while the game runs. A default lazer install is found automatically, and so is a data folder you moved in the game's settings.
2. Open proj7k. It starts on your **Home** page: play count, ranks, your star progress, which dans you have cleared, all from the scores lazer keeps. No login or API key.
3. Under "What to practise next" on Home, click **"Import replays"** (or go to "Replays & profile → Import lazer replays"), enter your player name and click "Start import". Once your replays are read, Home shows your **overall level**, your **eight technique levels** and the **weakness worth fixing first**.
4. The **English / 中文** button at the bottom left switches the interface language (it follows your system language the first time).

The proj7k window has no browser tabs or address bar: it draws its own minimise, maximise and close buttons at the top right, and you move it by dragging the title bar. Closing the window doesn't stop running jobs (downscaling, generating and importing carry on in the background).

---

## A tour of the window

The sidebar on the left has three groups:

| Group | Page | What it does |
| :--- | :--- | :--- |
| **Player** | Home | Your current level, the last 30 days' change, what to practise next |
| | History | Every play lazer saved; open one to diagnose it, watch it, or cut a practice pack |
| | Library | Every 7K chart on your computer, filtered by proj7k stars, dan and technique, including "Good for me to practise" |
| **Tools** | Live radar | Stars, dan and the eight techniques of the chart you select in game, live |
| | Replays & profile | Replay diagnosis, importing lazer replays, your player profile and habits |
| | Downscale | Brings a chart down to the dan or stars you choose and imports the practice chart into the game |
| | Chart generator | Generates a 7K chart for a song at a target difficulty |
| **Settings** | Library sync | Replaces the stars in osu!lazer with proj7k stars; can be restored at any time |

At the bottom of the sidebar:

- **Game status**: whether osu!lazer is running, and the **chart currently selected in the game** (click it for the chart's details). Most tools default to "Current chart in game", so the usual flow is: **pick the chart in the game, then click the button in proj7k.**
- **Restart osu!**: closes osu!lazer properly (like clicking its ✕, so it saves) and opens it again; handy after importing new charts.
- The **connection status** and the **language switch**.

When the window is narrow the sidebar shows icons only. Any chart name or play in a list can be opened: details slide in from the right and `Esc` closes them. The replay viewer opens inside the window; "Open in browser" moves it to your browser. You can drop `.osu` and `.osr` files straight onto a page.

---

## Player: Home, History, Library

- **Home**: your current level in big type (dan and stars); six stats such as play count, play time and accuracy, against the previous 30 days; "What to practise next" names your weakest technique (with one click to a practice chart for it), charts near your level you haven't played well yet, and how many charts of the next dan you have and have cleared. Below: your star progress over time (click a point to open that play), the eight technique levels, dan clears, activity over the last six months, your hardest clears and recent plays.
- **History**: every play with rank, accuracy, score, combo, mods and stars (DT/HT plays use the stars at that rate). Search, filter by rank / dan / mod, sort by stars or accuracy, or show only the best play per chart.
- **Library**: every 7K chart on your computer with cover, stars, dan, main technique and your best score. "Good for me to practise" lists charts close to your profile's level that you haven't played.

**Open a play** for its judgement breakdown, MAX:300 count and where it ranks among your plays on that chart. From there: "Diagnose this play" (replay viewer + saved to your profile), "Watch in osu!", download the `.osr`, "Practice pack from the breakdown", "Downscale this chart".

**Open a chart** for its engine stars, eight techniques, difficulty over time, each of your plays on it and an audio preview. From there you can downscale it, generate a new chart from its song, diagnose your latest play, or open its page in the online 7K catalog or on the osu! website.

---

## Tools

### Live radar

Select a 7K chart in osu!lazer and the page follows at once:

- **Stars and dan**: the chart's proj7k stars and the dan they match.
- **Eight-technique radar**: four rice techniques (Jack, Tech, Speed, Stream) and four LN ones (LN general, LN tech, Inverse, Release). Whichever sticks out is what the chart mainly tests.
- **Difficulty timeline**: how hard each part of the song is and which technique carries it. The highest peaks are where you are most likely to break combo.

It works well on a second screen while you pick songs. For streaming, add a Browser source in OBS with `http://127.0.0.1:7770/?mode=overlay` for a see-through overlay.

### Replays & profile

This page has four tabs:

**Import lazer replays**: start here. Enter your player name and click "Start import" to read all your 7K replays in osu!lazer into your profile. Importing again is safe and doesn't double-count; very short plays and obvious restarts are skipped.

**Replay diagnosis**: analyses one play. By default it takes your latest play on the chart selected in the game; you can also pick any play or drop in an `.osr` and `.osu`. Options:

- **Build replay viewer**: a falling-notes replay with the chart above the judgement line and what you actually pressed below it, so misses, ghost taps, extra presses and early LN releases stand out. Drag along the difficulty timeline at the bottom to jump between mistakes and the hardest sections.
- **Save to profile archive**: counts this play towards your player profile.
- **Cut a three-tier practice pack at the breakdown**: finds the stretch where you broke, cuts it out and builds three practice charts from easy to hard (Recovery at your current level, Push is the original section, Bridge in between), packed as an `.osz`.
- **Practice recommendations**: picks charts from your local library: "Fix the weakness" for your weak techniques, "Sharpen the strength" to push your strong ones (dan course charts are never recommended).

**Player profile**: the dan you play at in each of the eight techniques, and your overall level, over the last N days or all time.

**Habits**: your key-press habits across all replays: whether each column is early or late, how long you hold, which finger lands first in a chord, rolling vs. pressing together, how you release LNs, and which hand you use for the middle column.

### Downscale

A chart you can't pass becomes a practice chart with the same song and style, but easier.

1. Select the chart in the game (or choose "Specify a chart" and give a file path, beatmap ID or osu! beatmap link, or drop the `.osu` in).
2. Choose the target: a **dan** (e.g. 3rd) or **stars** (e.g. 4.5).
3. Choose how:
   - **Keep main technique**: a jack chart stays a jack chart, just easier. This is the default.
   - **Difficulty only**: gets to the target difficulty without insisting on the main technique. If "Keep main technique" can't get as low as you asked, the result tells you to use this.
4. Click **"Start downscaling"**.

Practice charts only remove notes, never add them, and keep the strong beats, chord skeleton and left/right balance. Tick "Pack .osz" to bring the song and background along; click **"Import to osu!"** in the result to put it in the game, and with the import option ticked it also goes into a "7K Practice" collection.

### Chart generator

Give it a song and it generates a 7K chart.

1. Pick the song: "Song of the current chart in game" (borrows that chart's BPM and offset, for the most accurate timing) or "Specify audio" (mp3 / ogg / wav / flac; you can drop the file in).
2. Choose a target dan or stars.
3. Timing: borrowed from the reference chart when there is one; otherwise "Auto-detect"; if auto-detect gets it wrong, choose "Manual" and enter the BPM and the time of the first downbeat.
4. Choose how much LN: No LN (rice only), A few LN, Rice and LN mixed, Mostly LN; optionally tick "Inverse".
5. Click **"Generate"**. It fine-tunes the chart until its difficulty lands near the target, packs it as an `.osz`, and can import it into the game in one click.

The generator first splits the song into sections (intro, verse, chorus, break…) and sets each section's density and patterns from its loudness and rhythm, so when the chorus comes back, so does its writing. Change the "Key layout seed" number to get another layout with the same settings. The result is a new chart of its own and never overwrites the original.

> [!NOTE]
> "Experimental: Demucs stems" splits the song into drums, bass, vocals and other, and has each section follow one layer. Ticking it the first time downloads and installs Demucs and PyTorch (several GB), and the first split of each song takes about 1–3 minutes. Nothing is downloaded unless you tick it.

### Library sync

Replaces the stars of the 7K charts in your game with proj7k stars, so sorting and filtering by stars in osu!lazer uses proj7k's difficulty. It also tags every chart with its dan and main technique, so you can find them by searching tags in the game.

1. Click **"Dry run (no writes)"** to see how many charts would change; nothing is written.
2. Click **"Sync now"**. If osu!lazer is open, it is closed first by default and reopened afterwards.

- **A backup is made before writing.** To go back to the official stars, click **"Restore official stars"**.
- Only your computer's game database changes; online scores and rankings are not affected.

---

## Online chart catalog

Open <https://7k.kaiz.dev/> and click into the catalog. Nothing to install. (The catalog's interface is in Chinese for now.)

- **Both star ratings side by side**: every difficulty shows the official osu! stars (yellow) and proj7k stars (blue), with the matching dan. Sort by the difference to find the charts where the two disagree most.
- **Find charts by technique**: the main-skill buttons filter to charts built mainly on jacks, streams, inverse, releases and so on.
- **Search conditions**: besides song title and mapper, the search box takes conditions such as `engine>5 engine<6` (proj7k stars between 5 and 6). The search syntax link next to the box lists them all.
- **Random**: random order shows a new set of charts on every refresh.
- **Mappers**: the mappers page shows which 7K charts each mapper made and the techniques they favour.
- Pending, WIP and graveyard 7K charts are included as well as Ranked, Loved and Qualified. A chart's page links to the osu! website for download, or opens it with osu!direct.

---

## Reading stars and dans

proj7k's stars are calibrated to the Jinjin 7K dan courses, 15 levels in all:

`0th` → `1st` → … → `10th` → `Gamma` → `Azimuth` → `Zenith` → `Stellium`

- More stars means harder; each dan covers a band of stars, and proj7k and the catalog show both.
- proj7k stars and official osu! stars are different rulers; a chart having two different numbers is normal.
- Each of the eight techniques has its own stars too, meaning "how hard this chart is on that technique"; the one that contributes most to the chart's difficulty is its main technique.

---

## FAQ

**Can this get me banned?**
No. proj7k only reads your replays and chart files; Library sync only changes the stars shown in your local game database, never your scores, and nothing is uploaded.

**Home stays empty / says "No osu!lazer database"?**
Make sure you are using osu!lazer, not stable. If your data folder is somewhere unusual, run `dashboard.cmd --lazer-dir "data folder"` in the proj7k folder (the folder that contains `client.realm`). If that doesn't help, please open an [issue](https://github.com/ka1z07/proj7k/issues).

**Home works but shows no level or eight techniques?**
You haven't imported replays yet. See step 3 of [First launch](#first-launch).

**The shortcut does nothing / the window flashes and closes?**
Double-click `install.cmd` in the proj7k folder again; it fills in whatever is missing. If that doesn't help, double-click `dashboard.cmd` to use proj7k in your browser (<http://127.0.0.1:7770/>); its console window shows the error. Please paste it into an issue.

**I'd rather use it in my browser than in the proj7k window.**
Double-click `dashboard.cmd` and your browser opens <http://127.0.0.1:7770/>. Keep its console window open while you use it.

**Port 7770 is already in use?**
Usually an earlier proj7k is still running. Or pick another port with `dashboard.cmd --port 7771`.

**How do I go back to the official stars after Library sync?**
Click "Restore official stars" on the Library sync page.

---

## Command line (advanced)

Everything is also available on the command line, which is handy for batch work. Run these in the proj7k folder (on Windows run `set PYTHONPATH=src` first; on macOS / Linux put `PYTHONPATH=src` in front of the command; with the environment the installer made, use `.venv\Scripts\python` (`.venv/bin/python` on a Mac) instead of `python3`):

| What | Command |
| :--- | :--- |
| Open the desktop app | `python3 -m proj7k.desktop` |
| Dashboard only (browser) | `python3 -m proj7k.dashboard` |
| Stars and techniques of a chart | `python3 -m proj7k.difficulty "chart.osu"` |
| Downscale to a dan / stars | `python3 -m proj7k.downscaler -i "chart.osu" -d 3rd -o ./out` (`-s 4.5` for stars) |
| Generate a chart | `python3 -m proj7k.generator --audio song.mp3 --target-dan 2nd` |
| Sync library stars / restore | `python3 -m proj7k.sync` / `python3 -m proj7k.sync --revert` |
| Diagnose a replay and open the viewer | `python3 -m proj7k.profiler -r "replay.osr" -b "chart.osu" --view --open` |
| Import lazer replays | `python3 -m proj7k.profiler --import-replays --player "name"` |
| Show a player profile | `python3 -m proj7k.profiler -p "name" --horizon-days 30` |

Add `--help` to any command for all its options.

Developer documentation (difficulty model, design decisions, tests) is in [`CONTEXT.md`](CONTEXT.md), [`docs/adr/`](docs/adr/) and [`CLAUDE.md`](CLAUDE.md), mostly in Chinese. Progress and devlog: <https://kaiz.dev/projects/proj7k/>.

---

## License

proj7k's code is open source under the [MIT License](LICENSE). Chart content included in the repository (the benchmark charts used in tests and so on) belongs to its mappers and is not covered by the MIT License; see [NOTICE](NOTICE.md).

proj7k is an unofficial third-party tool, not affiliated with or endorsed by ppy Pty Ltd. osu! is a trademark of ppy Pty Ltd.
