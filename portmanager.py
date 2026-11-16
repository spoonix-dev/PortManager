#!/usr/bin/env python3
"""
PortManager — an ultra-lightweight, macOS-inspired Task Manager & Network Port Monitor for Windows.

    Author      : Mustafa Al-Aqrawi (مصطفى العقراوي) — https://github.com/spoonix-dev
    Repository  : https://github.com/spoonix-dev/PortManager
    License     : MIT — Copyright (c) 2026 Mustafa Al-Aqrawi (spoonix-dev)

Design notes
------------
* Single instance  : a Windows named mutex; a second launch wakes the first one via a named event.
* Zero idle CPU    : polling exists only while the window is mapped. Hiding to the tray (or minimizing)
                     cancels the timer, drops the process cache and leaves *no* background loop running.
                     Every scan runs in a short-lived worker thread that exits when the scan is done.
* Startup          : HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run, launched with --minimized.
* No asset files   : window, tray and .exe icons are drawn in memory with Pillow.
"""
from __future__ import annotations

import argparse
import json
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
__version__ = "1.0.0"
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
FIRST_CYCLE_MS = 1000          # the 2nd scan after showing the window yields real CPU % values
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MB = 1024 * 1024

PROTECTED_PIDS = {0, 4, os.getpid()}
if getattr(sys, "frozen", False):               # PyInstaller --onefile: never kill our own bootloader
    PROTECTED_PIDS.add(os.getppid())
PROTECTED_NAMES = frozenset({
    "system", "system idle process", "registry", "memory compression", "smss.exe",
    "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
})

# ──────────────────────────────────────────────────────────────────────────────
# Palette (macOS Sonoma / Sequoia dark)
# ──────────────────────────────────────────────────────────────────────────────
BG = "#161618"
CARD = "#222226"
BORDER = "#323236"
TEXT = "#F5F5F7"
TEXT2 = "#98989F"
TEXT3 = "#636366"
BLUE = "#0A84FF"
BLUE_HOVER = "#3395FF"
RED = "#FF453A"
RED_HOVER = "#E5382E"
RED_TINT = "#3A2022"
GREEN = "#30D158"
ORANGE = "#FF9F0A"
ROW_HOVER = "#2B2B30"
ROW_SEP = "#2A2A2F"
DISABLED_BG = "#2C2C30"


# ══════════════════════════════════════════════════════════════════════════════
# Windows platform helpers: single instance, settings, auto-run
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
        self._event = k.CreateEventW(None, False, False, self.EVENT_NAME)   # auto-reset
        self._closed = False

    def signal_existing(self) -> None:
        if self._event:
            self._k.SetEvent(self._event)

    def listen(self, callback) -> None:
        """Block-wait (0 % CPU) on the show-event in a daemon thread; call `callback` when signalled."""
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
            self._k.SetEvent(self._event)       # unblock the listener thread
        if self._mutex and not self.already_running:
            self._k.ReleaseMutex(self._mutex)
        for h in (self._event, self._mutex):
            if h:
                self._k.CloseHandle(h)


SETTINGS_FILE = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def startup_command() -> str:
    """Command line stored in the Run key (always with --minimized)."""
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


def bootstrap_startup() -> None:
    """Register auto-run on the very first launch; afterwards respect the user's choice."""
    settings = load_settings()
    if not settings.get("startup_initialized"):
        if set_startup(True):
            settings["startup_initialized"] = True
            save_settings(settings)
    elif is_startup_enabled() and get_startup_value() != startup_command():
        set_startup(True)                      # exe was moved/updated: keep the entry valid


# ══════════════════════════════════════════════════════════════════════════════
# In-memory icon (no .ico/.png dependencies)
# ══════════════════════════════════════════════════════════════════════════════
def make_icon(size: int = 256) -> Image.Image:
    """Draw the PortManager icon (blue squircle + three port rows) at any size, supersampled 4×."""
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
        r = int(S * 0.04)
        cx = int(S * 0.255)
        d.ellipse((cx - r, int(S * cy) - r, cx + r, int(S * cy) + r), fill=dot)
        d.rounded_rectangle((int(S * 0.36), int(S * cy - S * 0.036), int(S * end), int(S * cy + S * 0.036)),
                            radius=int(S * 0.036), fill=white)
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
    key: str
    proto: str
    bind: str
    port: int
    pid: int


@dataclass(slots=True)
class Row:
    key: str
    port: int
    proto: str
    pid: int
    name: str
    cpu: float
    ram: float
    bind: str
    cmd: str
    dev: bool
    hay: str


def _safe(fn, default):
    try:
        return fn()
    except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return default


def list_listeners() -> list[Listener]:
    """Every listening TCP socket and bound UDP socket (deduplicated)."""
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        return []
    out: list[Listener] = []
    seen: set[str] = set()
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
        pid = c.pid or 0
        key = f"{proto}|{c.laddr.ip}|{c.laddr.port}|{pid}"
        if key not in seen:
            seen.add(key)
            out.append(Listener(key, proto, c.laddr.ip, c.laddr.port, pid))
    return out


