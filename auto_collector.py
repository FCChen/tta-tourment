#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTA Automated Replay & Statistics Collector (`auto_collector.py`)
=================================================================
Automates:
1. Fetching unparsed GameIDs (10-letter spectate codes, e.g. `LATADELUMA`) from Google Apps Script.
2. Locating the `ThroughTheAges_steam.exe` (`SDL_app`) window on 64-bit Windows and reading exact
   UI widget pixel coordinates (`pasteBtn`, `confirmBtn`, `spec_win_load`) directly from process memory.
3. Extracting `TTA::GameStats` (`vtable RVA = 0x00a02de0`) and `TTA::PlayerGraphStats`
   (`vtable RVA = 0x00a0aebc`) directly from process memory + decrypting `.info` / `.events`.
4. Automatically determining 1st-4th place rankings (`r1..r4`) and all 27 Fun Stats.
5. Uploading the parsed game statistics & rankings back to Google Sheets via `doPost`.
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import subprocess
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from tta_parser import (
    MEM_COMMIT,
    MEMORY_BASIC_INFORMATION32,
    MEMORY_BASIC_INFORMATION64,
    PAGE_READWRITE,
    TTAMemoryReader,
    decode_game_id,
    parse_events_fallback,
    parse_events_file,
    parse_gamestats_lines,
    parse_info_file,
)

DEFAULT_API_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbwCwlkjBuVV4ZXpsb3oK6S2-YnRkSveOZEen0Ng3dBQp28dIsZNMDvJNlMwB0hoAQeFkQ/exec"
)

# Must match FULL_VIEW_KEY in Code.gs (needed for ?action=ranks)
FULL_VIEW_KEY = "WuXPCletRB7V"

TTA_EXE_PATH = r"E:\steam\steamapps\common\Through the Ages\ThroughTheAges_steam.exe"
TTA_SAVES_DIR = r"E:\steam\steamapps\common\Through the Ages\data_ram\profiles\GGLab\saves"
PARSED_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parsed_games")

# Vtable RVAs for UI Dialogs
RVA_VTABLE_SPECTATE_INPUT_WIN = 0x00A10950  # SpectateInputWindow (sessionIDInput, pasteBtn, confirmBtn)
RVA_VTABLE_SPECTATE_DETAIL_WIN = 0x00A10AE8  # SpectateWindow (spec_win_load, spec_win_close)
RVA_VTABLE_MULTIPLAYER_LOBBY = 0x00A06D6C  # MultiplayerLobbyV2 (eye/spectate button at +0xd4)
RVA_VTABLE_SCORING_DIALOG = 0x009FB518  # TTA::ScoringDialog
RVA_VTABLE_END_OF_GAME_DIALOG = 0x009FA334  # TTA::EndOfGameDialog ("最终事件 / 结算 x/6", Continue button at +0x444)
RVA_VTABLE_END_OF_AGE_DIALOG = 0x009F9E98  # TTA::EndOfAgeDialog
RVA_VTABLE_MAIN_MENU = 0x00A05050  # TTA::MenuScene1 (main menu, "在线游戏 / Play Online" button at +0xb0)
RVA_VTABLE_UI_BUTTON = 0x009ED660  # Button widget class (same class as Load / Close buttons)

# Debug: stop right before clicking "Load Game" (set by --stop-before-load)
STOP_BEFORE_LOAD = False


# ============================================================================
# 64-bit Safe Win32 API Setup & Window Helpers
# ============================================================================

user32 = ctypes.windll.user32

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = wt.HWND
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.restype = wt.BOOL
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.ClientToScreen.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wt.BOOL


def set_clipboard_text(text: str) -> None:
    """Copy ASCII/UTF-8 text (e.g., 10-letter GameID) to Windows clipboard."""
    subprocess.run(["clip"], input=text.strip().encode("utf-8"), check=True)


def find_tta_hwnd(target_pid: Optional[int] = None) -> Optional[int]:
    """
    Find the main window handle (HWND) of `ThroughTheAges_steam.exe` on 64-bit Windows.
    Checks:
    1. `FindWindowW("SDL_app", None)` & `FindWindowW(None, "Through the Ages")`
    2. `EnumWindows` matching Process ID (`target_pid`), class `"SDL_app"`, or title `"Through the Ages"`.
    """
    if target_pid is None:
        r = TTAMemoryReader()
        target_pid = r._find_pid()

    # 1. Direct FindWindowW by SDL2 window class ("SDL_app") or title
    for cls_name, win_title in [("SDL_app", None), (None, "Through the Ages")]:
        hwnd = user32.FindWindowW(cls_name, win_title)
        if hwnd:
            if target_pid:
                pid = wt.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == target_pid:
                    return int(hwnd)
            else:
                return int(hwnd)

    # 2. Fallback: EnumWindows matching PID, class name, or title
    found_hwnd = None

    def _enum_cb(hwnd, _lparam):
        nonlocal found_hwnd
        if not hwnd or not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        cls_buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, cls_buf, 128)
        cls_str = cls_buf.value.lower()

        rect = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top

        if target_pid and pid.value == target_pid and w >= 200 and h >= 200:
            found_hwnd = int(hwnd)
            return False

        if cls_str == "sdl_app" and w >= 200 and h >= 200:
            found_hwnd = int(hwnd)
            return False

        tlen = user32.GetWindowTextLengthW(hwnd)
        if tlen > 0:
            tbuf = ctypes.create_unicode_buffer(tlen + 1)
            user32.GetWindowTextW(hwnd, tbuf, tlen + 1)
            if "through the ages" in tbuf.value.lower():
                found_hwnd = int(hwnd)
                return False
        return True

    cb_func = WNDENUMPROC(_enum_cb)
    user32.EnumWindows(cb_func, 0)
    return found_hwnd


def click_client_pixel(hwnd: int, client_x: int, client_y: int) -> None:
    """Click at exact client pixel coordinates `(client_x, client_y)` inside `hwnd`."""
    pt = wt.POINT(int(client_x), int(client_y))
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.12)
    user32.SetCursorPos(pt.x, pt.y)
    time.sleep(0.08)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    time.sleep(0.06)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP


