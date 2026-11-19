#!/usr/bin/env python3
"""
PortManager 2.0 — an iOS-styled, bilingual (English / العربية) Task Manager & Network Port Monitor for Windows.

    Author      : Mustafa Al-Aqrawi (مصطفى العقراوي) — https://github.com/spoonix-dev
    Repository  : https://github.com/spoonix-dev/PortManager
    License     : MIT — Copyright (c) 2026 Mustafa Al-Aqrawi (spoonix-dev)

Architecture (unchanged from 1.x)
---------------------------------
* Single instance  : Windows named mutex; a second launch wakes the first via a named event.
* Zero idle CPU    : scanning exists only while the window is mapped. Hiding to the tray (or minimizing) cancels
                     the timer and drops the process cache. Each scan runs in a short-lived worker thread.
* Startup          : HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run, launched with --minimized.
* No asset files   : window, tray and .exe icons (and every glyph) are drawn in memory with Pillow.

New in 2.0
----------
* iOS design language, Dark / Light themes (+ matching Windows title bar via DwmSetWindowAttribute).
* Arabic / English UI with full RTL mirroring; theme, language, pins and labels persist in ~/.portmanager.json.
* System Guard: core Windows processes are locked, Windows system services need a strict red confirmation.
* Network-exposure / Docker-WSL / DEV badges, pinning, custom labels, process inspector, right-click menu
  (open in browser, restart, end process tree, copy, open file location).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from pathlib import Path
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
__version__ = "2.0.0"
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
POLL_MS = 2000
FIRST_CYCLE_MS = 1000
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


# ══════════════════════════════════════════════════════════════════════════════
# Apple system colours — Dark and Light palettes
# ══════════════════════════════════════════════════════════════════════════════
DARK = dict(
    bg="#000000", card="#1C1C1E", card_hi="#2C2C2E", field="#1C1C1E", pop="#2C2C2E", hover="#2C2C2E",
    sep="#38383A", text="#FFFFFF", text2="#98989D", text3="#636366",
    blue="#0A84FF", red="#FF453A", green="#30D158", orange="#FF9F0A", purple="#BF5AF2", on_accent="#FFFFFF",
)
LIGHT = dict(
    bg="#F2F2F7", card="#FFFFFF", card_hi="#EFEFF4", field="#E3E3E8", pop="#FFFFFF", hover="#F2F2F7",
    sep="#D1D1D6", text="#000000", text2="#6C6C70", text3="#AEAEB2",
    blue="#007AFF", red="#FF3B30", green="#34C759", orange="#FF9500", purple="#AF52DE", on_accent="#FFFFFF",
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
    for name in ("blue", "red", "green", "orange", "purple"):
        d[f"{name}_tint"] = mix(p["card"], p[name], 0.16)
        d[f"{name}_tint_hi"] = mix(p["card"], p[name], 0.26)
    d["gray_tint"] = mix(p["card"], p["text2"], 0.16)
    d["blue_hi"] = mix(p["blue"], p["text"], 0.14)
    d["red_hi"] = mix(p["red"], p["text"], 0.14)
    d["card_hi_hi"] = mix(p["card_hi"], p["text"], 0.07)
    return d


_DD, _DL = derive(DARK), derive(LIGHT)
TK = {k: (_DL[k], _DD[k]) for k in _DD}          # (light, dark) tuples — CTk switches them automatically
PAL: dict[str, str] = dict(_DD)                    # flat palette for canvas drawing (updated by set_palette)


def set_palette(dark: bool) -> None:
    PAL.clear()
    PAL.update(_DD if dark else _DL)


# ══════════════════════════════════════════════════════════════════════════════
# Bilingual strings (English / Arabic)
# ══════════════════════════════════════════════════════════════════════════════
STR = {
    "en": {
        "subtitle": "Task Manager & Network Port Monitor",
        "search_ph": "Search ports, apps, PIDs…",
        "tab_all": "All", "tab_tcp": "TCP", "tab_udp": "UDP", "tab_pinned": "Pinned", "tab_protected": "Protected",
        "lang_btn": "عربي", "theme_to_light": "Light", "theme_to_dark": "Dark",
        "startup": "Launch at startup",
        "c_ports": "ACTIVE PORTS", "c_procs": "PROCESSES", "c_mem": "MEMORY (MB)",
        "c_ports_cap": "{tcp} TCP · {udp} UDP", "c_procs_cap": "own listening ports", "c_mem_cap": "resident memory",
        "btn_details": "Details", "btn_end": "End", "btn_locked": "Locked", "btn_clean": "Clean Dev Ports",
        "badge_core": "CORE SYSTEM", "badge_system": "SYSTEM", "badge_dev": "DEV", "badge_docker": "Docker/WSL",
        "badge_local": "Local ({ip})", "badge_lan": "LAN ({ip})",
        "live": "Live · {shown} of {total} · updated {time}", "scanning": "Scanning…",
        "empty_scanning": "Scanning ports…",
        "empty_none": "No ports match your search or filter",
        "empty_pinned": "No pinned ports yet — right-click a card and choose Pin Port",
        "menu_open": "Open in Browser", "menu_restart": "Restart Process", "menu_kill": "Force Kill",
        "menu_tree": "End Process Tree", "menu_pin": "Pin Port", "menu_unpin": "Unpin Port",
        "menu_label": "Set Custom Label…", "menu_copy_port": "Copy Port Number", "menu_copy_pid": "Copy PID",
        "menu_open_loc": "Open File Location",
        "sheet_ok": "OK", "sheet_cancel": "Cancel", "sheet_close": "Close", "sheet_save": "Save",
        "sheet_clear": "Remove label",
        "core_title": "Core Windows Process",
        "core_body": "Ending {name} (PID {pid}) is blocked.\n\nThis process is a core part of Windows. "
                     "Terminating it can trigger a blue screen (BSOD) or crash the whole system, "
                     "so PortManager will never end it.",
        "self_title": "That's PortManager",
        "self_body": "PortManager can't end itself. Use Quit from the tray menu (or Ctrl+Q) to close it.",
        "sys_title": "Windows System Process",
        "sys_body": "{name} (PID {pid}) is part of Windows. Ending it can break Windows features, sign you out, "
                    "or crash the system.\n\nContinue only if you are absolutely sure.",
        "sys_confirm": "End Anyway",
        "cf_end": "End {name}?", "cf_kill": "Force kill {name}?", "cf_tree": "End {name} and its child processes?",
        "cf_restart": "Restart {name}?",
        "cf_end_note": "PID {pid} · port {port}/{proto}. The app is asked to close first.",
        "cf_kill_note": "PID {pid} · port {port}/{proto}. It stops immediately — unsaved data is lost.",
        "cf_tree_note": "PID {pid} · port {port}/{proto}. The process and every child process stop immediately.",
        "cf_restart_note": "PID {pid} · port {port}/{proto}. It is stopped, then started again with its original "
                           "command line and folder.",
        "cf_btn_end": "End Task", "cf_btn_kill": "Force Kill", "cf_btn_tree": "End Tree", "cf_btn_restart": "Restart",
        "clean_title": "Stop dev processes ({n})?",
        "clean_body": "Everything listening on a dev port is stopped together with its child processes.",
        "clean_none": "No dev servers found on {ports}",
        "clean_more": "… and {n} more",
        "label_title": "Custom label", "label_hint": "Name for port {port}", "label_ph": "e.g. Frontend, Postgres DB",
        "d_pid": "PID", "d_port": "Port / Protocol", "d_expo": "Network exposure", "d_ram": "RAM", "d_cpu": "CPU",
        "d_uptime": "Uptime", "d_path": "Executable path", "d_cmd": "Command line", "d_na": "Unavailable",
        "d_expo_local": "Local only · {ip}", "d_expo_lan": "Network exposed · {ip}",
        "d_tier_core": "Core Windows process — protected",
        "d_tier_system": "Windows system component — confirmation required",
        "d_tier_user": "Regular process", "d_tier_self": "PortManager (this app)",
        "u_d": "{n}d", "u_h": "{n}h", "u_m": "{n}m", "u_s": "{n}s",
        "msg_nothing": "Nothing to do", "msg_working": "Working…", "msg_busy": "Another action is still running…",
        "msg_ended": "Ended {n}: {names}", "msg_restarted": "Restarted {name}",
        "msg_partial": "Ended {ok}, failed {bad} — {name} (PID {pid}): {why}",
        "msg_failed": "{name} (PID {pid}): {why}{more}", "msg_more": " (+{n} more)",
        "msg_copied": "Copied {what}", "what_port": "port number", "what_pid": "PID",
        "msg_pinned": "Pinned port {port}", "msg_unpinned": "Unpinned port {port}",
        "msg_label_saved": "Label saved for port {port}", "msg_label_cleared": "Label removed from port {port}",
        "msg_startup_fail": "Could not update the Windows startup entry.",
        "msg_open_fail": "Couldn't open that location.",
        "res_closed": "closed", "res_ended": "ended", "res_killed": "killed", "res_gone": "already exited",
        "res_denied": "access denied — run as Administrator", "res_protected": "protected system process",
        "res_not_closed": "did not close — try Force Kill", "res_not_ended": "did not exit — try Force Kill",
        "res_still": "still running", "res_no_cmdline": "command line unavailable",
        "res_launch_failed": "could not be started again", "res_error": "unexpected error",
        "res_restarted": "restarted",
        "tray_show": "Show PortManager", "tray_clean": "Clean Dev Ports", "tray_startup": "Start with Windows",
        "tray_github": "GitHub — @spoonix-dev/PortManager", "tray_quit": "Quit PortManager",
    },
    "ar": {
        "subtitle": "مدير المهام ومراقب منافذ الشبكة",
        "search_ph": "ابحث في المنافذ والتطبيقات…",
        "tab_all": "الكل", "tab_tcp": "TCP", "tab_udp": "UDP", "tab_pinned": "المثبتة", "tab_protected": "المحمية",
        "lang_btn": "EN", "theme_to_light": "فاتح", "theme_to_dark": "داكن",
        "startup": "التشغيل مع ويندوز",
        "c_ports": "المنافذ النشطة", "c_procs": "العمليات", "c_mem": "الذاكرة (MB)",
        "c_ports_cap": "{tcp} TCP · {udp} UDP", "c_procs_cap": "تملك منافذ مفتوحة", "c_mem_cap": "الذاكرة المستخدمة",
        "btn_details": "التفاصيل", "btn_end": "إنهاء", "btn_locked": "مقفل", "btn_clean": "تنظيف منافذ التطوير",
        "badge_core": "نظام أساسي", "badge_system": "نظام", "badge_dev": "تطوير", "badge_docker": "Docker/WSL",
        "badge_local": "محلي ({ip})", "badge_lan": "شبكة ({ip})",
        "live": "مباشر · {shown} من {total} · {time}", "scanning": "جارٍ الفحص…",
        "empty_scanning": "جارٍ فحص المنافذ…",
        "empty_none": "لا توجد منافذ تطابق بحثك أو التصفية",
        "empty_pinned": "لا توجد منافذ مثبتة — انقر بزر الفأرة الأيمن على بطاقة واختر تثبيت المنفذ",
        "menu_open": "فتح في المتصفح", "menu_restart": "إعادة تشغيل العملية", "menu_kill": "إنهاء إجباري",
        "menu_tree": "إنهاء شجرة العمليات", "menu_pin": "تثبيت المنفذ", "menu_unpin": "إلغاء تثبيت المنفذ",
        "menu_label": "تسمية مخصصة…", "menu_copy_port": "نسخ رقم المنفذ", "menu_copy_pid": "نسخ رقم العملية (PID)",
        "menu_open_loc": "فتح موقع الملف",
        "sheet_ok": "حسنًا", "sheet_cancel": "إلغاء", "sheet_close": "إغلاق", "sheet_save": "حفظ",
        "sheet_clear": "إزالة التسمية",
        "core_title": "عملية أساسية في ويندوز",
        "core_body": "إنهاء {name} (PID {pid}) محظور.\n\nهذه العملية جزء أساسي من ويندوز، وإنهاؤها قد يسبّب "
                     "شاشة زرقاء (BSOD) أو انهيار النظام بالكامل، لذلك لن يُنهيها PortManager أبدًا.",
        "self_title": "هذا هو PortManager",
        "self_body": "لا يستطيع PortManager إنهاء نفسه. استخدم «خروج» من قائمة الأيقونة (أو Ctrl+Q) لإغلاقه.",
        "sys_title": "عملية نظام في ويندوز",
        "sys_body": "{name} (PID {pid}) جزء من ويندوز. إنهاؤها قد يعطّل ميزات في ويندوز أو يُنهي جلستك "
                    "أو يتسبّب في انهيار النظام.\n\nتابع فقط إذا كنت متأكدًا تمامًا.",
        "sys_confirm": "إنهاء رغم ذلك",
        "cf_end": "إنهاء {name}؟", "cf_kill": "إنهاء {name} بالقوة؟", "cf_tree": "إنهاء {name} وكل عملياتها الفرعية؟",
        "cf_restart": "إعادة تشغيل {name}؟",
        "cf_end_note": "PID {pid} · المنفذ {port}/{proto}. سيُطلب من التطبيق أن يُغلق نفسه أولًا.",
        "cf_kill_note": "PID {pid} · المنفذ {port}/{proto}. ستتوقف فورًا — وستضيع أي بيانات غير محفوظة.",
        "cf_tree_note": "PID {pid} · المنفذ {port}/{proto}. ستتوقف العملية وكل عملياتها الفرعية فورًا.",
        "cf_restart_note": "PID {pid} · المنفذ {port}/{proto}. ستُوقَف ثم تُشغَّل من جديد بنفس سطر الأوامر والمجلد الأصلي.",
        "cf_btn_end": "إنهاء المهمة", "cf_btn_kill": "إنهاء إجباري", "cf_btn_tree": "إنهاء الشجرة",
        "cf_btn_restart": "إعادة التشغيل",
        "clean_title": "إيقاف عمليات التطوير ({n})؟",
        "clean_body": "سيتم إيقاف كل ما يستمع على منفذ تطوير مع عملياته الفرعية.",
        "clean_none": "لم يتم العثور على خوادم تطوير على {ports}",
        "clean_more": "… و{n} أخرى",
        "label_title": "تسمية مخصصة", "label_hint": "اسم للمنفذ {port}", "label_ph": "مثال: الواجهة الأمامية، قاعدة Postgres",
        "d_pid": "رقم العملية (PID)", "d_port": "المنفذ / البروتوكول", "d_expo": "إتاحة الشبكة", "d_ram": "الذاكرة",
        "d_cpu": "المعالج", "d_uptime": "مدة التشغيل", "d_path": "مسار الملف التنفيذي", "d_cmd": "سطر الأوامر",
        "d_na": "غير متاح",
        "d_expo_local": "محلي فقط · {ip}", "d_expo_lan": "متاح على الشبكة · {ip}",
        "d_tier_core": "عملية أساسية في ويندوز — محمية",
        "d_tier_system": "مكوّن نظام في ويندوز — يتطلب تأكيدًا",
        "d_tier_user": "عملية عادية", "d_tier_self": "PortManager (هذا التطبيق)",
        "u_d": "{n} يوم", "u_h": "{n} ساعة", "u_m": "{n} دقيقة", "u_s": "{n} ثانية",
        "msg_nothing": "لا شيء للتنفيذ", "msg_working": "جارٍ التنفيذ…", "msg_busy": "هناك إجراء آخر قيد التنفيذ…",
        "msg_ended": "تم إنهاء {n}: {names}", "msg_restarted": "تمت إعادة تشغيل {name}",
        "msg_partial": "تم إنهاء {ok}، وفشل {bad} — {name} (PID {pid}): {why}",
        "msg_failed": "{name} (PID {pid}): {why}{more}", "msg_more": " (+{n} أخرى)",
        "msg_copied": "تم نسخ {what}", "what_port": "رقم المنفذ", "what_pid": "رقم العملية",
        "msg_pinned": "تم تثبيت المنفذ {port}", "msg_unpinned": "تم إلغاء تثبيت المنفذ {port}",
        "msg_label_saved": "تم حفظ التسمية للمنفذ {port}", "msg_label_cleared": "تمت إزالة التسمية من المنفذ {port}",
        "msg_startup_fail": "تعذّر تحديث إدخال التشغيل مع ويندوز.",
        "msg_open_fail": "تعذّر فتح الموقع.",
        "res_closed": "تم الإغلاق", "res_ended": "تم الإنهاء", "res_killed": "تم الإنهاء بالقوة",
        "res_gone": "كانت قد انتهت أصلًا", "res_denied": "تم رفض الوصول — شغّل البرنامج كمسؤول",
        "res_protected": "عملية نظام محمية", "res_not_closed": "لم تُغلق — جرّب الإنهاء الإجباري",
        "res_not_ended": "لم تنتهِ — جرّب الإنهاء الإجباري", "res_still": "ما زالت تعمل",
        "res_no_cmdline": "سطر الأوامر غير متاح", "res_launch_failed": "تعذّر تشغيلها من جديد",
        "res_error": "حدث خطأ غير متوقع", "res_restarted": "أُعيد تشغيلها",
        "tray_show": "إظهار PortManager", "tray_clean": "تنظيف منافذ التطوير", "tray_startup": "التشغيل مع ويندوز",
        "tray_github": "GitHub — ‎@spoonix-dev/PortManager", "tray_quit": "إنهاء PortManager",
    },
}
I18N = {"lang": "en"}


def T(key: str, **kw) -> str:
    s = STR.get(I18N["lang"], STR["en"]).get(key) or STR["en"].get(key, key)
    return s.format(**kw) if kw else s


def tr_code(code: str) -> str:
    return T("res_" + code) if ("res_" + code) in STR["en"] else code


# ══════════════════════════════════════════════════════════════════════════════
# Config  (~/.portmanager.json)
# ══════════════════════════════════════════════════════════════════════════════
def default_language() -> str:
    try:
        langid = ctypes.WinDLL("kernel32").GetUserDefaultUILanguage()
        return "ar" if (langid & 0x3FF) == 0x01 else "en"
    except Exception:
        return "en"


class Config:
    """Language, theme, pinned ports and custom labels — one small JSON file in the user's home folder."""

    PATH = Path.home() / ".portmanager.json"
    LEGACY = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME / "settings.json"   # PortManager 1.x

    def __init__(self) -> None:
        self.lang = default_language() if not self.PATH.exists() else "en"
        self.theme = "dark"
        self.pinned: set[int] = set()
        self.labels: dict[int, str] = {}
        self.startup_initialized = False
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        if raw.get("language") in ("en", "ar"):
            self.lang = raw["language"]
        if raw.get("theme") in ("dark", "light"):
            self.theme = raw["theme"]
        self.pinned = {n for n in (self._int(p) for p in raw.get("pinned", []) if isinstance(raw.get("pinned"), list))
                       if n is not None}
        labels = raw.get("labels", {})
        self.labels = {}
        if isinstance(labels, dict):
            for k, v in labels.items():
                n = self._int(k)
                if n is not None and str(v).strip():
                    self.labels[n] = str(v).strip()[:40]
        self.startup_initialized = bool(raw.get("startup_initialized", False))
        if "startup_initialized" not in raw:                     # migrate the 1.x flag so we never re-enable auto-run
            try:
                if json.loads(self.LEGACY.read_text(encoding="utf-8")).get("startup_initialized"):
                    self.startup_initialized = True
            except (OSError, ValueError, AttributeError):
                pass

    @staticmethod
    def _int(v) -> int | None:
        try:
            n = int(v)
            return n if 0 < n < 65536 else None
        except (TypeError, ValueError):
            return None

    def save(self) -> None:
        payload = {
            "version": __version__, "language": self.lang, "theme": self.theme,
            "pinned": sorted(self.pinned), "labels": {str(k): v for k, v in sorted(self.labels.items())},
            "startup_initialized": self.startup_initialized,
        }
        try:
            tmp = self.PATH.with_name(self.PATH.name + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.PATH)
        except OSError:
            pass


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