class Scanner:
    """Builds table rows. Keeps psutil.Process objects so cpu_percent() has a baseline between scans."""

    def __init__(self) -> None:
        self._procs: dict[int, psutil.Process] = {}
        self._meta: dict[int, tuple[str, str]] = {}
        self._ncpu = psutil.cpu_count(logical=True) or 1
        self._reset_pending = False

    def reset(self) -> None:
        """Ask for a cache drop (applied at the start of the next scan; safe from any thread)."""
        self._reset_pending = True

    def scan(self) -> list[Row]:
        if self._reset_pending:
            self._procs.clear()
            self._meta.clear()
            self._reset_pending = False
        listeners = list_listeners()
        live = {l.pid for l in listeners}
        for pid in [p for p in self._procs if p not in live]:
            self._procs.pop(pid, None)
            self._meta.pop(pid, None)
        info = {pid: self._inspect(pid) for pid in live}
        rows: list[Row] = []
        for l in listeners:
            data = info.get(l.pid)
            if data is None:
                continue
            name, cpu, ram, cmd = data
            rows.append(Row(
                key=l.key, port=l.port, proto=l.proto, pid=l.pid, name=name, cpu=cpu, ram=ram,
                bind=l.bind, cmd=cmd, dev=l.port in DEV_PORTS,
                hay=f"{l.port} :{l.port} {l.proto} {name} {l.pid} {l.bind} {cmd}".lower(),
            ))
        return rows

    def _inspect(self, pid: int) -> tuple[str, float, float, str] | None:
        if pid == 0:
            return "System Idle Process", 0.0, 0.0, ""
        try:
            proc = self._procs.get(pid)
            if proc is None or not proc.is_running():
                proc = psutil.Process(pid)
                self._procs[pid] = proc
                self._meta.pop(pid, None)
            meta = self._meta.get(pid)
            if meta is None:
                name = _safe(proc.name, f"PID {pid}")
                cmd = _safe(lambda: subprocess.list2cmdline(proc.cmdline()), "") or _safe(proc.exe, "")
                meta = self._meta[pid] = (name, cmd)
            cpu = _safe(proc.cpu_percent, 0.0) / self._ncpu
            ram = _safe(lambda: proc.memory_info().rss, 0) / MB
            return meta[0], cpu, ram, meta[1]
        except psutil.NoSuchProcess:
            return None
        except psutil.AccessDenied:
            return f"PID {pid}", 0.0, 0.0, ""


def is_protected(pid: int, name: str = "") -> bool:
    return pid in PROTECTED_PIDS or (name or "").lower() in PROTECTED_NAMES


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


def end_process(pid: int, force: bool) -> tuple[bool, str]:
    """
    End Task   (force=False): WM_CLOSE to its windows and wait 3 s; windowless processes get TerminateProcess.
    Force Kill (force=True) : kill the process and its whole child tree immediately.
    """
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True, "already exited"
    except psutil.AccessDenied:
        return False, "access denied — run as Administrator"
    name = _safe(proc.name, "")
    if is_protected(pid, name):
        return False, "protected system process"
    try:
        if force:
            victims = [proc]
            for child in _safe(lambda: proc.children(recursive=True), []):
                if not is_protected(child.pid):
                    victims.append(child)
            for p in reversed(victims):
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(victims, timeout=3)
            return (False, "still running") if proc in alive else (True, "killed")
        if _post_wm_close(pid):
            _, alive = psutil.wait_procs([proc], timeout=3)
            return (False, "did not close — try Force Kill") if alive else (True, "closed")
        proc.terminate()
        _, alive = psutil.wait_procs([proc], timeout=3)
        return (False, "did not exit — try Force Kill") if alive else (True, "ended")
    except psutil.NoSuchProcess:
        return True, "already exited"
    except psutil.AccessDenied:
        return False, "access denied — run as Administrator"
    except OSError as exc:
        return False, str(exc)


def clean_dev_ports() -> list[tuple[str, int, bool, str]]:
    """Force-kill every process listening (TCP) on a dev port. Returns (name, pid, ok, message)."""
    targets: dict[int, list[int]] = {}
    for l in list_listeners():
        if l.proto == "TCP" and l.port in DEV_PORTS:
            targets.setdefault(l.pid, []).append(l.port)
    results = []
    for pid in targets:
        try:
            name = psutil.Process(pid).name()
        except psutil.Error:
            name = f"PID {pid}"
        if is_protected(pid, name):
            continue
        ok, msg = end_process(pid, force=True)
        results.append((name, pid, ok, msg))
    return results


def summarize(results: list[tuple[str, int, bool, str]]) -> tuple[str, str]:
    """Turn kill results into (message, kind) where kind is ok / warn / error / info."""
    if not results:
        return "Nothing to do", "info"
    ok = [r for r in results if r[2]]
    bad = [r for r in results if not r[2]]
    if not bad:
        names = sorted({r[0] for r in ok})
        shown = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
        return f"Ended {len(ok)} process{'es' if len(ok) != 1 else ''} · {shown}", "ok"
    first = bad[0]
    if ok:
        return f"Ended {len(ok)}, failed {len(bad)} — {first[0]} (PID {first[1]}): {first[3]}", "warn"
    extra = f" (+{len(bad) - 1} more)" if len(bad) > 1 else ""
    return f"{first[0]} (PID {first[1]}): {first[3]}{extra}", "error"


# ══════════════════════════════════════════════════════════════════════════════
# UI building blocks
# ══════════════════════════════════════════════════════════════════════════════
def ui_scale(widget) -> float:
    try:
        return max(1.0, float(widget.winfo_fpixels("1i")) / 96.0)
    except Exception:
        return 1.0


def left_justify(label: ctk.CTkLabel) -> None:
    try:
        label._label.configure(justify="left")
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

        def px(v: float) -> int:
            return -max(1, round(v * scale))

        self.cell = tkfont.Font(root=root, family=self.ui_family, size=px(13))
        self.head = tkfont.Font(root=root, family=self.ui_family, size=px(12), weight="bold")
        self.badge = tkfont.Font(root=root, family=self.ui_family, size=px(10), weight="bold")
        self.empty = tkfont.Font(root=root, family=self.ui_family, size=px(14))
        self.mono = tkfont.Font(root=root, family=self.mono_family, size=px(12))
        self.mono_bold = tkfont.Font(root=root, family=self.mono_family, size=px(12), weight="bold")
        self.mono_small = tkfont.Font(root=root, family=self.mono_family, size=px(11))
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