def press_key(hwnd: int, vk: int) -> None:
    """Send a key press (e.g. VK_SPACE = 0x20 advances the replay, VK_RETURN = 0x0D presses 继续/Continue)."""
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.08)
    scan = user32.MapVirtualKeyW(vk, 0) & 0xFF  # MAPVK_VK_TO_VSC (SDL listens to scan codes)
    user32.keybd_event(vk, scan, 0, 0)
    time.sleep(0.06)
    user32.keybd_event(vk, scan, 0x0002, 0)


VK_SPACE = 0x20
VK_RETURN = 0x0D


def press_escape_key(hwnd: int) -> None:
    """Send ESC key press to close active popup or exit replay back to lobby."""
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.1)
    user32.keybd_event(0x1B, 0, 0, 0)       # VK_ESCAPE down
    time.sleep(0.06)
    user32.keybd_event(0x1B, 0, 0x0002, 0)  # VK_ESCAPE up


# ============================================================================
# Memory-Guided UI Widget Coordinate Extraction
# ============================================================================

def get_widget_client_point(
    reader: TTAMemoryReader,
    widget_ptr: int,
    rel_x: float = 0.5,
    rel_y: float = 0.5,
) -> Optional[Tuple[int, int]]:
    """
    Read a Cocos2d-x/TTA widget's exact client pixel coordinate `(px, py)` at relative
    position `(rel_x, rel_y)` in `[0.0, 1.0]` from its in-memory screen transform (`+0x2a4..+0x310`):
      client_left = float(+0x2f0), client_top = float(+0x2f4)
      width = float(+0x2a4), height = float(+0x2b4)
      inv_scale_x = float(+0x2fc), inv_scale_y = float(+0x30c)
    """
    if not widget_ptr or widget_ptr < 0x10000:
        return None
    b = reader.read_bytes(widget_ptr, 0x318)
    if not b:
        return None
    w = struct.unpack_from("<f", b, 0x2A4)[0]
    h = struct.unpack_from("<f", b, 0x2B4)[0]
    # +0x2f0/+0x2f4 is the screen position of the widget's ANCHOR POINT (anchor at +0x198/+0x19c),
    # e.g. Load/Close buttons use anchor (0.5, 0.5) while panels use (0, 0).
    anchor_x, anchor_y = struct.unpack_from("<2f", b, 0x198)
    if not (0.0 <= anchor_x <= 1.0 and 0.0 <= anchor_y <= 1.0):
        anchor_x, anchor_y = 0.0, 0.0
    pos_x, pos_y = struct.unpack_from("<2f", b, 0x2F0)
    inv_sx = struct.unpack_from("<f", b, 0x2FC)[0]
    inv_sy = struct.unpack_from("<f", b, 0x30C)[0]
    if abs(inv_sx) < 0.01 or abs(inv_sy) < 0.01:
        return None
    px = int(round(pos_x + (w / inv_sx) * (rel_x - anchor_x)))
    py = int(round(pos_y + (h / inv_sy) * (rel_y - anchor_y)))
    if 0 <= px <= 8000 and 0 <= py <= 5000:
        return (px, py)
    return None


def get_widget_client_center(reader: TTAMemoryReader, widget_ptr: int) -> Optional[Tuple[int, int]]:
    """Read a Cocos2d-x/TTA widget's exact client pixel center `(cx, cy)`."""
    return get_widget_client_point(reader, widget_ptr, 0.5, 0.5)


