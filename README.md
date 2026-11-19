<div align="center">

# PortManager 2.0

**An iOS-styled, bilingual (English · العربية) Task Manager & Network Port Monitor for Windows.**

[![License: MIT](https://img.shields.io/badge/License-MIT-0A84FF.svg?style=for-the-badge)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.0.0-30D158.svg?style=for-the-badge)](https://github.com/spoonix-dev/PortManager/releases)
[![Python](https://img.shields.io/badge/Python-3.11%2B-0A84FF.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0A84FF.svg?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/spoonix-dev/PortManager/releases)
[![Downloads](https://img.shields.io/github/downloads/spoonix-dev/PortManager/total?style=for-the-badge&color=FF453A)](https://github.com/spoonix-dev/PortManager/releases)

[**⬇ Download PortManager.exe**](https://github.com/spoonix-dev/PortManager/releases) ·
[Report a bug](https://github.com/spoonix-dev/PortManager/issues) ·
[ملخص بالعربية](#-ملخص-بالعربية)

</div>

---

## Why PortManager?

*"Port 3000 is already in use."* — every developer has been there. PortManager shows **which process owns every listening port**, tells you whether it is **local-only or exposed to your network**, protects Windows itself from accidental kills, and then disappears into the system tray with **all scanning stopped** until you need it again.

## What's new in 2.0

| | |
|---|---|
| 📱 **iOS design language** | Rounded cards, capsule search bar and segmented tabs (`All · TCP · UDP · Pinned · Protected`), Apple system colours, generous spacing |
| 🌗 **Dark / Light mode** | One-click header toggle. Dark `#000000 / #1C1C1E / #0A84FF / #FF453A / #30D158` · Light `#F2F2F7 / #FFFFFF / #007AFF / #FF3B30 / #34C759`. The **Windows title bar** follows the theme (`DwmSetWindowAttribute`) |
| 🌍 **Arabic & English** | `عربي` / `EN` button switches every label, tab, badge, menu and dialog instantly, with full **right-to-left mirroring** |
| 🛡 **System Guard** | Core Windows processes are **locked**, Windows system services need a **strict red confirmation** (see below) |
| 🏷 **Smart badges** | `Local (127.0.0.1)` · `LAN (0.0.0.0 / ::)` · `Docker/WSL` · `DEV` · `CORE SYSTEM` · `SYSTEM` |
| 📌 **Pins & labels** | Pin favourite ports to the top, name them ("Frontend", "Postgres DB") |
| 🔎 **Process inspector** | PID, port/protocol, network exposure, RAM, uptime, full executable path and command line |
| 🖱 **Right-click menu** | Open in Browser · Restart Process · End Process Tree · Pin/Unpin · Custom Label · Copy Port/PID · Open File Location |

Everything from 1.x is still here: single instance (named mutex), tray-only background mode, Windows auto-start with `--minimized`, tray **Clean Dev Ports**, programmatic icons, and a single-file PyInstaller `.exe`.

## System Guard

PortManager classifies every process into three tiers and enforces them in the UI **and** in the kill engine itself:

| Tier | Examples | Card | What happens when you try to end it |
|---|---|---|---|
| 🔒 **Core Windows** | `System`, PID `0`/`4`, `csrss.exe`, `lsass.exe`, `wininit.exe`, `services.exe`, `smss.exe`, `dwm.exe`, `winlogon.exe` | **Red border + red tint**, solid red `CORE SYSTEM` badge, button reads *Locked* | **Blocked.** A red warning sheet explains that ending it can cause a BSOD / system crash. There is no override. |
| ⚠️ **Windows system** | `svchost.exe`, `explorer.exe`, `spoolsv.exe`, `RuntimeBroker.exe`, anything under `C:\Windows\System32` | **Red border + red tint**, red `SYSTEM` badge | A **strict red warning sheet** must be confirmed before any End / Kill / Restart. |
| ✅ **Normal** | Node, Vite, Python, Docker, databases, your apps | Clean card | Standard confirmation sheet. |

A fourth silent tier protects **PortManager itself** (and its PyInstaller bootloader). Look-alikes such as `C:\Users\me\lsass.exe` are *not* treated as Windows processes, so you can still kill them. *"Bulk" actions (Clean Dev Ports) only ever touch normal processes.*

## Using PortManager

### The card

```
┌──────────────────────────────────────────────────────────────────────┐
│ 3000   node.exe ★ [Frontend]                          [Details] [End] │
│ ● TCP  [DEV] [Local (127.0.0.1)]                                     │
│        PID 18244 · 63.2 MB · 0.4% CPU   node ./node_modules/vite…    │
└──────────────────────────────────────────────────────────────────────┘
```

| Badge | Meaning |
|---|---|
| 🟢 `Local (127.0.0.1)` | Bound to loopback only — not reachable from your network |
| 🟠 `LAN (0.0.0.0 / ::)` | Bound to all interfaces (or a LAN address) — **reachable by other devices** |
| 🟣 `Docker/WSL` | `wslrelay.exe`, `com.docker.*`, `vmmem`, `vpnkit` … |
| 🔵 `DEV` | `3000, 3001, 4200, 5000, 5173, 8000, 8080, 8888` |
| 🔴 `CORE SYSTEM` / `SYSTEM` | System Guard tiers |
| ★ | Pinned port (pinned ports sort to the top) |

A process listening on both IPv4 and IPv6 shows up as **one** card.

### Right-click menu

| Item | What it does |
|---|---|
| **Open in Browser** | Opens `http://localhost:<port>` (TCP only) |
| **Restart Process** | Stops it (gracefully, then force) and relaunches it with its **original command line and working directory**, detached and without a console window |
| **Force Kill** | Kills just that process |
| **End Process Tree** | Kills the process **and all recursive child processes** (children first) |
| **Pin / Unpin Port** · **Set Custom Label…** | Persisted in the config file |
| **Copy Port Number** · **Copy PID** | To the clipboard |
| **Open File Location** | Opens Explorer with the executable selected |

> Restart re-launches with the same arguments and folder, but not the original shell environment or terminal. For dev servers you started in a terminal, restarting from there is still the most faithful option.

### Keyboard

| Action | Keys |
|---|---|
| Search | `Ctrl+F` · `Esc` clears search / selection / menu |
| Move between cards | `↑ ↓ PgUp PgDn Home End` |
| Process inspector | `Enter` or double-click |
| End Task (graceful) | `Delete` |
| Force Kill | `Shift+Delete` |
| Clean Dev Ports | `Ctrl+Shift+D` |
| Refresh now | `F5` / `Ctrl+R` |
| Hide to tray | window `✕` |
| Quit completely | `Ctrl+Q` or tray → **Quit PortManager** |

### System tray

* **Left-click** → show the window · **Right-click** → *Show*, *Clean Dev Ports* (no window needed, result shown as a notification), *Start with Windows* ✓, *GitHub*, *Quit*. The menu follows your language.

> **Tip:** other users' and elevated processes can only be inspected or ended when PortManager runs **as Administrator**. Otherwise you will see "access denied" for those cards.

## Configuration

Settings live in one small JSON file: **`%USERPROFILE%\.portmanager.json`**

```json
{
  "version": "2.0.0",
  "language": "ar",
  "theme": "dark",
  "pinned": [3000, 5432],
  "labels": { "3000": "Frontend", "5432": "Postgres DB" },
  "startup_initialized": true
}
```

On first launch the language defaults to your Windows display language (Arabic or English). The file is written atomically, and a corrupt or hand-edited file is ignored safely. Delete it to reset everything.

### Upgrading from 1.x

* Your auto-start choice is carried over (the old `%APPDATA%\PortManager\settings.json` flag is migrated — PortManager will not re-enable startup if you turned it off).
* The old **Dev Ports** filter became the **DEV badge** plus the **Clean Dev Ports** button; the **Pinned** and **Protected** tabs replace it. Column sorting is gone (cards are sorted pinned-first, then by port).

## Zero idle cost

* A scan timer exists **only while the window is visible**. Hiding to the tray or minimizing cancels it and drops the process cache — no polling loop, no background thread. Each scan runs in a short-lived worker thread that exits when finished.
* The "second launch wakes the first" feature is a blocking `WaitForSingleObject` — no polling.
* The card list is virtualized: only visible cards are drawn. Context menu, sheets and fades exist only while you use them.
* (customtkinter keeps its own tiny idle timers for theme/DPI tracking; in practice the hidden app sits at 0 % CPU.)

## Download

Grab the latest `PortManager.exe` from the **[Releases page](https://github.com/spoonix-dev/PortManager/releases)** — a single standalone file, no installer, no Python required. A `.sha256` checksum is attached to every release.

## Build from source

Requirements: **Windows 10/11**, **Python 3.11+**.

```bat
git clone https://github.com/spoonix-dev/PortManager.git
cd PortManager
build.bat
```

`build.bat` creates a virtual environment, installs `requirements.txt`, renders the icon from code and runs PyInstaller (`--onefile --noconsole`) → **`dist\PortManager.exe`**.

Run without building:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python portmanager.py              :: normal start
python portmanager.py --minimized  :: start hidden in the tray
```

> Running from source also registers auto-run on first launch (pointing at `pythonw.exe` + the script). Turn it off with the header switch if you only want to test.

### Publishing a release

```bash
git tag v2.0.0
git push origin v2.0.0
```

GitHub Actions (`.github/workflows/release.yml`) builds `PortManager.exe` on `windows-latest` and attaches it, plus its checksum, to the release.

## Project layout

```
PortManager/
├── portmanager.py            # the whole application
├── requirements.txt          # customtkinter, psutil, pystray, Pillow, pyinstaller
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

---

## 🇮🇶 ملخص بالعربية

<div dir="rtl">

**PortManager 2.0** هو مدير مهام ومراقب منافذ شبكة خفيف لنظام ويندوز، بتصميم مستوحى من واجهات iOS، ومفتوح المصدر بالكامل. يعمل بالعربية والإنجليزية مع قلب كامل لاتجاه الواجهة (RTL).

**الجديد في الإصدار 2.0**

- **تصميم iOS:** بطاقات مدوّرة، وشريط بحث وتبويبات على شكل كبسولات (الكل · TCP · UDP · المثبتة · المحمية).
- **الوضع الداكن والفاتح** بزر واحد في الأعلى، مع تنسيق شريط عنوان ويندوز تلقائيًا.
- **تبديل اللغة** بين «عربي» و«EN» فورًا لكل النصوص والقوائم والنوافذ، ويُحفظ اختيارك مع الثيم والمنافذ المثبتة والتسميات في الملف `‎%USERPROFILE%\.portmanager.json`.
- **حارس النظام (System Guard):**
  - **عمليات ويندوز الأساسية** (`System` و`csrss.exe` و`lsass.exe` و`wininit.exe` و`services.exe` و`smss.exe` و`dwm.exe`...): بطاقة بحدّ أحمر وخلفية حمراء وشارة «نظام أساسي»، **وإنهاؤها محظور تمامًا** لمنع الشاشة الزرقاء (BSOD).
  - **خدمات ويندوز** (`svchost.exe` و`explorer.exe` و`spoolsv.exe` و`RuntimeBroker.exe` وكل ما في `System32`): بطاقة حمراء وشارة «نظام»، وتتطلب **نافذة تحذير حمراء صارمة** قبل الإنهاء.
  - **العمليات العادية:** بطاقة عادية مع تأكيد قياسي.
- **شارات ذكية:** «محلي (127.0.0.1)» باللون الأخضر مقابل «شبكة (0.0.0.0 / ::)» بالبرتقالي للمنافذ المكشوفة على الشبكة، وشارة Docker/WSL، وشارة «تطوير» للمنافذ الشائعة.
- **تثبيت المنافذ وتسميتها** مثل «الواجهة الأمامية» أو «قاعدة Postgres».
- **نافذة التفاصيل:** رقم العملية والمنفذ والبروتوكول وإتاحة الشبكة والذاكرة ومدة التشغيل والمسار الكامل وسطر الأوامر.
- **قائمة الزر الأيمن:** فتح في المتصفح، إعادة تشغيل العملية، إنهاء شجرة العمليات، تثبيت/إلغاء تثبيت، تسمية مخصصة، نسخ المنفذ أو PID، وفتح موقع الملف في المستكشف.

**ما بقي كما هو:** نسخة واحدة فقط تعمل، والإخفاء إلى أيقونة الإشعارات مع **إيقاف كامل للمسح** (0% معالج)، والتشغيل التلقائي مع ويندوز، وزر «تنظيف منافذ التطوير» من القائمة أو الأيقونة، وملف تنفيذي واحد `PortManager.exe`.

**التحميل:** <https://github.com/spoonix-dev/PortManager/releases> · **البناء:** شغّل `build.bat` وستجد النتيجة في `dist\PortManager.exe`.

**المطوّر:** مصطفى العقراوي — [@spoonix-dev](https://github.com/spoonix-dev) · الترخيص: MIT

</div>
