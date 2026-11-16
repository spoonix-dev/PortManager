<div align="center">

# PortManager

**An ultra-lightweight, macOS-inspired Task Manager & Network Port Monitor for Windows.**

[![License: MIT](https://img.shields.io/badge/License-MIT-0A84FF.svg?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-30D158.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0A84FF.svg?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/spoonix-dev/PortManager/releases)
[![Release](https://img.shields.io/github/v/release/spoonix-dev/PortManager?style=for-the-badge&color=30D158)](https://github.com/spoonix-dev/PortManager/releases)
[![Downloads](https://img.shields.io/github/downloads/spoonix-dev/PortManager/total?style=for-the-badge&color=FF453A)](https://github.com/spoonix-dev/PortManager/releases)

[**⬇ Download PortManager.exe**](https://github.com/spoonix-dev/PortManager/releases) ·
[Report a bug](https://github.com/spoonix-dev/PortManager/issues) ·
[Arabic summary](#-ملخص-بالعربية)

</div>

---

## Why PortManager?

*"Port 3000 is already in use."* — every developer has been there. PortManager shows **which process owns every listening port**, lets you end it in one click, and then quietly disappears into the system tray using **0 % CPU** until you need it again.

## Features

| | |
|---|---|
| 🔍 **Live port map** | Every listening TCP and bound UDP port → `Port`, `Protocol`, `Process Name`, `PID`, `CPU %`, `RAM (MB)`, `Bind Address`, `Command Line` |
| ⚡ **Instant search & pills** | Live search bar and segmented filters: `All` · `Dev Ports` · `TCP` · `UDP` |
| 🏷 **Dev-port badges** | `3000, 3001, 4200, 5000, 5173, 8000, 8080, 8888` get a blue **DEV** badge |
| 🛑 **End Task** | Graceful: asks the app's windows to close (`WM_CLOSE`), falls back to terminate for windowless processes |
| 💥 **Force Kill** | Immediately kills the process **and its child tree** (confirmation required) |
| 🧹 **Clean Dev Ports** | One click stops every stuck dev server — from the main window *or straight from the tray menu* |
| 🛡 **Safety rails** | PIDs `0`, `4`, PortManager itself and core Windows processes (`csrss`, `lsass`, `services`, …) can never be ended |
| 🪶 **Zero idle CPU** | Closing the window (`✕`) hides it to the tray and **stops all polling**; scans run only while the window is visible |
| 🔒 **Single instance** | Windows named mutex — launching it twice just brings the running copy to the front |
| 🚀 **Auto-run** | Registered in `HKCU\…\Run` on first launch with `--minimized`; toggle it from the window or the tray |
| 🎨 **macOS dark UI** | Sonoma/Sequoia look: rounded cards, System Blue / Crimson / Emerald accents, Segoe UI + Cascadia Code / Consolas |
| 📦 **No asset files** | Window, tray and `.exe` icons are drawn in memory — nothing to break inside PyInstaller |

### Design tokens

| Role | Value |
|---|---|
| Window | `#161618` |
| Cards / toolbar | `#222226`, `1px #323236` border, 12 px radius |
| System Blue (selection, pills) | `#0A84FF` |
| Crimson (End / Force Kill) | `#FF453A` |
| Emerald (active status) | `#30D158` |

## Usage

### Window

| Action | How |
|---|---|
| Search | `Ctrl+F`, then type — matches port, process, PID, address, command line (try `:3000`) |
| Sort | Click any column header (click again to reverse) |
| Select | Click · `Ctrl`+click · `Shift`+click · `Ctrl+A` · `↑ ↓ PgUp PgDn Home End` |
| End Task | `Delete` or the **End Task** button |
| Force Kill | `Shift+Delete` or the **Force Kill** button |
| Clean Dev Ports | `Ctrl+Shift+D` or the **Clean Dev Ports** button |
| Refresh now | `F5` or `Ctrl+R` |
| Clear search / selection | `Esc` |
| Hide to tray | window `✕` |
| Quit completely | `Ctrl+Q` or tray → **Quit PortManager** |

### System tray (notification area near the clock)

* **Left-click** the icon → show the window
* **Right-click** →
  * **Show PortManager**
  * **Clean Dev Ports** — kills all dev-port listeners without opening the window (a notification reports the result)
  * **Start with Windows** ✓ — toggle auto-run
  * **GitHub — @spoonix-dev/PortManager**
  * **Quit PortManager**

> **Tip:** processes owned by other users or by the system can only be inspected/ended when PortManager runs **as Administrator**. Without it you will see "access denied" for those rows.

## Download

Grab the latest `PortManager.exe` from the **[Releases page](https://github.com/spoonix-dev/PortManager/releases)** — a single standalone file, no installer, no Python required. A `.sha256` checksum is attached to every release.

## Build from source

Requirements: **Windows 10/11**, **Python 3.11+**.

```bat
git clone https://github.com/spoonix-dev/PortManager.git
cd PortManager
build.bat
```

`build.bat` creates a virtual environment, installs `requirements.txt`, renders the icon from code and runs PyInstaller (`--onefile --noconsole`). The result is **`dist\PortManager.exe`**.

Run without building:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python portmanager.py            :: normal start
python portmanager.py --minimized :: start hidden in the tray
```

> Running from source also registers auto-run on first launch (pointing at `pythonw.exe` + the script). Turn it off with the switch in the window if you only want to test.

### Publishing a release

Push a tag and GitHub Actions builds and attaches the exe automatically:

```bash
git tag v1.0.0
git push origin v1.0.0
```

See [`.github/workflows/release.yml`](.github/workflows/release.yml).

## How it stays light

* Polling is a single `after()` timer that **exists only while the window is mapped** — hiding to the tray or minimizing cancels it and drops the process cache.
* Each scan runs in a short-lived worker thread that exits when done; no persistent background loops.
* The second-instance "wake-up" is a blocking `WaitForSingleObject` — 0 % CPU while idle.
* The table is a virtualized canvas: only visible rows are drawn.

## Project layout

```
PortManager/
├── portmanager.py            # the whole application
├── requirements.txt
├── build.bat                 # one-click PyInstaller build
├── README.md
├── LICENSE                   # MIT
├── .gitignore
└── .github/workflows/release.yml
```

## Author & attribution

**PortManager** is open source, created and maintained by **Mustafa Al-Aqrawi (مصطفى العقراوي)** — GitHub [**@spoonix-dev**](https://github.com/spoonix-dev).

* Repository: <https://github.com/spoonix-dev/PortManager>
* Forks and redistributions must keep the copyright notice and the MIT license text, including the credit to *Mustafa Al-Aqrawi (spoonix-dev)*. The in-app credit line (`PortManager • Open Source by Mustafa Al-Aqrawi (@spoonix-dev)`) is part of that attribution.

## License

[MIT](LICENSE) © 2026 Mustafa Al-Aqrawi (spoonix-dev)


**البناء من المصدر:** شغّل الملف `build.bat` وستجد النتيجة في `dist\PortManager.exe`.

**المطوّر:** مصطفى العقراوي — [@spoonix-dev](https://github.com/spoonix-dev) · الترخيص: MIT

</div>
