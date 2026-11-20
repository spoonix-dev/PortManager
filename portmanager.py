#!/usr/bin/env python3
"""
PortManager 2.4 — an iOS-styled, animated, multilingual Task Manager & Network Port Monitor for Windows.

    Author      : Mustafa Al-Aqrawi (مصطفى العقراوي) — https://github.com/spoonix-dev
    Repository  : https://github.com/spoonix-dev/PortManager
    License     : MIT — Copyright (c) 2026 Mustafa Al-Aqrawi (spoonix-dev)

Architecture (unchanged since 1.x)
----------------------------------
* Single instance  : Windows named mutex; a second launch wakes the first via a named event.
* Zero idle CPU    : scanning, pulsing and every animation timer exist only while the window is mapped. Hiding to
                     the tray (or minimizing) cancels them and drops the process cache. The global hotkey and the
                     second-instance listener are blocking waits (GetMessage / WaitForSingleObject), not polls.
* Startup          : HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run, launched with --minimized.
* System Guard     : core Windows processes are locked, system services need a strict red confirmation.
* No asset files   : window, tray and .exe icons and every glyph are drawn in memory with Pillow.

New in 2.4
----------
* Animations (all switchable): cubic ease-in-out theme morph of every colour + the DWM title bar, sliding tab
  indicator, sheet fade/rise, collapsible sections, animated counters, breathing live dot.
* locales/*.json — drop in a file with a "_meta" header to add a language (LTR/RTL, falls back to en.json);
  read from the PyInstaller bundle and from a folder next to the exe.
* Settings gear: language, theme (Dark / Light / System), animations, density, startup, start minimized, close
  action, global hotkey Ctrl+Alt+P, scan interval, NEW badges, deep threat scanner, custom protected ports,
  export / import / reset.
* Dedicated sections: Pinned · Apps & Developer Tools · Windows System.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
import weakref
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog
from tkinter import font as tkfont

if sys.platform != "win32":
    sys.exit("PortManager is a Windows application.")

import ctypes
import winreg
from ctypes import wintypes

import customtkinter as ctk
import psutil
import pystray
from PIL import Image, ImageDraw, ImageTk

# ──────────────────────────────────────────────────────────────────────────────
# Identity
# ──────────────────────────────────────────────────────────────────────────────
APP_NAME = "PortManager"
__version__ = "2.4.0"
__author__ = "Mustafa Al-Aqrawi (مصطفى العقراوي)"
__license__ = "MIT"
AUTHOR_HANDLE = "spoonix-dev"
PROFILE_URL = "https://github.com/spoonix-dev"
REPO_URL = "https://github.com/spoonix-dev/PortManager"
CREDIT_TEXT = "PortManager • Open Source by Mustafa Al-Aqrawi (@spoonix-dev)"

# ──────────────────────────────────────────────────────────────────────────────
# Behaviour
# ──────────────────────────────────────────────────────────────────────────────
DEV_PORTS = frozenset({3000, 3001, 4200, 5000, 5173, 8000, 8080, 8888})
FIRST_CYCLE_MS = 800
SCAN_INTERVALS = (1, 2, 3, 5)             # seconds (Settings → Monitoring)
ANIM_MS, ANIM_FRAMES = 300, 18            # theme morph: ~300 ms, nominally 18 frames
NEW_BADGE_SECONDS = 45
HOTKEY_ID, HOTKEY_VK = 0x504D, 0x50       # Ctrl+Alt+P
SUSPECT_PORTS = frozenset({1337, 4444, 6667, 12345, 31337, 54321})
SUSPECT_DIR_MARKERS = ("\\appdata\\local\\temp\\", "\\windows\\temp\\", "\\downloads\\", "\\users\\public\\",
                       "$recycle.bin", "\\programdata\\temp\\")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MB = 1024 * 1024

TIER_CORE, TIER_SYSTEM, TIER_USER, TIER_SELF = "core", "system", "user", "self"
SELF_PIDS = {os.getpid()}
if getattr(sys, "frozen", False):                 # PyInstaller --onefile: never touch our own bootloader
    SELF_PIDS.add(os.getppid())

SYSROOT = (os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows").lower().rstrip("\\")
# Kernel-level pseudo processes (no executable on disk).
KERNEL_NAMES = frozenset({"system", "system idle process", "registry", "memory compression", "secure system"})
# Hard-locked: killing any of these crashes Windows.
CORE_NAMES = frozenset({"csrss.exe", "lsass.exe", "wininit.exe", "services.exe", "smss.exe", "dwm.exe",
                        "winlogon.exe", "lsaiso.exe"})
# Windows system services / shell: allowed only behind a strict warning.
SYSTEM_NAMES = frozenset({
    "svchost.exe", "explorer.exe", "spoolsv.exe", "runtimebroker.exe", "sihost.exe", "taskhostw.exe",
    "searchindexer.exe", "searchhost.exe", "audiodg.exe", "wudfhost.exe", "dllhost.exe", "ctfmon.exe",
    "fontdrvhost.exe", "conhost.exe", "wmiprvse.exe", "msdtc.exe", "lsm.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "textinputhost.exe", "securityhealthservice.exe", "msmpeng.exe",
})
SYSTEM_DIRS = ("system32", "syswow64", "winsxs", "systemapps", "servicing")
MASQ_NAMES = CORE_NAMES | frozenset({"svchost.exe", "explorer.exe", "spoolsv.exe", "runtimebroker.exe",
                                     "taskhostw.exe", "sihost.exe", "dllhost.exe", "conhost.exe"})


# ══════════════════════════════════════════════════════════════════════════════
# Apple system colours — Dark and Light palettes
# ══════════════════════════════════════════════════════════════════════════════
DARK = dict(
    bg="#000000", card="#1C1C1E", card_hi="#2C2C2E", field="#1C1C1E", pop="#2C2C2E", hover="#2C2C2E",
    sep="#38383A", text="#FFFFFF", text2="#98989D", text3="#636366",
    blue="#0A84FF", red="#FF453A", green="#30D158", orange="#FF9F0A", purple="#BF5AF2", teal="#64D2FF",
    on_accent="#FFFFFF",
)
LIGHT = dict(
    bg="#F2F2F7", card="#FFFFFF", card_hi="#EFEFF4", field="#E3E3E8", pop="#FFFFFF", hover="#F2F2F7",
    sep="#D1D1D6", text="#000000", text2="#6C6C70", text3="#AEAEB2",
    blue="#007AFF", red="#FF3B30", green="#34C759", orange="#FF9500", purple="#AF52DE", teal="#32ADE6",
    on_accent="#FFFFFF",
)


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(a: str, b: str, t: float) -> str:
    ra, rb = hex_rgb(a), hex_rgb(b)
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(ra, rb))


def derive(p: dict) -> dict:
    d = dict(p)
    d["core_fill"] = mix(p["card"], p["red"], 0.17)
    d["sys_fill"] = mix(p["card"], p["red"], 0.09)
    for name in ("blue", "red", "green", "orange", "purple", "teal"):
        d[f"{name}_tint"] = mix(p["card"], p[name], 0.16)
        d[f"{name}_tint_hi"] = mix(p["card"], p[name], 0.26)
    d["gray_tint"] = mix(p["card"], p["text2"], 0.16)
    d["blue_hi"] = mix(p["blue"], p["text"], 0.14)
    d["red_hi"] = mix(p["red"], p["text"], 0.14)
    d["card_hi_hi"] = mix(p["card_hi"], p["text"], 0.07)
    return d


_DD, _DL = derive(DARK), derive(LIGHT)
PAL: dict[str, str] = dict(_DD)          # the live palette: canvas drawing reads it, theme morphing rewrites it


def palette_for(dark: bool) -> dict:
    return _DD if dark else _DL


def set_palette(dark: bool) -> None:
    PAL.clear()
    PAL.update(palette_for(dark))


def ease_in_out_cubic(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 4 * t * t * t if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2


# ── Themed widgets ───────────────────────────────────────────────────────────
# Every colour handed to a widget is a `Tok`: a str that remembers its palette token. The Themed mixin records
# those tokens, so the whole widget tree can be re-painted from the live palette on every frame of a theme morph
# (CustomTkinter's own light/dark tuples cannot be interpolated).
class Tok(str):
    token: str

    def __new__(cls, value: str, token: str):
        o = super().__new__(cls, value)
        o.token = token
        return o


class _Tokens:
    def __getitem__(self, name: str) -> Tok:
        return Tok(PAL[name], name)


TK = _Tokens()
THEMED: "weakref.WeakSet" = weakref.WeakSet()        # widgets that carry colour tokens
REFRESHABLE: "weakref.WeakSet" = weakref.WeakSet()   # canvas widgets that redraw from PAL (.refresh())


class Themed:
    _pm_nobg = False

    def __init__(self, *args, **kw):
        self._toks = {k: v.token for k, v in kw.items() if isinstance(v, Tok)}
        super().__init__(*args, **kw)
        THEMED.add(self)

    def configure(self, *args, **kw):
        for k, v in kw.items():
            if isinstance(v, Tok):
                self._toks[k] = v.token
            elif k in self._toks and k.endswith("color"):
                del self._toks[k]
        return super().configure(*args, **kw)

    def repaint(self, bg: str | None) -> None:
        kw = {k: PAL[t] for k, t in self._toks.items()}
        if bg is not None:
            kw["bg_color"] = bg
        if not kw:
            return
        try:
            super().configure(**kw)               # next in the MRO: skips the token bookkeeping above
        except (tk.TclError, ValueError, AttributeError):
            pass


def effective_bg(w) -> str:
    """Colour of whatever sits behind `w` (nearest ancestor with a solid fill) — for rounded-corner canvases."""
    m = getattr(w, "master", None)
    while m is not None:
        toks = getattr(m, "_toks", None)
        if toks and "fg_color" in toks:
            return PAL[toks["fg_color"]]
        m = getattr(m, "master", None)
    return PAL["bg"]


def repaint_all() -> None:
    for w in list(THEMED):
        try:
            solid = hasattr(w, "_bg_color") and not w._pm_nobg
            w.repaint(effective_bg(w) if solid else None)
        except Exception:
            pass
    for r in list(REFRESHABLE):
        try:
            r.refresh()
        except Exception:
            pass


class Frame(Themed, ctk.CTkFrame):
    pass


class Label(Themed, ctk.CTkLabel):
    pass


class Button(Themed, ctk.CTkButton):
    pass


class Entry(Themed, ctk.CTkEntry):
    pass


class Switch(Themed, ctk.CTkSwitch):
    pass


class Scrollbar(Themed, ctk.CTkScrollbar):
    pass


class Textbox(Themed, ctk.CTkTextbox):
    pass


class ScrollFrame(Themed, ctk.CTkScrollableFrame):
    _pm_nobg = True


class Toplevel(Themed, ctk.CTkToplevel):
    pass


class Root(Themed, ctk.CTk):
    pass




# ══════════════════════════════════════════════════════════════════════════════
# Localization — external JSON files in  locales/  (bundled + user-extensible)
# ══════════════════════════════════════════════════════════════════════════════
LANG_CODE_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")
# Windows primary-language id → locale code (used only for the very first launch)
LANGID_MAP = {0x01: "ar", 0x1F: "tr", 0x0C: "fr", 0x07: "de", 0x0A: "es", 0x12: "ko", 0x04: "zh", 0x19: "ru",
              0x16: "pt", 0x10: "it", 0x11: "ja", 0x92: "ku", 0x29: "fa", 0x20: "ur", 0x0D: "he"}


class Locales:
    """Loads locales/*.json. A file needs a "_meta" header: {"code", "name", "native_name", "rtl"}; every other
    key is a string. Missing keys fall back to en.json. Folders are read from the PyInstaller bundle
    (sys._MEIPASS/locales) first, then from the folder next to the exe / script — the external one wins, so users
    can add or override languages without rebuilding."""

    def __init__(self) -> None:
        self.meta: dict[str, dict] = {}
        self.data: dict[str, dict[str, str]] = {}
        self.scan()

    @staticmethod
    def external_dir() -> Path:
        base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        return base / "locales"

    @classmethod
    def dirs(cls) -> list[Path]:
        out = []
        bundled = getattr(sys, "_MEIPASS", None)
        if bundled:
            out.append(Path(bundled) / "locales")
        ext = cls.external_dir()
        if ext not in out:
            out.append(ext)
        return out

    def scan(self) -> None:
        meta: dict[str, dict] = {}
        data: dict[str, dict[str, str]] = {}
        for d in self.dirs():
            try:
                files = sorted(d.glob("*.json")) if d.is_dir() else []
            except OSError:
                files = []
            for f in files:
                try:
                    raw = json.loads(f.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError):
                    continue
                m = raw.get("_meta") if isinstance(raw, dict) else None
                if not isinstance(m, dict):
                    continue
                code = str(m.get("code") or f.stem).strip().lower()
                if not LANG_CODE_RE.match(code):
                    continue
                name = str(m.get("name") or code)
                meta[code] = {"code": code, "name": name, "native_name": str(m.get("native_name") or name),
                              "rtl": bool(m.get("rtl", False))}
                data[code] = {k: v for k, v in raw.items() if k != "_meta" and isinstance(v, str)}
        self.meta, self.data = meta, data

    def codes(self) -> list[str]:
        return sorted(self.meta, key=lambda c: (c != "en", self.meta[c]["name"].lower()))

    def is_rtl(self, code: str) -> bool:
        return bool(self.meta.get(code, {}).get("rtl", False))

    def native_name(self, code: str) -> str:
        return self.meta.get(code, {}).get("native_name", code)

    def get(self, lang: str, key: str) -> str | None:
        return self.data.get(lang, {}).get(key) or self.data.get("en", {}).get(key)


LOC = Locales()
I18N = {"lang": "en", "rtl": False}


def set_language(code: str) -> str:
    """Activate a locale (falls back to English if it is not available). Returns the code actually used."""
    code = (code or "en").lower()
    if code not in LOC.meta:
        code = "en"
    I18N["lang"], I18N["rtl"] = code, LOC.is_rtl(code)
    return code


def T(key: str, **kw) -> str:
    s = LOC.get(I18N["lang"], key)
    if s is None:
        return key
    if not kw:
        return s
    try:
        return s.format(**kw)
    except (KeyError, IndexError, ValueError):          # a broken translation must never crash the UI
        return s


def tr_code(code: str) -> str:
    return LOC.get(I18N["lang"], "res_" + code) or code


def default_language() -> str:
    """First launch only: the Windows display language if we ship / the user added that locale."""
    try:
        langid = ctypes.WinDLL("kernel32").GetUserDefaultUILanguage()
        code = LANGID_MAP.get(langid & 0x3FF, "en")
        return code if code in LOC.meta else "en"
    except Exception:
        return "en"


# ══════════════════════════════════════════════════════════════════════════════
# Config  (~/.portmanager.json)
# ══════════════════════════════════════════════════════════════════════════════
class Config:
    """Every setting, pin, label and protected port — one small JSON file in the user's home folder."""

    PATH = Path.home() / ".portmanager.json"
    LEGACY = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME / "settings.json"   # PortManager 1.x

    def __init__(self) -> None:
        self.reset_values()
        fresh = not self.PATH.exists()
        self.load()
        if fresh:
            self.lang = default_language()

    def reset_values(self) -> None:
        self.lang = "en"
        self.theme = "dark"                     # dark | light | system
        self.animations = True
        self.compact = False
        self.start_minimized = False
        self.close_action = "tray"              # tray | quit
        self.hotkey = False                     # Ctrl+Alt+P
        self.scan_interval = 2                  # seconds
        self.highlight_new = True
        self.deep_scan = False
        self.protected_ports: set[int] = set()
        self.pinned: set[int] = set()
        self.labels: dict[int, str] = {}
        self.collapsed: set[str] = {"system"}
        self.startup_initialized = False

    @staticmethod
    def _port(v) -> int | None:
        try:
            if isinstance(v, bool):
                return None
            n = int(v)
            return n if 0 < n < 65536 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bool(raw: dict, key: str, current: bool) -> bool:
        return raw[key] if isinstance(raw.get(key), bool) else current

    def apply_dict(self, raw) -> int:
        """Validate and apply a settings dict (file load / import). Returns how many known keys were accepted."""
        if not isinstance(raw, dict):
            return 0
        n = 0
        if isinstance(raw.get("language"), str) and LANG_CODE_RE.match(raw["language"].lower()):
            self.lang, n = raw["language"].lower(), n + 1
        if raw.get("theme") in ("dark", "light", "system"):
            self.theme, n = raw["theme"], n + 1
        for key in ("animations", "compact", "start_minimized", "hotkey", "highlight_new", "deep_scan"):
            if isinstance(raw.get(key), bool):
                setattr(self, key, raw[key])
                n += 1
        if raw.get("close_action") in ("tray", "quit"):
            self.close_action, n = raw["close_action"], n + 1
        if raw.get("scan_interval") in SCAN_INTERVALS:
            self.scan_interval, n = raw["scan_interval"], n + 1
        for key in ("pinned", "protected_ports"):
            if isinstance(raw.get(key), list):
                setattr(self, key, {p for p in (self._port(x) for x in raw[key]) if p is not None})
                n += 1
        if isinstance(raw.get("labels"), dict):
            self.labels = {p: str(v).strip()[:40] for k, v in raw["labels"].items()
                           if (p := self._port(k)) is not None and str(v).strip()}
            n += 1
        if isinstance(raw.get("collapsed"), list):
            self.collapsed = {str(x) for x in raw["collapsed"] if str(x) in ("pinned", "apps", "system")}
            n += 1
        if isinstance(raw.get("startup_initialized"), bool):
            self.startup_initialized = raw["startup_initialized"]
            n += 1
        return n

    def load(self) -> None:
        try:
            raw = json.loads(self.PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self.apply_dict(raw)
        if isinstance(raw, dict) and "startup_initialized" not in raw:   # migrate the 1.x flag
            try:
                if json.loads(self.LEGACY.read_text(encoding="utf-8")).get("startup_initialized"):
                    self.startup_initialized = True
            except (OSError, ValueError, AttributeError):
                pass

    def to_dict(self) -> dict:
        return {
            "version": __version__, "language": self.lang, "theme": self.theme, "animations": self.animations,
            "compact": self.compact, "start_minimized": self.start_minimized, "close_action": self.close_action,
            "hotkey": self.hotkey, "scan_interval": self.scan_interval, "highlight_new": self.highlight_new,
            "deep_scan": self.deep_scan, "protected_ports": sorted(self.protected_ports),
            "pinned": sorted(self.pinned), "labels": {str(k): v for k, v in sorted(self.labels.items())},
            "collapsed": sorted(self.collapsed), "startup_initialized": self.startup_initialized,
        }

    def save(self) -> None:
        try:
            tmp = self.PATH.with_name(self.PATH.name + ".tmp")
            tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.PATH)
        except OSError:
            pass

    def resolve_dark(self) -> bool:
        return self.theme == "dark" or (self.theme == "system" and system_is_dark())


# ══════════════════════════════════════════════════════════════════════════════
# Windows platform helpers
# ══════════════════════════════════════════════════════════════════════════════
class SingleInstance:
    """Named-mutex single-instance guard + a named event so a 2nd launch can wake the 1st."""

    MUTEX_NAME = "Local\\PortManager.SingleInstance.Mutex"
    EVENT_NAME = "Local\\PortManager.SingleInstance.Show"
    ERROR_ALREADY_EXISTS = 183
    INFINITE = 0xFFFFFFFF

    def __init__(self) -> None:
        k = self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        k.CreateMutexW.restype = wintypes.HANDLE
        k.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
        k.CreateEventW.restype = wintypes.HANDLE
        k.SetEvent.argtypes = (wintypes.HANDLE,)
        k.SetEvent.restype = wintypes.BOOL
        k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        k.ReleaseMutex.restype = wintypes.BOOL
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        k.CloseHandle.restype = wintypes.BOOL
        self._mutex = k.CreateMutexW(None, False, self.MUTEX_NAME)
        self.already_running = ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS
        self._event = k.CreateEventW(None, False, False, self.EVENT_NAME)
        self._closed = False

    def signal_existing(self) -> None:
        if self._event:
            self._k.SetEvent(self._event)

    def listen(self, callback) -> None:
        """Block-wait (0 % CPU) on the show-event in a daemon thread."""
        if not self._event:
            return

        def loop() -> None:
            while not self._closed:
                self._k.WaitForSingleObject(self._event, self.INFINITE)
                if self._closed:
                    break
                callback()

        threading.Thread(target=loop, name="pm-wake", daemon=True).start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._event:
            self._k.SetEvent(self._event)
        if self._mutex and not self.already_running:
            self._k.ReleaseMutex(self._mutex)
        for h in (self._event, self._mutex):
            if h:
                self._k.CloseHandle(h)


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pyw if pyw.exists() else Path(sys.executable)
    return f'"{interpreter}" "{Path(__file__).resolve()}" --minimized'


def get_startup_value() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, APP_NAME)[0]
    except OSError:
        return None


def is_startup_enabled() -> bool:
    return get_startup_value() is not None


def set_startup(enabled: bool) -> bool:
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def bootstrap_startup(cfg: Config) -> None:
    """Register auto-run on the very first launch; afterwards respect the user's choice."""
    if not cfg.startup_initialized:
        if set_startup(True):
            cfg.startup_initialized = True
            cfg.save()
    elif is_startup_enabled() and get_startup_value() != startup_command():
        set_startup(True)


_DWM: dict = {}


def system_is_dark() -> bool:
    """Windows "app mode" (Settings → Personalization → Colors). Read on demand — never polled."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return True


def set_titlebar(window, dark: bool, bg: str, fg: str, repaint: bool = True) -> None:
    """Dark/light Windows title bar via DwmSetWindowAttribute (+ caption colours on Windows 11).
    `repaint=False` is used for the in-between frames of a theme morph (only the colours change)."""
    try:
        if not _DWM:
            user32, dwm = ctypes.WinDLL("user32"), ctypes.WinDLL("dwmapi")
            user32.GetParent.argtypes = (wintypes.HWND,)
            user32.GetParent.restype = wintypes.HWND
            user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, wintypes.UINT)
            dwm.DwmSetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
            dwm.DwmSetWindowAttribute.restype = ctypes.c_long
            _DWM.update(user32=user32, dwm=dwm)
        user32, dwm = _DWM["user32"], _DWM["dwm"]
        child = window.winfo_id()
        hwnd = user32.GetParent(child) or child
        if repaint:
            flag = ctypes.c_int(1 if dark else 0)
            for attr in (20, 19):                               # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old id)
                if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(flag), ctypes.sizeof(flag)) == 0:
                    break
        for attr, colour in ((35, bg), (36, fg)):               # DWMWA_CAPTION_COLOR / TEXT_COLOR (Windows 11)
            r, g, b = hex_rgb(colour)
            ref = ctypes.c_uint(r | (g << 8) | (b << 16))
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ref), ctypes.sizeof(ref))
        if repaint:
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)   # redraw the frame
    except Exception:
        pass


class Hotkey:
    """Global Ctrl+Alt+P via RegisterHotKey. The thread sits in GetMessage (blocking, 0 % CPU) and exists only
    while the setting is on."""

    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
    WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

    def __init__(self, callback) -> None:
        self.callback = callback
        self._tid = 0
        self._thread: threading.Thread | None = None
        self.active = False

    def start(self) -> bool:
        if self.active:
            return True
        ready, result = threading.Event(), {"ok": False}

        def run() -> None:
            u = ctypes.WinDLL("user32", use_last_error=True)
            k = ctypes.WinDLL("kernel32")
            u.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
            u.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
            u.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
            self._tid = k.GetCurrentThreadId()
            result["ok"] = bool(u.RegisterHotKey(None, HOTKEY_ID, self.MOD_CONTROL | self.MOD_ALT | self.MOD_NOREPEAT,
                                                 HOTKEY_VK))
            ready.set()
            if not result["ok"]:
                return
            msg = wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == self.WM_HOTKEY:
                    self.callback()
            u.UnregisterHotKey(None, HOTKEY_ID)

        self._thread = threading.Thread(target=run, name="pm-hotkey", daemon=True)
        self._thread.start()
        ready.wait(2.0)
        self.active = result["ok"]
        return self.active

    def stop(self) -> None:
        if self.active and self._tid:
            try:
                u = ctypes.WinDLL("user32")
                u.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
                u.PostThreadMessageW(self._tid, self.WM_QUIT, 0, 0)
            except Exception:
                pass
        self.active = False
        self._tid = 0


# ══════════════════════════════════════════════════════════════════════════════
# In-memory icons (no .ico/.png files)
# ══════════════════════════════════════════════════════════════════════════════
def make_icon(size: int = 256) -> Image.Image:
    """PortManager app icon: blue squircle with three port rows, drawn 4× supersampled."""
    S = size * 4
    top, bottom = (52, 160, 255), (0, 90, 210)
    col = Image.new("RGB", (1, S))
    px = col.load()
    for y in range(S):
        t = y / (S - 1)
        px[0, y] = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    gradient = col.resize((S, S))
    mask = Image.new("L", (S, S), 0)
    m = int(S * 0.035)
    ImageDraw.Draw(mask).rounded_rectangle((m, m, S - m, S - m), radius=int(S * 0.225), fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(gradient, (0, 0), mask)
    d = ImageDraw.Draw(img)
    white, green = (255, 255, 255, 255), (48, 209, 88, 255)
    for cy, end, dot in ((0.335, 0.73, white), (0.50, 0.64, green), (0.665, 0.70, white)):
        r, cx = int(S * 0.04), int(S * 0.255)
        d.ellipse((cx - r, int(S * cy) - r, cx + r, int(S * cy) + r), fill=dot)
        d.rounded_rectangle((int(S * 0.36), int(S * cy - S * 0.036), int(S * end), int(S * cy + S * 0.036)),
                            radius=int(S * 0.036), fill=white)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def draw_glyph(kind: str, color: str, size: int = 64) -> Image.Image:
    """Small monochrome UI glyphs (sun / moon / search) drawn in memory."""
    S = size * 4
    rgba = hex_rgb(color) + (255,)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c, w = S / 2, max(2, int(S * 0.075))
    if kind == "sun":
        r = S * 0.17
        d.ellipse((c - r, c - r, c + r, c + r), fill=rgba)
        for k in range(8):
            a = math.pi / 4 * k
            d.line((c + S * 0.29 * math.cos(a), c + S * 0.29 * math.sin(a),
                    c + S * 0.42 * math.cos(a), c + S * 0.42 * math.sin(a)), fill=rgba, width=w)
    elif kind == "moon":
        r = S * 0.36
        d.ellipse((c - r, c - r, c + r, c + r), fill=rgba)
        cut = Image.new("L", (S, S), 0)
        ImageDraw.Draw(cut).ellipse((c - r + S * 0.20, c - r - S * 0.14, c + r + S * 0.20, c + r - S * 0.14), fill=255)
        img.paste((0, 0, 0, 0), (0, 0, S, S), cut)
    elif kind == "gear":
        n, ro, ri = 8, S * 0.42, S * 0.31
        pts = []
        for i in range(n):
            a0, w = 2 * math.pi * i / n, math.pi / n
            for da, rad in ((-0.60 * w, ri), (-0.36 * w, ro), (0.36 * w, ro), (0.60 * w, ri)):
                pts.append((c + rad * math.cos(a0 + da), c + rad * math.sin(a0 + da)))
        d.polygon(pts, fill=rgba)
        hole = S * 0.15
        d.ellipse((c - hole, c - hole, c + hole, c + hole), fill=(0, 0, 0, 0))
    elif kind == "search":
        r, o = S * 0.26, S * 0.42
        d.ellipse((o - r, o - r, o + r, o + r), outline=rgba, width=w)
        d.line((o + r * 0.72, o + r * 0.72, S * 0.84, S * 0.84), fill=rgba, width=w)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def export_icon(path: str) -> None:
    """`portmanager.py --export-icon file.ico` — used by build.bat so the .exe gets the same icon."""
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    make_icon(256).save(path, format="ICO", sizes=sizes)


# ══════════════════════════════════════════════════════════════════════════════
# Process / port engine
# ══════════════════════════════════════════════════════════════════════════════
@dataclass(slots=True)
class Listener:
    proto: str
    bind: str
    port: int
    pid: int


@dataclass(slots=True)
class ProcInfo:
    name: str
    cmdline: list
    cmd: str
    exe: str
    started: float
    tier: str
    docker: bool
    threats: tuple = ()


@dataclass(slots=True)
class Row:
    key: str
    port: int
    proto: str
    pid: int
    name: str
    cpu: float
    ram: float
    bind: str            # all bound addresses, joined
    exposure: str        # "local" | "lan"
    expo_ip: str         # text shown inside the exposure badge
    cmd: str
    exe: str
    started: float
    tier: str
    docker: bool
    dev: bool
    hay: str
    threats: tuple = ()


def _safe(fn, default):
    try:
        return fn()
    except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return default


def _norm(path: str) -> str:
    return (path or "").lower().replace("/", "\\")


def _in_windows_dir(exe: str) -> bool:
    return _norm(exe).startswith(SYSROOT + "\\")


def _in_system_dirs(exe: str) -> bool:
    p = _norm(exe)
    return any(p.startswith(f"{SYSROOT}\\{d}\\") for d in SYSTEM_DIRS) or p == f"{SYSROOT}\\explorer.exe"


def classify(pid: int, name: str = "", exe: str = "") -> str:
    """System Guard tiers: core (locked) · system (strict warning) · user · self."""
    n = (name or "").lower()
    if pid in SELF_PIDS:
        return TIER_SELF
    if pid in (0, 4) or n in KERNEL_NAMES:
        return TIER_CORE
    if n in CORE_NAMES:                                   # a look-alike outside C:\Windows is just a user process
        return TIER_CORE if (not exe or _in_windows_dir(exe)) else TIER_USER
    if exe:
        if n in SYSTEM_NAMES and _in_windows_dir(exe):
            return TIER_SYSTEM
        return TIER_SYSTEM if _in_system_dirs(exe) else TIER_USER
    return TIER_SYSTEM if n in SYSTEM_NAMES else TIER_USER


def _suspicious_dir(exe: str) -> bool:
    p = _norm(exe)
    return any(m in p for m in SUSPECT_DIR_MARKERS)


def static_threats(name: str, exe: str) -> tuple[str, ...]:
    """Heuristics that depend only on the process image (computed once per process)."""
    out = []
    if exe and (name or "").lower() in MASQ_NAMES and not _in_windows_dir(exe):
        out.append("masq")                       # svchost.exe & co. running from outside the Windows folder
    if exe and _suspicious_dir(exe):
        out.append("temp")                       # runs from temp / downloads / other user-writable places
    return tuple(out)


def live_threats(pi: "ProcInfo", port: int, exposure: str) -> tuple[str, ...]:
    """Per-port heuristics: only network-exposed listeners of regular processes are judged."""
    out = list(pi.threats)
    if pi.tier == TIER_USER and exposure == "lan":
        if port in SUSPECT_PORTS:
            out.append("port")
        if pi.name.startswith("PID ") or not pi.exe:
            out.append("hidden")
    return tuple(out)


def is_docker(name: str, exe: str = "") -> bool:
    n = (name or "").lower()
    return ("docker" in n or n.startswith(("wsl", "vmmem", "vpnkit")) or n in ("vmwp.exe", "com.docker.backend.exe")
            or "\\docker\\" in _norm(exe))


def is_loopback(ip: str) -> bool:
    ip = ip.lower()
    return ip == "::1" or ip.startswith("127.") or ip.startswith("::ffff:127.")


def exposure_of(binds: list[str]) -> tuple[str, str]:
    non_local = [b for b in binds if not is_loopback(b)]
    if not non_local:
        return "local", ("127.0.0.1" if "127.0.0.1" in binds else binds[0])
    if any(b in ("0.0.0.0", "::") for b in non_local):
        return "lan", "0.0.0.0 / ::"
    return "lan", non_local[0]


def list_listeners() -> list[Listener]:
    """Every listening TCP socket and bound UDP socket (deduplicated)."""
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        return []
    out, seen = [], set()
    for c in conns:
        if not c.laddr:
            continue
        if c.type == socket.SOCK_STREAM:
            if c.status != psutil.CONN_LISTEN:
                continue
            proto = "TCP"
        elif c.type == socket.SOCK_DGRAM:
            proto = "UDP"
        else:
            continue
        k = (proto, c.laddr.ip, c.laddr.port, c.pid or 0)
        if k not in seen:
            seen.add(k)
            out.append(Listener(proto, c.laddr.ip, c.laddr.port, c.pid or 0))
    return out


class Scanner:
    """Builds the card rows. Keeps psutil.Process objects so cpu_percent() has a baseline between scans."""

    def __init__(self) -> None:
        self._procs: dict[int, psutil.Process] = {}
        self._meta: dict[int, ProcInfo] = {}
        self._ncpu = psutil.cpu_count(logical=True) or 1
        self._reset_pending = False
        self._idle = ProcInfo("System Idle Process", [], "", "", 0.0, TIER_CORE, False)

    def reset(self) -> None:
        self._reset_pending = True

    def scan(self) -> list[Row]:
        if self._reset_pending:
            self._procs.clear()
            self._meta.clear()
            self._reset_pending = False
        groups: dict[tuple, list[str]] = {}
        for l in list_listeners():
            groups.setdefault((l.proto, l.port, l.pid), []).append(l.bind)
        live = {k[2] for k in groups}
        for pid in [p for p in self._procs if p not in live]:
            self._procs.pop(pid, None)
            self._meta.pop(pid, None)
        info = {pid: self._inspect(pid) for pid in live}
        rows: list[Row] = []
        for (proto, port, pid), binds in groups.items():
            data = info.get(pid)
            if data is None:
                continue
            pi, cpu, ram = data
            binds = sorted(set(binds), key=lambda b: (":" in b, b))
            exposure, expo_ip = exposure_of(binds)
            threats = live_threats(pi, port, exposure)
            rows.append(Row(
                key=f"{proto}|{port}|{pid}", port=port, proto=proto, pid=pid, name=pi.name, cpu=cpu, ram=ram,
                bind=", ".join(binds), exposure=exposure, expo_ip=expo_ip, cmd=pi.cmd, exe=pi.exe,
                started=pi.started, tier=pi.tier, docker=pi.docker, dev=port in DEV_PORTS,
                hay=f"{port} :{port} {proto} {pi.name} {pid} {', '.join(binds)} {pi.cmd} {pi.exe}".lower(),
                threats=threats,
            ))
        return rows

    def _inspect(self, pid: int):
        if pid == 0:
            return self._idle, 0.0, 0.0
        try:
            proc = self._procs.get(pid)
            if proc is None or not proc.is_running():
                proc = psutil.Process(pid)
                self._procs[pid] = proc
                self._meta.pop(pid, None)
            meta = self._meta.get(pid)
            if meta is None:
                name = _safe(proc.name, f"PID {pid}")
                cmdline = _safe(proc.cmdline, []) or []
                exe = _safe(proc.exe, "")
                meta = self._meta[pid] = ProcInfo(
                    name, cmdline, subprocess.list2cmdline(cmdline) if cmdline else exe, exe,
                    _safe(proc.create_time, 0.0), classify(pid, name, exe), is_docker(name, exe),
                    static_threats(name, exe))
            cpu = _safe(proc.cpu_percent, 0.0) / self._ncpu
            ram = _safe(lambda: proc.memory_info().rss, 0) / MB
            return meta, cpu, ram
        except psutil.NoSuchProcess:
            return None
        except psutil.AccessDenied:
            return ProcInfo(f"PID {pid}", [], "", "", 0.0, classify(pid), False), 0.0, 0.0


def process_details(pid: int) -> dict:
    """Fresh data for the inspector sheet."""
    out = {"exe": "", "cmd": "", "started": 0.0}
    try:
        p = psutil.Process(pid)
        out["exe"] = _safe(p.exe, "")
        cl = _safe(p.cmdline, [])
        out["cmd"] = subprocess.list2cmdline(cl) if cl else ""
        out["started"] = _safe(p.create_time, 0.0)
    except psutil.Error:
        pass
    return out


def fmt_uptime(seconds: float) -> str:
    sec = int(max(0, seconds))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = [T(k, n=v) for k, v in (("u_d", d), ("u_h", h), ("u_m", m), ("u_s", s)) if v][:3]
    return "  ".join(parts) if parts else T("u_s", n=0)


_WM_CLOSE = 0x0010
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _post_wm_close(pid: int) -> bool:
    """Politely ask every visible top-level window of `pid` to close. True if one was found."""
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.EnumWindows.argtypes = (_WNDENUMPROC, wintypes.LPARAM)
    u.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    u.IsWindowVisible.argtypes = (wintypes.HWND,)
    u.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    found = [False]

    def cb(hwnd, _lparam):
        owner = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and u.IsWindowVisible(hwnd):
            u.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
            found[0] = True
        return True

    u.EnumWindows(_WNDENUMPROC(cb), 0)
    return found[0]


def _tier_of(proc: psutil.Process) -> str:
    return classify(proc.pid, _safe(proc.name, ""), _safe(proc.exe, ""))


def end_process(pid: int, mode: str = "end") -> tuple[bool, str]:
    """
    mode "end"  : WM_CLOSE to its windows (3 s); windowless processes get TerminateProcess.
    mode "kill" : kill just this process immediately.
    mode "tree" : kill the process and every recursive child immediately.
    Core / self processes are refused here as well — the guard does not depend on the UI.
    Returns (ok, result-code); result codes are translated by tr_code().
    """
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True, "gone"
    except psutil.AccessDenied:
        return False, "denied"
    if _tier_of(proc) in (TIER_CORE, TIER_SELF):
        return False, "protected"
    try:
        if mode in ("kill", "tree"):
            victims = [proc]
            if mode == "tree":
                for child in _safe(lambda: proc.children(recursive=True), []):
                    if _tier_of(child) not in (TIER_CORE, TIER_SELF):
                        victims.append(child)
            for p in reversed(victims):                  # deepest children first
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
                except psutil.AccessDenied:
                    if p is proc:
                        raise
            _, alive = psutil.wait_procs(victims, timeout=3)
            return (False, "still") if proc in alive else (True, "killed")
        if _post_wm_close(pid):
            _, alive = psutil.wait_procs([proc], timeout=3)
            return (False, "not_closed") if alive else (True, "closed")
        proc.terminate()
        _, alive = psutil.wait_procs([proc], timeout=3)
        return (False, "not_ended") if alive else (True, "ended")
    except psutil.NoSuchProcess:
        return True, "gone"
    except psutil.AccessDenied:
        return False, "denied"
    except OSError:
        return False, "error"


def restart_process(pid: int) -> tuple[bool, str]:
    """Stop the process, then launch it again with its original command line and working directory."""
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return False, "gone"
    except psutil.AccessDenied:
        return False, "denied"
    if _tier_of(proc) in (TIER_CORE, TIER_SELF):
        return False, "protected"
    cmdline = _safe(proc.cmdline, []) or []
    cwd = _safe(proc.cwd, None)
    if not cmdline:
        return False, "no_cmdline"
    ok, code = end_process(pid, "end")
    if not ok:
        ok, code = end_process(pid, "tree")
        if not ok:
            return False, code
    time.sleep(0.6)                                       # give the OS a moment to release the port
    try:
        subprocess.Popen(cmdline, cwd=cwd or None, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    except (OSError, ValueError):
        return False, "launch_failed"
    return True, "restarted"


def clean_dev_ports(skip_ports=frozenset()) -> list[tuple[str, int, bool, str]]:
    """Kill (with child tree) every *regular* process listening on a dev port, except ports the user protected.
    Returns (name, pid, ok, code)."""
    targets: dict[int, set[int]] = {}
    for l in list_listeners():
        if l.proto == "TCP" and l.port in DEV_PORTS:
            targets.setdefault(l.pid, set()).add(l.port)
    results = []
    for pid, ports in sorted(targets.items()):
        if ports & set(skip_ports):
            continue
        try:
            p = psutil.Process(pid)
            name, tier = _safe(p.name, f"PID {pid}"), _tier_of(p)
        except psutil.Error:
            continue
        if tier != TIER_USER:                             # never bulk-kill system / core / self
            continue
        ok, code = end_process(pid, "tree")
        results.append((name, pid, ok, code))
    return results


def summarize(results: list[tuple[str, int, bool, str]], mode: str = "end") -> tuple[str, str]:
    """Turn job results into (message, kind) with kind ok / warn / error / info."""
    if not results:
        return T("msg_nothing"), "info"
    ok = [r for r in results if r[2]]
    bad = [r for r in results if not r[2]]
    if not bad:
        if mode == "restart":
            return T("msg_restarted", name=ok[0][0]), "ok"
        names = sorted({r[0] for r in ok})
        shown = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
        return T("msg_ended", n=len(ok), names=shown), "ok"
    first = bad[0]
    if ok:
        return T("msg_partial", ok=len(ok), bad=len(bad), name=first[0], pid=first[1], why=tr_code(first[3])), "warn"
    more = T("msg_more", n=len(bad) - 1) if len(bad) > 1 else ""
    return T("msg_failed", name=first[0], pid=first[1], why=tr_code(first[3]), more=more), "error"


# ══════════════════════════════════════════════════════════════════════════════
# UI building blocks
# ══════════════════════════════════════════════════════════════════════════════
def ui_scale(widget) -> float:
    try:
        return max(1.0, float(widget.winfo_fpixels("1i")) / 96.0)
    except Exception:
        return 1.0


def align_label(label: ctk.CTkLabel, justify: str) -> None:
    """CTkLabel has no public `justify`; set it on the inner tk.Label (left / right / center)."""
    try:
        label._label.configure(justify=justify)
    except Exception:
        pass


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = (x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1)
    return canvas.create_polygon(pts, smooth=True, **kw)


class Fonts:
    """Segoe UI for labels, Cascadia Code / Consolas for numbers — as tk fonts (canvas) and CTk fonts."""

    def __init__(self, root, scale: float) -> None:
        fams = set(tkfont.families(root))
        self.ui_family = "Segoe UI" if "Segoe UI" in fams else "Arial"
        self.mono_family = next((f for f in ("Cascadia Code", "Cascadia Mono", "Consolas") if f in fams),
                                "Courier New")

        def mk(family: str, size: float, bold: bool = False) -> tkfont.Font:
            return tkfont.Font(root=root, family=family, size=-max(1, round(size * scale)),
                               weight="bold" if bold else "normal")

        self.name = mk(self.ui_family, 15, True)
        self.label = mk(self.ui_family, 12, True)
        self.badge = mk(self.ui_family, 10, True)
        self.btn = mk(self.ui_family, 12, True)
        self.proto = mk(self.ui_family, 11, True)
        self.star = mk(self.ui_family, 14, True)
        self.empty = mk(self.ui_family, 15)
        self.menu = mk(self.ui_family, 14)
        self.tab = mk(self.ui_family, 13, True)
        self.tab_small = mk(self.ui_family, 12, True)
        self.section = mk(self.ui_family, 12, True)
        self.port = mk(self.mono_family, 22, True)
        self.meta = mk(self.mono_family, 11)
        self._ctk: dict[tuple, ctk.CTkFont] = {}

    def _get(self, family: str, size: int, bold: bool) -> ctk.CTkFont:
        k = (family, size, bold)
        if k not in self._ctk:
            self._ctk[k] = ctk.CTkFont(family=family, size=size, weight="bold" if bold else "normal")
        return self._ctk[k]

    def ui(self, size: int, bold: bool = False) -> ctk.CTkFont:
        return self._get(self.ui_family, size, bold)

    def monof(self, size: int, bold: bool = False) -> ctk.CTkFont:
        return self._get(self.mono_family, size, bold)


BTN_STYLES = {                                            # fill, hover, text  (palette token names)
    "primary": ("blue", "blue_hi", "on_accent"),
    "secondary": ("card_hi", "card_hi_hi", "blue"),
    "danger": ("red", "red_hi", "on_accent"),
    "danger_soft": ("red_tint", "red_tint_hi", "red"),
}


def pill(master, text: str, style: str, command, fonts: Fonts, height: int = 46, **kw) -> Button:
    fg, hv, tc = BTN_STYLES[style]
    return Button(master, text=text, command=command, height=height, corner_radius=height // 2,
                  fg_color=TK[fg], hover_color=TK[hv], text_color=TK[tc], font=fonts.ui(15, True),
                  border_width=0, **kw)


class MetricCard(Frame):
    """iOS-style summary card: coloured dot + caption, big animated value, small footnote. Mirrors in RTL."""

    def __init__(self, master, app: "PortManagerApp", dot_token: str) -> None:
        super().__init__(master, fg_color=TK["card"], corner_radius=20, border_width=0)
        F, self.app, self._shown = app.fonts, app, None
        self.dot = Label(self, text="●", text_color=TK[dot_token], font=F.ui(11), width=14, height=18)
        self.title = Label(self, text="", text_color=TK["text2"], font=F.ui(11, True), height=18)
        self.value = Label(self, text="—", text_color=TK["text"], font=F.ui(30, True), height=40)
        self.caption = Label(self, text=" ", text_color=TK["text3"], font=F.ui(12), height=18)
        self.set_rtl(False)

    def set_rtl(self, rtl: bool) -> None:
        a = "e" if rtl else "w"
        for w in (self.dot, self.title, self.value, self.caption):
            w.grid_forget()
        self.grid_columnconfigure(0, weight=1 if rtl else 0)
        self.grid_columnconfigure(1, weight=0 if rtl else 1)
        self.dot.grid(row=0, column=1 if rtl else 0, sticky=a, padx=(6, 16) if rtl else (16, 6), pady=(14, 0))
        self.title.grid(row=0, column=0 if rtl else 1, sticky=a, pady=(14, 0))
        self.value.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16)
        self.caption.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 14))
        for w in (self.value, self.caption, self.title):
            w.configure(anchor=a)

    def set_title(self, text: str) -> None:
        self.title.configure(text=text)

    def set_dash(self, caption: str) -> None:
        self._shown = None
        self.value.configure(text="—")
        self.caption.configure(text=caption)

    def set_number(self, value: float, fmt, caption: str) -> None:
        """Count smoothly from the number currently shown to `value`."""
        self.caption.configure(text=caption)
        old, self._shown = self._shown, float(value)
        if old is None or old == value:
            self.value.configure(text=fmt(value))
            return
        self.app.animate(f"num{id(self)}", 380, lambda e: self.value.configure(text=fmt(old + (value - old) * e)))


class Segmented(tk.Canvas):
    """Capsule segmented control whose highlight slides between segments (cubic ease)."""

    def __init__(self, master, app: "PortManagerApp", command, height: int = 46, width: int = 0,
                 bg_token: str = "bg", track_token: str = "field", font=None, pad: int = 4) -> None:
        self.app, self._cmd = app, command
        self.h = round(height * app.scale)
        kw = dict(height=self.h, bg=PAL[bg_token], highlightthickness=0, bd=0)
        if width:
            kw["width"] = round(width * app.scale)
        super().__init__(master, **kw)
        self.bg_token, self.track_token = bg_token, track_token
        self.font = font or app.fonts.tab
        self.pad = round(pad * app.scale)
        self._items: list[tuple[str, str]] = []
        self._rtl, self._sel, self._pos, self._hover_i = False, None, 0.0, None
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        REFRESHABLE.add(self)

    def _disp(self, key) -> float:
        keys = [k for k, _ in self._items]
        i = keys.index(key) if key in keys else 0
        return float(len(keys) - 1 - i) if self._rtl else float(i)

    def set_items(self, items: list[tuple[str, str]], rtl: bool, selected: str) -> None:
        self._items, self._rtl, self._sel = list(items), rtl, selected
        self._pos = self._disp(selected)
        self._redraw()

    def select(self, key: str, animate: bool = True) -> None:
        if key == self._sel:
            return
        old, self._sel = self._pos, key
        new = self._disp(key)
        if animate:
            def step(e: float) -> None:
                self._pos = old + (new - old) * e
                self._redraw()
            self.app.animate(f"seg{id(self)}", 240, step)
        else:
            self._pos = new
            self._redraw()

    def refresh(self) -> None:
        self.configure(bg=PAL[self.bg_token])
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        W, H, n, p = self.winfo_width(), self.h, len(self._items), self.pad
        if n == 0 or W < 20:
            return
        round_rect(self, 1, 1, W - 1, H - 1, H / 2, fill=PAL[self.track_token], outline="")
        seg = (W - 2 * p) / n
        order = list(reversed(self._items)) if self._rtl else self._items
        if self._hover_i is not None and abs(self._hover_i - self._pos) > 0.5:
            round_rect(self, p + self._hover_i * seg, p, p + (self._hover_i + 1) * seg, H - p, (H - 2 * p) / 2,
                       fill=mix(PAL[self.track_token], PAL["text"], 0.07), outline="")
        x = p + self._pos * seg
        round_rect(self, x, p, x + seg, H - p, (H - 2 * p) / 2, fill=PAL["blue"], outline="")
        for d, (_k, label) in enumerate(order):
            on = abs(d - self._pos) < 0.5
            self.create_text(p + (d + 0.5) * seg, H / 2, text=label, font=self.font,
                             fill=PAL["on_accent"] if on else PAL["text"])

    def _seg_at(self, x: float) -> int | None:
        n, p = len(self._items), self.pad
        if n == 0:
            return None
        d = int((x - p) // ((self.winfo_width() - 2 * p) / n))
        return d if 0 <= d < n else None

    def _on_click(self, e) -> None:
        d = self._seg_at(e.x)
        if d is None:
            return
        order = list(reversed(self._items)) if self._rtl else self._items
        key = order[d][0]
        if key != self._sel:
            self.select(key)
            self._cmd(key)

    def _on_motion(self, e) -> None:
        self._set_hover(self._seg_at(e.x))

    def _set_hover(self, i: int | None) -> None:
        if i != self._hover_i:
            self._hover_i = i
            self._redraw()


class PulseDot(tk.Canvas):
    """Breathing live-indicator: a solid green dot with a halo that swells and fades."""

    def __init__(self, master, app: "PortManagerApp", size: int = 18) -> None:
        self.px = round(size * app.scale)
        super().__init__(master, width=self.px, height=self.px, bg=PAL["bg"], highlightthickness=0, bd=0)
        self.phase = 0.0
        REFRESHABLE.add(self)
        self.set_phase(0.0)

    def set_phase(self, phase: float) -> None:
        self.phase = phase
        self.delete("all")
        c, core = self.px / 2, self.px * 0.20
        breath = 0.5 - 0.5 * math.cos(2 * math.pi * phase)          # 0 → 1 → 0
        halo = core + (self.px / 2 - core - 1) * breath
        self.create_oval(c - halo, c - halo, c + halo, c + halo, outline="",
                         fill=mix(PAL["bg"], PAL["green"], 0.50 * (1 - breath) + 0.10))
        self.create_oval(c - core, c - core, c + core, c + core, fill=PAL["green"], outline="")

    def refresh(self) -> None:
        self.configure(bg=PAL["bg"])
        self.set_phase(self.phase)


class Sheet(Toplevel):
    """iOS-style modal sheet: rounded card, centred icon/title/message, stacked capsule buttons.
    Fades in while rising a few pixels, fades out on close (instant when animations are off)."""

    RISE = 14

    def __init__(self, app: "PortManagerApp", width: int = 440, plain: bool = False) -> None:
        super().__init__(app, fg_color=TK["bg"])
        self.attributes("-alpha", 0.0)                   # invisible until positioned + title bar styled
        self.app, self.result, self.value = app, None, ""
        self._closing, self._key = False, f"sheet{id(self)}"
        self.title(APP_NAME)
        self.resizable(False, False)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.dismiss)
        self.bind("<Escape>", lambda _e: self.dismiss())
        self.card = Frame(self, fg_color=TK["bg" if plain else "card"], corner_radius=22, border_width=0)
        self.card.pack(fill="both", expand=True, padx=14, pady=14)
        Frame(self.card, width=width, height=1, fg_color="transparent").pack()
        app._sheets.append(self)

    def red_border(self) -> None:
        self.card.configure(border_width=2, border_color=TK["red"])

    def icon(self, glyph: str, token: str = "red") -> None:
        Label(self.card, text=glyph, width=60, height=60, corner_radius=30, fg_color=TK[token],
              text_color=TK["on_accent"], font=self.app.fonts.ui(30, True)).pack(pady=(26, 8))

    def title_text(self, text: str, token: str = "text", size: int = 19, pady=(10, 4)) -> Label:
        lab = Label(self.card, text=text, text_color=TK[token], font=self.app.fonts.ui(size, True), wraplength=390)
        lab.pack(padx=22, pady=pady)
        align_label(lab, "center")
        return lab

    def message(self, text: str, muted: bool = False, token: str | None = None, justify: str = "center",
                boxed: bool = False, mono: bool = False) -> Label:
        F = self.app.fonts
        lab = Label(self.card, text=text, text_color=TK[token or ("text2" if muted else "text")],
                    font=F.monof(12) if mono else F.ui(13), wraplength=390)
        if boxed:
            lab.configure(fg_color=TK["card_hi"], corner_radius=12)
            lab.pack(padx=22, pady=(4, 8), fill="x", ipady=8)
        else:
            lab.pack(padx=22, pady=(2, 8))
        align_label(lab, justify)
        return lab

    def button(self, text: str, style: str, command) -> Button:
        b = pill(self.card, text, style, command, self.app.fonts)
        b.pack(fill="x", padx=22, pady=(4, 6))
        return b

    def choose(self, value) -> None:
        self.result = value
        self._close()

    def dismiss(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.grab_release()
        except tk.TclError:
            pass

        def step(e: float) -> None:
            try:
                self.attributes("-alpha", 1.0 - e)
            except tk.TclError:
                pass

        self.app.animate(self._key + "x", 110, step, done=self._destroy)

    def _destroy(self) -> None:
        if self in self.app._sheets:
            self.app._sheets.remove(self)
        try:
            self.destroy()
        except tk.TclError:
            pass
        if self.app._sheets:                             # nested sheet closed: give the modal grab back
            self.app._sheets[-1]._grab()

    def _grab(self) -> None:
        try:
            self.lift()
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _icon(self) -> None:
        try:
            self.iconphoto(False, self.app._photo)
        except tk.TclError:
            pass

    def _enter(self) -> None:
        if self._closing:
            return
        self._grab()
        x, y = self._pos

        def step(e: float) -> None:
            try:
                self.attributes("-alpha", e)
                tk.Wm.geometry(self, f"+{x}+{int(y + self.RISE * (1 - e))}")
            except tk.TclError:
                pass

        self.app.animate(self._key, 240, step)

    def run(self):
        Frame(self.card, height=12, fg_color="transparent").pack()
        self.update_idletasks()
        app = self.app
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = max(0, app.winfo_rootx() + (app.winfo_width() - w) // 2)
        y = max(0, app.winfo_rooty() + (app.winfo_height() - h) // 3)
        self._pos = (x, y)
        try:
            tk.Wm.geometry(self, f"+{x}+{y + self.RISE}")
        except tk.TclError:
            pass
        self.after(50, lambda: app.style_titlebar(self))
        self.after(110, self._enter)
        self.after(260, self._icon)
        app.wait_window(self)
        return self.result


class PopupMenu(tk.Toplevel):
    """Rounded iOS-style context menu (borderless, transparent corners). `items`: None = separator,
    else (label, callback, style) with style normal / danger / disabled."""

    def __init__(self, app: "PortManagerApp", items: list, x: int, y: int) -> None:
        super().__init__(app)
        self.app, self.items, self.born = app, items, time.monotonic()
        self.rtl = I18N["rtl"]
        s, f = app.scale, app.fonts
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        key = "#010203"
        self.configure(bg=key)
        try:
            self.attributes("-transparentcolor", key)
        except tk.TclError:
            pass
        self.ih, self.sep_h, self.pad, self.radius = round(38 * s), round(11 * s), round(8 * s), round(14 * s)
        labels = [it[0] for it in items if it]
        self.w = max(round(250 * s), max(f.menu.measure(t) for t in labels) + round(52 * s))
        self.h = self.pad * 2 + sum(self.ih if it else self.sep_h for it in items)
        self.canvas = tk.Canvas(self, width=self.w, height=self.h, bg=key, highlightthickness=0, bd=0)
        self.canvas.pack()
        self._hover_i: int | None = None
        self._rects: list[tuple[int, int, int]] = []
        self._draw()
        x = max(0, min(x, self.winfo_screenwidth() - self.w - 4))
        y = max(0, min(y, self.winfo_screenheight() - self.h - 4))
        self.geometry(f"{self.w}x{self.h}+{x}+{y}")
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda _e: self._set_hover(None))
        self.canvas.bind("<Button-1>", self._on_click)
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<FocusOut>", lambda _e: self.after(120, self._maybe_close))
        self.deiconify()
        self.lift()
        self.focus_force()

    def _draw(self) -> None:
        c, f, P, s = self.canvas, self.app.fonts, PAL, self.app.scale
        c.delete("all")
        round_rect(c, 1, 1, self.w - 1, self.h - 1, self.radius, fill=P["pop"], outline=P["sep"], width=1)
        self._rects = []
        y = self.pad
        for i, it in enumerate(self.items):
            if it is None:
                mid = y + self.sep_h / 2
                c.create_line(self.pad + round(10 * s), mid, self.w - self.pad - round(10 * s), mid, fill=P["sep"])
                y += self.sep_h
                continue
            label, _cb, style = it
            if i == self._hover_i and style != "disabled":
                round_rect(c, self.pad, y, self.w - self.pad, y + self.ih, round(9 * s), fill=P["hover"], outline="")
            color = P["red"] if style == "danger" else (P["text3"] if style == "disabled" else P["text"])
            tx = self.w - self.pad - round(14 * s) if self.rtl else self.pad + round(14 * s)
            c.create_text(tx, y + self.ih / 2, text=label, anchor="e" if self.rtl else "w", fill=color, font=f.menu)
            self._rects.append((y, y + self.ih, i))
            y += self.ih

    def _item_at(self, py: int) -> int | None:
        for y1, y2, i in self._rects:
            if y1 <= py < y2:
                return i
        return None

    def _set_hover(self, i: int | None) -> None:
        if i != self._hover_i:
            self._hover_i = i
            self._draw()

    def _on_motion(self, e) -> None:
        self._set_hover(self._item_at(e.y))

    def _on_click(self, e) -> None:
        i = self._item_at(e.y)
        if i is None or self.items[i][2] == "disabled":
            return
        cb = self.items[i][1]
        self.close()
        self.app.after(10, cb)

    def _maybe_close(self) -> None:
        try:
            fg = self.focus_get()
            if fg is None or not str(fg).startswith(str(self)):
                self.close()
        except (KeyError, tk.TclError):
            self.close()

    def close(self) -> None:
        if getattr(self.app, "_popup", None) is self:
            self.app._popup = None
        try:
            self.destroy()
        except tk.TclError:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# The card list: collapsible sections (Pinned · Apps & Developer Tools · Windows System) of rounded cards.
# Custom canvas: badges, per-card buttons, virtual scrolling, RTL mirroring, animated expand/collapse.
# ──────────────────────────────────────────────────────────────────────────────
class Section:
    __slots__ = ("key", "title", "rows", "open")

    def __init__(self, key: str, title: str, rows: list, open_: bool = True) -> None:
        self.key, self.title, self.rows, self.open = key, title, rows, open_


class CardList(Frame):
    def __init__(self, master, app: "PortManagerApp") -> None:
        super().__init__(master, fg_color="transparent")
        self.app, self.f, self.s = app, app.fonts, app.scale
        self.compact = app.cfg.compact
        self._metrics()
        s = self.s
        self.margin, self.pad, self.top = round(4 * s), round(18 * s), round(2 * s)
        self.hdr_h, self.sec_gap = round(40 * s), round(6 * s)
        self.rtl = False
        self.empty_text = ""
        self._groups: list[Section] = []
        self._fac: dict[str, float] = {}                 # section → current open fraction (0 collapsed … 1 open)
        self._rowmap: dict[str, Row] = {}
        self._nav: list[str] = []
        self._ypos: dict[str, float] = {}
        self._layout_cache: list[tuple] = []
        self._total = 0.0
        self._sel_key: str | None = None
        self._hover_key: str | None = None
        self._offset = 0.0
        self._hits: list[tuple[float, float, float, float, str, str]] = []
        self._fit_cache: dict[tuple, str] = {}

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg=PAL["bg"], highlightthickness=0, bd=0, takefocus=True)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar = Scrollbar(self, orientation="vertical", width=10, command=self._on_vbar,
                              fg_color="transparent", button_color=TK["text3"], button_hover_color=TK["text2"])
        self.vbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        REFRESHABLE.add(self)

        c = self.canvas
        c.bind("<Configure>", lambda _e: (self._fit_cache.clear(), self._redraw()))
        c.bind("<Button-1>", self._on_click)
        c.bind("<Double-Button-1>", self._on_double)
        c.bind("<Button-3>", self._on_right)
        c.bind("<Motion>", self._on_motion)
        c.bind("<Leave>", lambda _e: self._set_hover(None))
        for seq, delta, absolute in (("<Up>", -1, None), ("<Down>", 1, None), ("<Prior>", -4, None),
                                     ("<Next>", 4, None), ("<Home>", 0, 0), ("<End>", 0, -1)):
            c.bind(seq, lambda _e, d=delta, a=absolute: (self._move(d, a), "break")[1])
        c.bind("<Return>", lambda _e: (self._open_selected(), "break")[1])
        c.bind_all("<MouseWheel>", self._on_wheel, add="+")    # CTkFrame forbids bind_all; tk.Canvas allows it

    def _metrics(self) -> None:
        s = self.s
        self.card_h = round((78 if self.compact else 104) * s)
        self.gap = round(10 * s)
        self.pitch = self.card_h + self.gap

    # ── public API ────────────────────────────────────────────────────────────
    def set_rtl(self, rtl: bool) -> None:
        self.rtl = rtl
        self.canvas.grid_forget()
        self.vbar.grid_forget()
        self.grid_columnconfigure(0, weight=0 if rtl else 1)
        self.grid_columnconfigure(1, weight=1 if rtl else 0)
        self.canvas.grid(row=0, column=1 if rtl else 0, sticky="nsew")
        self.vbar.grid(row=0, column=0 if rtl else 1, sticky="ns", padx=(0, 6) if rtl else (6, 0))
        self._redraw()

    def set_compact(self, flag: bool) -> None:
        self.compact = flag
        self._metrics()
        self._fit_cache.clear()
        self._redraw()

    def set_empty_text(self, text: str) -> None:
        self.empty_text = text

    def set_groups(self, groups: list[tuple[str, str, list]], force_open: bool = False) -> None:
        """groups: (key, title, rows). Section open/closed state comes from the config unless `force_open`."""
        collapsed = self.app.cfg.collapsed
        self._groups = [Section(k, t, rows, force_open or k not in collapsed) for k, t, rows in groups]
        self._rowmap = {r.key: r for g in self._groups for r in g.rows}
        for g in self._groups:
            if not self.app.is_animating(f"sec_{g.key}"):
                self._fac[g.key] = 1.0 if g.open else 0.0
        if self._hover_key not in self._rowmap:
            self._hover_key = None
        self._redraw()

    def animate_section(self, key: str, open_: bool) -> None:
        for g in self._groups:
            if g.key == key:
                g.open = open_
        start, end = self._fac.get(key, 1.0), 1.0 if open_ else 0.0

        def step(e: float) -> None:
            self._fac[key] = start + (end - start) * e
            self._redraw()

        self.app.animate(f"sec_{key}", 280, step)

    def refresh(self) -> None:                           # theme morph frame
        self.canvas.configure(bg=PAL["bg"])
        self._redraw()

    def redraw(self) -> None:
        self._fit_cache.clear()
        self._redraw()

    def selected_row(self) -> Row | None:
        return self._rowmap.get(self._sel_key) if self._sel_key else None

    def select_key(self, key: str | None) -> None:
        self._sel_key = key
        self._redraw()

    def focus_canvas(self) -> None:
        self.canvas.focus_set()

    # ── layout ────────────────────────────────────────────────────────────────
    def _layout(self) -> None:
        """Section stack: header, then the cards revealed so far (`fac`). Cards keep their position and are
        clipped at `clip`, so expanding feels like a drawer opening."""
        out, ypos, nav, y = [], {}, [], float(self.top)
        for g in self._groups:
            hy = y
            y += self.hdr_h
            fac = self._fac.get(g.key, 1.0)
            full = len(g.rows) * self.pitch
            out.append((g, hy, y, fac, y + full * fac, y + full))
            if fac > 0.001:
                for i, r in enumerate(g.rows):
                    ypos[r.key] = y + i * self.pitch
            if g.open:
                nav.extend(r.key for r in g.rows)
            y += full * fac + self.sec_gap
        self._layout_cache, self._ypos, self._nav, self._total = out, ypos, nav, y

    # ── drawing ───────────────────────────────────────────────────────────────
    def _fit(self, text: str, width: float, font: tkfont.Font) -> str:
        width = int(width)
        if width <= 6:
            return ""
        key = (id(font), text, width)
        hit = self._fit_cache.get(key)
        if hit is not None:
            return hit
        t = text[:400]
        if len(t) == len(text) and font.measure(t) <= width:
            out = t
        else:
            lo, hi = 0, len(t)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.measure(t[:mid] + "…") <= width:
                    lo = mid
                else:
                    hi = mid - 1
            out = (t[:lo] + "…") if lo else "…"
        if len(self._fit_cache) > 4000:
            self._fit_cache.clear()
        self._fit_cache[key] = out
        return out

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        self._hits = []
        self._layout()
        W, H = c.winfo_width(), c.winfo_height()
        if W < 40 or H < 40:
            return
        self._offset = max(0.0, min(self._offset, max(0.0, self._total - H)))
        if not self._groups:
            c.create_text(W / 2, H / 2, text=self.empty_text, fill=PAL["text2"], font=self.f.empty,
                          width=max(200, W - 80), justify="center")
            self.vbar.set(0, 1)
            return
        off = self._offset
        for g, hy, y0, fac, clip, bottom in self._layout_cache:
            if hy + self.hdr_h >= off and hy <= off + H:
                self._draw_header(g, hy - off, fac, W)
            if fac > 0.001:
                for i, r in enumerate(g.rows):
                    cy = y0 + i * self.pitch
                    if cy + self.card_h < off or cy > off + H or cy >= clip:
                        continue
                    self._draw_card(r, cy - off, W, whole=cy + self.card_h <= clip + 1)
                if fac < 0.999:                                   # mask whatever sticks out below the drawer
                    c.create_rectangle(0, clip - off, W, bottom - off + 1, fill=PAL["bg"], outline="")
        total = max(self._total, 1.0)
        self.vbar.set(off / total, min(1.0, (off + H) / total))

    def _draw_header(self, g: Section, y: float, fac: float, W: int) -> None:
        c, f, s, P, rtl = self.canvas, self.f, self.s, PAL, self.rtl
        x1, x2 = self.margin, W - self.margin
        lead = "e" if rtl else "w"

        def px(local: float) -> float:
            return x2 - local if rtl else x1 + local

        mid = y + self.hdr_h / 2 + round(2 * s)
        size = round(5 * s)
        base = 180 if rtl else 0
        angle = base - 90 * fac if rtl else 90 * fac
        a = math.radians(angle)
        cx, cy = px(round(18 * s)), mid
        pts = []
        for ux, uy in ((-0.5, -0.75), (0.65, 0.0), (-0.5, 0.75)):
            pts += [cx + (ux * math.cos(a) - uy * math.sin(a)) * size, cy + (ux * math.sin(a) + uy * math.cos(a)) * size]
        c.create_polygon(pts, fill=P["text2"], outline=P["text2"], joinstyle="round", width=2)
        tx = round(34 * s)
        if g.key == "pinned":
            c.create_text(px(tx), mid, text="★", anchor=lead, fill=P["orange"], font=f.section)
            tx += f.section.measure("★") + round(6 * s)
        c.create_text(px(tx), mid, text=g.title, anchor=lead, fill=P["text2"], font=f.section)
        tx += f.section.measure(g.title) + round(10 * s)
        cnt = str(len(g.rows))
        pw, ph = f.badge.measure(cnt) + round(14 * s), round(18 * s)
        l, r = (x2 - tx - pw, x2 - tx) if rtl else (x1 + tx, x1 + tx + pw)
        round_rect(c, l, mid - ph / 2, r, mid + ph / 2, ph / 2, fill=P["gray_tint"], outline="")
        c.create_text((l + r) / 2, mid, text=cnt, fill=P["text2"], font=f.badge)
        self._hits.append((x1, y, x2, y + self.hdr_h, "section", g.key))

    def _draw_card(self, r: Row, y: float, W: int, whole: bool = True) -> None:
        c, f, s, P, app = self.canvas, self.f, self.s, PAL, self.app
        rtl, pad, cfg = self.rtl, self.pad, app.cfg
        x1, x2 = self.margin, W - self.margin
        cw = x2 - x1
        sel, hov, tier = r.key == self._sel_key, r.key == self._hover_key, r.tier
        protected = tier == TIER_USER and r.port in cfg.protected_ports
        suspect = bool(cfg.deep_scan and r.threats and tier == TIER_USER)

        if tier == TIER_CORE:
            fill, border, bw = P["core_fill"], P["red"], max(2, round(2 * s))
        elif tier == TIER_SYSTEM:
            fill, border, bw = P["sys_fill"], P["red"], max(1, round(1.5 * s))
        elif suspect:
            fill, border, bw = P["card"], P["orange"], max(1, round(1.5 * s))
        else:
            fill, border, bw = P["card"], (P["blue"] if sel else ""), max(2, round(2 * s))
        if sel and border and tier != TIER_USER:
            bw += max(1, round(s))
        if sel and suspect:
            border, bw = P["blue"], max(2, round(2 * s))
        if hov and not sel:
            fill = mix(fill, P["text"], 0.05)
        round_rect(c, x1, y, x2, y + self.card_h, round(18 * s), fill=fill, outline=border, width=bw if border else 0)

        lead = "e" if rtl else "w"

        def px(local: float) -> float:
            return x2 - local if rtl else x1 + local

        def span(a: float, b: float) -> tuple[float, float]:
            return (x2 - b, x2 - a) if rtl else (x1 + a, x1 + b)

        k = 1 if self.compact else 0
        y_port, y_proto = y + round((30, 40)[1 - k] * s), y + round((56, 72)[1 - k] * s)
        y1, y2, y3 = y + round((25, 28)[1 - k] * s), y + round((52, 58)[1 - k] * s), y + round(84 * s)

        # port block
        c.create_text(px(pad), y_port, text=str(r.port), anchor=lead, fill=P["text"], font=f.port)
        dr = round(4 * s)
        dl, dr_ = span(pad, pad + 2 * dr)
        c.create_oval(dl, y_proto - dr, dr_, y_proto + dr, fill=P["green"], outline="")
        c.create_text(px(pad + 2 * dr + round(6 * s)), y_proto, text=r.proto, anchor=lead, fill=P["text2"], font=f.proto)

        # action buttons (trailing edge)
        bh, b_det, b_end, gap_b = round(30 * s), round(88 * s), round(78 * s), round(8 * s)
        mid = y + self.card_h / 2
        end_b = cw - pad
        end_a = end_b - b_end
        det_b = end_a - gap_b
        det_a = det_b - b_det
        for a, b, label, fg, bg, kind in (
                (det_a, det_b, T("btn_details"), P["blue"], P["blue_tint"], "details"),
                (end_a, end_b, T("btn_locked" if tier == TIER_CORE else "btn_end"), P["red"],
                 P["gray_tint"] if tier == TIER_CORE else P["red_tint"], "end")):
            l, rr = span(a, b)
            round_rect(c, l, mid - bh / 2, rr, mid + bh / 2, bh / 2, fill=bg, outline="")
            c.create_text((l + rr) / 2, mid, text=label, fill=fg, font=f.btn)
            if whole:
                self._hits.append((l, mid - bh / 2, rr, mid + bh / 2, kind, r.key))

        # content block
        cx0 = pad + round(104 * s)
        cx1 = det_a - round(14 * s)
        width = cx1 - cx0
        if width < 60:
            return
        pinned = r.port in cfg.pinned
        label = cfg.labels.get(r.port, "")
        star_w = f.star.measure("★") + round(8 * s) if pinned else 0
        lab_txt = self._fit(label, round(170 * s), f.label) if label else ""
        lab_w = f.label.measure(lab_txt) + round(18 * s) if lab_txt else 0
        name_txt = self._fit(r.name, max(round(60 * s), width - star_w - lab_w - round(10 * s)), f.name)
        c.create_text(px(cx0), y1, text=name_txt, anchor=lead, fill=P["text"], font=f.name)
        cur = cx0 + f.name.measure(name_txt) + round(8 * s)
        if pinned:
            c.create_text(px(cur), y1, text="★", anchor=lead, fill=P["orange"], font=f.star)
            cur += star_w
        if lab_txt and cur + lab_w <= cx1:
            l, rr = span(cur, cur + lab_w)
            ph = round(22 * s)
            round_rect(c, l, y1 - ph / 2, rr, y1 + ph / 2, ph / 2, fill=P["blue_tint"], outline="")
            c.create_text((l + rr) / 2, y1, text=lab_txt, fill=P["blue"], font=f.label)

        badges: list[tuple[str, str, str, str]] = []
        if tier == TIER_CORE:
            badges.append((T("badge_core"), "#FFFFFF", P["red"], ""))
        elif tier == TIER_SYSTEM:
            badges.append((T("badge_system"), P["red"], P["red_tint"], P["red"]))
        if protected:
            badges.append((T("badge_protected"), P["teal"], P["teal_tint"], P["teal"]))
        if suspect:
            badges.append((T("badge_suspect"), "#FFFFFF", P["orange"], ""))
        if app.is_new(r.key):
            badges.append((T("badge_new"), "#FFFFFF", P["green"], ""))
        if r.dev:
            badges.append((T("badge_dev"), P["blue"], P["blue_tint"], ""))
        if r.docker:
            badges.append((T("badge_docker"), P["purple"], P["purple_tint"], ""))
        if r.exposure == "local":
            badges.append((T("badge_local", ip=r.expo_ip), P["green"], P["green_tint"], ""))
        else:
            badges.append((T("badge_lan", ip=r.expo_ip), P["orange"], P["orange_tint"], ""))
        cur, bh2 = cx0, round(20 * s)
        for text, fg, bg, outline in badges:
            bw_ = f.badge.measure(text) + round(16 * s)
            if cur + bw_ > cx1:
                break
            l, rr = span(cur, cur + bw_)
            round_rect(c, l, y2 - bh2 / 2, rr, y2 + bh2 / 2, bh2 / 2, fill=bg, outline=outline,
                       width=1 if outline else 0)
            c.create_text((l + rr) / 2, y2, text=text, fill=fg, font=f.badge)
            cur += bw_ + round(6 * s)

        if not self.compact:
            meta = f"PID {r.pid}  ·  {r.ram:,.1f} MB  ·  {r.cpu:.1f}% CPU"
            c.create_text(px(cx0), y3, text=meta, anchor=lead, fill=P["text2"], font=f.meta)
            mw = f.meta.measure(meta) + round(14 * s)
            if r.cmd and width - mw > round(60 * s):
                c.create_text(px(cx0 + mw), y3, text=self._fit(r.cmd, width - mw, f.meta), anchor=lead,
                              fill=P["text3"], font=f.meta)

    # ── interaction ───────────────────────────────────────────────────────────
    def _key_at(self, py: float) -> str | None:
        ay = py + self._offset
        for g, _hy, y0, fac, clip, _b in self._layout_cache:
            if fac <= 0.001:
                continue
            for i, r in enumerate(g.rows):
                cy = y0 + i * self.pitch
                if cy <= ay < cy + self.card_h and ay < clip:
                    return r.key
        return None

    def _hit(self, x: float, y: float) -> tuple[str, str] | None:
        for l, t, r, b, kind, key in self._hits:
            if l <= x <= r and t <= y <= b:
                return kind, key
        return None

    def _on_click(self, e) -> None:
        self.canvas.focus_set()
        self.app.close_popup()
        hit = self._hit(e.x, e.y)
        if hit and hit[0] == "section":
            self.app.toggle_section(hit[1])
            return
        if hit:
            self._sel_key = hit[1]
            self._redraw()
            row = self._rowmap.get(hit[1])
            if row:
                self.app.card_action(hit[0], row)
            return
        self._sel_key = self._key_at(e.y)
        self._redraw()

    def _on_double(self, e) -> None:
        key = self._key_at(e.y)
        if key and not self._hit(e.x, e.y):
            self.app.card_action("details", self._rowmap[key])

    def _on_right(self, e) -> None:
        key = self._key_at(e.y)
        if key is None:
            return
        self.canvas.focus_set()
        self._sel_key = key
        self._redraw()
        self.app.open_context_menu(self._rowmap[key], e.x_root, e.y_root)

    def _open_selected(self) -> None:
        row = self.selected_row()
        if row:
            self.app.card_action("details", row)

    def _set_hover(self, key: str | None) -> None:
        if key != self._hover_key:
            self._hover_key = key
            self._redraw()

    def _on_motion(self, e) -> None:
        self._set_hover(self._key_at(e.y))

    def _move(self, delta: int, absolute: int | None = None) -> None:
        nav = self._nav
        if not nav:
            return
        cur = nav.index(self._sel_key) if self._sel_key in nav else -1
        if absolute is not None:
            j = 0 if absolute == 0 else len(nav) - 1
        elif cur < 0:
            j = 0 if delta > 0 else len(nav) - 1
        else:
            j = cur + delta
        j = max(0, min(len(nav) - 1, j))
        self._sel_key = nav[j]
        top = self._ypos.get(nav[j], 0.0)
        H = self.canvas.winfo_height()
        if top < self._offset:
            self._offset = max(0.0, top - self.hdr_h)
        elif top + self.card_h > self._offset + H:
            self._offset = top + self.card_h - H + self.gap
        self._redraw()

    def _on_wheel(self, e) -> None:
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except (KeyError, tk.TclError):
            return
        if w is self.canvas:
            self._offset -= e.delta / 120 * self.pitch * 0.8
            self._redraw()

    def _on_vbar(self, *args) -> None:
        total, H = max(self._total, 1.0), self.canvas.winfo_height()
        if args[0] == "moveto":
            self._offset = float(args[1]) * total
        elif args[0] == "scroll":
            step = self.pitch if args[2] == "units" else H
            self._offset += int(args[1]) * step
        self._redraw()


class SettingsSheet(Sheet):
    """Scrollable, grouped iOS-style Settings. Every control applies immediately and is saved to the config."""

    def __init__(self, app: "PortManagerApp") -> None:
        super().__init__(app, width=540, plain=True)
        self.switches: dict[str, Switch] = {}
        self.heading = self.title_text(T("settings_title"), size=22, pady=(18, 6))
        h = int(min(600, max(320, app.winfo_screenheight() / app.scale * 0.62)))
        self.scroll = ScrollFrame(self.card, fg_color=TK["bg"], corner_radius=0, width=520, height=h,
                                  scrollbar_button_color=TK["text3"], scrollbar_button_hover_color=TK["text2"])
        self.scroll.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.done = self.button(T("sheet_close"), "primary", lambda: self.choose(True))
        self.build()

    # ── small builders ────────────────────────────────────────────────────────
    def _group(self, title_key: str) -> Frame:
        F, rtl = self.app.fonts, I18N["rtl"]
        text = T(title_key) if rtl else T(title_key).upper()
        Label(self.scroll, text=text, font=F.ui(12, True), text_color=TK["text2"], anchor="e" if rtl else "w").pack(
            fill="x", padx=20, pady=(16, 6))
        card = Frame(self.scroll, fg_color=TK["card"], corner_radius=16, border_width=0)
        card.pack(fill="x", padx=8)
        card._n = 0
        return card

    def _sep(self, card: Frame) -> None:
        if card._n:
            Frame(card, height=1, fg_color=TK["sep"], corner_radius=0).pack(fill="x", padx=16)
        card._n += 1

    def _row(self, card: Frame, key: str, factory) -> None:
        F, rtl = self.app.fonts, I18N["rtl"]
        a = "e" if rtl else "w"
        self._sep(card)
        row = Frame(card, fg_color="transparent")
        row.pack(fill="x")
        row.grid_columnconfigure(1 if rtl else 0, weight=1)
        Label(row, text=T(key), font=F.ui(14), text_color=TK["text"], anchor=a, wraplength=250).grid(
            row=0, column=1 if rtl else 0, sticky=a, padx=16, pady=12)
        factory(row).grid(row=0, column=0 if rtl else 1, sticky="w" if rtl else "e", padx=16, pady=8)

    def _hint(self, card: Frame, key: str) -> None:
        rtl = I18N["rtl"]
        lab = Label(card, text=T(key), font=self.app.fonts.ui(11), text_color=TK["text3"],
                    anchor="e" if rtl else "w", wraplength=440)
        lab.pack(fill="x", padx=16, pady=(0, 10))
        align_label(lab, "right" if rtl else "left")

    def _switch(self, name: str, value: bool, command):
        def make(parent) -> Switch:
            sw = Switch(parent, text="", progress_color=TK["green"], fg_color=TK["sep"], button_color="#FFFFFF",
                        button_hover_color="#F2F2F7", command=lambda: command(bool(sw.get())))
            sw.select() if value else sw.deselect()
            self.switches[name] = sw
            return sw
        return make

    def _seg(self, items, selected: str, command, width: int):
        def make(parent) -> Segmented:
            s = Segmented(parent, self.app, command, height=34, width=width, bg_token="card", track_token="card_hi",
                          font=self.app.fonts.tab_small, pad=3)
            s.set_items(items, I18N["rtl"], selected)
            return s
        return make

    def _button_row(self, card: Frame, text_key: str, style: str, command) -> None:
        self._sep(card)
        row = Frame(card, fg_color="transparent")
        row.pack(fill="x")
        pill(row, T(text_key), style, command, self.app.fonts, height=40).pack(fill="x", padx=16, pady=8)

    def _lang_row(self, card: Frame, code: str) -> None:
        F, rtl, app = self.app.fonts, I18N["rtl"], self.app
        a = "e" if rtl else "w"
        self._sep(card)
        row = Frame(card, fg_color="transparent", cursor="hand2")
        row.pack(fill="x")
        row.grid_columnconfigure(1 if rtl else 0, weight=1)
        meta = LOC.meta[code]
        box = Frame(row, fg_color="transparent", cursor="hand2")
        box.grid(row=0, column=1 if rtl else 0, sticky=a, padx=16, pady=10)
        name = Label(box, text=meta["native_name"], font=F.ui(14), text_color=TK["text"], anchor=a, cursor="hand2")
        name.pack(anchor=a)
        sub = Label(box, text=f"{meta['name']} · {code}", font=F.ui(11), text_color=TK["text3"], anchor=a,
                    cursor="hand2")
        sub.pack(anchor=a)
        mark = Label(row, text="✓" if code == I18N["lang"] else "", font=F.ui(18, True), text_color=TK["blue"],
                     width=24, cursor="hand2")
        mark.grid(row=0, column=0 if rtl else 1, padx=16)
        for w in (row, box, name, sub, mark):
            w.bind("<Button-1>", lambda _e, c=code: app.choose_language(c))

    def _ports_entry(self, parent) -> Entry:
        F, cfg = self.app.fonts, self.app.cfg
        e = Entry(parent, width=230, height=34, corner_radius=17, border_width=0, fg_color=TK["card_hi"],
                  text_color=TK["text"], placeholder_text=T("set_protected_ph"), placeholder_text_color=TK["text3"],
                  font=F.ui(13))
        cur = ", ".join(str(p) for p in sorted(cfg.protected_ports))
        if cur:
            e.insert(0, cur)
        try:
            e._entry.configure(justify="right" if I18N["rtl"] else "left")
        except Exception:
            pass
        commit = lambda _e=None: self.app.set_protected_ports(e.get(), e)
        e.bind("<Return>", commit)
        e.bind("<FocusOut>", commit)
        return e

    # ── content ───────────────────────────────────────────────────────────────
    def build(self) -> None:
        app, cfg, F, rtl = self.app, self.app.cfg, self.app.fonts, I18N["rtl"]
        for w in self.scroll.winfo_children():
            w.destroy()
        self.switches = {}
        self.heading.configure(text=T("settings_title"))
        self.done.configure(text=T("sheet_close"))
        LOC.scan()                                   # pick up language files dropped into locales/ meanwhile

        g = self._group("grp_language")
        for code in LOC.codes():
            self._lang_row(g, code)
        self._button_row(g, "set_open_locales", "secondary", app.open_locales_folder)
        self._hint(g, "set_locales_hint")

        g = self._group("grp_appearance")
        self._row(g, "set_theme", self._seg([("dark", T("theme_dark")), ("light", T("theme_light")),
                                              ("system", T("theme_system"))], cfg.theme, app.set_theme_mode, 290))
        self._row(g, "set_anim", self._switch("animations", cfg.animations, app.set_animations))
        self._row(g, "set_compact", self._switch("compact", cfg.compact, app.set_compact))

        g = self._group("grp_startup")
        self._row(g, "set_startup", self._switch("startup", is_startup_enabled(), app.set_startup_flag))
        self._row(g, "set_min", self._switch("start_minimized", cfg.start_minimized, app.set_start_minimized))
        self._row(g, "set_close", self._seg([("tray", T("close_tray")), ("quit", T("close_quit"))], cfg.close_action,
                                            app.set_close_action, 270))
        self._row(g, "set_hotkey", self._switch("hotkey", cfg.hotkey, app.set_hotkey_flag))
        self._hint(g, "set_hotkey_hint")

        g = self._group("grp_monitor")
        self._row(g, "set_interval", self._seg([(str(n), T("interval_s", n=n)) for n in SCAN_INTERVALS],
                                               str(cfg.scan_interval), app.set_scan_interval, 270))
        self._row(g, "set_new", self._switch("highlight_new", cfg.highlight_new, app.set_highlight_new))
        self._row(g, "set_deep", self._switch("deep_scan", cfg.deep_scan, app.set_deep_scan))
        self._hint(g, "set_deep_hint")
        self._row(g, "set_protected", self._ports_entry)
        self._hint(g, "set_protected_hint")

        g = self._group("grp_data")
        self._button_row(g, "set_export", "secondary", lambda: app.export_settings(self))
        self._button_row(g, "set_import", "secondary", lambda: app.import_settings(self))
        self._button_row(g, "set_reset", "danger_soft", lambda: app.reset_settings(self))
        self._hint(g, "set_config_path")

        Label(self.scroll, text=f"{APP_NAME} {__version__}  ·  Mustafa Al-Aqrawi (@spoonix-dev)", font=F.ui(11),
              text_color=TK["text3"]).pack(pady=(18, 10))

    def sync(self, name: str, value: bool) -> None:
        sw = self.switches.get(name)
        if sw is not None:
            try:
                sw.select() if value else sw.deselect()
            except tk.TclError:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# System tray
# ══════════════════════════════════════════════════════════════════════════════
class Tray:
    def __init__(self, app: "PortManagerApp") -> None:
        self.app = app
        self.icon: pystray.Icon | None = None
        self.ok = False

    def start(self) -> None:
        try:
            menu = pystray.Menu(
                pystray.MenuItem(lambda _i: T("tray_show"), self._show, default=True),
                pystray.MenuItem(lambda _i: T("tray_clean"), self._clean),
                pystray.MenuItem(lambda _i: T("tray_settings"), self._settings),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda _i: T("tray_startup"), self._toggle_startup,
                                 checked=lambda _i: is_startup_enabled()),
                pystray.MenuItem(lambda _i: T("tray_github"), self._open_repo),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda _i: T("tray_quit"), self._quit),
            )
            self.icon = pystray.Icon(APP_NAME, make_icon(256), f"{APP_NAME} {__version__}", menu)
            self.icon.run_detached()
            self.ok = True
        except Exception:
            self.ok = False

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass

    def refresh(self) -> None:
        if self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def notify(self, message: str) -> None:
        if self.icon is not None:
            try:
                self.icon.notify(message[:240], APP_NAME)
            except Exception:
                pass

    # menu callbacks run on pystray's thread — hand anything UI-related to the Tk thread
    def _show(self, _icon=None, _item=None) -> None:
        self.app.call_ui(self.app.show_window)

    def _settings(self, _icon=None, _item=None) -> None:
        self.app.call_ui(self.app.show_settings)

    def _clean(self, _icon=None, _item=None) -> None:
        threading.Thread(target=self.app.tray_clean_dev_ports, name="pm-clean", daemon=True).start()

    def _toggle_startup(self, _icon=None, _item=None) -> None:
        set_startup(not is_startup_enabled())
        self.refresh()
        self.app.call_ui(self.app.sync_startup_switch)

    def _open_repo(self, _icon=None, _item=None) -> None:
        webbrowser.open(REPO_URL)

    def _quit(self, _icon=None, _item=None) -> None:
        self.app.call_ui(self.app.quit_app)


# ══════════════════════════════════════════════════════════════════════════════
# Main window
# ══════════════════════════════════════════════════════════════════════════════
class PortManagerApp(Root):
    TABS = ("all", "tcp", "udp", "pinned", "protected")

    def __init__(self, cfg: Config, start_hidden: bool, guard: SingleInstance) -> None:
        super().__init__(fg_color=TK["bg"])
        self.cfg, self.guard = cfg, guard
        self.title(APP_NAME)
        self.geometry("1120x840")
        self.minsize(960, 680)

        self.scale = ui_scale(self)
        self.fonts = Fonts(self, self.scale)
        self._icon_img = make_icon(256)
        self._photo = ImageTk.PhotoImage(self._icon_img.resize((64, 64), Image.Resampling.LANCZOS))
        self.after(350, self._apply_window_icon)          # CTk re-applies its own icon at ~200 ms

        self._dark_now = cfg.resolve_dark()
        self.scanner = Scanner()
        self.tray: Tray | None = None
        self.hotkey = Hotkey(lambda: self.call_ui(self.toggle_window))
        self._rows: list[Row] = []
        self._have_data = False
        self._live = False
        self._scan_busy = False
        self._first_cycle = True
        self._scan_timer: str | None = None
        self._pulse_job: str | None = None
        self._busy = False
        self._quitting = False
        self._fade_job: str | None = None
        self._status_job: str | None = None
        self._last_update = "—"
        self._tab = "all"
        self._popup: PopupMenu | None = None
        self._settings_sheet: SettingsSheet | None = None
        self._sheets: list[Sheet] = []
        self._anims: dict[str, str] = {}
        self._force_open = False
        self._known: set[str] | None = None
        self._new_until: dict[str, float] = {}

        self._build_ui()
        self.apply_direction()
        self.apply_glyphs()
        self.apply_language()
        self.cards_list.set_compact(cfg.compact)

        self.tray = Tray(self)
        self.tray.start()
        self.tray.refresh()
        if cfg.hotkey and not self.hotkey.start():
            cfg.hotkey = False
            cfg.save()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Map>", self._on_map)
        self.bind("<Unmap>", self._on_unmap)
        self.bind("<ButtonPress>", self._on_any_click, add="+")
        self._bind_shortcuts()
        for ms in (30, 260, 650):                         # after CTk's own title-bar redraw
            self.after(ms, self._style_titlebars, True)

        if start_hidden and self.tray.ok:
            # CTk briefly withdraws/re-shows the window (~5 ms) to repaint its title bar, which would undo an
            # immediate withdraw(). Stay fully transparent until that is over, then hide to the tray silently.
            self.attributes("-alpha", 0.0)
            self.after(80, self._finish_hide)
        else:
            self.after(150, self._ensure_polling)

    @property
    def rtl(self) -> bool:
        return I18N["rtl"]

    # ── thread bridge / window chrome ─────────────────────────────────────────
    def call_ui(self, fn, *args) -> None:
        """Run `fn(*args)` on the Tk thread. Safe to call from any thread."""
        try:
            self.after(0, lambda: fn(*args))
        except (RuntimeError, tk.TclError):
            pass

    def _apply_window_icon(self) -> None:
        try:
            self.iconphoto(True, self._photo)
        except tk.TclError:
            pass

    def style_titlebar(self, win) -> None:
        set_titlebar(win, self._dark_now, PAL["bg"], PAL["text"])

    def _style_titlebars(self, repaint: bool = True) -> None:
        for w in [self] + list(self._sheets):
            set_titlebar(w, self._dark_now, PAL["bg"], PAL["text"], repaint)

    # ── animation engine: timers exist only while something is animating ──────
    def animate(self, key: str, ms: int, step, done=None) -> None:
        """Call step(eased 0→1) about every ms/18 for `ms` milliseconds (cubic in-out). Same key = replace.
        With "Smooth UI animations" off the end state is applied immediately."""
        self._cancel_anim(key)
        if not self.cfg.animations or ms <= 0:
            step(1.0)
            if done:
                done()
            return
        t0 = time.perf_counter()

        def tick() -> None:
            raw = min(1.0, (time.perf_counter() - t0) * 1000.0 / ms)
            try:
                step(ease_in_out_cubic(raw))
            except Exception:                         # never leave a half-finished animation behind
                raw = 1.0
            if raw < 1.0:
                self._anims[key] = self.after(max(1, round(ms / ANIM_FRAMES)), tick)
            else:
                self._anims.pop(key, None)
                if done:
                    done()

        tick()

    def _cancel_anim(self, key: str) -> None:
        job = self._anims.pop(key, None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass

    def is_animating(self, key: str) -> bool:
        return key in self._anims

    # ── theme morph ───────────────────────────────────────────────────────────
    def morph_to(self, dark: bool) -> None:
        """Cubic ease-in-out interpolation of the whole palette (~300 ms): canvases, every themed widget, badges,
        borders and the Windows title bar are re-painted on each frame — no flash, no restart."""
        self.close_popup()
        src, dst = dict(PAL), palette_for(dark)
        state = {"flipped": self._dark_now == dark}

        def step(e: float) -> None:
            for k, v in dst.items():
                PAL[k] = mix(src[k], v, e)
            repaint_all()
            flip = e >= 0.5 and not state["flipped"]
            if flip:
                state["flipped"] = True
                self._dark_now = dark
                self.apply_glyphs()
            self._style_titlebars(repaint=flip)

        def done() -> None:
            self._dark_now = dark
            PAL.update(dst)
            repaint_all()
            self.apply_glyphs()
            self._style_titlebars(True)

        self.animate("theme", ANIM_MS, step, done)

    def toggle_theme_quick(self) -> None:
        self.set_theme_mode("light" if self._dark_now else "dark")

    def set_theme_mode(self, mode: str) -> None:
        self.cfg.theme = mode
        self.cfg.save()
        self.morph_to(self.cfg.resolve_dark())

    # ── UI construction ───────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        F = self.fonts
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        # Header: brand + theme / settings controls
        self.hdr = Frame(self, fg_color="transparent")
        self.hdr.grid(row=0, column=0, sticky="ew", padx=22, pady=(16, 8))
        self.hdr.grid_columnconfigure(1, weight=1)
        self.brand = Frame(self.hdr, fg_color="transparent")
        logo = ctk.CTkImage(light_image=self._icon_img, dark_image=self._icon_img, size=(46, 46))
        self.logo_lbl = Label(self.brand, image=logo, text="")
        self.text_col = Frame(self.brand, fg_color="transparent")
        self.title_lbl = Label(self.text_col, text=APP_NAME, font=F.ui(26, True), text_color=TK["text"], height=32)
        self.sub_lbl = Label(self.text_col, text="", font=F.ui(12), text_color=TK["text2"], height=16)
        self.ctrl = Frame(self.hdr, fg_color="transparent")
        chip = dict(height=38, corner_radius=19, fg_color=TK["card"], hover_color=TK["card_hi"],
                    text_color=TK["text"], font=F.ui(13, True))
        self.theme_btn = Button(self.ctrl, text="", width=108, compound="left", command=self.toggle_theme_quick, **chip)
        self.gear_btn = Button(self.ctrl, text="", width=40, command=self.open_settings, **chip)

        # Summary cards
        self.cards_row = Frame(self, fg_color="transparent")
        self.cards_row.grid(row=1, column=0, sticky="ew", padx=16)
        for i in range(3):
            self.cards_row.grid_columnconfigure(i, weight=1, uniform="metric")
        self.card_ports = MetricCard(self.cards_row, self, "green")
        self.card_procs = MetricCard(self.cards_row, self, "blue")
        self.card_mem = MetricCard(self.cards_row, self, "orange")
        self.metric_cards = [self.card_ports, self.card_procs, self.card_mem]

        # Search capsule + Clean Dev Ports
        self.srow = Frame(self, fg_color="transparent")
        self.srow.grid(row=2, column=0, sticky="ew", padx=22, pady=(12, 10))
        self.search_box = Frame(self.srow, fg_color=TK["field"], corner_radius=23)
        self.search_icon = Label(self.search_box, text="", width=22)
        self.search = Entry(self.search_box, height=40, border_width=0, corner_radius=20, fg_color="transparent",
                            text_color=TK["text"], placeholder_text="", placeholder_text_color=TK["text3"],
                            font=F.ui(14))
        self.search.bind("<KeyRelease>", lambda _e: self._render())
        self.btn_clean = Button(self.srow, text="", height=46, width=190, corner_radius=23, fg_color=TK["blue_tint"],
                                hover_color=TK["blue_tint_hi"], text_color=TK["blue"], font=F.ui(13, True),
                                command=self._clean_dev_ports)

        # Sliding segmented tabs
        self.tabs = Segmented(self, self, self._on_tab, height=46, bg_token="bg", track_token="field")
        self.tabs.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 10))

        # Sectioned port cards
        self.cards_list = CardList(self, self)
        self.cards_list.grid(row=4, column=0, sticky="nsew", padx=22)

        # Status bar
        self.sb = Frame(self, fg_color="transparent")
        self.sb.grid(row=5, column=0, sticky="ew", padx=24, pady=(8, 12))
        self.sb.grid_columnconfigure(1, weight=1)
        self.live_box = Frame(self.sb, fg_color="transparent")
        self.live_dot = PulseDot(self.live_box, self)
        self.live_lbl = Label(self.live_box, text="", text_color=TK["text2"], font=F.ui(12), height=18)
        self.msg_lbl = Label(self.live_box, text="", text_color=TK["text2"], font=F.ui(12, True), height=18)
        self.credit = Label(self.sb, text=CREDIT_TEXT, text_color=TK["text3"], font=F.ui(11), height=18,
                            cursor="hand2")
        self.credit.bind("<Button-1>", lambda _e: webbrowser.open(REPO_URL))
        self.credit.bind("<Enter>", lambda _e: self.credit.configure(text_color=TK["text2"]))
        self.credit.bind("<Leave>", lambda _e: self.credit.configure(text_color=TK["text3"]))

    def apply_glyphs(self) -> None:
        """Glyph images follow the theme (drawn in memory, swapped halfway through a morph)."""
        dark = self._dark_now
        col, muted = ("#FFFFFF", "#98989D") if dark else ("#3A3A3C", "#8E8E93")

        def mk(kind: str, color: str, size: int) -> ctk.CTkImage:
            img = draw_glyph(kind, color)
            return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))

        self._img_theme, self._img_gear = mk("sun" if dark else "moon", col, 18), mk("gear", col, 20)
        self._img_search = mk("search", muted, 20)
        self.theme_btn.configure(image=self._img_theme, text=T("theme_to_light" if dark else "theme_to_dark"))
        self.gear_btn.configure(image=self._img_gear)
        self.search_icon.configure(image=self._img_search)

    def apply_direction(self) -> None:
        """(Re)lay out every row for LTR or RTL."""
        rtl = self.rtl
        a, side = ("e", "right") if rtl else ("w", "left")
        for w in (self.brand, self.ctrl):
            w.grid_forget()
        self.brand.grid(row=0, column=2 if rtl else 0, sticky=a)
        self.ctrl.grid(row=0, column=0 if rtl else 2, sticky="w" if rtl else "e")
        for w in (self.logo_lbl, self.text_col, self.title_lbl, self.sub_lbl, self.theme_btn, self.gear_btn):
            w.pack_forget()
        self.logo_lbl.pack(side=side, padx=(12, 0) if rtl else (0, 12))
        self.text_col.pack(side=side)
        self.title_lbl.pack(anchor=a)
        self.sub_lbl.pack(anchor=a)
        gap = (8, 0) if rtl else (0, 8)
        self.theme_btn.pack(side=side, padx=gap)
        self.gear_btn.pack(side=side)
        for i, card in enumerate(self.metric_cards):
            card.grid_forget()
            card.grid(row=0, column=2 - i if rtl else i, sticky="ew", padx=6)
            card.set_rtl(rtl)
        self.search_box.grid_forget()
        self.btn_clean.grid_forget()
        self.srow.grid_columnconfigure(0, weight=0 if rtl else 1)
        self.srow.grid_columnconfigure(1, weight=1 if rtl else 0)
        self.search_box.grid(row=0, column=1 if rtl else 0, sticky="ew")
        self.btn_clean.grid(row=0, column=0 if rtl else 1, padx=(0, 12) if rtl else (12, 0))
        self.search_icon.pack_forget()
        self.search.pack_forget()
        self.search_icon.pack(side=side, padx=(6, 16) if rtl else (16, 6))
        self.search.pack(side=side, fill="x", expand=True, pady=3, padx=(8, 0) if rtl else (0, 8))
        try:
            self.search._entry.configure(justify="right" if rtl else "left")
        except Exception:
            pass
        self.live_box.grid_forget()
        self.credit.grid_forget()
        self.live_box.grid(row=0, column=2 if rtl else 0, sticky="e" if rtl else "w")
        self.credit.grid(row=0, column=0 if rtl else 2, sticky="w" if rtl else "e")
        for w in (self.live_dot, self.live_lbl, self.msg_lbl):
            w.pack_forget()
            w.pack(side=side, padx=(6, 0) if rtl else (0, 6))
        self.cards_list.set_rtl(rtl)

    def apply_language(self) -> None:
        """Push every translatable string into the widgets."""
        self.sub_lbl.configure(text=f"{T('subtitle')}  ·  v{__version__}")
        self.search.configure(placeholder_text=T("search_ph"))
        self.tabs.set_items([(k, T("tab_" + k)) for k in self.TABS], self.rtl, self._tab)
        self.theme_btn.configure(text=T("theme_to_light" if self._dark_now else "theme_to_dark"))
        self.card_ports.set_title(T("c_ports"))
        self.card_procs.set_title(T("c_procs"))
        self.card_mem.set_title(T("c_mem"))
        self.btn_clean.configure(text=T("btn_clean"))
        self._update_cards()
        self._render()
        if self.tray is not None:
            self.tray.refresh()

    # ── settings: every handler applies live and saves ────────────────────────
    def open_settings(self) -> None:
        if self._settings_sheet is not None or self._quitting:
            return
        self.close_popup()
        sh = SettingsSheet(self)
        self._settings_sheet = sh
        try:
            sh.run()
        finally:
            self._settings_sheet = None

    def show_settings(self) -> None:
        self.show_window()
        self.after(350, self.open_settings)

    def choose_language(self, code: str) -> None:
        code = set_language(code)
        self.cfg.lang = code
        self.cfg.save()
        self.close_popup()
        self.apply_direction()
        self.apply_glyphs()
        self.apply_language()
        if self._settings_sheet is not None:
            self._settings_sheet.build()

    def set_animations(self, flag: bool) -> None:
        self.cfg.animations = flag
        self.cfg.save()
        if flag:
            if self._live:
                self._start_pulse()
        else:
            for k in list(self._anims):
                self._cancel_anim(k)
            self._stop_pulse()

    def set_compact(self, flag: bool) -> None:
        self.cfg.compact = flag
        self.cfg.save()
        self.cards_list.set_compact(flag)

    def set_startup_flag(self, flag: bool) -> None:
        if not set_startup(flag):
            self.sync_startup_switch()
            self.set_status(T("msg_startup_fail"), "error")
        if self.tray is not None:
            self.tray.refresh()

    def sync_startup_switch(self) -> None:
        if self._settings_sheet is not None:
            self._settings_sheet.sync("startup", is_startup_enabled())

    def set_start_minimized(self, flag: bool) -> None:
        self.cfg.start_minimized = flag
        self.cfg.save()

    def set_close_action(self, value: str) -> None:
        self.cfg.close_action = value
        self.cfg.save()

    def set_hotkey_flag(self, flag: bool) -> None:
        if flag:
            if self.hotkey.start():
                self.cfg.hotkey = True
            else:
                self.cfg.hotkey = False
                self.set_status(T("msg_hotkey_fail"), "error")
                if self._settings_sheet is not None:
                    self._settings_sheet.sync("hotkey", False)
        else:
            self.hotkey.stop()
            self.cfg.hotkey = False
        self.cfg.save()

    def set_scan_interval(self, value: str) -> None:
        self.cfg.scan_interval = int(value)
        self.cfg.save()

    def set_highlight_new(self, flag: bool) -> None:
        self.cfg.highlight_new = flag
        self.cfg.save()
        self.cards_list.redraw()

    def set_deep_scan(self, flag: bool) -> None:
        self.cfg.deep_scan = flag
        self.cfg.save()
        self.cards_list.redraw()

    def set_protected_ports(self, text: str, entry=None) -> None:
        ports = {p for p in (Config._port(x) for x in re.findall(r"\d+", text)) if p is not None}
        self.cfg.protected_ports = ports
        self.cfg.save()
        if entry is not None:
            try:
                entry.delete(0, "end")
                if ports:
                    entry.insert(0, ", ".join(str(p) for p in sorted(ports)))
            except tk.TclError:
                pass
        self._render()

    def open_locales_folder(self) -> None:
        d = Locales.external_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
            note = d / "README.txt"
            if not note.exists():
                note.write_text(
                    "PortManager language files\n\nDrop a .json file here (for example tr.json, ku.json, fr.json).\n"
                    'Copy en.json from the PortManager repository and translate the values. The "_meta" block\n'
                    'defines the language: {"code": "tr", "name": "Turkish", "native_name": "Türkçe", "rtl": false}.\n'
                    'Set "rtl": true for right-to-left languages. Missing keys fall back to English.\n'
                    "Reopen Settings and the new language appears in the list — no rebuild needed.\n", encoding="utf-8")
            os.startfile(str(d))
        except OSError:
            self.set_status(T("msg_open_fail"), "error")

    def export_settings(self, parent) -> None:
        path = filedialog.asksaveasfilename(parent=parent, title=T("dlg_export"), defaultextension=".json",
                                            initialfile="portmanager-settings.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self.cfg.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            self.set_status(T("msg_exported"), "ok")
        except OSError:
            self.set_status(T("msg_open_fail"), "error")

    def import_settings(self, parent) -> None:
        path = filedialog.askopenfilename(parent=parent, title=T("dlg_import"), filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raw = None
        if self.cfg.apply_dict(raw) == 0:
            self.set_status(T("msg_import_fail"), "error")
            return
        self.cfg.save()
        self.apply_all_settings()
        self.set_status(T("msg_imported"), "ok")

    def reset_settings(self, parent) -> None:
        sh = Sheet(self)
        sh.red_border()
        sh.icon("!", "red")
        sh.title_text(T("reset_title"), "red")
        sh.message(T("reset_body"))
        sh.button(T("set_reset"), "danger", lambda: sh.choose(True))
        sh.button(T("sheet_cancel"), "secondary", lambda: sh.choose(False))
        if not sh.run():
            return
        self.cfg.reset_values()
        self.cfg.lang = default_language()
        self.cfg.startup_initialized = set_startup(True)       # factory default: start with Windows
        self.cfg.save()
        self.apply_all_settings()
        self.set_status(T("msg_reset_done"), "ok")

    def apply_all_settings(self) -> None:
        """Re-apply the whole config (after Import / Reset)."""
        cfg = self.cfg
        if cfg.hotkey:
            if not self.hotkey.start():
                cfg.hotkey = False
                cfg.save()
        else:
            self.hotkey.stop()
        self.cards_list.set_compact(cfg.compact)
        self.morph_to(cfg.resolve_dark())
        self.choose_language(cfg.lang)                          # re-lays out, re-translates, rebuilds Settings
        if not cfg.animations:
            self.set_animations(False)

    # ── shortcuts / popup ─────────────────────────────────────────────────────
    def _bind_shortcuts(self) -> None:
        for key in ("f", "F"):
            self.bind(f"<Control-{key}>", lambda _e: (self.search.focus_set(), "break")[1])
        for key in ("r", "R"):
            self.bind(f"<Control-{key}>", lambda _e: self.refresh_now())
        for key in ("d", "D"):
            self.bind(f"<Control-Shift-{key}>", lambda _e: self._clean_dev_ports())
        for key in ("q", "Q"):
            self.bind(f"<Control-{key}>", lambda _e: self.quit_app())
        self.bind("<Control-comma>", lambda _e: self.open_settings())
        self.bind("<F5>", lambda _e: self.refresh_now())
        self.bind("<Escape>", self._on_escape)
        self.bind("<Delete>", lambda _e: None if self._typing() else self._kb_stop("end"))
        self.bind("<Shift-Delete>", lambda _e: None if self._typing() else self._kb_stop("kill"))

    def _typing(self) -> bool:
        try:
            return isinstance(self.focus_get(), tk.Entry)
        except (KeyError, tk.TclError):
            return False

    def _on_escape(self, _e=None) -> None:
        if self._popup is not None:
            self.close_popup()
        elif self.search.get():
            self.search.delete(0, "end")
            self._render()
        else:
            self.cards_list.select_key(None)
        self.cards_list.focus_canvas()

    def _kb_stop(self, mode: str) -> None:
        row = self.cards_list.selected_row()
        if row:
            self.request_stop(row, mode)

    def _on_any_click(self, _e=None) -> None:
        if self._popup is not None and time.monotonic() - self._popup.born > 0.25:
            self.close_popup()

    def close_popup(self) -> None:
        if self._popup is not None:
            self._popup.close()
            self._popup = None

    # ── visibility / polling lifecycle (0 % CPU while hidden) ────────────────
    def _on_map(self, e) -> None:
        if e.widget is self or str(e.widget) == ".":
            if self.cfg.theme == "system" and system_is_dark() != self._dark_now:
                self.morph_to(not self._dark_now)               # Windows mode changed while we were hidden
            self._start_polling()

    def _on_unmap(self, e) -> None:
        if e.widget is self or str(e.widget) == ".":
            self.close_popup()
            self._stop_polling()

    def _ensure_polling(self) -> None:
        try:
            if self.state() == "normal":
                self._start_polling()
        except tk.TclError:
            pass

    def _start_polling(self) -> None:
        if self._live or self._quitting:
            return
        self._live = True
        self._first_cycle = True
        self._known = None                                     # next scan is the baseline for NEW badges
        self._start_pulse()
        self._scan_now()

    def _stop_polling(self) -> None:
        """Cancel every timer and free the process cache: nothing runs while hidden."""
        self._live = False
        if self._scan_timer is not None:
            try:
                self.after_cancel(self._scan_timer)
            except tk.TclError:
                pass
            self._scan_timer = None
        self._stop_pulse()
        self.scanner.reset()

    def _start_pulse(self) -> None:
        if self._pulse_job is not None or not self.cfg.animations:
            return

        def tick() -> None:
            self.live_dot.set_phase((time.monotonic() % 2.4) / 2.4)
            self._pulse_job = self.after(70, tick)

        tick()

    def _stop_pulse(self) -> None:
        if self._pulse_job is not None:
            try:
                self.after_cancel(self._pulse_job)
            except tk.TclError:
                pass
            self._pulse_job = None
        self.live_dot.set_phase(0.0)

    def refresh_now(self) -> None:
        if self._live and not self._scan_busy:
            if self._scan_timer is not None:
                try:
                    self.after_cancel(self._scan_timer)
                except tk.TclError:
                    pass
                self._scan_timer = None
            self._scan_now()

    def _scan_now(self) -> None:
        self._scan_timer = None
        if not self._live or self._scan_busy:
            return
        self._scan_busy = True
        threading.Thread(target=self._scan_worker, name="pm-scan", daemon=True).start()

    def _scan_worker(self) -> None:
        try:
            rows = self.scanner.scan()
        except Exception:
            rows = None
        self.call_ui(self._scan_done, rows)

    def _scan_done(self, rows: list[Row] | None) -> None:
        self._scan_busy = False
        if not self._live or self._quitting:
            return                                   # hidden meanwhile: drop the result
        if rows is not None:
            self._mark_new(rows)
            self._rows = rows
            self._have_data = True
            self._last_update = time.strftime("%H:%M:%S")
            self._update_cards()
            self._render()
        delay = FIRST_CYCLE_MS if self._first_cycle else self.cfg.scan_interval * 1000
        self._first_cycle = False
        self._scan_timer = self.after(delay, self._scan_now)

    def _mark_new(self, rows: list[Row]) -> None:
        keys = {r.key for r in rows}
        now = time.time()
        if self._known is not None:
            for k in keys - self._known:
                self._new_until[k] = now + NEW_BADGE_SECONDS
        self._known = keys
        for k in [k for k, t in self._new_until.items() if t < now]:
            del self._new_until[k]

    def is_new(self, key: str) -> bool:
        return self.cfg.highlight_new and self._new_until.get(key, 0.0) > time.time()

    # ── data → UI ─────────────────────────────────────────────────────────────
    def _update_cards(self) -> None:
        if not self._have_data:
            for card in self.metric_cards:
                card.set_dash(T("scanning"))
            return
        rows = self._rows
        tcp = {r.port for r in rows if r.proto == "TCP"}
        udp = {r.port for r in rows if r.proto == "UDP"}
        mem = sum({r.pid: r.ram for r in rows}.values())
        self.card_ports.set_number(len(tcp) + len(udp), lambda v: f"{round(v):,}",
                                   T("c_ports_cap", tcp=len(tcp), udp=len(udp)))
        self.card_procs.set_number(len({r.pid for r in rows}), lambda v: f"{round(v):,}", T("c_procs_cap"))
        self.card_mem.set_number(mem, lambda v: f"{v:,.1f}", T("c_mem_cap"))

    def _filtered(self) -> list[Row]:
        q = self.search.get().strip().lower()
        tab, cfg = self._tab, self.cfg
        out = []
        for r in self._rows:
            if tab == "tcp" and r.proto != "TCP":
                continue
            if tab == "udp" and r.proto != "UDP":
                continue
            if tab == "pinned" and r.port not in cfg.pinned:
                continue
            if tab == "protected" and r.tier not in (TIER_CORE, TIER_SYSTEM) and r.port not in cfg.protected_ports:
                continue
            if q and q not in r.hay and q not in cfg.labels.get(r.port, "").lower():
                continue
            out.append(r)
        return out

    def _groups(self, rows: list[Row]) -> list[tuple[str, str, list[Row]]]:
        """Dedicated separation: pinned · apps & developer tools · Windows system (core + system tiers)."""
        key = lambda r: (r.port, r.proto, r.pid)
        pinned = sorted((r for r in rows if r.port in self.cfg.pinned), key=key)
        rest = [r for r in rows if r.port not in self.cfg.pinned]
        apps = sorted((r for r in rest if r.tier in (TIER_USER, TIER_SELF)), key=key)
        system = sorted((r for r in rest if r.tier in (TIER_CORE, TIER_SYSTEM)), key=key)
        return [(k, T("sec_" + k), rs) for k, rs in (("pinned", pinned), ("apps", apps), ("system", system)) if rs]

    def _render(self) -> None:
        rows = self._filtered()
        if not self._have_data:
            msg = T("empty_scanning")
        elif self._tab == "pinned" and not self.cfg.pinned:
            msg = T("empty_pinned")
        else:
            msg = T("empty_none")
        self._force_open = bool(self.search.get().strip()) or self._tab == "protected"
        self.cards_list.set_empty_text(msg)
        self.cards_list.set_groups(self._groups(rows), self._force_open)
        if self._have_data:
            self.live_lbl.configure(text=T("live", shown=len(rows), total=len(self._rows), time=self._last_update))
        else:
            self.live_lbl.configure(text=T("scanning"))

    def _on_tab(self, key: str) -> None:
        self._tab = key
        self._render()

    def toggle_section(self, key: str) -> None:
        if self._force_open:                                    # a search / the Protected tab shows everything
            return
        if key in self.cfg.collapsed:
            self.cfg.collapsed.discard(key)
            open_ = True
        else:
            self.cfg.collapsed.add(key)
            open_ = False
        self.cfg.save()
        self.cards_list.animate_section(key, open_)

    def set_status(self, text: str, kind: str = "info", seconds: float = 7.0) -> None:
        if self._status_job is not None:
            try:
                self.after_cancel(self._status_job)
            except tk.TclError:
                pass
        colors = {"ok": TK["green"], "warn": TK["orange"], "error": TK["red"], "info": TK["text2"]}
        self.msg_lbl.configure(text=text, text_color=colors.get(kind, TK["text2"]))
        self._status_job = self.after(int(seconds * 1000), lambda: self.msg_lbl.configure(text=""))

    # ── card actions ──────────────────────────────────────────────────────────
    def card_action(self, kind: str, row: Row) -> None:
        if kind == "details":
            self.sheet_details(row)
        elif kind == "end":
            self.request_stop(row, "end")

    def request_stop(self, row: Row, mode: str) -> None:
        """Every stop / restart goes through System Guard: core = blocked, system or user-protected = strict."""
        if self._busy:
            self.set_status(T("msg_busy"), "warn")
            return
        if row.tier == TIER_CORE:
            self.sheet_blocked(row)
            return
        if row.tier == TIER_SELF:
            self.sheet_self()
            return
        custom = row.tier == TIER_USER and row.port in self.cfg.protected_ports
        if not self.sheet_confirm(row, mode, strict=row.tier == TIER_SYSTEM or custom, custom=custom):
            return
        self._run_job(mode, [(row.pid, row.name)])

    def sheet_blocked(self, row: Row) -> None:
        sh = Sheet(self)
        sh.red_border()
        sh.icon("!", "red")
        sh.title_text(T("core_title"), "red")
        sh.message(T("core_body", name=row.name, pid=row.pid))
        sh.button(T("sheet_ok"), "danger", lambda: sh.choose(True))
        sh.run()

    def sheet_self(self) -> None:
        sh = Sheet(self)
        sh.icon("i", "blue")
        sh.title_text(T("self_title"))
        sh.message(T("self_body"), muted=True)
        sh.button(T("sheet_ok"), "primary", lambda: sh.choose(True))
        sh.run()

    def sheet_confirm(self, row: Row, mode: str, strict: bool, custom: bool = False) -> bool:
        note = T(f"cf_{mode}_note", pid=row.pid, port=row.port, proto=row.proto)
        sh = Sheet(self)
        if strict:
            sh.red_border()
            sh.icon("!", "red")
            if custom:
                sh.title_text(T("pp_title", port=row.port), "red")
                sh.message(T("pp_body", name=row.name, pid=row.pid, port=row.port))
            else:
                sh.title_text(T("sys_title"), "red")
                sh.message(T("sys_body", name=row.name, pid=row.pid))
            sh.message(note, muted=True)
            sh.button(T("sys_confirm"), "danger", lambda: sh.choose(True))
        else:
            sh.title_text(T(f"cf_{mode}", name=row.name), pady=(26, 4))
            sh.message(note, muted=True)
            sh.button(T(f"cf_btn_{mode}"), "primary" if mode == "restart" else "danger", lambda: sh.choose(True))
        sh.button(T("sheet_cancel"), "secondary", lambda: sh.choose(False))
        return bool(sh.run())

    def sheet_details(self, row: Row) -> None:
        F, rtl = self.fonts, self.rtl
        a = "e" if rtl else "w"
        d = process_details(row.pid)
        exe, cmd = d["exe"] or row.exe, d["cmd"] or row.cmd
        started = d["started"] or row.started
        flagged = row.tier in (TIER_CORE, TIER_SYSTEM)
        sh = Sheet(self, width=500)
        sh.title_text(row.name, "red" if flagged else "text", size=21, pady=(24, 2))
        sh.message(T("d_tier_" + row.tier), muted=not flagged, token="red" if flagged else None)
        box = Frame(sh.card, fg_color=TK["card_hi"], corner_radius=14)
        box.pack(fill="x", padx=18, pady=(6, 8))
        box.grid_columnconfigure(0, weight=1)
        box.grid_columnconfigure(1, weight=1)
        expo = T("d_expo_local" if row.exposure == "local" else "d_expo_lan", ip=row.bind)
        info = [("d_pid", str(row.pid)), ("d_port", f"{row.port} / {row.proto}"), ("d_expo", expo),
                ("d_ram", f"{row.ram:,.1f} MB"), ("d_cpu", f"{row.cpu:.1f}%"),
                ("d_uptime", fmt_uptime(time.time() - started) if started else T("d_na"))]
        for i, (k, v) in enumerate(info):
            py = (10 if i == 0 else 3, 10 if i == len(info) - 1 else 3)
            Label(box, text=T(k), font=F.ui(13), text_color=TK["text2"], anchor=a).grid(
                row=i, column=1 if rtl else 0, sticky=a, padx=14, pady=py)
            Label(box, text=v, font=F.monof(13), text_color=TK["text"], anchor="w" if rtl else "e").grid(
                row=i, column=0 if rtl else 1, sticky="w" if rtl else "e", padx=14, pady=py)
        if self.cfg.deep_scan and row.threats:
            Label(sh.card, text=T("d_findings"), font=F.ui(12, True), text_color=TK["orange"], anchor=a).pack(
                fill="x", padx=24, pady=(6, 2))
            sh.message("\n".join("• " + T("th_" + c) for c in row.threats), boxed=True,
                       justify="right" if rtl else "left")
        for key, text in (("d_path", exe), ("d_cmd", cmd)):
            Label(sh.card, text=T(key), font=F.ui(12, True), text_color=TK["text2"], anchor=a).pack(
                fill="x", padx=24, pady=(6, 2))
            tb = Textbox(sh.card, height=62, corner_radius=12, border_width=0, fg_color=TK["card_hi"],
                         text_color=TK["text"], font=F.monof(12), wrap="word")
            tb.pack(fill="x", padx=18, pady=(0, 4))
            tb.insert("1.0", text or T("d_na"))
            tb.configure(state="disabled")
        sh.button(T("sheet_close"), "primary", lambda: sh.choose(True))
        sh.run()

    def edit_label(self, row: Row) -> None:
        F = self.fonts
        sh = Sheet(self)
        sh.title_text(T("label_title"), pady=(26, 4))
        sh.message(T("label_hint", port=row.port), muted=True)
        entry = Entry(sh.card, height=44, corner_radius=22, border_width=0, fg_color=TK["card_hi"],
                      text_color=TK["text"], placeholder_text=T("label_ph"), placeholder_text_color=TK["text3"],
                      font=F.ui(14))
        entry.pack(fill="x", padx=22, pady=(6, 12))
        try:
            entry._entry.configure(justify="right" if self.rtl else "left")
        except Exception:
            pass
        current = self.cfg.labels.get(row.port, "")
        if current:
            entry.insert(0, current)

        def save() -> None:
            sh.value = entry.get().strip()[:40]
            sh.choose("save")

        entry.bind("<Return>", lambda _e: save())
        sh.button(T("sheet_save"), "primary", save)
        if current:
            sh.button(T("sheet_clear"), "secondary", lambda: sh.choose("clear"))
        sh.button(T("sheet_cancel"), "secondary", lambda: sh.choose(None))
        sh.after(350, lambda: entry.focus_set())
        res = sh.run()
        if res == "save" and sh.value:
            self.cfg.labels[row.port] = sh.value
            self.set_status(T("msg_label_saved", port=row.port), "ok")
        elif res in ("save", "clear"):
            self.cfg.labels.pop(row.port, None)
            self.set_status(T("msg_label_cleared", port=row.port), "info")
        else:
            return
        self.cfg.save()
        self._render()

    # ── context menu actions ──────────────────────────────────────────────────
    def open_context_menu(self, row: Row, x: int, y: int) -> None:
        self.close_popup()
        pinned = row.port in self.cfg.pinned
        N, D, X = "normal", "danger", "disabled"
        items = [
            (T("menu_open"), lambda: webbrowser.open(f"http://localhost:{row.port}"), N if row.proto == "TCP" else X),
            (T("menu_restart"), lambda: self.request_stop(row, "restart"), N if row.cmd else X),
            (T("menu_kill"), lambda: self.request_stop(row, "kill"), D),
            (T("menu_tree"), lambda: self.request_stop(row, "tree"), D),
            None,
            (T("menu_unpin" if pinned else "menu_pin"), lambda: self.toggle_pin(row), N),
            (T("menu_label"), lambda: self.edit_label(row), N),
            None,
            (T("menu_copy_port"), lambda: self.copy_text(str(row.port), "what_port"), N),
            (T("menu_copy_pid"), lambda: self.copy_text(str(row.pid), "what_pid"), N),
            (T("menu_open_loc"), lambda: self.open_location(row), N if row.exe else X),
        ]
        self._popup = PopupMenu(self, items, x, y)

    def toggle_pin(self, row: Row) -> None:
        if row.port in self.cfg.pinned:
            self.cfg.pinned.discard(row.port)
            self.set_status(T("msg_unpinned", port=row.port), "info")
        else:
            self.cfg.pinned.add(row.port)
            self.set_status(T("msg_pinned", port=row.port), "ok")
        self.cfg.save()
        self._render()

    def copy_text(self, text: str, what_key: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        self.set_status(T("msg_copied", what=f"{T(what_key)} ({text})"), "ok")

    def open_location(self, row: Row) -> None:
        try:
            subprocess.Popen(f'explorer /select,"{row.exe}"')
        except OSError:
            self.set_status(T("msg_open_fail"), "error")

    # ── jobs (run in worker threads, never block the UI) ──────────────────────
    def _clean_dev_ports(self) -> None:
        if self._busy:
            self.set_status(T("msg_busy"), "warn")
            return
        by_pid: dict[int, tuple[str, set[int]]] = {}
        for r in self._rows:
            if r.dev and r.proto == "TCP" and r.tier == TIER_USER and r.port not in self.cfg.protected_ports:
                by_pid.setdefault(r.pid, (r.name, set()))[1].add(r.port)
        if not by_pid:
            self.set_status(T("clean_none", ports=", ".join(str(p) for p in sorted(DEV_PORTS))), "info")
            return
        items = list(by_pid.items())
        lines = "\n".join(f"{name}  (PID {pid})  ·  " + ", ".join(f":{p}" for p in sorted(ports))
                          for pid, (name, ports) in items[:10])
        if len(items) > 10:
            lines += "\n" + T("clean_more", n=len(items) - 10)
        sh = Sheet(self)
        sh.title_text(T("clean_title", n=len(items)), pady=(26, 4))
        sh.message(lines, boxed=True, mono=True, justify="left")
        sh.message(T("clean_body"), muted=True)
        sh.button(T("btn_clean"), "danger", lambda: sh.choose(True))
        sh.button(T("sheet_cancel"), "secondary", lambda: sh.choose(False))
        if sh.run():
            self._run_job("tree", [(pid, name) for pid, (name, _p) in items])

    def _run_job(self, mode: str, targets: list[tuple[int, str]]) -> None:
        self._busy = True
        self.set_status(T("msg_working"), "info", 30)
        threading.Thread(target=self._job_worker, args=(mode, targets), name="pm-job", daemon=True).start()

    def _job_worker(self, mode: str, targets: list[tuple[int, str]]) -> None:
        results = []
        for pid, name in targets:
            ok, code = restart_process(pid) if mode == "restart" else end_process(pid, mode)
            results.append((name, pid, ok, code))
        self.call_ui(self._job_done, mode, results)

    def _job_done(self, mode: str, results) -> None:
        self._busy = False
        text, kind = summarize(results, mode)
        self.set_status(text, kind)
        if self._live:
            self.after(500, self.refresh_now)

    def tray_clean_dev_ports(self) -> None:
        """Tray one-click cleanup. Runs in a worker thread; works even while the window is hidden."""
        results = clean_dev_ports(frozenset(self.cfg.protected_ports))
        if results:
            text, _kind = summarize(results, "tree")
        else:
            text = T("clean_none", ports=", ".join(str(p) for p in sorted(DEV_PORTS)))
        if self.tray is not None:
            self.tray.notify(text)
        self.call_ui(self.refresh_now)

    # ── window management ─────────────────────────────────────────────────────
    def _fade(self, start: float, end: float, done=None, steps: int = 8, delay: int = 12) -> None:
        if self._fade_job is not None:
            try:
                self.after_cancel(self._fade_job)
            except tk.TclError:
                pass
            self._fade_job = None

        def step(i: int = 0) -> None:
            try:
                self.attributes("-alpha", start + (end - start) * i / steps)
            except tk.TclError:
                return
            if i < steps:
                self._fade_job = self.after(delay, step, i + 1)
            else:
                self._fade_job = None
                if done:
                    done()

        step()

    def _on_close(self) -> None:
        if self.cfg.close_action == "quit":
            self.quit_app()
        else:
            self.hide_to_tray()

    def hide_to_tray(self) -> None:
        """Fade out, withdraw to the notification area, and stop ALL polling and animation timers."""
        if self.tray is None or not self.tray.ok:
            self.iconify()                               # no tray available: never lose the window
            return
        self.close_popup()
        self._stop_polling()
        self._fade(1.0, 0.0, done=self._finish_hide)

    def _finish_hide(self) -> None:
        self.withdraw()
        try:
            self.attributes("-alpha", 1.0)
        except tk.TclError:
            pass

    def show_window(self) -> None:
        if self._quitting:
            return
        try:
            if self.state() in ("withdrawn", "iconic"):
                self.attributes("-alpha", 0.0)
                self.deiconify()
                self._fade(0.0, 1.0)
            self.lift()
            self.attributes("-topmost", True)
            self.after(200, lambda: self.attributes("-topmost", False))
            self.focus_force()
            self._start_polling()
        except tk.TclError:
            pass

    def toggle_window(self) -> None:
        """Global hotkey: hide when it is the active window, otherwise bring it to the front."""
        try:
            visible = self.state() == "normal" and bool(self.winfo_viewable())
            active = visible and self.focus_displayof() is not None
        except tk.TclError:
            visible = active = False
        if active:
            self.hide_to_tray()
        else:
            self.show_window()

    def quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.close_popup()
        self._stop_polling()
        self.hotkey.stop()
        if self.tray is not None:
            self.tray.stop()
        self.guard.close()
        try:
            self.destroy()
        except tk.TclError:
            pass


# ══════════════════════════════════════════════════════════════════════════════
def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)     # --noconsole builds have no stderr: never print/exit
    parser.add_argument("--minimized", action="store_true")
    parser.add_argument("--export-icon", metavar="PATH")
    args, _unknown = parser.parse_known_args()

    if args.export_icon:
        export_icon(args.export_icon)
        return 0

    guard = SingleInstance()
    if guard.already_running:
        if not args.minimized:
            guard.signal_existing()                      # bring the running instance to the front
        guard.close()
        return 0

    cfg = Config()
    cfg.lang = set_language(cfg.lang)
    bootstrap_startup(cfg)
    dark = cfg.resolve_dark()
    set_palette(dark)
    ctk.set_appearance_mode("dark" if dark else "light")   # set once: every colour is explicit, never switched again
    ctk.set_default_color_theme("blue")
    app = PortManagerApp(cfg, start_hidden=args.minimized or cfg.start_minimized, guard=guard)
    guard.listen(lambda: app.call_ui(app.show_window))
    app.mainloop()
    guard.close()
    return 0


if __name__ == "__main__":
    code = main()
    os._exit(code)      # guarantees no lingering tray/hotkey thread keeps the process alive after Quit