def set_titlebar(window, dark: bool, bg: str, fg: str) -> None:
    """Dark/light Windows title bar via DwmSetWindowAttribute (+ caption colours on Windows 11)."""
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
        flag = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):                                   # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old id)
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(flag), ctypes.sizeof(flag)) == 0:
                break
        for attr, colour in ((35, bg), (36, fg)):               # DWMWA_CAPTION_COLOR / TEXT_COLOR (Windows 11)
            r, g, b = hex_rgb(colour)
            ref = ctypes.c_uint(r | (g << 8) | (b << 16))
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ref), ctypes.sizeof(ref))
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)   # repaint the frame
    except Exception:
        pass


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
            rows.append(Row(
                key=f"{proto}|{port}|{pid}", port=port, proto=proto, pid=pid, name=pi.name, cpu=cpu, ram=ram,
                bind=", ".join(binds), exposure=exposure, expo_ip=expo_ip, cmd=pi.cmd, exe=pi.exe,
                started=pi.started, tier=pi.tier, docker=pi.docker, dev=port in DEV_PORTS,
                hay=f"{port} :{port} {proto} {pi.name} {pid} {', '.join(binds)} {pi.cmd} {pi.exe}".lower(),
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
                    _safe(proc.create_time, 0.0), classify(pid, name, exe), is_docker(name, exe))
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


def clean_dev_ports() -> list[tuple[str, int, bool, str]]:
    """Kill (with child tree) every *regular* process listening on a dev port. Returns (name, pid, ok, code)."""
    targets: set[int] = {l.pid for l in list_listeners() if l.proto == "TCP" and l.port in DEV_PORTS}
    results = []
    for pid in sorted(targets):
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


BTN_STYLES = {                                            # fill, hover, text
    "primary": (TK["blue"], TK["blue_hi"], TK["on_accent"]),
    "secondary": (TK["card_hi"], TK["card_hi_hi"], TK["blue"]),
    "danger": (TK["red"], TK["red_hi"], TK["on_accent"]),
}


def pill(master, text: str, style: str, command, fonts: Fonts, height: int = 46, **kw) -> ctk.CTkButton:
    fg, hover, tc = BTN_STYLES[style]
    return ctk.CTkButton(master, text=text, command=command, height=height, corner_radius=height // 2,
                         fg_color=fg, hover_color=hover, text_color=tc, font=fonts.ui(15, True),
                         border_width=0, **kw)


class MetricCard(ctk.CTkFrame):
    """iOS-style summary card: coloured dot + caption, big value, small footnote. Mirrors in RTL."""

    def __init__(self, master, fonts: Fonts, dot_token: str) -> None:
        super().__init__(master, fg_color=TK["card"], corner_radius=20, border_width=0)
        self.dot = ctk.CTkLabel(self, text="●", text_color=TK[dot_token], font=fonts.ui(11), width=14, height=18)
        self.title = ctk.CTkLabel(self, text="", text_color=TK["text2"], font=fonts.ui(11, True), height=18)
        self.value = ctk.CTkLabel(self, text="—", text_color=TK["text"], font=fonts.ui(30, True), height=40)
        self.caption = ctk.CTkLabel(self, text=" ", text_color=TK["text3"], font=fonts.ui(12), height=18)
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
        self.value.configure(anchor=a)
        self.caption.configure(anchor=a)
        self.title.configure(anchor=a)

    def set_title(self, text: str) -> None:
        self.title.configure(text=text)

    def set(self, value: str, caption: str) -> None:
        self.value.configure(text=value)
        self.caption.configure(text=caption)


class PillTabs(ctk.CTkFrame):
    """Capsule segmented control (All · TCP · UDP · Pinned · Protected)."""

    def __init__(self, master, fonts: Fonts, command) -> None:
        super().__init__(master, fg_color=TK["field"], corner_radius=23)
        self._f, self._cmd = fonts, command
        self._btns: dict[str, ctk.CTkButton] = {}
        self._sel = "all"

    def set_items(self, items: list[tuple[str, str]], rtl: bool, selected: str) -> None:
        for b in self._btns.values():
            b.destroy()
        self._btns, self._sel = {}, selected
        order = list(reversed(items)) if rtl else list(items)
        for col, (key, label) in enumerate(order):
            self.grid_columnconfigure(col, weight=1, uniform="tab")
            b = ctk.CTkButton(self, text=label, height=36, corner_radius=18, border_width=0,
                              font=self._f.ui(13, True), command=lambda k=key: self._pick(k))
            b.grid(row=0, column=col, sticky="ew", padx=4, pady=5)
            self._btns[key] = b
        self._paint()

    def _pick(self, key: str) -> None:
        self._sel = key
        self._paint()
        self._cmd(key)

    def _paint(self) -> None:
        for key, b in self._btns.items():
            on = key == self._sel
            b.configure(fg_color=TK["blue"] if on else "transparent", hover_color=TK["blue_hi"] if on else TK["hover"],
                        text_color=TK["on_accent"] if on else TK["text"])


class Sheet(ctk.CTkToplevel):
    """iOS-style modal sheet: rounded card, centred icon/title/message, stacked capsule buttons."""

    def __init__(self, app: "PortManagerApp", width: int = 440) -> None:
        super().__init__(app, fg_color=TK["bg"])
        self.app, self.result, self.value = app, None, ""
        self.title(APP_NAME)
        self.resizable(False, False)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.dismiss)
        self.bind("<Escape>", lambda _e: self.dismiss())
        self.card = ctk.CTkFrame(self, fg_color=TK["card"], corner_radius=22, border_width=0)
        self.card.pack(fill="both", expand=True, padx=14, pady=14)
        ctk.CTkFrame(self.card, width=width, height=1, fg_color="transparent").pack()

    def red_border(self) -> None:
        self.card.configure(border_width=2, border_color=TK["red"])

    def icon(self, glyph: str, token: str = "red") -> None:
        ctk.CTkLabel(self.card, text=glyph, width=60, height=60, corner_radius=30, fg_color=TK[token],
                     text_color=TK["on_accent"], font=self.app.fonts.ui(30, True)).pack(pady=(26, 8))

    def title_text(self, text: str, token: str = "text", size: int = 19, pady=(10, 4)) -> None:
        lab = ctk.CTkLabel(self.card, text=text, text_color=TK[token], font=self.app.fonts.ui(size, True),
                           wraplength=390)
        lab.pack(padx=22, pady=pady)
        align_label(lab, "center")

    def message(self, text: str, muted: bool = False, token: str | None = None, justify: str = "center",
                boxed: bool = False, mono: bool = False) -> ctk.CTkLabel:
        F = self.app.fonts
        lab = ctk.CTkLabel(self.card, text=text, text_color=TK[token or ("text2" if muted else "text")],
                           font=F.monof(12) if mono else F.ui(13), wraplength=390)
        if boxed:
            lab.configure(fg_color=TK["card_hi"], corner_radius=12)
            lab.pack(padx=22, pady=(4, 8), fill="x", ipady=8)
        else:
            lab.pack(padx=22, pady=(2, 8))
        align_label(lab, justify)
        return lab

    def button(self, text: str, style: str, command) -> None:
        pill(self.card, text, style, command, self.app.fonts).pack(fill="x", padx=22, pady=(4, 6))

    def choose(self, value) -> None:
        self.result = value
        self._close()

    def dismiss(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

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

    def run(self):
        ctk.CTkFrame(self.card, height=12, fg_color="transparent").pack()
        self.update_idletasks()
        app = self.app
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = app.winfo_rootx() + (app.winfo_width() - w) // 2
        y = app.winfo_rooty() + (app.winfo_height() - h) // 3
        try:
            tk.Wm.geometry(self, f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass
        self.after(60, lambda: app.style_titlebar(self))
        self.after(120, self._grab)
        self.after(260, self._icon)
        app.wait_window(self)
        return self.result


class PopupMenu(tk.Toplevel):
    """Rounded iOS-style context menu (borderless, transparent corners). `items`: None = separator,
    else (label, callback, style) with style normal / danger / disabled."""

    def __init__(self, app: "PortManagerApp", items: list, x: int, y: int) -> None:
        super().__init__(app)
        self.app, self.items, self.born = app, items, time.monotonic()
        self.rtl = app.rtl
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
# The card list (custom canvas: rounded cards, badges, per-card buttons, virtual scrolling, RTL mirroring)
# ──────────────────────────────────────────────────────────────────────────────
class CardList(ctk.CTkFrame):
    def __init__(self, master, app: "PortManagerApp") -> None:
        super().__init__(master, fg_color="transparent")
        self.app, self.f, self.s = app, app.fonts, app.scale
        s = self.s
        self.card_h, self.gap = round(104 * s), round(10 * s)
        self.pitch = self.card_h + self.gap
        self.margin, self.pad, self.top = round(4 * s), round(18 * s), round(2 * s)
        self.rtl = False
        self.empty_text = ""
        self._rows: list[Row] = []
        self._index: dict[str, int] = {}
        self._sel_key: str | None = None
        self._hover_i: int | None = None
        self._offset = 0
        self._hits: list[tuple[float, float, float, float, str, str]] = []
        self._fit_cache: dict[tuple, str] = {}

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg=PAL["bg"], highlightthickness=0, bd=0, takefocus=True)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar = ctk.CTkScrollbar(self, orientation="vertical", width=10, command=self._on_vbar,
                                     fg_color="transparent", button_color=TK["text3"], button_hover_color=TK["text2"])
        self.vbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))

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

    def set_empty_text(self, text: str) -> None:
        self.empty_text = text

    def set_rows(self, rows: list[Row]) -> None:
        self._rows = rows
        self._index = {r.key: i for i, r in enumerate(rows)}
        if self._hover_i is not None and self._hover_i >= len(rows):
            self._hover_i = None
        self._redraw()

    def refresh_theme(self) -> None:
        self.canvas.configure(bg=PAL["bg"])
        self._redraw()

    def redraw(self) -> None:
        self._fit_cache.clear()
        self._redraw()

    def selected_row(self) -> Row | None:
        i = self._index.get(self._sel_key) if self._sel_key else None
        return self._rows[i] if i is not None else None

    def select_key(self, key: str | None) -> None:
        self._sel_key = key
        self._redraw()

    def focus_canvas(self) -> None:
        self.canvas.focus_set()

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
        W, H = c.winfo_width(), c.winfo_height()
        if W < 40 or H < 40:
            return
        n = len(self._rows)
        total = n * self.pitch + self.top
        self._offset = max(0, min(self._offset, max(0, total - H)))
        if n == 0:
            c.create_text(W / 2, H / 2, text=self.empty_text, fill=PAL["text2"], font=self.f.empty,
                          width=max(200, W - 80), justify="center")
            self.vbar.set(0, 1)
            return
        first = max(0, (self._offset - self.top) // self.pitch)
        last = min(n, (self._offset + H - self.top) // self.pitch + 1)
        for i in range(first, last):
            self._draw_card(i, i * self.pitch + self.top - self._offset, W)
        self.vbar.set(self._offset / total, min(1.0, (self._offset + H) / total))

    def _draw_card(self, i: int, y: float, W: int) -> None:
        c, f, s, P, app = self.canvas, self.f, self.s, PAL, self.app
        r = self._rows[i]
        rtl, pad = self.rtl, self.pad
        x1, x2 = self.margin, W - self.margin
        cw = x2 - x1
        sel, hov, tier = r.key == self._sel_key, i == self._hover_i, r.tier

        if tier == TIER_CORE:
            fill, border, bw = P["core_fill"], P["red"], max(2, round(2 * s))
        elif tier == TIER_SYSTEM:
            fill, border, bw = P["sys_fill"], P["red"], max(1, round(1.5 * s))
        else:
            fill, border, bw = P["card"], (P["blue"] if sel else ""), max(2, round(2 * s))
        if sel and tier in (TIER_CORE, TIER_SYSTEM):
            bw += max(1, round(s))
        if hov and not sel:
            fill = mix(fill, P["text"], 0.05)
        round_rect(c, x1, y, x2, y + self.card_h, round(18 * s), fill=fill, outline=border,
                   width=bw if border else 0)

        lead = "e" if rtl else "w"

        def px(local: float) -> float:                  # a point `local` px from the card's leading edge
            return x2 - local if rtl else x1 + local

        def span(a: float, b: float) -> tuple[float, float]:   # a..b measured from the leading edge
            return (x2 - b, x2 - a) if rtl else (x1 + a, x1 + b)

        # — port block ————————————————————————————————————————————————
        c.create_text(px(pad), y + round(40 * s), text=str(r.port), anchor=lead, fill=P["text"], font=f.port)
        dr = round(4 * s)
        dl, dr_ = span(pad, pad + 2 * dr)
        cy = y + round(72 * s)
        c.create_oval(dl, cy - dr, dr_, cy + dr, fill=P["green"], outline="")
        c.create_text(px(pad + 2 * dr + round(6 * s)), cy, text=r.proto, anchor=lead, fill=P["text2"], font=f.proto)

        # — action buttons (trailing edge) ————————————————————————————
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
            self._hits.append((l, mid - bh / 2, rr, mid + bh / 2, kind, r.key))

        # — content block ————————————————————————————————————————————
        cx0 = pad + round(104 * s)
        cx1 = det_a - round(14 * s)
        width = cx1 - cx0
        if width < 60:
            return
        # row 1: name · pin · custom label
        y1 = y + round(28 * s)
        pinned = r.port in app.cfg.pinned
        label = app.cfg.labels.get(r.port, "")
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

        # row 2: badges
        y2 = y + round(58 * s)
        badges: list[tuple[str, str, str, str]] = []
        if tier == TIER_CORE:
            badges.append((T("badge_core"), "#FFFFFF", P["red"], ""))
        elif tier == TIER_SYSTEM:
            badges.append((T("badge_system"), P["red"], P["red_tint"], P["red"]))
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

        # row 3: meta + command line
        y3 = y + round(84 * s)
        meta = f"PID {r.pid}  ·  {r.ram:,.1f} MB  ·  {r.cpu:.1f}% CPU"
        c.create_text(px(cx0), y3, text=meta, anchor=lead, fill=P["text2"], font=f.meta)
        mw = f.meta.measure(meta) + round(14 * s)
        if r.cmd and width - mw > round(60 * s):
            c.create_text(px(cx0 + mw), y3, text=self._fit(r.cmd, width - mw, f.meta), anchor=lead,
                          fill=P["text3"], font=f.meta)

    # ── interaction ───────────────────────────────────────────────────────────
    def _index_at(self, py: float) -> int | None:
        rel = py + self._offset - self.top
        if rel < 0:
            return None
        i, rem = divmod(int(rel), self.pitch)
        return i if (rem < self.card_h and 0 <= i < len(self._rows)) else None

    def _hit(self, x: float, y: float) -> tuple[str, str] | None:
        for l, t, r, b, kind, key in self._hits:
            if l <= x <= r and t <= y <= b:
                return kind, key
        return None

    def _row_by_key(self, key: str) -> Row | None:
        i = self._index.get(key)
        return self._rows[i] if i is not None else None

    def _on_click(self, e) -> None:
        self.canvas.focus_set()
        self.app.close_popup()
        hit = self._hit(e.x, e.y)
        i = self._index_at(e.y)
        if hit:
            self._sel_key = hit[1]
            self._redraw()
            row = self._row_by_key(hit[1])
            if row:
                self.app.card_action(hit[0], row)
            return
        self._sel_key = self._rows[i].key if i is not None else None
        self._redraw()

    def _on_double(self, e) -> None:
        i = self._index_at(e.y)
        if i is not None and not self._hit(e.x, e.y):
            self.app.card_action("details", self._rows[i])

    def _on_right(self, e) -> None:
        i = self._index_at(e.y)
        if i is None:
            return
        self.canvas.focus_set()
        self._sel_key = self._rows[i].key
        self._redraw()
        self.app.open_context_menu(self._rows[i], e.x_root, e.y_root)

    def _open_selected(self) -> None:
        row = self.selected_row()
        if row:
            self.app.card_action("details", row)

    def _set_hover(self, i: int | None) -> None:
        if i != self._hover_i:
            self._hover_i = i
            self._redraw()

    def _on_motion(self, e) -> None:
        self._set_hover(self._index_at(e.y))

    def _move(self, delta: int, absolute: int | None = None) -> None:
        n = len(self._rows)
        if not n:
            return
        cur = self._index.get(self._sel_key, -1) if self._sel_key else -1
        if absolute is not None:
            j = 0 if absolute == 0 else n - 1
        elif cur < 0:
            j = 0 if delta > 0 else n - 1
        else:
            j = cur + delta
        j = max(0, min(n - 1, j))
        self._sel_key = self._rows[j].key
        H = self.canvas.winfo_height()
        top, bottom = j * self.pitch + self.top, j * self.pitch + self.top + self.card_h
        if top < self._offset:
            self._offset = max(0, top - self.top)
        elif bottom > self._offset + H:
            self._offset = bottom - H + self.gap
        self._redraw()

    def _on_wheel(self, e) -> None:
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except (KeyError, tk.TclError):
            return
        if w is self.canvas:
            self._offset -= int(e.delta / 120 * self.pitch * 0.8)
            self._redraw()

    def _on_vbar(self, *args) -> None:
        total, H = len(self._rows) * self.pitch + self.top, self.canvas.winfo_height()
        if args[0] == "moveto":
            self._offset = int(float(args[1]) * total)
        elif args[0] == "scroll":
            step = self.pitch if args[2] == "units" else H
            self._offset += int(args[1]) * step
        self._redraw()


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
class PortManagerApp(ctk.CTk):
    TABS = ("all", "tcp", "udp", "pinned", "protected")

    def __init__(self, cfg: Config, start_minimized: bool, guard: SingleInstance) -> None:
        super().__init__(fg_color=TK["bg"])
        self.cfg, self.guard = cfg, guard
        self.rtl = cfg.lang == "ar"
        self.title(APP_NAME)
        self.geometry("1120x840")
        self.minsize(960, 680)

        self.scale = ui_scale(self)
        self.fonts = Fonts(self, self.scale)
        self._icon_img = make_icon(256)
        self._photo = ImageTk.PhotoImage(self._icon_img.resize((64, 64), Image.Resampling.LANCZOS))
        self.after(350, self._apply_window_icon)          # CTk re-applies its own icon at ~200 ms

        self.scanner = Scanner()
        self.tray: Tray | None = None
        self._rows: list[Row] = []
        self._have_data = False
        self._live = False
        self._scan_busy = False
        self._first_cycle = True
        self._scan_timer: str | None = None
        self._busy = False
        self._quitting = False
        self._fade_job: str | None = None
        self._status_job: str | None = None
        self._last_update = "—"
        self._tab = "all"
        self._popup: PopupMenu | None = None

        self._build_ui()
        self.apply_direction()
        self.apply_language()
        self.apply_theme_assets()

        self.tray = Tray(self)
        self.tray.start()
        self.tray.refresh()

        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.bind("<Map>", self._on_map)
        self.bind("<Unmap>", self._on_unmap)
        self.bind("<ButtonPress>", self._on_any_click, add="+")
        self._bind_shortcuts()

        if start_minimized and self.tray.ok:
            # CTk briefly withdraws/re-shows the window (~5 ms) to repaint its title bar, which would undo an
            # immediate withdraw(). Stay fully transparent until that is over, then hide to the tray silently.
            self.attributes("-alpha", 0.0)
            self.after(80, self._finish_hide)
        else:
            self.after(150, self._ensure_polling)

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
        set_titlebar(win, self.cfg.theme == "dark", PAL["bg"], PAL["text"])

    @staticmethod
    def _glyph_image(kind: str, light: str, dark: str, size: int = 18) -> ctk.CTkImage:
        return ctk.CTkImage(light_image=draw_glyph(kind, light), dark_image=draw_glyph(kind, dark), size=(size, size))

    # ── UI construction ───────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        F = self.fonts
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        # Header: brand + language / theme / startup controls
        self.hdr = ctk.CTkFrame(self, fg_color="transparent")
        self.hdr.grid(row=0, column=0, sticky="ew", padx=22, pady=(16, 8))
        self.hdr.grid_columnconfigure(1, weight=1)
        self.brand = ctk.CTkFrame(self.hdr, fg_color="transparent")
        logo = ctk.CTkImage(light_image=self._icon_img, dark_image=self._icon_img, size=(46, 46))
        self.logo_lbl = ctk.CTkLabel(self.brand, image=logo, text="")
        self.text_col = ctk.CTkFrame(self.brand, fg_color="transparent")
        self.title_lbl = ctk.CTkLabel(self.text_col, text=APP_NAME, font=F.ui(26, True), text_color=TK["text"],
                                      height=32)
        self.sub_lbl = ctk.CTkLabel(self.text_col, text="", font=F.ui(12), text_color=TK["text2"], height=16)
        self.ctrl = ctk.CTkFrame(self.hdr, fg_color="transparent")
        chip = dict(height=38, corner_radius=19, fg_color=TK["card"], hover_color=TK["card_hi"],
                    text_color=TK["text"], font=F.ui(13, True))
        self.lang_btn = ctk.CTkButton(self.ctrl, text="", width=70, command=self.toggle_language, **chip)
        self._img_sun = self._glyph_image("sun", "#3A3A3C", "#FFFFFF")
        self._img_moon = self._glyph_image("moon", "#3A3A3C", "#FFFFFF")
        self._img_search = self._glyph_image("search", "#8E8E93", "#98989D", 20)
        self.theme_btn = ctk.CTkButton(self.ctrl, text="", width=108, compound="left", command=self.toggle_theme,
                                       **chip)
        self.startup_switch = ctk.CTkSwitch(
            self.ctrl, text="", font=F.ui(13), text_color=TK["text"], command=self._on_startup_toggle,
            progress_color=TK["green"], fg_color=TK["sep"], button_color="#FFFFFF", button_hover_color="#F2F2F7")
        self.sync_startup_switch()

        # Summary cards
        self.cards_row = ctk.CTkFrame(self, fg_color="transparent")
        self.cards_row.grid(row=1, column=0, sticky="ew", padx=16)
        for i in range(3):
            self.cards_row.grid_columnconfigure(i, weight=1, uniform="metric")
        self.card_ports = MetricCard(self.cards_row, F, "green")
        self.card_procs = MetricCard(self.cards_row, F, "blue")
        self.card_mem = MetricCard(self.cards_row, F, "orange")
        self.metric_cards = [self.card_ports, self.card_procs, self.card_mem]

        # Search capsule + Clean Dev Ports
        self.srow = ctk.CTkFrame(self, fg_color="transparent")
        self.srow.grid(row=2, column=0, sticky="ew", padx=22, pady=(12, 10))
        self.search_box = ctk.CTkFrame(self.srow, fg_color=TK["field"], corner_radius=23)
        self.search_icon = ctk.CTkLabel(self.search_box, text="", image=self._img_search, width=22)
        self.search = ctk.CTkEntry(self.search_box, height=40, border_width=0, corner_radius=20,
                                   fg_color="transparent", text_color=TK["text"], placeholder_text="",
                                   placeholder_text_color=TK["text3"], font=F.ui(14))
        self.search.bind("<KeyRelease>", lambda _e: self._render())
        self.btn_clean = ctk.CTkButton(self.srow, text="", height=46, width=190, corner_radius=23,
                                       fg_color=TK["blue_tint"], hover_color=TK["blue_tint_hi"],
                                       text_color=TK["blue"], font=F.ui(13, True), command=self._clean_dev_ports)

        # Segmented filter tabs
        self.tabs = PillTabs(self, F, self._on_tab)
        self.tabs.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 10))

        # Port cards
        self.cards_list = CardList(self, self)
        self.cards_list.grid(row=4, column=0, sticky="nsew", padx=22)

        # Status bar
        self.sb = ctk.CTkFrame(self, fg_color="transparent")
        self.sb.grid(row=5, column=0, sticky="ew", padx=24, pady=(8, 12))
        self.sb.grid_columnconfigure(1, weight=1)
        self.live_box = ctk.CTkFrame(self.sb, fg_color="transparent")
        self.live_dot = ctk.CTkLabel(self.live_box, text="●", text_color=TK["green"], font=F.ui(10), width=14, height=18)
        self.live_lbl = ctk.CTkLabel(self.live_box, text="", text_color=TK["text2"], font=F.ui(12), height=18)
        self.msg_lbl = ctk.CTkLabel(self.live_box, text="", text_color=TK["text2"], font=F.ui(12, True), height=18)
        self.credit = ctk.CTkLabel(self.sb, text=CREDIT_TEXT, text_color=TK["text3"], font=F.ui(11), height=18,
                                   cursor="hand2")
        self.credit.bind("<Button-1>", lambda _e: webbrowser.open(REPO_URL))
        self.credit.bind("<Enter>", lambda _e: self.credit.configure(text_color=TK["text2"]))
        self.credit.bind("<Leave>", lambda _e: self.credit.configure(text_color=TK["text3"]))

    def apply_direction(self) -> None:
        """(Re)lay out every row for LTR or RTL."""
        rtl = self.rtl
        a, side = ("e", "right") if rtl else ("w", "left")
        # header
        for w in (self.brand, self.ctrl):
            w.grid_forget()
        self.brand.grid(row=0, column=2 if rtl else 0, sticky=a)
        self.ctrl.grid(row=0, column=0 if rtl else 2, sticky="w" if rtl else "e")
        for w in (self.logo_lbl, self.text_col, self.title_lbl, self.sub_lbl, self.lang_btn, self.theme_btn,
                  self.startup_switch):
            w.pack_forget()
        self.logo_lbl.pack(side=side, padx=(12, 0) if rtl else (0, 12))
        self.text_col.pack(side=side)
        self.title_lbl.pack(anchor=a)
        self.sub_lbl.pack(anchor=a)
        gap = (8, 0) if rtl else (0, 8)
        self.lang_btn.pack(side=side, padx=gap)
        self.theme_btn.pack(side=side, padx=gap)
        self.startup_switch.pack(side=side)
        # summary cards
        for i, card in enumerate(self.metric_cards):
            card.grid_forget()
            card.grid(row=0, column=2 - i if rtl else i, sticky="ew", padx=6)
            card.set_rtl(rtl)
        # search row
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
        # status bar
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
        I18N["lang"] = self.cfg.lang
        self.sub_lbl.configure(text=f"{T('subtitle')}  ·  v{__version__}")
        self.search.configure(placeholder_text=T("search_ph"))
        self.tabs.set_items([(k, T("tab_" + k)) for k in self.TABS], self.rtl, self._tab)
        self.lang_btn.configure(text=T("lang_btn"))
        self.startup_switch.configure(text=T("startup"))
        self.card_ports.set_title(T("c_ports"))
        self.card_procs.set_title(T("c_procs"))
        self.card_mem.set_title(T("c_mem"))
        self.btn_clean.configure(text=T("btn_clean"))
        self._update_cards()
        self._render()
        if self.tray is not None:
            self.tray.refresh()

    def apply_theme_assets(self) -> None:
        dark = self.cfg.theme == "dark"
        self.theme_btn.configure(image=self._img_sun if dark else self._img_moon,
                                 text=T("theme_to_light" if dark else "theme_to_dark"))
        self.cards_list.refresh_theme()
        self.close_popup()
        self.after(60, self.style_titlebar, self)
        self.after(450, self.style_titlebar, self)      # again after CTk's own title-bar redraw

    def toggle_language(self) -> None:
        self.cfg.lang = "en" if self.cfg.lang == "ar" else "ar"
        self.cfg.save()
        I18N["lang"] = self.cfg.lang
        self.rtl = self.cfg.lang == "ar"
        self.close_popup()
        self.apply_direction()
        self.apply_language()
        self.apply_theme_assets()                        # theme button caption is translated too

    def toggle_theme(self) -> None:
        self.cfg.theme = "light" if self.cfg.theme == "dark" else "dark"
        self.cfg.save()
        set_palette(self.cfg.theme == "dark")
        ctk.set_appearance_mode(self.cfg.theme)
        self.apply_theme_assets()

    # ── shortcuts ─────────────────────────────────────────────────────────────
    def _bind_shortcuts(self) -> None:
        for key in ("f", "F"):
            self.bind(f"<Control-{key}>", lambda _e: (self.search.focus_set(), "break")[1])
        for key in ("r", "R"):
            self.bind(f"<Control-{key}>", lambda _e: self.refresh_now())
        for key in ("d", "D"):
            self.bind(f"<Control-Shift-{key}>", lambda _e: self._clean_dev_ports())
        for key in ("q", "Q"):
            self.bind(f"<Control-{key}>", lambda _e: self.quit_app())
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
        self._scan_now()

    def _stop_polling(self) -> None:
        """Cancel the timer and free the process cache: nothing runs while hidden."""
        self._live = False
        if self._scan_timer is not None:
            try:
                self.after_cancel(self._scan_timer)
            except tk.TclError:
                pass
            self._scan_timer = None
        self.scanner.reset()

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
            self._rows = rows
            self._have_data = True
            self._last_update = time.strftime("%H:%M:%S")
            self._update_cards()
            self._render()
        delay = FIRST_CYCLE_MS if self._first_cycle else POLL_MS
        self._first_cycle = False
        self._scan_timer = self.after(delay, self._scan_now)

    # ── data → UI ─────────────────────────────────────────────────────────────
    def _update_cards(self) -> None:
        if not self._have_data:
            for card in self.metric_cards:
                card.set("—", T("scanning"))
            return
        rows = self._rows
        tcp = {r.port for r in rows if r.proto == "TCP"}
        udp = {r.port for r in rows if r.proto == "UDP"}
        mem = sum({r.pid: r.ram for r in rows}.values())
        self.card_ports.set(f"{len(tcp) + len(udp):,}", T("c_ports_cap", tcp=len(tcp), udp=len(udp)))
        self.card_procs.set(f"{len({r.pid for r in rows}):,}", T("c_procs_cap"))
        self.card_mem.set(f"{mem:,.1f}", T("c_mem_cap"))

    def _filtered(self) -> list[Row]:
        q = self.search.get().strip().lower()
        tab, pinned, labels = self._tab, self.cfg.pinned, self.cfg.labels
        out = []
        for r in self._rows:
            if tab == "tcp" and r.proto != "TCP":
                continue
            if tab == "udp" and r.proto != "UDP":
                continue
            if tab == "pinned" and r.port not in pinned:
                continue
            if tab == "protected" and r.tier not in (TIER_CORE, TIER_SYSTEM):
                continue
            if q and q not in r.hay and q not in labels.get(r.port, "").lower():
                continue
            out.append(r)
        out.sort(key=lambda r: (0 if r.port in pinned else 1, r.port, r.proto, r.pid))
        return out

    def _render(self) -> None:
        rows = self._filtered()
        if not self._have_data:
            msg = T("empty_scanning")
        elif self._tab == "pinned" and not self.cfg.pinned:
            msg = T("empty_pinned")
        else:
            msg = T("empty_none")
        self.cards_list.set_empty_text(msg)
        self.cards_list.set_rows(rows)
        if self._have_data:
            self.live_lbl.configure(text=T("live", shown=len(rows), total=len(self._rows), time=self._last_update))
        else:
            self.live_lbl.configure(text=T("scanning"))

    def _on_tab(self, key: str) -> None:
        self._tab = key
        self._render()

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
        """Every stop / restart goes through System Guard: core = blocked, system = strict warning."""
        if self._busy:
            self.set_status(T("msg_busy"), "warn")
            return
        if row.tier == TIER_CORE:
            self.sheet_blocked(row)
            return
        if row.tier == TIER_SELF:
            self.sheet_self()
            return
        if not self.sheet_confirm(row, mode, strict=row.tier == TIER_SYSTEM):
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

    def sheet_confirm(self, row: Row, mode: str, strict: bool) -> bool:
        note = T(f"cf_{mode}_note", pid=row.pid, port=row.port, proto=row.proto)
        sh = Sheet(self)
        if strict:
            sh.red_border()
            sh.icon("!", "red")
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
        sh = Sheet(self, width=500)
        sh.title_text(row.name, "red" if row.tier in (TIER_CORE, TIER_SYSTEM) else "text", size=21, pady=(24, 2))
        sh.message(T("d_tier_" + row.tier), muted=row.tier in (TIER_USER, TIER_SELF),
                   token="red" if row.tier in (TIER_CORE, TIER_SYSTEM) else None)
        box = ctk.CTkFrame(sh.card, fg_color=TK["card_hi"], corner_radius=14)
        box.pack(fill="x", padx=18, pady=(6, 8))
        box.grid_columnconfigure(0, weight=1)
        box.grid_columnconfigure(1, weight=1)
        expo = T("d_expo_local" if row.exposure == "local" else "d_expo_lan", ip=row.bind)
        info = [("d_pid", str(row.pid)), ("d_port", f"{row.port} / {row.proto}"), ("d_expo", expo),
                ("d_ram", f"{row.ram:,.1f} MB"), ("d_cpu", f"{row.cpu:.1f}%"),
                ("d_uptime", fmt_uptime(time.time() - started) if started else T("d_na"))]
        for i, (k, v) in enumerate(info):
            py = (10 if i == 0 else 3, 10 if i == len(info) - 1 else 3)
            ctk.CTkLabel(box, text=T(k), font=F.ui(13), text_color=TK["text2"], anchor=a).grid(
                row=i, column=1 if rtl else 0, sticky=a, padx=14, pady=py)
            ctk.CTkLabel(box, text=v, font=F.monof(13), text_color=TK["text"], anchor="w" if rtl else "e").grid(
                row=i, column=0 if rtl else 1, sticky="w" if rtl else "e", padx=14, pady=py)
        for key, text in (("d_path", exe), ("d_cmd", cmd)):
            ctk.CTkLabel(sh.card, text=T(key), font=F.ui(12, True), text_color=TK["text2"], anchor=a).pack(
                fill="x", padx=24, pady=(6, 2))
            tb = ctk.CTkTextbox(sh.card, height=62, corner_radius=12, border_width=0, fg_color=TK["card_hi"],
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
        entry = ctk.CTkEntry(sh.card, height=44, corner_radius=22, border_width=0, fg_color=TK["card_hi"],
                             text_color=TK["text"], placeholder_text=T("label_ph"),
                             placeholder_text_color=TK["text3"], font=F.ui(14))
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
        sh.after(300, lambda: entry.focus_set())
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
            if r.dev and r.proto == "TCP" and r.tier == TIER_USER:
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
        results = clean_dev_ports()
        if results:
            text, _kind = summarize(results, "tree")
        else:
            text = T("clean_none", ports=", ".join(str(p) for p in sorted(DEV_PORTS)))
        if self.tray is not None:
            self.tray.notify(text)
        self.call_ui(self.refresh_now)

    # ── startup switch ────────────────────────────────────────────────────────
    def _on_startup_toggle(self) -> None:
        if not set_startup(bool(self.startup_switch.get())):
            self.sync_startup_switch()
            self.set_status(T("msg_startup_fail"), "error")
        if self.tray is not None:
            self.tray.refresh()

    def sync_startup_switch(self) -> None:
        if is_startup_enabled():
            self.startup_switch.select()
        else:
            self.startup_switch.deselect()

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

    def hide_to_tray(self) -> None:
        """Window ✕ → fade out, withdraw to the notification area, and stop ALL polling."""
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

    def quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.close_popup()
        self._stop_polling()
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
    I18N["lang"] = cfg.lang
    bootstrap_startup(cfg)
    set_palette(cfg.theme == "dark")
    ctk.set_appearance_mode(cfg.theme)
    ctk.set_default_color_theme("blue")
    app = PortManagerApp(cfg, start_minimized=args.minimized, guard=guard)
    guard.listen(lambda: app.call_ui(app.show_window))
    app.mainloop()
    guard.close()
    return 0


if __name__ == "__main__":
    code = main()
    os._exit(code)      # guarantees no lingering tray thread keeps the process alive after Quit
