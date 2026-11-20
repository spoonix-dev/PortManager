<div align="center">

# PortManager 2.4

**An iOS-styled, animated, multilingual Task Manager & Network Port Monitor for Windows.**

[![License: MIT](https://img.shields.io/badge/License-MIT-0A84FF.svg?style=for-the-badge)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.4.0-30D158.svg?style=for-the-badge)](https://github.com/spoonix-dev/PortManager/releases)
[![Python](https://img.shields.io/badge/Python-3.11%2B-0A84FF.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0A84FF.svg?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/spoonix-dev/PortManager/releases)
[![Languages](https://img.shields.io/badge/languages-EN%20%C2%B7%20AR%20%2B%20your%20own-FF9F0A.svg?style=for-the-badge)](#languages--localization)
[![Downloads](https://img.shields.io/github/downloads/spoonix-dev/PortManager/total?style=for-the-badge&color=FF453A)](https://github.com/spoonix-dev/PortManager/releases)

[**⬇ Download PortManager.exe**](https://github.com/spoonix-dev/PortManager/releases) ·
[Report a bug](https://github.com/spoonix-dev/PortManager/issues) ·
[ملخص بالعربية](#-ملخص-بالعربية)

</div>

---

## Why PortManager?

*"Port 3000 is already in use."* — every developer has been there. PortManager shows **which process owns every listening port**, whether it is **local-only or exposed to your network**, protects Windows itself from accidental kills, and then disappears into the system tray with **all scanning and animation stopped** until you need it again.

## What's new in 2.4

| | |
|---|---|
| 🎞 **Animations** | Cubic ease-in-out **theme morph** (~300 ms, 18 frames) of every colour *and the Windows title bar* · **sliding** tab indicator · sheets **fade and rise** in · sections **expand / collapse** like a drawer · **animated counters** · a **breathing** live dot |
| 🌍 **Modular languages** | All text lives in `locales/*.json`. Drop in `ku.json`, `tr.json`, `fr.json`… and it appears in Settings automatically — with **automatic LTR/RTL mirroring** and **fallback to English** for missing keys. No rebuild needed |
| ⚙️ **Settings gear** | A grouped, scrollable iOS Settings sheet: language, theme (**Dark / Light / System**), animations, density, startup, start minimized, close action, **global hotkey `Ctrl+Alt+P`**, scan interval, NEW badges, **deep threat scanner**, **custom protected ports**, export / import / reset |
| 🗂 **Dedicated sections** | The list is split into collapsible **Pinned · Apps & Developer Tools · Windows System** sections, so system noise never drowns out your dev servers |
| 🏷 **New badges** | `NEW` (just opened) · `PROTECTED` (your own protected ports) · `SUSPICIOUS` (deep scanner) |

Everything from 2.0 is still here: System Guard, Local/LAN/Docker/DEV badges, pinning & custom labels, process inspector, right-click menu (open in browser, restart, end process tree, copy, open file location), Dark/Light palettes, RTL layout, single instance, tray-only background mode, Windows auto-start with `--minimized`, programmatic icons, one-file PyInstaller `.exe`.

## Animations

| Animation | Details |
|---|---|
| **Theme morph** | Switching theme interpolates *every* palette colour (canvas cards, badges, borders, all widgets) with cubic ease-in-out over ~300 ms, and animates the DWM caption colour; the dark/light title-bar flag flips at the midpoint. No flash, no restart |
| **Sliding tab indicator** | The blue pill glides between `All · TCP · UDP · Pinned · Protected` (mirrored in RTL) |
| **Sheets** | Fade in while rising a few pixels; fade out on close |
| **Sections** | Click a header: the section opens/closes like a drawer and the chevron rotates |
| **Counters** | The three summary numbers count up/down to the new value |
| **Live dot** | A soft green "breathing" halo in the status bar while the window is visible |

Timers exist **only while something is animating or the window is visible** — hiding to the tray cancels all of them. *Settings → Appearance → Smooth UI animations* turns every animation off (end states apply instantly, the pulse stops).

## Languages & localization

PortManager ships with **English** and **العربية**. Strings live in plain JSON files:

```json
{
  "_meta": { "code": "tr", "name": "Turkish", "native_name": "Türkçe", "rtl": false },
  "tab_all": "Tümü",
  "tab_pinned": "Sabitlenenler",
  "settings_title": "Ayarlar"
}
```

* `_meta.code` — 2–3 letter language code (`en`, `ku`, `pt-br`). `native_name` is what the Settings list shows. `rtl: true` mirrors the whole UI.
* Any key you leave out falls back to **`en.json`**; a malformed file is ignored (it can never crash the app).
* Start from a copy of [`locales/en.json`](locales/en.json) (181 keys) and translate the values. Keep placeholders like `{port}`, `{name}`, `{n}` unchanged.

**Where files are loaded from** (the external folder wins when both define the same code):

1. `sys._MEIPASS/locales` — the copy bundled inside `PortManager.exe`
2. `locales/` **next to `PortManager.exe`** (or next to `portmanager.py` when running from source)

So users can add languages without rebuilding: create a `locales` folder beside the exe (*Settings → Language & Region → **Open Locales Folder*** does it for you), drop the `.json` in, and reopen Settings. The release page also offers `PortManager-locales.zip`.

## Settings

Open with the **gear** in the top corner (or `Ctrl+,`). Everything applies instantly and is saved.

| Group | Option |
|---|---|
| **Language & Region** | Language list (native names, from `locales/*.json`) · *Open Locales Folder* |
| **Appearance & Animations** | Theme: **Dark / Light / System** · Smooth UI animations · Compact card density |
| **Startup & Window Behavior** | Launch at Windows startup · Start minimized to tray · Close button: **Hide to Tray / Quit App** · Global hotkey `Ctrl+Alt+P` |
| **Monitoring & Security** | Scan interval **1 / 2 / 3 / 5 s** · Highlight newly opened ports (`NEW`) · Deep threat & hidden port scanner · Custom protected ports |
| **Data & Reset** | Export settings · Import settings · Reset all settings |

Notes:

* **System** theme follows Windows' app mode. It is read when the window is shown — never polled.
* **Global hotkey** — `Ctrl+Alt+P` shows the window, or hides it when it is already active. It is a blocking `RegisterHotKey` thread (0 % CPU) that exists only while the option is on; if another app owns the combination you get a message and the switch turns back off.
* **Start minimized** applies to manual launches; the Windows auto-start entry always passes `--minimized`.
* **Custom protected ports** (`3306, 5432, 6379`) — ending a process listening there needs the strict red confirmation, shows a teal `PROTECTED` badge, appears in the *Protected* tab, and is skipped by *Clean Dev Ports*.
* **Deep threat & hidden port scanner** flags (orange border + `SUSPICIOUS`; details in the inspector): a Windows system name running from outside the Windows folder · running from Temp / Downloads / Public / Recycle Bin · LAN-exposed listener on a backdoor-style port (`1337, 4444, 6667, 12345, 31337, 54321`) · LAN-exposed process whose details are hidden. It is a **heuristic — expect false positives** (e.g. elevated dev servers look "hidden" unless PortManager runs as Administrator).
* **New ports** — the first scan after the window is shown is the baseline; ports that appear afterwards wear `NEW` for 45 s.

## Sections & System Guard

The list is grouped into **Pinned** (★), **Apps & Developer Tools** and **Windows System**. *Windows System* is collapsed by default; collapse state is remembered. While you search, or on the *Protected* tab, sections are shown open.

| Tier | Examples | Card | Ending it |
|---|---|---|---|
| 🔒 **Core Windows** | `System`, PID `0`/`4`, `csrss.exe`, `lsass.exe`, `wininit.exe`, `services.exe`, `smss.exe`, `dwm.exe`, `winlogon.exe` | Red border + red tint, solid red `CORE SYSTEM` badge, button reads *Locked* | **Blocked** with a red warning sheet — a BSOD risk. No override |
| ⚠️ **Windows system** | `svchost.exe`, `explorer.exe`, `spoolsv.exe`, `RuntimeBroker.exe`, anything under `System32` | Red border + tint, red `SYSTEM` badge | Strict red warning sheet first |
| ✅ **Normal** | Node, Vite, Python, Docker, databases, your apps | Clean card | Standard confirmation |

PortManager itself is protected too, and look-alikes such as `C:\Users\me\lsass.exe` are *not* trusted as Windows processes. The guard also lives inside the kill engine, not just the UI.

## Using PortManager

### The card

```
┌──────────────────────────────────────────────────────────────────────┐
│ 3000   node.exe ★ [Frontend]                          [Details] [End] │
│ ● TCP  [NEW] [DEV] [Local (127.0.0.1)]                               │
│        PID 18244 · 63.2 MB · 0.4% CPU   node ./node_modules/vite…    │
└──────────────────────────────────────────────────────────────────────┘
```

| Badge | Meaning |
|---|---|
| 🟢 `Local (127.0.0.1)` | Loopback only — not reachable from your network |
| 🟠 `LAN (0.0.0.0 / ::)` | Bound to all interfaces or a LAN address — **reachable by other devices** |
| 🟣 `Docker/WSL` | `wslrelay.exe`, `com.docker.*`, `vmmem`, `vpnkit` … |
| 🔵 `DEV` | `3000, 3001, 4200, 5000, 5173, 8000, 8080, 8888` |
| 🟩 `NEW` · 🟦 `PROTECTED` · 🟧 `SUSPICIOUS` | New port · your protected port · deep-scanner finding |
| 🔴 `CORE SYSTEM` / `SYSTEM` | System Guard tiers |

Compact density hides the third line for denser lists.

### Right-click menu

**Open in Browser** · **Restart Process** (stop, then relaunch with the original command line and folder, detached) · **Force Kill** · **End Process Tree** (process + all recursive children) · **Pin / Unpin Port** · **Set Custom Label…** · **Copy Port Number** · **Copy PID** · **Open File Location**.

> Restart reuses the arguments and working folder, not the original shell environment or terminal.

### Keyboard

| Action | Keys |
|---|---|
| Search · clear | `Ctrl+F` · `Esc` |
| Move between cards | `↑ ↓ PgUp PgDn Home End` |
| Inspector | `Enter` or double-click |
| End Task · Force Kill | `Delete` · `Shift+Delete` |
| Clean Dev Ports | `Ctrl+Shift+D` |
| Settings | `Ctrl+,` |
| Refresh | `F5` / `Ctrl+R` |
| Show / hide from anywhere | `Ctrl+Alt+P` (when enabled) |
| Quit | `Ctrl+Q` or tray → **Quit PortManager** |

### System tray

Left-click shows the window. Right-click: *Show*, *Clean Dev Ports* (no window needed, result as a notification), *Settings…*, *Start with Windows* ✓, *GitHub*, *Quit* — in your language.

> Other users' and elevated processes can only be inspected or ended when PortManager runs **as Administrator**.

## Configuration

One small JSON file: **`%USERPROFILE%\.portmanager.json`** (also what *Export / Import* use).

```json
{
  "version": "2.4.0",
  "language": "ar",
  "theme": "system",
  "animations": true,
  "compact": false,
  "start_minimized": false,
  "close_action": "tray",
  "hotkey": true,
  "scan_interval": 2,
  "highlight_new": true,
  "deep_scan": false,
  "protected_ports": [3306, 5432],
  "pinned": [3000, 5432],
  "labels": { "3000": "Frontend", "5432": "Postgres DB" },
  "collapsed": ["system"],
  "startup_initialized": true
}
```

Writes are atomic; invalid or hand-edited values are ignored individually. On first launch the language follows your Windows display language *if that locale file exists*.

### Upgrading from 2.0

* The header's `عربي`/`EN` chip and startup switch moved into **Settings** (the quick **Dark/Light** button stays). Your existing config is read as-is; new keys get defaults.
* Theme is now `dark | light | system`.
* Languages are no longer embedded in `portmanager.py` — keep the `locales/` folder with the script (the build bundles it).

## Zero idle cost

* Scanning, the live-dot pulse and every animation use `after()` timers that exist **only while the window is visible**; hiding to the tray or minimizing cancels them and drops the process cache. Each scan runs in a short-lived worker thread.
* The global hotkey (`GetMessage`) and the second-instance wake-up (`WaitForSingleObject`) are blocking waits, not polls.
* The card list is virtualized — only visible cards are drawn.
* customtkinter keeps a few tiny idle timers of its own; in practice the hidden app sits at 0 % CPU.

## Download

Grab `PortManager.exe` from **[Releases](https://github.com/spoonix-dev/PortManager/releases)** — one standalone file, no installer, no Python. Each release also has a `.sha256` checksum and `PortManager-locales.zip`.

## Build from source

Requirements: **Windows 10/11**, **Python 3.11+**.

```bat
git clone https://github.com/spoonix-dev/PortManager.git
cd PortManager
build.bat
```

`build.bat` creates a venv, installs `requirements.txt`, renders the icon from code and runs PyInstaller (`--onefile --noconsole`) with **`--add-data "locales;locales"`**, producing **`dist\PortManager.exe`**. Keep `locales\` beside `build.bat` (the script checks).

Run without building:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python portmanager.py              :: normal start (reads .\locales\)
python portmanager.py --minimized  :: start hidden in the tray
```

> Running from source also registers auto-run on first launch. Turn it off in *Settings → Startup*.

### Publishing a release

```bash
git tag v2.4.0
git push origin v2.4.0
```

`.github/workflows/release.yml` validates every `locales/*.json` (`_meta` header, language code, missing keys → warnings), builds on `windows-latest` via `build.bat` (so `locales/` is bundled), and attaches `PortManager.exe`, its checksum and `PortManager-locales.zip`.

## Project layout

```
PortManager/
├── portmanager.py            # the application
├── locales/
│   ├── en.json               # English (fallback language)
│   └── ar.json               # العربية (rtl)
├── requirements.txt          # customtkinter, psutil, pystray, Pillow, pyinstaller
├── build.bat                 # one-click PyInstaller build (bundles locales/)
├── README.md
├── LICENSE                   # MIT
├── .gitignore
└── .github/workflows/release.yml
```

## Known limitations

* Sheets fade and rise (no scaling); during a theme morph with Settings open, very slow machines may drop frames — the duration stays ~300 ms.
* The deep scanner is heuristic, not an antivirus. *Hidden-process* findings need Administrator rights to be conclusive.
* Restart cannot recreate the original terminal or environment.

## Author & attribution

**PortManager** is open source, created and maintained by **Mustafa Al-Aqrawi (مصطفى العقراوي)** — GitHub [**@spoonix-dev**](https://github.com/spoonix-dev).

* Repository: <https://github.com/spoonix-dev/PortManager>
* Forks and redistributions must keep the copyright notice and the MIT license text, including the credit to *Mustafa Al-Aqrawi (spoonix-dev)*. The in-app credit line (`PortManager • Open Source by Mustafa Al-Aqrawi (@spoonix-dev)`) is part of that attribution.

## License

[MIT](LICENSE) © 2026 Mustafa Al-Aqrawi (spoonix-dev)

---

## 🇮🇶 ملخص بالعربية

<div dir="rtl">

**PortManager 2.4** هو مدير مهام ومراقب منافذ شبكة لنظام ويندوز بتصميم iOS، مفتوح المصدر، ويعمل بالعربية والإنجليزية (وأي لغة تضيفها).

**الجديد في 2.4**

- **حركات سلسة:** انتقال ناعم بين الوضعين الداكن والفاتح (بما في ذلك شريط عنوان ويندوز)، ومؤشر منزلق في التبويبات، ونوافذ تظهر بتلاشٍ وارتفاع خفيف، وأقسام قابلة للطيّ، وعدّادات متحركة، ونبضة خضراء للمؤشر الحي. يمكن إيقاف كل الحركات من الإعدادات.
- **لغات مرنة:** كل النصوص في ملفات `locales/*.json`. ضع ملف لغة جديد (مثل `ku.json` أو `tr.json`) فيظهر تلقائيًا في الإعدادات مع قلب الاتجاه (RTL) وعودة للإنجليزية للمفاتيح الناقصة — **دون إعادة بناء البرنامج**. تُقرأ الملفات من داخل الملف التنفيذي ومن مجلد `locales` بجوار `PortManager.exe`.
- **زر الإعدادات (الترس):** اللغة والمنطقة، المظهر (داكن / فاتح / النظام) والحركات والكثافة، التشغيل مع ويندوز والبدء مصغّرًا وسلوك زر الإغلاق واختصار `Ctrl+Alt+P` العام، فترة الفحص (1/2/3/5 ث) وتمييز المنافذ الجديدة وفاحص التهديدات والمنافذ المحمية المخصصة، وتصدير/استيراد/إعادة ضبط الإعدادات.
- **فصل مخصص للأقسام:** «المثبتة» و«التطبيقات وأدوات المطوّر» و«نظام ويندوز» (مطوي افتراضيًا)، فلا تُغرق عمليات النظام خوادم التطوير.
- **حارس النظام:** عمليات ويندوز الأساسية مقفلة تمامًا، وخدمات النظام تتطلب تأكيدًا أحمر صارمًا، والمنافذ التي تحميها بنفسك تتطلب تأكيدًا صارمًا أيضًا.

**ما بقي:** نسخة واحدة فقط، والإخفاء إلى أيقونة الإشعارات مع إيقاف كل المؤقتات (0% معالج)، والتشغيل التلقائي مع `--minimized`، وملف تنفيذي واحد.

**التحميل:** <https://github.com/spoonix-dev/PortManager/releases> · **البناء:** شغّل `build.bat` (يضمّ مجلد `locales`) فتجد `dist\PortManager.exe`.

**المطوّر:** مصطفى العقراوي — [@spoonix-dev](https://github.com/spoonix-dev) · الترخيص: MIT

</div>