class ActionButton(ctk.CTkButton):
    """Rounded macOS-style button that can look 'disabled' properly (CTk keeps the fill otherwise)."""

    def __init__(self, master, text, color, hover, command, fonts: Fonts, solid=True, width=112):
        self._style_on = dict(
            fg_color=color if solid else "transparent", hover_color=hover,
            text_color="#FFFFFF" if solid else color, border_color=color,
        )
        self._style_off = dict(fg_color=DISABLED_BG, hover_color=DISABLED_BG, text_color=TEXT3, border_color=BORDER)
        super().__init__(master, text=text, command=command, width=width, height=34, corner_radius=8,
                         border_width=0 if solid else 1, font=fonts.ui(13, True), **self._style_on)

    def set_enabled(self, enabled: bool) -> None:
        self.configure(state="normal" if enabled else "disabled", **(self._style_on if enabled else self._style_off))


class MetricCard(ctk.CTkFrame):
    def __init__(self, master, title: str, fonts: Fonts, dot: str | None = None) -> None:
        super().__init__(master, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 0))
        if dot:
            ctk.CTkLabel(top, text="●", text_color=dot, font=fonts.ui(10), width=12, height=16).pack(
                side="left", padx=(0, 6))
        ctk.CTkLabel(top, text=title, text_color=TEXT2, font=fonts.ui(11, True), height=16).pack(side="left")
        self.value = ctk.CTkLabel(self, text="—", text_color=TEXT, font=fonts.monof(30, True), anchor="w", height=42)
        self.value.pack(fill="x", padx=16, pady=(2, 0))
        self.caption = ctk.CTkLabel(self, text=" ", text_color=TEXT3, font=fonts.ui(12), anchor="w", height=18)
        self.caption.pack(fill="x", padx=16, pady=(0, 12))

    def set(self, value: str, caption: str) -> None:
        self.value.configure(text=value)
        self.caption.configure(text=caption)