def find_ui_dialogs_in_memory(reader: TTAMemoryReader) -> Dict[str, Any]:
    """
    Scan `ThroughTheAges_steam.exe` memory for active instances of:
    1. `SpectateInputWindow` (`vtable = module_base + 0x00a10950`, active when `refcount > 0`, `+0x78 != 0`, `+0xd8 != 0`)
       - `pasteBtn` at `+0x480`
       - `confirmBtn` at `+0x47c`
       - `closeButton` at `+0x428`
    2. `SpectateWindow` (`vtable = module_base + 0x00a10ae8`, active when `refcount > 0`, `+0x78 != 0`, `+0xd8 != 0`)
       - `roundSelector` at `+0x464` (`max_rounds` at `+0x42c`, `slider` at `+0x434`, `selected_round` at `slider + 0x454`)
       - `loadBtn` (`spec_win_load`) at `+0x500`
       - `closeBtn` (`spec_win_close`) at `+0x4fc`
    """
    if not reader.h_proc or not reader.module_base:
        return {}

    vt_input = struct.pack("<I", reader.module_base + RVA_VTABLE_SPECTATE_INPUT_WIN)
    vt_detail = struct.pack("<I", reader.module_base + RVA_VTABLE_SPECTATE_DETAIL_WIN)
    vt_lobby = struct.pack("<I", reader.module_base + RVA_VTABLE_MULTIPLAYER_LOBBY)

    is_64 = ctypes.sizeof(ctypes.c_void_p) == 8
    mbi = MEMORY_BASIC_INFORMATION64() if is_64 else MEMORY_BASIC_INFORMATION32()
    mbi_sz = ctypes.sizeof(mbi)

    input_win_info = None
    detail_win_info = None
    lobby_info = None

    addr = 0x10000
    while addr < 0x7FFF0000:
        if not reader.k32.VirtualQueryEx(reader.h_proc, ctypes.c_void_p(addr), ctypes.byref(mbi), mbi_sz):
            break
        base, size = int(mbi.BaseAddress), int(mbi.RegionSize)
        if mbi.State == MEM_COMMIT and (mbi.Protect & PAGE_READWRITE) and size <= 64 * 1024 * 1024:
            chunk = reader.read_bytes(base, size)
            if chunk:
                # 0. Check for MultiplayerLobbyV2 (eye / spectate button at +0xd4)
                pos = 0
                while True:
                    pos = chunk.find(vt_lobby, pos)
                    if pos == -1:
                        break
                    if pos + 0xE0 <= len(chunk):
                        parent_ptr = struct.unpack_from("<I", chunk, pos + 0xD8)[0]
                        eye_ptr = struct.unpack_from("<I", chunk, pos + 0xD4)[0]
                        if parent_ptr > 0x10000 and eye_ptr > 0x10000:
                            eye_xy = get_widget_client_center(reader, eye_ptr)
                            if eye_xy:
                                lobby_info = {"address": base + pos, "eye_xy": eye_xy}
                    pos += 4
                # 1. Check for SpectateInputWindow
                pos = 0
                while True:
                    pos = chunk.find(vt_input, pos)
                    if pos == -1:
                        break
                    if pos + 0x490 <= len(chunk):
                        parent_ptr = struct.unpack_from("<I", chunk, pos + 0xD8)[0]
                        paste_ptr = struct.unpack_from("<I", chunk, pos + 0x480)[0]
                        confirm_ptr = struct.unpack_from("<I", chunk, pos + 0x47C)[0]
                        if (
                            parent_ptr > 0x10000
                            and paste_ptr > 0x10000
                            and confirm_ptr > 0x10000
                        ):
                            paste_xy = get_widget_client_center(reader, paste_ptr)
                            confirm_xy = get_widget_client_center(reader, confirm_ptr)
                            if paste_xy and confirm_xy:
                                input_win_info = {
                                    "address": base + pos,
                                    "paste_xy": paste_xy,
                                    "confirm_xy": confirm_xy,
                                }
                    pos += 4

                # 2. Check for SpectateWindow (detail popup with RoundSelector and Load button)
                pos = 0
                while True:
                    pos = chunk.find(vt_detail, pos)
                    if pos == -1:
                        break
                    if pos + 0x510 <= len(chunk):
                        parent_ptr = struct.unpack_from("<I", chunk, pos + 0xD8)[0]
                        round_sel_ptr = struct.unpack_from("<I", chunk, pos + 0x464)[0]
                        load_ptr = struct.unpack_from("<I", chunk, pos + 0x500)[0]
                        close_ptr = struct.unpack_from("<I", chunk, pos + 0x4FC)[0]
                        if parent_ptr > 0x10000 and load_ptr > 0x10000:
                            load_xy = get_widget_client_center(reader, load_ptr)
                            close_xy = get_widget_client_center(reader, close_ptr)
                            max_rounds = 0
                            slider_ptr = 0
                            selected_round = 0
                            slider_right_xy = None
                            if round_sel_ptr and round_sel_ptr > 0x10000:
                                max_rounds = reader.read_u32(round_sel_ptr + 0x42C) or 0
                                slider_ptr = reader.read_u32(round_sel_ptr + 0x434) or 0
                                if slider_ptr and slider_ptr > 0x10000:
                                    selected_round = reader.read_u32(slider_ptr + 0x454) or 0
                                    slider_right_xy = get_widget_client_point(
                                        reader, slider_ptr, rel_x=0.98, rel_y=0.5
                                    )
                            if load_xy:
                                detail_win_info = {
                                    "address": base + pos,
                                    "load_ptr": load_ptr,
                                    "load_xy": load_xy,
                                    "close_xy": close_xy,
                                    "round_sel_ptr": round_sel_ptr,
                                    "max_rounds": max_rounds,
                                    "slider_ptr": slider_ptr,
                                    "selected_round": selected_round,
                                    "slider_right_xy": slider_right_xy,
                                }
                    pos += 4
        addr = base + size

    return {
        "input_window": input_win_info,
        "detail_window": detail_win_info,
        "lobby": lobby_info,
    }


# ============================================================================
# Core Extraction & Backend Sync Functions
# ============================================================================

def find_scoring_continue_button(reader: TTAMemoryReader) -> Optional[Tuple[int, int]]:
    """
    Find an active end-of-game dialog and return the client coordinates of its "继续 / Continue" button.
    - `TTA::EndOfGameDialog` ("最终事件 / 结算 x/6"): Continue button pointer at `+0x444`.
    - `TTA::EndOfAgeDialog` / `TTA::ScoringDialog`: bottom-most button-like child widget.
    """
    if not reader.h_proc or not reader.module_base:
        return None
    dialogs = [
        (struct.pack("<I", reader.module_base + RVA_VTABLE_END_OF_GAME_DIALOG), 0x444),
        (struct.pack("<I", reader.module_base + RVA_VTABLE_END_OF_AGE_DIALOG), None),
        (struct.pack("<I", reader.module_base + RVA_VTABLE_SCORING_DIALOG), None),
    ]
    mod_lo, mod_hi = reader.module_base, reader.module_base + 0xD00000
    is_64 = ctypes.sizeof(ctypes.c_void_p) == 8
    mbi = MEMORY_BASIC_INFORMATION64() if is_64 else MEMORY_BASIC_INFORMATION32()
    mbi_sz = ctypes.sizeof(mbi)
    best: Optional[Tuple[int, int]] = None
    addr = 0x10000
    while addr < 0x7FFF0000:
        if not reader.k32.VirtualQueryEx(reader.h_proc, ctypes.c_void_p(addr), ctypes.byref(mbi), mbi_sz):
            break
        base, size = int(mbi.BaseAddress), int(mbi.RegionSize)
        if mbi.State == MEM_COMMIT and (mbi.Protect & PAGE_READWRITE) and size <= 64 * 1024 * 1024:
            chunk = reader.read_bytes(base, size)
            if chunk:
                for vt_dialog, btn_off in dialogs:
                    pos = 0
                    while True:
                        pos = chunk.find(vt_dialog, pos)
                        if pos == -1:
                            break
                        if pos + 0x600 <= len(chunk) and struct.unpack_from("<I", chunk, pos + 0xD8)[0] > 0x10000:
                            if btn_off is not None:
                                wp = struct.unpack_from("<I", chunk, pos + btn_off)[0]
                                xy = get_widget_client_center(reader, wp) if 0x10000 < wp < 0x7FFF0000 else None
                                if xy:
                                    return xy
                            for off in range(0x3F0, 0x600, 4):
                                wp = struct.unpack_from("<I", chunk, pos + off)[0]
                                if not (0x10000 < wp < 0x7FFF0000):
                                    continue
                                vt = reader.read_u32(wp)
                                if not vt or not (mod_lo < vt < mod_hi):
                                    continue
                                if (reader.read_u32(wp + 0xD8) or 0) < 0x10000:
                                    continue
                                b = reader.read_bytes(wp + 0x2A4, 0x14)
                                if not b:
                                    continue
                                w, h = struct.unpack_from("<f", b, 0)[0], struct.unpack_from("<f", b, 0x10)[0]
                                if not (h > 20 and 1.8 < w / h < 10):
                                    continue  # not button-shaped
                                xy = get_widget_client_center(reader, wp)
                                if xy and (best is None or xy[1] > best[1]):
                                    best = xy
                        pos += 4
        addr = base + size
    return best