class ConfirmDialog(ctk.CTkToplevel):
    """Dark modal confirmation. `.result` is True only if the confirm button was pressed."""

    def __init__(self, parent, fonts: Fonts, photo, title: str, heading: str, body: str,
                 confirm_text: str, danger: bool = True) -> None:
        super().__init__(parent, fg_color=BG)
        self.result = False
        self._photo = photo
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())

        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        card.pack(fill="both", expand=True, padx=16, pady=16)
        head = ctk.CTkLabel(card, text=heading, text_color=TEXT, font=fonts.ui(16, True), anchor="w",
                            wraplength=440)
        head.pack(fill="x", padx=20, pady=(18, 6))
        left_justify(head)
        if body:
            box = ctk.CTkLabel(card, text=body, text_color=TEXT2, font=fonts.monof(12), anchor="w",
                               wraplength=440, fg_color=BG, corner_radius=8)
            box.pack(fill="x", padx=20, pady=(2, 4), ipadx=10, ipady=8)
            left_justify(box)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=(14, 18))
        ActionButton(row, confirm_text, RED if danger else BLUE, RED_HOVER if danger else BLUE_HOVER,
                     self._confirm, fonts, solid=True, width=130).pack(side="right")
        ActionButton(row, "Cancel", TEXT2, "#34343A", self._cancel, fonts, solid=False, width=96).pack(
            side="right", padx=(0, 10))

        self.after(10, self._center, parent)
        self.after(120, self._grab)
        self.after(260, self._icon)

    def _center(self, parent) -> None:
        try:
            self.update_idletasks()
            w, h = self.winfo_reqwidth(), self.winfo_reqheight()
            x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
            tk.Wm.geometry(self, f"+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass

    def _grab(self) -> None:
        try:
            self.lift()
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _icon(self) -> None:
        try:
            self.iconphoto(False, self._photo)
        except tk.TclError:
            pass

    def _confirm(self) -> None:
        self.result = True
        self.destroy()

    def _cancel(self) -> None:
        self.result = False
        self.destroy()


# ──────────────────────────────────────────────────────────────────────────────
# The port table (custom canvas: real badges, per-column fonts, virtual scrolling)
# ──────────────────────────────────────────────────────────────────────────────
COLUMNS = (   # key, title, anchor, fixed width (px at 100 %) — None = flexible
    ("port", "Port", "w", 136),
    ("proto", "Protocol", "w", 86),
    ("name", "Process Name", "w", None),
    ("pid", "PID", "e", 78),
    ("cpu", "CPU %", "e", 78),
    ("ram", "RAM (MB)", "e", 100),
    ("bind", "Bind Address", "w", 150),
    ("cmd", "Command Line", "w", None),
)
FLEX = {"name": (1.0, 150), "cmd": (2.4, 220)}      # weight, minimum width


class PortTable(ctk.CTkFrame):
    def __init__(self, master, fonts: Fonts, scale: float, on_select, on_sort) -> None:
        super().__init__(master, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        self.f, self.s = fonts, scale
        self.on_select, self.on_sort = on_select, on_sort
        self.rh = round(34 * scale)
        self.pad = round(10 * scale)
        self._rows: list[Row] = []
        self._index: dict[str, int] = {}
        self._sel: set[str] = set()
        self._anchor_key: str | None = None
        self._cursor_key: str | None = None
        self._hover_i: int | None = None
        self._offset = 0
        self._cols: list[tuple[str, str, int, int, str]] = []
        self._fit_cache: dict[tuple, str] = {}
        self._sort_key, self._sort_rev = "port", False
        self.empty_text = "Scanning ports…"

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.head = tk.Canvas(self, height=self.rh, bg=CARD, highlightthickness=0, bd=0, cursor="hand2")
        self.head.grid(row=0, column=0, sticky="ew", padx=(8, 0), pady=(8, 0))
        self.body = tk.Canvas(self, bg=CARD, highlightthickness=0, bd=0, takefocus=True)
        self.body.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))
        self.vbar = ctk.CTkScrollbar(self, orientation="vertical", width=12, command=self._on_vbar,
                                     fg_color=CARD, button_color="#48484C", button_hover_color="#636366")
        self.vbar.grid(row=0, column=1, rowspan=2, sticky="ns", padx=(4, 6), pady=8)

        b = self.body
        b.bind("<Configure>", self._on_resize)
        b.bind("<Button-1>", self._on_click)
        b.bind("<Motion>", self._on_motion)
        b.bind("<Leave>", self._on_leave)
        self.head.bind("<Button-1>", self._on_head_click)
        for seq, fn in (("<Up>", lambda e: self._move(-1, e)), ("<Down>", lambda e: self._move(1, e)),
                        ("<Shift-Up>", lambda e: self._move(-1, e, True)),
                        ("<Shift-Down>", lambda e: self._move(1, e, True)),
                        ("<Prior>", lambda e: self._move(-self._page_rows(), e)),
                        ("<Next>", lambda e: self._move(self._page_rows(), e)),
                        ("<Home>", lambda e: self._move(0, e, absolute=0)),
                        ("<End>", lambda e: self._move(0, e, absolute=-1)),
                        ("<Control-a>", lambda e: self.select_all()), ("<Control-A>", lambda e: self.select_all())):
            b.bind(seq, lambda e, fn=fn: (fn(e), "break")[1])
        self.body.bind_all("<MouseWheel>", self._on_wheel, add="+")   # CTkFrame forbids bind_all; tk.Canvas allows it

    # ── public API ────────────────────────────────────────────────────────────
    def set_rows(self, rows: list[Row]) -> None:
        self._rows = rows
        self._index = {r.key: i for i, r in enumerate(rows)}
        self._sel = {k for k in self._sel if k in self._index}
        if self._hover_i is not None and self._hover_i >= len(rows):
            self._hover_i = None
        self._redraw()
        self.on_select()

    def set_sort(self, key: str, reverse: bool) -> None:
        self._sort_key, self._sort_rev = key, reverse
        self._draw_header()

    def set_empty_text(self, text: str) -> None:
        self.empty_text = text

    def selected_rows(self) -> list[Row]:
        return [self._rows[self._index[k]] for k in self._sel if k in self._index]

    def clear_selection(self) -> None:
        if self._sel:
            self._sel.clear()
            self._redraw()
            self.on_select()

    def select_all(self) -> None:
        self._sel = set(self._index)
        self._redraw()
        self.on_select()

    def focus_body(self) -> None:
        self.body.focus_set()

    # ── layout ────────────────────────────────────────────────────────────────
    def _layout(self, width: int) -> list[tuple[str, str, int, int, str]]:
        s = self.s
        fixed = sum(round(w * s) for _k, _t, _a, w in COLUMNS if w)
        mins = {k: round(m * s) for k, (_w, m) in FLEX.items()}
        flex_total = max(width - fixed, sum(mins.values()))
        weights = sum(w for w, _m in FLEX.values())
        name_w = max(mins["name"], int(flex_total * FLEX["name"][0] / weights))
        widths = {"name": name_w, "cmd": max(mins["cmd"], flex_total - name_w)}
        cols, x = [], 0
        for key, title, anchor, w in COLUMNS:
            cw = round(w * s) if w else widths[key]
            cols.append((key, title, x, x + cw, anchor))
            x += cw
        return cols

    def _on_resize(self, event) -> None:
        self._cols = self._layout(event.width)
        self._fit_cache.clear()
        self._draw_header()
        self._redraw()

    def _page_rows(self) -> int:
        return max(1, self.body.winfo_height() // self.rh - 1)

    # ── drawing ───────────────────────────────────────────────────────────────
    def _fit(self, text: str, width: int, font: tkfont.Font) -> str:
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
        if len(self._fit_cache) > 6000:
            self._fit_cache.clear()
        self._fit_cache[key] = out
        return out

    def _draw_header(self) -> None:
        c = self.head
        c.delete("all")
        W = c.winfo_width()
        for key, title, x0, x1, anchor in self._cols:
            active = key == self._sort_key
            label = title + ((" ▼" if self._sort_rev else " ▲") if active else "")
            color = TEXT if active else TEXT2
            if anchor == "e":
                c.create_text(x1 - self.pad, self.rh / 2, text=label, anchor="e", fill=color, font=self.f.head)
            else:
                c.create_text(x0 + self.pad, self.rh / 2, text=label, anchor="w", fill=color, font=self.f.head)
        c.create_line(0, self.rh - 1, W, self.rh - 1, fill=BORDER)

    def _redraw(self) -> None:
        c = self.body
        c.delete("all")
        W, H = c.winfo_width(), c.winfo_height()
        if not self._cols or W < 20 or H < 20:
            return
        n, rh = len(self._rows), self.rh
        total = n * rh
        self._offset = max(0, min(self._offset, max(0, total - H)))
        if n == 0:
            c.create_text(W / 2, H / 2, text=self.empty_text, fill=TEXT3, font=self.f.empty)
            self.vbar.set(0, 1)
            return
        first = self._offset // rh
        last = min(n, (self._offset + H) // rh + 1)
        for i in range(first, last):
            self._draw_row(i, i * rh - self._offset, W)
        self.vbar.set(self._offset / total, min(1.0, (self._offset + H) / total))

    def _draw_row(self, i: int, y: int, W: int) -> None:
        c, f, s, pad, rh = self.body, self.f, self.s, self.pad, self.rh
        r = self._rows[i]
        sel = r.key in self._sel
        if sel:
            round_rect(c, 2, y + 1, W - 2, y + rh - 1, round(7 * s), fill=BLUE, outline="")
        elif i == self._hover_i:
            round_rect(c, 2, y + 1, W - 2, y + rh - 1, round(7 * s), fill=ROW_HOVER, outline="")
        else:
            c.create_line(pad, y + rh - 1, W - pad, y + rh - 1, fill=ROW_SEP)
        mid = y + rh / 2
        fg = "#FFFFFF" if sel else TEXT
        fg2 = "#D2E6FF" if sel else TEXT2

        for key, _title, x0, x1, anchor in self._cols:
            width = x1 - x0 - 2 * pad
            if key == "port":
                rad = round(4 * s)
                cx = x0 + pad + rad
                c.create_oval(cx - rad, mid - rad, cx + rad, mid + rad, fill=GREEN, outline="")
                tx = cx + rad + round(8 * s)
                txt = str(r.port)
                c.create_text(tx, mid, text=txt, anchor="w", fill=fg, font=f.mono_bold)
                if r.dev:
                    bx = tx + f.mono_bold.measure(txt) + round(8 * s)
                    bw, bh = f.badge.measure("DEV") + round(12 * s), round(16 * s)
                    if bx + bw <= x1:
                        round_rect(c, bx, mid - bh / 2, bx + bw, mid + bh / 2, bh / 2,
                                   fill="#FFFFFF" if sel else "#12315C", outline="")
                        c.create_text(bx + bw / 2, mid, text="DEV", font=f.badge, fill=BLUE if sel else "#6CB4FF")
                continue
            if key == "proto":
                text, font, color = r.proto, f.cell, fg2
            elif key == "name":
                text, font, color = r.name, f.cell, fg
            elif key == "pid":
                text, font, color = str(r.pid), f.mono, fg
            elif key == "cpu":
                text, font, color = f"{r.cpu:.1f}", f.mono, (ORANGE if r.cpu >= 50 and not sel else fg)
            elif key == "ram":
                text, font, color = f"{r.ram:,.1f}", f.mono, fg
            elif key == "bind":
                text, font, color = r.bind, f.mono, fg2
            else:
                text, font, color = r.cmd or "—", f.mono_small, fg2
            text = self._fit(text, width, font)
            if anchor == "e":
                c.create_text(x1 - pad, mid, text=text, anchor="e", fill=color, font=font)
            else:
                c.create_text(x0 + pad, mid, text=text, anchor="w", fill=color, font=font)

    # ── interaction ───────────────────────────────────────────────────────────
    def _index_at(self, y: int) -> int | None:
        i = (y + self._offset) // self.rh
        return i if 0 <= i < len(self._rows) else None

    def _on_click(self, e) -> None:
        self.body.focus_set()
        i = self._index_at(e.y)
        ctrl, shift = bool(e.state & 0x4), bool(e.state & 0x1)
        if i is None:
            if not (ctrl or shift):
                self.clear_selection()
            return
        key = self._rows[i].key
        if shift and self._anchor_key in self._index:
            a = self._index[self._anchor_key]
            lo, hi = min(a, i), max(a, i)
            span = {self._rows[k].key for k in range(lo, hi + 1)}
            self._sel = (self._sel | span) if ctrl else span
        elif ctrl:
            self._sel ^= {key}
            self._anchor_key = key
        else:
            self._sel = {key}
            self._anchor_key = key
        self._cursor_key = key
        self._redraw()
        self.on_select()

    def _move(self, delta: int, _e=None, extend: bool = False, absolute: int | None = None) -> None:
        n = len(self._rows)
        if not n:
            return
        cur = self._index.get(self._cursor_key, -1) if self._cursor_key else -1
        if absolute is not None:
            j = 0 if absolute == 0 else n - 1
        elif cur < 0:
            j = 0 if delta > 0 else n - 1
        else:
            j = cur + delta
        j = max(0, min(n - 1, j))
        key = self._rows[j].key
        if extend and self._anchor_key in self._index:
            a = self._index[self._anchor_key]
            self._sel = {self._rows[k].key for k in range(min(a, j), max(a, j) + 1)}
        else:
            self._sel, self._anchor_key = {key}, key
        self._cursor_key = key
        H = self.body.winfo_height()
        top, bottom = j * self.rh, (j + 1) * self.rh
        if top < self._offset:
            self._offset = top
        elif bottom > self._offset + H:
            self._offset = bottom - H
        self._redraw()
        self.on_select()

    def _on_motion(self, e) -> None:
        i = self._index_at(e.y)
        if i != self._hover_i:
            self._hover_i = i
            self._redraw()

    def _on_leave(self, _e) -> None:
        if self._hover_i is not None:
            self._hover_i = None
            self._redraw()

    def _on_head_click(self, e) -> None:
        for key, _t, x0, x1, _a in self._cols:
            if x0 <= e.x < x1:
                self.on_sort(key)
                return

    def _on_wheel(self, e) -> None:
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except (KeyError, tk.TclError):
            return
        if w is self.body or w is self.head:
            self._offset -= int(e.delta / 120 * self.rh * 2)
            self._redraw()

    def _on_vbar(self, *args) -> None:
        total, H = len(self._rows) * self.rh, self.body.winfo_height()
        if args[0] == "moveto":
            self._offset = int(float(args[1]) * total)
        elif args[0] == "scroll":
            step = self.rh if args[2] == "units" else H
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
                pystray.MenuItem("Show PortManager", self._show, default=True),
                pystray.MenuItem("Clean Dev Ports", self._clean),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Start with Windows", self._toggle_startup,
                                 checked=lambda _item: is_startup_enabled()),
                pystray.MenuItem("GitHub — @spoonix-dev/PortManager", self._open_repo),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit PortManager", self._quit),
            )
            self.icon = pystray.Icon(APP_NAME, make_icon(256), f"{APP_NAME} — Port & Task Monitor", menu)
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
SORT_KEYS = {
    "port": lambda r: (r.port, r.proto),
    "proto": lambda r: (r.proto, r.port),
    "name": lambda r: (r.name.lower(), r.port),
    "pid": lambda r: (r.pid, r.port),
    "cpu": lambda r: (r.cpu, r.port),
    "ram": lambda r: (r.ram, r.port),
    "bind": lambda r: (r.bind, r.port),
    "cmd": lambda r: (r.cmd.lower(), r.port),
}
STATUS_COLORS = {"ok": GREEN, "warn": ORANGE, "error": RED, "info": TEXT2}