def find_latest_info_and_events(saves_dir: str = TTA_SAVES_DIR) -> Tuple[Optional[str], Optional[str]]:
    """Locate the most recently modified multiplayer `.info`/`.v2` and `.events` files."""
    candidates = [
        ("autosavemultiplayer_finished.info", "autosavemultiplayer_finished.events"),
        ("autosavemultiplayer.v2", "autosavemultiplayer.events"),
    ]
    best_pair = (None, None)
    best_mtime = -1.0
    for info_fn, ev_fn in candidates:
        ip = os.path.join(saves_dir, info_fn)
        ep = os.path.join(saves_dir, ev_fn)
        if os.path.exists(ip):
            mt = os.path.getmtime(ip)
            if mt > best_mtime:
                best_mtime = mt
                best_pair = (ip, ep if os.path.exists(ep) else None)
    return best_pair


def extract_current_game(
    game_id: str,
    saves_dir: str = TTA_SAVES_DIR,
    pre_scanned_dump: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Extract complete statistics for the currently loaded replay from:
    1. Live `ThroughTheAges_steam.exe` memory (`TTA::GameStats` + `TTA::Player` + `TTA::PlayerGraphStats`)
    2. `.info` / `.v2` player slot metadata (only if sessionID matches `decode_game_id(game_id)`)
    3. Fallback `.events` stream if process is not running
    """
    target_session_id = decode_game_id(game_id)
    info_path, events_path = find_latest_info_and_events(saves_dir)
    info_data = parse_info_file(info_path) if info_path else None
    if info_data and target_session_id and info_data.get("sessionID") != target_session_id:
        # Disk .info belongs to a different game (Spectate mode does not overwrite disk .info)
        info_data = None

    gamestats_lines: List[str] = []
    pgs_list: List[Dict[str, Any]] = []
    memory_players: Dict[int, str] = {}

    if pre_scanned_dump and pre_scanned_dump.get("gamestats_lines"):
        gamestats_lines = pre_scanned_dump["gamestats_lines"]
        pgs_list = pre_scanned_dump.get("player_graph_stats", [])
        memory_players = pre_scanned_dump.get("memory_players", {})
    else:
        reader = TTAMemoryReader()
        if reader.attach():
            try:
                dump = reader.scan_gamestats_and_graphs(target_session_id=target_session_id)
                gamestats_lines = dump.get("gamestats_lines", [])
                pgs_list = dump.get("player_graph_stats", [])
                memory_players = dump.get("memory_players", {})
                print(
                    f"  [RAM] Extracted {len(gamestats_lines)} GameStats lines, "
                    f"{len(memory_players)} player mappings ({memory_players}) & "
                    f"{len(pgs_list)} PlayerGraphStats objects from PID {reader.pid}"
                )
            finally:
                reader.close()

    if gamestats_lines:
        parsed = parse_gamestats_lines(
            gamestats_lines,
            info_data=info_data,
            pgs_list=pgs_list,
            memory_players=memory_players,
        )
        parsed["source"] = "memory_gamestats"
    elif events_path and os.path.exists(events_path):
        print(f"  [Disk Fallback] Parsing {events_path}...")
        events = parse_events_file(events_path)
        parsed = parse_events_fallback(events, info_data=info_data)
        parsed["source"] = "disk_events"
    else:
        raise RuntimeError("Neither ThroughTheAges_steam.exe memory nor .events file is available.")

    cancels = (pre_scanned_dump or {}).get("pact_cancels") or []
    if cancels:
        parsed["pact_cancels"] = cancels
        apply_pact_cancels(parsed)

    parsed["gameId"] = game_id.strip().upper()
    parsed["sessionID"] = target_session_id or (info_data.get("sessionID") if info_data else None)
    if info_data:
        parsed["gameName"] = info_data.get("name", "")

    os.makedirs(PARSED_CACHE_DIR, exist_ok=True)
    if gamestats_lines:
        raw_path = os.path.join(PARSED_CACHE_DIR, f"{game_id.strip().upper()}.gamestats.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write("\n".join(gamestats_lines))
    cache_path = os.path.join(PARSED_CACHE_DIR, f"{parsed['gameId']}.json")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)
    print(f"  [Saved] {cache_path} -> Rankings: {parsed['rankings']}")
    return parsed


_SHEET_RANKS_CACHE: Optional[Dict[str, List[str]]] = None


def fetch_sheet_ranks(api_url: str) -> Dict[str, List[str]]:
    """GET `?action=ranks` -> {GameID: [r1, r2, r3, r4]} currently stored in the main sheet."""
    global _SHEET_RANKS_CACHE
    if _SHEET_RANKS_CACHE is None:
        try:
            with urllib.request.urlopen(f"{api_url}?action=ranks&k={FULL_VIEW_KEY}", timeout=30) as resp:
                _resp = json.loads(resp.read().decode("utf-8"))
            if "ranks" not in _resp:
                raise RuntimeError("Apps Script 尚未部署 ?action=ranks（請重新部署 Code.gs）")
            _SHEET_RANKS_CACHE = _resp["ranks"]
        except Exception as e:
            print(f"  [Warn] 無法取得 Google Sheet 現有名次（{e}），為安全起見本次不覆寫名次。")
            _SHEET_RANKS_CACHE = {"__unavailable__": []}
    return _SHEET_RANKS_CACHE


def safe_rankings(api_url: str, parsed_game: Dict[str, Any]) -> List[str]:
    """
    Decide which r1..r4 to send. Only overwrite the sheet's rankings when the parsed result is
    4 known tournament players AND the sheet row is empty or identical. Mismatches are logged to
    `parsed_games/_rank_mismatch.txt` and the manual ranking is kept.
    """
    from tta_parser import TOURNAMENT_PLAYERS
    gid = parsed_game["gameId"]
    parsed = [parsed_game.get(k, "") for k in ("r1", "r2", "r3", "r4")]
    if not all(p in TOURNAMENT_PLAYERS for p in parsed) or len(set(parsed)) != 4:
        print(f"  [Warn] {gid} 名次含非賽事玩家或不足 4 人 {parsed}，只上傳統計、不改名次。")
        return ["", "", "", ""]
    sheet = fetch_sheet_ranks(api_url)
    if "__unavailable__" in sheet:
        return ["", "", "", ""]
    existing = sheet.get(gid, [])
    if existing and any(existing) and existing != parsed:
        print(f"  [Mismatch] {gid} Sheet 名次 {existing} ≠ Replay 名次 {parsed}，保留 Sheet 原名次，請人工確認！")
        os.makedirs(PARSED_CACHE_DIR, exist_ok=True)
        with open(os.path.join(PARSED_CACHE_DIR, "_rank_mismatch.txt"), "a", encoding="utf-8") as f:
            f.write(f"{gid}: sheet={existing} replay={parsed}\n")
        return ["", "", "", ""]
    return parsed


def apply_pact_cancels(parsed: Dict[str, Any]) -> None:
    """
    Add active pact cancellations (EventCancelPact objects from the replay; not logged in GameStats)
    to `pacts_broken`. When a player attacks their pact partner the game ALSO creates an
    EventCancelPact (sometimes attributed to the partner), but that break is already counted via
    `PACT_BREAK` -> ignore cancel events of slots involved in a `PACT_BREAK`
    (verified: GEZITELUMA DHYellow = 1, SAZISIBUMA bala = 0 / duck = 1, TELUNIDUMA bob = 1).
    """
    skip = set(parsed.get("pact_break_slots") or [])
    by_slot = {ps.get("slot"): ps for ps in parsed.get("players", {}).values()}
    for c in parsed.get("pact_cancels") or []:
        if c.get("slot") in by_slot and c.get("slot") not in skip:
            by_slot[c["slot"]]["pacts_broken"] = by_slot[c["slot"]].get("pacts_broken", 0) + 1


def reparse_local_games() -> None:
    """Re-run the current parser on all saved raw GameStats logs, keeping slot->login and pact cancels."""
    for fn in sorted(f for f in os.listdir(PARSED_CACHE_DIR) if f.endswith(".gamestats.txt")):
        gid = fn[: -len(".gamestats.txt")]
        jpath = os.path.join(PARSED_CACHE_DIR, f"{gid}.json")
        if not os.path.exists(jpath):
            continue
        with open(jpath, encoding="utf-8") as f:
            old = json.load(f)
        players = {v["slot"]: v["login"] for v in old.get("players", {}).values() if "slot" in v}
        with open(os.path.join(PARSED_CACHE_DIR, fn), encoding="utf-8") as f:
            lines = f.read().splitlines()
        new = parse_gamestats_lines(lines, memory_players=players)
        for k in ("gameId", "sessionID", "source", "pact_cancels", "gameName"):
            if k in old:
                new[k] = old[k]
        apply_pact_cancels(new)
        with open(jpath, "w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False, indent=2)
        changed = "" if new.get("rankings") == old.get("rankings") else f"  (原本 {old.get('rankings')})"
        print(f"  [Reparse] {gid}: {new.get('rankings')}{changed}")


def compare_local_with_sheet(api_url: str) -> None:
    """Print a comparison of every locally parsed game's ranking against the Google Sheet."""
    sheet = fetch_sheet_ranks(api_url)
    files = sorted(f for f in os.listdir(PARSED_CACHE_DIR) if f.endswith(".json"))
    ok = bad = 0
    for fn in files:
        with open(os.path.join(PARSED_CACHE_DIR, fn), encoding="utf-8") as f:
            g = json.load(f)
        gid = g.get("gameId", fn[:-5])
        parsed = [g.get(k, "") for k in ("r1", "r2", "r3", "r4")]
        existing = sheet.get(gid, [])
        same = existing == parsed
        ok += same
        bad += not same
        mark = "OK " if same else "DIFF"
        extra = ""
        print(f"  {mark} {gid}: sheet={existing} replay={parsed}{extra}")
    print(f"[Compare] 一致 {ok} 場、不一致 {bad} 場")


def upload_parsed_game(api_url: str, parsed_game: Dict[str, Any]) -> Dict[str, Any]:
    """POST parsed game rankings (`r1..r4`, only when safe) and per-player `gameStats` to Google Apps Script."""
    r1, r2, r3, r4 = safe_rankings(api_url, parsed_game)
    payload = {
        "action": "uploadGameStats",
        "gameId": parsed_game["gameId"],
        "r1": r1,
        "r2": r2,
        "r3": r3,
        "r4": r4,
        "gameStats": parsed_game.get("players", {}),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=body,
        headers={"Content-Type": "text/plain;charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_unparsed_game_ids(api_url: str) -> List[str]:
    """Fetch list of GameIDs in Sheet1 that do not yet have detailed stats in `GameStats` tab."""
    url = f"{api_url}?action=unparsed"
    with urllib.request.urlopen(url, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("unparsedGameIds", [])


def find_main_menu_online_button(reader: TTAMemoryReader) -> Optional[Tuple[int, int]]:
    """Return the client coords of the main menu's "在线游戏 / Play Online" button (MenuScene1 + 0xb0)."""
    if not reader.h_proc or not reader.module_base:
        return None
    vt = struct.pack("<I", reader.module_base + RVA_VTABLE_MAIN_MENU)
    mod_lo, mod_hi = reader.module_base, reader.module_base + 0xD00000
    is_64 = ctypes.sizeof(ctypes.c_void_p) == 8
    mbi = MEMORY_BASIC_INFORMATION64() if is_64 else MEMORY_BASIC_INFORMATION32()
    mbi_sz = ctypes.sizeof(mbi)
    addr = 0x10000
    while addr < 0x7FFF0000:
        if not reader.k32.VirtualQueryEx(reader.h_proc, ctypes.c_void_p(addr), ctypes.byref(mbi), mbi_sz):
            break
        base, size = int(mbi.BaseAddress), int(mbi.RegionSize)
        if mbi.State == MEM_COMMIT and (mbi.Protect & PAGE_READWRITE) and size <= 64 * 1024 * 1024:
            chunk = reader.read_bytes(base, size)
            if chunk:
                pos = 0
                while True:
                    pos = chunk.find(vt, pos)
                    if pos == -1:
                        break
                    if pos % 4 == 0 and pos + 0xB4 <= len(chunk):
                        btn = struct.unpack_from("<I", chunk, pos + 0xB0)[0]
                        if 0x10000 < btn < 0x7FFF0000:
                            bvt = reader.read_u32(btn)
                            if bvt and mod_lo < bvt < mod_hi and (reader.read_u32(btn + 0xD8) or 0) > 0x10000:
                                xy = get_widget_client_center(reader, btn)
                                if xy:
                                    return xy
                    pos += 4
        addr = base + size
    return None


def resolve_session_players(reader: TTAMemoryReader, session_id: Optional[int], dump: Dict[str, Any]) -> None:
    """
    Make `dump["memory_players"]` (slot -> login) complete and specific to THIS session:
    1. slot -> userID from this session's event records (`extract_session_events`)
    2. the session's 4 userIDs from its settings (`leaders` JSON is keyed by userID)
    3. fill missing slots from in-memory TTA::Player objects whose userID belongs to this session
    """
    if not session_id:
        return
    from tta_parser import KNOWN_USER_ID_TO_LOGIN
    players: Dict[int, str] = dict(dump.get("memory_players") or {})
    sess = reader.extract_session_events(session_id)
    session_uids: set = set()
    slot_uid: Dict[int, int] = {}
    if sess:
        for ev in sess.get("events", []):
            if 0 <= ev.get("player_slot", -1) <= 3 and ev.get("user_id"):
                slot_uid.setdefault(ev["player_slot"], ev["user_id"])
        try:
            session_uids = {int(k) for k in json.loads(sess.get("settings", {}).get("leaders") or "{}")}
        except Exception:
            session_uids = set()
    net = dump.get("net_players") or {}
    uid_login = dict(KNOWN_USER_ID_TO_LOGIN)
    for info in net.values():
        uid_login[info["userID"]] = info["login"]
    for slot, uid in slot_uid.items():
        if uid in uid_login:
            players[slot] = uid_login[uid]
    for slot, info in net.items():
        if slot not in players and (not session_uids or info["userID"] in session_uids):
            players[slot] = info["login"]
    dump["memory_players"] = players


def gamestats_signature(lines: List[str]) -> Optional[Tuple[int, str]]:
    """Identify a GameStats log (count + first/EOG lines) to tell a new replay from a stale one."""
    if not lines:
        return None
    eog = "|".join(ln for ln in lines if "EOG_ORDER" in ln)
    return (len(lines), "|".join(lines[:60]) + "#" + eog)


def return_to_lobby(reader: TTAMemoryReader, hwnd: int, timeout_sec: int = 60) -> bool:
    """
    After the replay finished: keep clicking the end-of-game "继续 / Continue" button until the
    MultiplayerLobbyV2 (with its eye/spectate button) is shown again.
    """
    print("  [UI] 擷取完成 -> 自動點擊「继续」離開 Replay，回到線上大廳...")
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    client_w, client_h = rect.right - rect.left, rect.bottom - rect.top
    start = time.time()
    quiet_checks = 0
    while time.time() - start < timeout_sec:
        cont_xy = find_scoring_continue_button(reader)
        if cont_xy:
            press_key(hwnd, VK_RETURN)
            quiet_checks = 0
            time.sleep(1.5)
            continue
        ui = find_ui_dialogs_in_memory(reader)
        if ui.get("lobby"):
            quiet_checks += 1
            if quiet_checks >= 2:
                print("  [UI] 已回到線上大廳。")
                return True
            time.sleep(1.0)
            continue
        menu_xy = find_main_menu_online_button(reader)
        if menu_xy:
            print(f"  [UI] 回到主畫面 -> 點擊「在线游戏」({menu_xy[0]}, {menu_xy[1]})...")
            click_client_pixel(hwnd, *menu_xy)
            time.sleep(3.0)
            continue
        if time.time() - start > 10:
            press_key(hwnd, VK_RETURN)  # final results screen "继续"
        time.sleep(1.5)
    print("  [Warn] 未能自動回到大廳，請手動回到「線上遊戲」大廳，批次會繼續等待。")
    return False


def drive_spectate_window_and_extract(
    game_id: str, timeout_sec: int = 60, return_lobby: bool = True
) -> Dict[str, Any]:
    """
    1. Locate `ThroughTheAges_steam.exe` window (`SDL_app`) and attach to its memory.
    2. If the replay for `game_id` is already open at the end of the game (`EOG_ORDER` in memory),
       extract it immediately!
    3. Otherwise, wait for `SpectateInputWindow` to be open in memory; read exact pixel coords of
       `pasteBtn` and `confirmBtn`, copy `game_id` to clipboard, and click `pasteBtn` -> `confirmBtn`.
    4. Wait for `SpectateWindow` popup (`RoundSelector` + `spec_win_load` button) to appear in memory,
       automatically set the round slider to `max_rounds` (both in memory at `slider + 0x454` and by
       clicking `98%` right edge of the slider bar), and click `Load`.
    5. Wait for `TTA::GameStats` (`EOG_ORDER`) to populate in memory and extract all stats!
    """
    target_session_id = decode_game_id(game_id)
    reader = TTAMemoryReader()
    if not reader.attach(writable=True):
        raise RuntimeError("ThroughTheAges_steam.exe is not running. Please launch the game first.")

    try:
        # Fast-path: check if the replay is already loaded at the end of the game in memory
        initial_dump = reader.scan_gamestats_and_graphs(target_session_id=target_session_id)
        # GameStats already in memory belongs to a PREVIOUS replay (batch mode): remember its signature
        # so we never mistake it for the game we are about to load.
        stale_sig = gamestats_signature(initial_dump.get("gamestats_lines", []))
        stale_gs_addr = initial_dump.get("gamestats_addr", 0)
        # EventCancelPact objects already in memory belong to previously watched replays
        from tta_parser import RVA_VTABLE_EVENT_CANCEL_PACT
        stale_cancels = {(e["addr"], e["ts"]) for e in reader.scan_event_objects(RVA_VTABLE_EVENT_CANCEL_PACT)}

        hwnd = find_tta_hwnd(reader.pid)
        if not hwnd:
            raise RuntimeError(
                f"Found ThroughTheAges_steam.exe (PID={reader.pid}), but could not locate its SDL_app window."
            )

        # Step 1: Check if SpectateWindow or SpectateInputWindow is open in memory
        ui = find_ui_dialogs_in_memory(reader)
        if not ui.get("input_window") and not ui.get("detail_window"):
            wait_start = time.time()
            last_eye_click = 0.0
            notified = False
            while time.time() - wait_start < 45:
                lobby = ui.get("lobby")
                if not lobby and time.time() - last_eye_click > 4.0:
                    menu_xy = find_main_menu_online_button(reader)
                    if menu_xy:
                        print(f"  [UI] 偵測到主畫面 -> 點擊「在线游戏」({menu_xy[0]}, {menu_xy[1]})...")
                        click_client_pixel(hwnd, *menu_xy)
                        last_eye_click = time.time()
                if lobby and time.time() - last_eye_click > 4.0:
                    ex, ey = lobby["eye_xy"]
                    print(f"  [UI] 偵測到線上大廳 -> 自動點擊左上角「觀戰（眼睛）」按鈕 ({ex}, {ey})...")
                    click_client_pixel(hwnd, ex, ey)
                    last_eye_click = time.time()
                elif not lobby and not notified:
                    print("  [Wait] 請在遊戲內進入「線上遊戲」大廳（偵測中...）")
                    notified = True
                time.sleep(1.2)
                ui = find_ui_dialogs_in_memory(reader)
                if ui.get("input_window") or ui.get("detail_window"):
                    break
            if not ui.get("input_window") and not ui.get("detail_window"):
                raise RuntimeError("Timed out waiting for SpectateInputWindow to open in game.")

        # Step 2: If SpectateInputWindow is open, paste GameID and click Confirm
        if ui.get("input_window") and not ui.get("detail_window"):
            inp_win = ui["input_window"]
            paste_x, paste_y = inp_win["paste_xy"]
            confirm_x, confirm_y = inp_win["confirm_xy"]

            set_clipboard_text(game_id.strip().upper())
            print(
                f"  [UI] 已複製 '{game_id}' 至剪貼簿 -> 點擊貼上 ({paste_x}, {paste_y}) & 確認 ({confirm_x}, {confirm_y})..."
            )
            click_client_pixel(hwnd, paste_x, paste_y)
            time.sleep(0.45)
            click_client_pixel(hwnd, confirm_x, confirm_y)

        # Step 3: Wait for SpectateWindow detail popup (`RoundSelector` + `spec_win_load` button)
        print("  [UI] 等待伺服器回傳對局資訊與回合數滑桿...")
        load_clicked = False
        popup_start = time.time()
        while time.time() - popup_start < 18:
            time.sleep(0.6)
            ui = find_ui_dialogs_in_memory(reader)
            det_win = ui.get("detail_window")
            if det_win and det_win.get("load_xy") and det_win.get("max_rounds", 0) >= 1:
                # Let popup scale-in animation finish so widget screen coordinates are settled
                time.sleep(0.65)
                ui = find_ui_dialogs_in_memory(reader)
                det_win = ui.get("detail_window") or det_win

                max_rounds = det_win.get("max_rounds", 0)
                slider_ptr = det_win.get("slider_ptr", 0)
                slider_right_xy = det_win.get("slider_right_xy")

                # Set RoundSelector slider to the final round via UI click.
                # NOTE: Do NOT write [slider + 0x454] directly: it is a 0-based step index
                # (getSelectedRound = [slider+0x454] * [slider+0x43c]); writing max_rounds
                # overflowed the index and made the replay start from round 1.
                if slider_right_xy:
                    sx, sy = slider_right_xy
                    print(
                        f"  [UI] 將觀戰回合滑桿拉至最後一回合 (Round {max_rounds}) -> 點擊滑桿最右端 ({sx}, {sy})..."
                    )
                    click_client_pixel(hwnd, sx, sy)
                    time.sleep(0.35)

                if STOP_BEFORE_LOAD:
                    sel = reader.read_u32(slider_ptr + 0x454) if slider_ptr else None
                    print(f"  [Debug] 已停在「Load Game」之前 (slider index = {sel})，請保持此視窗開啟。")
                    return {}

                lx, ly = det_win["load_xy"]
                print(f"  [UI] 點擊「載入 (Load)」 ({lx}, {ly})...")
                time.sleep(0.25)
                click_client_pixel(hwnd, lx, ly)
                load_clicked = True
                break

        if not load_clicked:
            print("  [Notice] 未偵測到第二層載入視窗，等待對局直接載入或請手動點擊「載入」...")

        # Step 4: Wait for replay to finish loading (`EOG_ORDER` in `TTA::GameStats`).
        # After the final round loads, the game shows the final Age III scoring events
        # ("最终事件 / 结算 x/6") one by one; each needs a click on the "继续 / Continue" button.
        print("  [RAM] 等待 Replay 載入，並自動點擊「继续」推進 Age III 最終結算事件...")
        rect = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        client_w = rect.right - rect.left
        client_h = rect.bottom - rect.top
        time.sleep(3.0)  # give the replay scene time to load
        start_t = time.time()
        clicks = 0
        clicks_any = 0
        while time.time() - start_t < max(timeout_sec, 150):
            dump = reader.scan_gamestats_and_graphs(target_session_id=target_session_id)
            lines = dump.get("gamestats_lines", [])
            is_new = (
                stale_sig is None
                or gamestats_signature(lines) != stale_sig
                or dump.get("gamestats_addr", 0) != stale_gs_addr  # same game re-watched -> new object
            )
            if any("EOG_ORDER" in ln for ln in lines) and is_new:
                print(
                    f"  [RAM] 成功擷取完整對局！(點擊「继续」{clicks} 次) 共 {len(lines)} 條 GameStats 紀錄, "
                    f"玩家對應: {dump.get('memory_players', {})}"
                )
                resolve_session_players(reader, target_session_id, dump)
                cancels = [
                    e for e in reader.scan_event_objects(RVA_VTABLE_EVENT_CANCEL_PACT)
                    if (e["addr"], e["ts"]) not in stale_cancels
                ]
                dump["pact_cancels"] = [{"slot": e["slot"], "user_id": e["user_id"], "ts": e["ts"]} for e in cancels]
                if cancels:
                    print(f"  [RAM] 偵測到主動取消合約（毀約）: {[(e['slot'], e['user_id']) for e in cancels]}")
                print(f"  [RAM] 玩家對應（依本場紀錄）: {dump.get('memory_players', {})}")
                parsed = extract_current_game(game_id, pre_scanned_dump=dump)
                if return_lobby:
                    return_to_lobby(reader, hwnd)
                return parsed
            cont_xy = find_scoring_continue_button(reader)
            if cont_xy:
                print("  [UI] 按 Enter「继续」推進最終結算...")
                press_key(hwnd, VK_RETURN)
                clicks += 1
                if clicks >= 3 and clicks % 3 == 0:
                    click_client_pixel(hwnd, *cont_xy)  # fallback if Enter did not register
                time.sleep(1.0)
            elif time.time() - start_t > 2.0:
                # Replay is still animating the last player's final actions: Space advances it.
                click_client_pixel(hwnd, client_w // 2, client_h // 2)
                time.sleep(0.15)
                press_key(hwnd, VK_SPACE)
                clicks_any += 1
                if clicks_any == 1:
                    print("  [UI] 自動點擊畫面中央 + 空白鍵，推進最後一位玩家的最後動作...")
                time.sleep(0.8)
            else:
                time.sleep(1.0)

        raise RuntimeError(f"Timed out waiting for replay '{game_id}' to populate EOG_ORDER in memory.")
    finally:
        reader.close()


def main():
    parser = argparse.ArgumentParser(description="TTA Automated Replay & Statistics Collector")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="Google Apps Script Web App URL")
    parser.add_argument("--game-id", help="Parse a specific 10-letter GameID (e.g. LATADELUMA)")
    parser.add_argument(
        "--capture-current",
        metavar="GAME_ID",
        help="Immediately extract stats from the currently open replay in ThroughTheAges_steam.exe",
    )
    parser.add_argument("--no-upload", action="store_true", help="Save JSON locally without POSTing to Google Sheet")
    parser.add_argument(
        "--stay-in-replay",
        action="store_true",
        help="With --game-id: do not leave the replay after extraction (for memory inspection)",
    )
    parser.add_argument(
        "--redo",
        action="store_true",
        help="Batch: re-run games that already have a local parsed_games/<ID>.json (default: skip them)",
    )
    parser.add_argument(
        "--reparse",
        action="store_true",
        help="Re-parse every parsed_games/<ID>.gamestats.txt with the current parser (keeps players & pact cancels)",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare rankings of locally parsed games (parsed_games/*.json) with the Google Sheet and exit",
    )
    parser.add_argument(
        "--upload-local",
        action="store_true",
        help="Upload all locally parsed games (parsed_games/*.json) without replaying them again",
    )
    parser.add_argument(
        "--stop-before-load",
        action="store_true",
        help="Debug: drive the UI up to the spectate popup and stop before clicking Load Game",
    )
    args = parser.parse_args()

    if args.reparse:
        reparse_local_games()
        return
    if args.compare:
        compare_local_with_sheet(args.api_url)
        return
    if args.upload_local:
        for fn in sorted(f for f in os.listdir(PARSED_CACHE_DIR) if f.endswith(".json")):
            with open(os.path.join(PARSED_CACHE_DIR, fn), encoding="utf-8") as f:
                g = json.load(f)
            res = upload_parsed_game(args.api_url, g)
            print(f"  [Uploaded] {g.get('gameId')}: {res}")
        return

    global STOP_BEFORE_LOAD
    STOP_BEFORE_LOAD = args.stop_before_load
    if STOP_BEFORE_LOAD and args.game_id:
        drive_spectate_window_and_extract(args.game_id)
        return

    if args.capture_current:
        parsed = extract_current_game(args.capture_current)
        if not args.no_upload:
            res = upload_parsed_game(args.api_url, parsed)
            print(f"[Uploaded] Server response: {res}")
        return

    if args.game_id:
        parsed = drive_spectate_window_and_extract(args.game_id, return_lobby=not args.stay_in_replay)
        if not args.no_upload:
            res = upload_parsed_game(args.api_url, parsed)
            print(f"[Uploaded] Server response: {res}")
        return

    print("[Batch] Fetching unparsed GameIDs from Google Apps Script...")
    unparsed_ids = fetch_unparsed_game_ids(args.api_url)
    if not unparsed_ids:
        print("[Batch] All games in Google Sheets are already parsed and up to date!")
        return

    print(f"[Batch] Found {len(unparsed_ids)} unparsed games: {unparsed_ids}")
    if not args.redo:
        done = [g for g in unparsed_ids if os.path.exists(os.path.join(PARSED_CACHE_DIR, f"{g}.json"))]
        if done:
            print(f"[Batch] 跳過本機已解析的 {len(done)} 場（加 --redo 可重跑）: {done}")
        unparsed_ids = [g for g in unparsed_ids if g not in done]
    failed: List[str] = []
    for gid in unparsed_ids:
        print(f"\n--- Processing GameID: {gid} ---")
        try:
            parsed = drive_spectate_window_and_extract(gid)
            if not args.no_upload:
                res = upload_parsed_game(args.api_url, parsed)
                print(f"  [Uploaded] {gid}: {res}")
        except Exception as e:
            print(f"  [Error] Failed to process {gid}: {e}")
            failed.append(gid)
    if failed:
        print(f"\n[Batch] 失敗 {len(failed)} 場（可再執行一次重試）: {failed}")
    print("[Batch] 完成。建議接著執行: python auto_collector.py --compare")


if __name__ == "__main__":
    main()