class PortManagerApp(ctk.CTk):
    def __init__(self, start_minimized: bool, guard: SingleInstance) -> None:
        super().__init__(fg_color=BG)
        self.guard = guard
        self.title(APP_NAME)
        self.geometry("1240x780")
        self.minsize(1060, 640)

        self.scale = ui_scale(self)
        self.fonts = Fonts(self, self.scale)
        self._icon_img = make_icon(256)
        self._photo = ImageTk.PhotoImage(self._icon_img.resize((64, 64), Image.Resampling.LANCZOS))
        self.after(350, self._apply_window_icon)          # CTk re-applies its own icon at ~200 ms

        self.scanner = Scanner()
        self._rows: list[Row] = []
        self._have_data = False
        self._polling = False
        self._scanning = False
        self._first_cycle = True
        self._after_id: str | None = None
        self._busy = False
        self._quitting = False
        self._fade_job: str | None = None
        self._status_job: str | None = None
        self._last_update = "—"
        self._filter = "All"
        self._sort_key, self._sort_rev = "port", False

        self._build_ui()
        self.table.set_sort(self._sort_key, self._sort_rev)
        self._sync_buttons()

        self.tray = Tray(self)
        self.tray.start()

        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.bind("<Map>", self._on_map)
        self.bind("<Unmap>", self._on_unmap)
        self._bind_shortcuts()

        if start_minimized and self.tray.ok:
            # CTk briefly withdraws/re-shows the window (~5 ms) to repaint its dark title bar, which would undo an
            # immediate withdraw(). Stay fully transparent until that is over, then hide to the tray silently.
            self.attributes("-alpha", 0.0)
            self.after(80, self._finish_hide)
        else:
            self.after(150, self._ensure_polling)

    # ── thread bridge ─────────────────────────────────────────────────────────
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

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        F = self.fonts
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 12))
        hdr.grid_columnconfigure(1, weight=1)
        logo = ctk.CTkImage(light_image=self._icon_img, dark_image=self._icon_img, size=(42, 42))
        ctk.CTkLabel(hdr, image=logo, text="").grid(row=0, column=0, rowspan=2, padx=(0, 12))
        ctk.CTkLabel(hdr, text="PortManager", font=F.ui(22, True), text_color=TEXT, anchor="w", height=28).grid(
            row=0, column=1, sticky="w")
        ctk.CTkLabel(hdr, text="Task Manager & Network Port Monitor", font=F.ui(12), text_color=TEXT2,
                     anchor="w", height=16).grid(row=1, column=1, sticky="w")
        self.startup_switch = ctk.CTkSwitch(
            hdr, text="Launch at Windows startup", font=F.ui(13), text_color=TEXT, command=self._on_startup_toggle,
            progress_color=GREEN, fg_color="#39393D", button_color="#FFFFFF", button_hover_color="#E5E5EA")
        self.startup_switch.grid(row=0, column=2, rowspan=2, sticky="e")
        self.sync_startup_switch()

        # Summary cards
        cards = ctk.CTkFrame(self, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="ew", padx=22)
        for i in range(3):
            cards.grid_columnconfigure(i, weight=1, uniform="cards")
        self.card_ports = MetricCard(cards, "ACTIVE PORTS", F, dot=GREEN)
        self.card_procs = MetricCard(cards, "TRACKED PROCESSES", F)
        self.card_mem = MetricCard(cards, "TOTAL MEMORY (MB)", F)
        self.card_ports.grid(row=0, column=0, sticky="ew", padx=(0, 7))
        self.card_procs.grid(row=0, column=1, sticky="ew", padx=7)
        self.card_mem.grid(row=0, column=2, sticky="ew", padx=(7, 0))

        # Toolbar
        bar = ctk.CTkFrame(self, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        bar.grid(row=2, column=0, sticky="ew", padx=22, pady=12)
        bar.grid_columnconfigure(0, weight=1)
        self.search = ctk.CTkEntry(
            bar, height=34, corner_radius=8, border_width=1, border_color=BORDER, fg_color=BG, text_color=TEXT,
            placeholder_text="Search port, process, PID, command…  (Ctrl+F)", placeholder_text_color=TEXT3,
            font=F.ui(13))
        self.search.grid(row=0, column=0, sticky="ew", padx=(12, 10), pady=12)
        self.search.bind("<KeyRelease>", lambda _e: self._render())
        self.pills = ctk.CTkSegmentedButton(
            bar, values=["All", "Dev Ports", "TCP", "UDP"], command=self._on_filter, height=34, corner_radius=8,
            fg_color=BG, selected_color=BLUE, selected_hover_color=BLUE_HOVER, unselected_color=BG,
            unselected_hover_color="#2A2A2F", text_color=TEXT, font=F.ui(13, True))
        self.pills.set("All")
        self.pills.grid(row=0, column=1, padx=(0, 16), pady=12)
        self.btn_end = ActionButton(bar, "End Task", RED, RED_TINT, lambda: self._act(False), F, solid=False, width=104)
        self.btn_kill = ActionButton(bar, "Force Kill", RED, RED_HOVER, lambda: self._act(True), F, solid=True, width=112)
        self.btn_clean = ActionButton(bar, "Clean Dev Ports", BLUE, BLUE_HOVER, self._clean_dev_ports, F,
                                      solid=True, width=148)
        self.btn_end.grid(row=0, column=2, padx=(0, 8), pady=12)
        self.btn_kill.grid(row=0, column=3, padx=(0, 8), pady=12)
        self.btn_clean.grid(row=0, column=4, padx=(0, 12), pady=12)

        # Table
        self.table = PortTable(self, F, self.scale, self._on_select, self._on_sort)
        self.table.grid(row=3, column=0, sticky="nsew", padx=22)

        # Detail strip
        det = ctk.CTkFrame(self, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        det.grid(row=4, column=0, sticky="ew", padx=22, pady=(12, 0))
        self.detail_head = ctk.CTkLabel(det, text="", font=F.ui(12, True), text_color=TEXT, anchor="w", height=18)
        self.detail_head.pack(fill="x", padx=16, pady=(10, 0))
        self.detail_body = ctk.CTkLabel(det, text="", font=F.monof(11), text_color=TEXT2, anchor="nw",
                                        wraplength=980, height=34)
        self.detail_body.pack(fill="x", padx=16, pady=(2, 10))
        left_justify(self.detail_body)

        # Status bar
        sb = ctk.CTkFrame(self, fg_color="transparent")
        sb.grid(row=5, column=0, sticky="ew", padx=24, pady=(8, 12))
        sb.grid_columnconfigure(2, weight=1)
        self.live_dot = ctk.CTkLabel(sb, text="●", text_color=GREEN, font=F.ui(10), width=14, height=18)
        self.live_dot.grid(row=0, column=0)
        self.live_lbl = ctk.CTkLabel(sb, text="Scanning…", text_color=TEXT2, font=F.ui(12), height=18, anchor="w")
        self.live_lbl.grid(row=0, column=1, sticky="w")
        self.msg_lbl = ctk.CTkLabel(sb, text="", text_color=TEXT2, font=F.ui(12, True), height=18, anchor="w")
        self.msg_lbl.grid(row=0, column=2, sticky="w", padx=(16, 0))
        credit = ctk.CTkLabel(sb, text=CREDIT_TEXT, text_color=TEXT3, font=F.ui(11), height=18, cursor="hand2")
        credit.grid(row=0, column=3, sticky="e")
        credit.bind("<Button-1>", lambda _e: webbrowser.open(REPO_URL))
        credit.bind("<Enter>", lambda _e: credit.configure(text_color=TEXT2))
        credit.bind("<Leave>", lambda _e: credit.configure(text_color=TEXT3))

        self._on_select()

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
        self.bind("<Delete>", lambda _e: None if self._typing() else self._act(False))
        self.bind("<Shift-Delete>", lambda _e: None if self._typing() else self._act(True))

    def _typing(self) -> bool:
        try:
            return isinstance(self.focus_get(), tk.Entry)
        except (KeyError, tk.TclError):
            return False

    def _on_escape(self, _e=None) -> None:
        if self.search.get():
            self.search.delete(0, "end")
            self._render()
        else:
            self.table.clear_selection()
        self.table.focus_body()

    # ── visibility / polling lifecycle ────────────────────────────────────────
    def _on_map(self, e) -> None:
        if e.widget is self or str(e.widget) == ".":
            self._start_polling()

    def _on_unmap(self, e) -> None:
        if e.widget is self or str(e.widget) == ".":
            self._stop_polling()

    def _ensure_polling(self) -> None:
        try:
            if self.state() == "normal":
                self._start_polling()
        except tk.TclError:
            pass

    def _start_polling(self) -> None:
        if self._polling or self._quitting:
            return
        self._polling = True
        self._first_cycle = True
        self.live_dot.configure(text_color=GREEN)
        self._scan_now()

    def _stop_polling(self) -> None:
        """Cancel the timer and free the process cache: nothing runs while hidden."""
        self._polling = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        self.scanner.reset()

    def refresh_now(self) -> None:
        if self._polling and not self._scanning:
            if self._after_id is not None:
                try:
                    self.after_cancel(self._after_id)
                except tk.TclError:
                    pass
                self._after_id = None
            self._scan_now()

    def _scan_now(self) -> None:
        self._after_id = None
        if not self._polling or self._scanning:
            return
        self._scanning = True
        threading.Thread(target=self._scan_worker, name="pm-scan", daemon=True).start()

    def _scan_worker(self) -> None:
        try:
            rows = self.scanner.scan()
        except Exception:
            rows = None
        self.call_ui(self._scan_done, rows)

    def _scan_done(self, rows: list[Row] | None) -> None:
        self._scanning = False
        if not self._polling or self._quitting:
            return                                  # window was hidden meanwhile: drop the result
        if rows is not None:
            self._rows = rows
            self._have_data = True
            self._last_update = time.strftime("%H:%M:%S")
            self._update_cards()
            self._render()
        delay = FIRST_CYCLE_MS if self._first_cycle else POLL_MS
        self._first_cycle = False
        self._after_id = self.after(delay, self._scan_now)

    # ── data → UI ─────────────────────────────────────────────────────────────
    def _update_cards(self) -> None:
        rows = self._rows
        tcp = {r.port for r in rows if r.proto == "TCP"}
        udp = {r.port for r in rows if r.proto == "UDP"}
        pids = {r.pid for r in rows}
        mem = sum({r.pid: r.ram for r in rows}.values())
        self.card_ports.set(f"{len(tcp) + len(udp):,}", f"{len(tcp)} TCP listening · {len(udp)} UDP bound")
        self.card_procs.set(f"{len(pids):,}", "processes owning ports")
        self.card_mem.set(f"{mem:,.1f}", "resident memory (RSS)")

    def _filtered(self) -> list[Row]:
        q = self.search.get().strip().lower()
        mode = self._filter
        out = []
        for r in self._rows:
            if mode == "Dev Ports" and not r.dev:
                continue
            if mode in ("TCP", "UDP") and r.proto != mode:
                continue
            if q and q not in r.hay:
                continue
            out.append(r)
        out.sort(key=SORT_KEYS[self._sort_key], reverse=self._sort_rev)
        return out

    def _render(self) -> None:
        rows = self._filtered()
        self.table.set_empty_text("No ports match your search or filter" if self._have_data else "Scanning ports…")
        self.table.set_rows(rows)
        self.live_lbl.configure(text=f"Live · {len(rows)} of {len(self._rows)} listeners · updated {self._last_update}")

    def _on_filter(self, value: str) -> None:
        self._filter = value
        self._render()

    def _on_sort(self, key: str) -> None:
        if key == self._sort_key:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_key, self._sort_rev = key, key in ("cpu", "ram")
        self.table.set_sort(self._sort_key, self._sort_rev)
        self._render()

    def _on_select(self) -> None:
        rows = self.table.selected_rows()
        if not rows:
            head = "Select a process to inspect it"
            body = "Del = End Task  ·  Shift+Del = Force Kill  ·  Ctrl+Shift+D = Clean Dev Ports  ·  F5 = Refresh"
        elif len(rows) == 1:
            r = rows[0]
            head = f"{r.name}  ·  PID {r.pid}  ·  {r.proto} {r.bind}:{r.port}"
            body = (r.cmd[:280] + ("…" if len(r.cmd) > 280 else "")) if r.cmd else \
                "Command line unavailable (access denied — run as Administrator to see it)"
        else:
            names = sorted({r.name for r in rows})
            head = f"{len(rows)} entries selected"
            body = ", ".join(names[:8]) + ("…" if len(names) > 8 else "")
        self.detail_head.configure(text=head)
        self.detail_body.configure(text=body)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        has_sel = bool(self.table.selected_rows()) if hasattr(self, "table") else False
        self.btn_end.set_enabled(has_sel and not self._busy)
        self.btn_kill.set_enabled(has_sel and not self._busy)
        self.btn_clean.set_enabled(not self._busy)

    def set_status(self, text: str, kind: str = "info", seconds: float = 7.0) -> None:
        if self._status_job is not None:
            try:
                self.after_cancel(self._status_job)
            except tk.TclError:
                pass
        self.msg_lbl.configure(text=text, text_color=STATUS_COLORS.get(kind, TEXT2))
        self._status_job = self.after(int(seconds * 1000), lambda: self.msg_lbl.configure(text=""))

    # ── actions ───────────────────────────────────────────────────────────────
    def _act(self, force: bool) -> None:
        if self._busy:
            return
        rows = self.table.selected_rows()
        if not rows:
            return
        unique: dict[int, Row] = {}
        for r in rows:
            unique.setdefault(r.pid, r)
        allowed = [r for r in unique.values() if not is_protected(r.pid, r.name)]
        if not allowed:
            self.set_status("Protected system process — PortManager will not end it.", "warn")
            return
        if force:
            lines = "\n".join(f"{r.name}  (PID {r.pid})" for r in allowed[:10])
            if len(allowed) > 10:
                lines += f"\n… and {len(allowed) - 10} more"
            dlg = ConfirmDialog(self, self.fonts, self._photo, "Force Kill",
                                f"Force kill {len(allowed)} process{'es' if len(allowed) != 1 else ''}?",
                                lines + "\n\nThe process and its child processes stop immediately. "
                                        "Unsaved data is lost.", "Force Kill")
            self.wait_window(dlg)
            if not dlg.result:
                return
        self._run_kill([(r.pid, r.name) for r in allowed], force)

    def _clean_dev_ports(self) -> None:
        if self._busy:
            return
        by_pid: dict[int, tuple[str, set[int]]] = {}
        for r in self._rows:
            if r.dev and r.proto == "TCP" and not is_protected(r.pid, r.name):
                by_pid.setdefault(r.pid, (r.name, set()))[1].add(r.port)
        if not by_pid:
            self.set_status("No dev servers found on " + ", ".join(str(p) for p in sorted(DEV_PORTS)), "info")
            return
        items = list(by_pid.items())
        lines = "\n".join(f"{name}  (PID {pid})  ·  " + ", ".join(f":{p}" for p in sorted(ports))
                          for pid, (name, ports) in items[:10])
        if len(items) > 10:
            lines += f"\n… and {len(items) - 10} more"
        dlg = ConfirmDialog(self, self.fonts, self._photo, "Clean Dev Ports",
                            f"Stop {len(items)} dev server{'s' if len(items) != 1 else ''}?", lines,
                            "Clean Dev Ports", danger=False)
        self.wait_window(dlg)
        if dlg.result:
            self._run_kill([(pid, name) for pid, (name, _p) in items], True)

    def _run_kill(self, targets: list[tuple[int, str]], force: bool) -> None:
        self._busy = True
        self._sync_buttons()
        self.set_status("Working…", "info", 30)
        threading.Thread(target=self._kill_worker, args=(targets, force), name="pm-kill", daemon=True).start()

    def _kill_worker(self, targets: list[tuple[int, str]], force: bool) -> None:
        results = []
        for pid, name in targets:
            ok, msg = end_process(pid, force)
            results.append((name, pid, ok, msg))
        self.call_ui(self._kill_done, results)

    def _kill_done(self, results) -> None:
        self._busy = False
        text, kind = summarize(results)
        self.set_status(text, kind)
        self._sync_buttons()
        if self._polling:
            self.after(400, self.refresh_now)

    def tray_clean_dev_ports(self) -> None:
        """Tray one-click cleanup. Runs in a worker thread; works even while the window is hidden."""
        results = clean_dev_ports()
        if results:
            text, _kind = summarize(results)
        else:
            text = "No dev servers found on " + ", ".join(str(p) for p in sorted(DEV_PORTS))
        self.tray.notify(text)
        self.call_ui(self.refresh_now)

    # ── startup switch ────────────────────────────────────────────────────────
    def _on_startup_toggle(self) -> None:
        want = bool(self.startup_switch.get())
        if not set_startup(want):
            self.sync_startup_switch()
            self.set_status("Could not update the Windows startup entry.", "error")
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
        if not self.tray.ok:
            self.iconify()                               # no tray available: never lose the window
            return
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
        self._stop_polling()
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

    bootstrap_startup()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = PortManagerApp(start_minimized=args.minimized, guard=guard)
    guard.listen(lambda: app.call_ui(app.show_window))
    app.mainloop()
    guard.close()
    return 0


if __name__ == "__main__":
    code = main()
    os._exit(code)      # guarantees no lingering tray thread keeps the process alive after Quit
