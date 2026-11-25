#!/usr/bin/env python3
"""
thor_setup.py - Setup helper for an AYN Thor Pro (or most Android handhelds),
run from a PC over ADB. Based on the TechDweeb Android handheld guide, using
the iiSU frontend, ARMSX2 (PS2), Eden (Switch) and SteamDeck (Steam + Proton).

Run it with no options for a KIAUH-style menu:

    python thor_setup.py

The menu shows the Thor, its SD card and every app's installed version, and
lets you install, update and remove apps, copy games and BIOS files, apply
emulator settings and change device settings.

Created by pdsavas with the assistance of Claude, Anthropic's AI assistant.
https://github.com/pdsavas/Thor-Setup-Script

The older one-shot mode still works when you pass step options:

    python thor_setup.py --download            # full setup, start to finish
    python thor_setup.py --only push           # just one step
    python thor_setup.py --list-steps
"""

import argparse
import json
import os
import posixpath
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
APK_DIR = HERE / "apks"
BIOS_DIR = HERE / "bios"
ROMS_DIR = HERE / "roms"
STATE_FILE = HERE / ".thor_state.json"
VERSION = "4.1"

# =====================================================================
#  Tweakables
# =====================================================================

# Android keycodes RetroArch uses as joypad button numbers.
K = {
    "A": "96", "B": "97", "X": "99", "Y": "100",
    "L1": "102", "R1": "103", "L2": "104", "R2": "105",
    "L3": "106", "R3": "107", "START": "108", "SELECT": "109",
}
# D-pad: most handhelds report it as a hat ("h0up"). If D-pad hotkeys
# don't work, change these to "19", "20", "21", "22" (Android keycodes).
DPAD = {"UP": "h0up", "DOWN": "h0down", "LEFT": "h0left", "RIGHT": "h0right"}

RETROARCH_SETTINGS = {
    # User Interface
    "menu_driver": "glui",                      # Material UI
    "menu_widget_scale_auto": "false",
    "menu_widget_scale_factor": "0.750000",
    "input_overlay_enable": "false",
    "builtin_imageviewer_enable": "false",      # needed for PICO-8
    # Input
    "input_player1_analog_dpad_mode": "1",      # Left Analog
    "input_bind_hold": "0",
    "quit_press_twice": "false",                # "Confirm Quit" off
    "input_block_timeout": "1",
    # Hotkeys (the guide's layout)
    "input_enable_hotkey_btn": K["SELECT"],
    "input_menu_toggle_gamepad_combo": "2",     # L3 + R3
    "input_menu_toggle_btn": K["A"],
    "input_exit_emulator_btn": K["START"],
    "input_toggle_fast_forward_btn": K["R2"],
    "input_toggle_slowmotion_btn": K["L2"],
    "input_volume_up_btn": DPAD["UP"],
    "input_volume_down_btn": DPAD["DOWN"],
    "input_load_state_btn": K["L1"],
    "input_save_state_btn": K["R1"],
    "input_state_slot_increase_btn": DPAD["RIGHT"],
    "input_state_slot_decrease_btn": DPAD["LEFT"],
    "input_screenshot_btn": K["Y"],
    # Saving
    "savestate_auto_save": "true",
    "savestate_auto_load": "true",
    "savestate_auto_index": "true",
    "cheevos_hardcore_mode_enable": "false",
    # Directories are filled in at runtime from the SD card path.
}

# Dolphin: the Thor Pro's Adreno 740 handles 3x + Vulkan comfortably.
DOLPHIN_INI = {"Core": {"GFXBackend": "Vulkan"}}
DOLPHIN_GFX_INI = {
    "Settings": {
        "InternalResolution": "3",
        "ShaderCompilationMode": "2",           # Hybrid Ubershaders
        "WaitForShadersBeforeStarting": "True", # compile before starting
        "AspectRatio": "1",                     # Force 16:9
        "wideScreenHack": "True",
    }
}

# Package names used to recognise emulators (pre-installed or otherwise).
EMU_PATTERN = re.compile(
    r"retroarch|dolphin|ppsspp|citra|lime3ds|azahar|yuzu|sudachi|\beden|"
    r"suyu|skyline|sx2|drastic|melonds|mupen|redream|flycast|duckstation|"
    r"mame4droid|m64plus|nethersx2|armsx2|winlator|vita3k|cemu|steamdeck|"
    r"hakux|xemu|x1box",
    re.I,
)
# Never offer to remove these as "pre-installed emulators".
PROTECTED = re.compile(r"^(android|com\.android|com\.google|com\.qualcomm|"
                       r"com\.ayn|org\.es_de|com\.iisulauncher)", re.I)

IISU_PKG = "com.iisulauncher"

# ROM folder names (iiSU's accepted folder names; also valid for ES-DE).
# "xbox" isn't an iiSU console yet; hakuX reads that folder itself.
COMMON_SYSTEMS = [
    "nes", "snes", "n64", "gb", "gbc", "gba", "nds", "n3ds", "gc", "wii",
    "switch", "psx", "ps2", "psp", "genesis", "saturn", "dreamcast",
    "arcade", "pico8", "xbox",
]

# hakuX (original Xbox). rfandango/hakuX is the upstream project; the README
# says its GitHub releases are the only official download. Other "hakuX"
# repos are forks or look-alikes.
HAKUX_REPO = "rfandango/hakuX"

EDEN_API = "https://git.eden-emu.dev/api/v1/repos/eden-emu/eden/releases"
STEAMDECK_REPO = "The412Banner/winlator-contents"
STEAMDECK_TAG = re.compile(r"^steamdeck-v?(\d+(?:\.\d+)*)$", re.I)

UA = {"User-Agent": "thor-setup/" + VERSION}   # for APIs (GitHub, Forgejo)
# Download pages (Dolphin, PPSSPP) may block unfamiliar clients, so look like a browser.
BROWSER_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# =====================================================================
#  Console helpers
# =====================================================================

ARGS = None
COLOR = False
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def setup_console(no_color=False):
    global COLOR
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    if os.name == "nt":
        os.system("")                  # turns on colour codes in Windows consoles
    COLOR = (sys.stdout.isatty() and not no_color and not os.environ.get("NO_COLOR"))


def c(text, style):
    if not COLOR:
        return str(text)
    codes = {"green": "32", "yellow": "33", "red": "31", "cyan": "36",
             "dim": "2", "bold": "1", "title": "1;36"}
    return f"\x1b[{codes[style]}m{text}\x1b[0m"


def say(msg=""):
    print(msg, flush=True)


def header(title):
    say()
    say(c("=" * 64, "cyan"))
    say(c(f"  {title}", "bold"))
    say(c("=" * 64, "cyan"))


def confirm(question, default=True):
    if ARGS and ARGS.yes:
        return default
    hint = "[Y/n]" if default else "[y/N]"
    ans = input(f"{question} {hint} ").strip().lower()
    if not ans:
        return default
    return ans.startswith("y")


def pause(msg):
    if ARGS and ARGS.yes:
        say(f"(--yes) Skipping wait: {msg}")
        return
    input(f"\n>>> {msg}\n    Press Enter when done... ")


def clear():
    if sys.stdout.isatty():
        os.system("cls" if os.name == "nt" else "clear")


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


# =====================================================================
#  ADB helpers
# =====================================================================

def adb_cmd(*args):
    return [ARGS.adb] + (["-s", ARGS.serial] if ARGS.serial else []) + list(args)


def adb(*args, check=True):
    res = subprocess.run(adb_cmd(*args), capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if check and res.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed:\n{res.stderr or res.stdout}")
    return (res.stdout or "").strip()


def shell(command, check=True):
    return adb("shell", command, check=check)


def remote_exists(path):
    return shell(f'[ -e "{path}" ] && echo yes || echo no', check=False) == "yes"


def installed_packages():
    out = shell("pm list packages", check=False)
    return {l.split(":", 1)[1].strip() for l in out.splitlines() if ":" in l}


def find_pkg(*candidates, pattern=None):
    pkgs = installed_packages()
    for cand in candidates:
        if cand in pkgs:
            return cand
    if pattern:
        for p in sorted(pkgs):
            if re.search(pattern, p, re.I):
                return p
    return None


def package_versions(pkgs):
    """{package: versionName} for the given installed packages (one adb call)."""
    pkgs = [p for p in pkgs if p]
    if not pkgs:
        return {}
    loop = ("for p in " + " ".join(shlex.quote(p) for p in pkgs) + "; do "
            "v=$(dumpsys package $p | grep -m1 versionName); echo \"$p ${v#*=}\"; done")
    out = {}
    for line in shell(loop, check=False).splitlines():
        pkg, _, ver = line.strip().partition(" ")
        if pkg:
            out[pkg] = ver.strip()
    return out


def launch(pkg):
    shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1", check=False)


# =====================================================================
#  Web helpers
# =====================================================================

def http_get(url, browser=False):
    req = urllib.request.Request(url, headers=BROWSER_UA if browser else UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def download(url, dest):
    say(f"    downloading {url}")
    req = urllib.request.Request(url, headers=BROWSER_UA)
    tmp = Path(str(dest) + ".part")
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got, last = 0, -1
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total and sys.stdout.isatty():
                pct = got * 100 // total
                if pct != last:
                    print(f"\r    {pct:3d}%  {human_size(got)} of {human_size(total)}",
                          end="", flush=True)
                    last = pct
        if total and sys.stdout.isatty():
            print()
    # An HTML error page saved as .apk would fail to install later; catch it here.
    with open(tmp, "rb") as f:
        if f.read(2) != b"PK":
            tmp.unlink()
            raise RuntimeError(f"{url} didn't return an APK (got a web page instead)")
    tmp.replace(dest)


def find_apk_links(html, base, prefer=None):
    """All .apk links in a page (any quoting, &amp; decoded), preferred ones first."""
    raw = re.findall(r'href\s*=\s*["\']?([^"\'\s>]+?\.apk)(?=["\'\s>?#])', html, re.I)
    raw += re.findall(r'(https?://[^"\'\s<>]+?\.apk)\b', html, re.I)
    links = []
    for l in raw:
        l = urllib.parse.urljoin(base, l.replace("&amp;", "&"))
        if l not in links:
            links.append(l)
    if prefer:
        links.sort(key=lambda l: not re.search(prefer, l))
    return links


def first_apk_link(page, prefer=None):
    html = http_get(page, browser=True).decode("utf-8", "replace")
    links = find_apk_links(html, page, prefer)
    if not links:
        raise RuntimeError(f"no .apk link found on {page}")
    return links[0]


# =====================================================================
#  Reading an APK's package name and version (binary AndroidManifest.xml)
# =====================================================================

def apk_info(path):
    """(package, versionName) from an APK file, or (None, None) if unreadable."""
    try:
        with zipfile.ZipFile(path) as z:
            return _parse_axml(z.read("AndroidManifest.xml"))
    except Exception:  # noqa: BLE001
        return None, None


def _parse_axml(d):
    u16 = lambda o: struct.unpack_from("<H", d, o)[0]   # noqa: E731
    u32 = lambda o: struct.unpack_from("<I", d, o)[0]   # noqa: E731
    strings, resids, pos = [], [], 8
    while pos + 8 <= len(d):
        ctype, hsize, csize = u16(pos), u16(pos + 2), u32(pos + 4)
        if csize < 8:
            break
        if ctype == 0x0001:                              # string pool
            count, flags, sstart = u32(pos + 8), u32(pos + 16), u32(pos + 20)
            utf8, base = flags & 0x100, pos + sstart
            for i in range(count):
                p = base + u32(pos + hsize + 4 * i)
                if utf8:
                    p += 2 if d[p] & 0x80 else 1         # skip char count
                    n = d[p]
                    if n & 0x80:
                        n, p = ((n & 0x7F) << 8) | d[p + 1], p + 2
                    else:
                        p += 1
                    strings.append(d[p:p + n].decode("utf-8", "replace"))
                else:
                    n = u16(p)
                    p += 2
                    if n & 0x8000:
                        n, p = ((n & 0x7FFF) << 16) | u16(p), p + 2
                    strings.append(d[p:p + 2 * n].decode("utf-16-le", "replace"))
        elif ctype == 0x0180:                            # resource id map
            resids = [u32(pos + 8 + 4 * i) for i in range((csize - 8) // 4)]
        elif ctype == 0x0102:                            # start element
            ext = pos + hsize
            if strings[u32(ext + 4)] == "manifest":
                astart, asize, acount = u16(ext + 8), u16(ext + 10), u16(ext + 12)
                pkg = ver = None
                for i in range(acount):
                    a = ext + astart + i * asize
                    name_i, raw, dtype, data = u32(a + 4), u32(a + 8), d[a + 15], u32(a + 16)
                    name = strings[name_i] if name_i < len(strings) else ""
                    rid = resids[name_i] if name_i < len(resids) else 0
                    if raw != 0xFFFFFFFF and raw < len(strings):
                        val = strings[raw]
                    elif dtype in (0x10, 0x11):
                        val = str(data)
                    else:
                        val = None
                    if name == "package":
                        pkg = val
                    elif name == "versionName" or rid == 0x0101021C:
                        ver = val
                return pkg, ver
        pos += csize
    return None, None


# =====================================================================
#  Versions
# =====================================================================

def vtuple(s):
    m = re.search(r"\d+(?:[._-]\d+)*", s or "")
    if not m:
        return ()
    return tuple(int(x) for x in re.split(r"[._-]", m.group(0)))


def is_newer(latest, installed):
    """True / False, or None when the two versions can't be compared."""
    a, b = vtuple(latest), vtuple(installed)
    if not a or not b:
        return None
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _ver(s):
    return tuple(int(x) for x in s.split("."))


# =====================================================================
#  Where each app comes from
# =====================================================================

class Release:
    def __init__(self, version, url, filename):
        self.version, self.url, self.filename = version, url, filename


def choose_apk(name, names, sdk):
    """Pick the right APK from a release's assets for this device."""
    low = {n: n.lower() for n in names}
    # Skip builds for other hardware / debug builds when there's a choice.
    bad = re.compile(r"x86|armeabi|armv7|debug|chromeos|genshin|spoof")
    pool = [n for n in names if not bad.search(low[n])] or list(names)

    if name == "armsx2":
        # e.g. ARMSX2-2.7-a13-armv8.2-sdk33.apk: newest build whose minimum
        # SDK the device meets; the armv8.0 "legacy" build only as a fallback.
        def sdk_of(n):
            m = re.search(r"sdk(\d+)", low[n])
            return int(m.group(1)) if m else 0
        fits = [n for n in pool if sdk_of(n) <= sdk and "legacy" not in low[n]]
        if fits:
            return max(fits, key=sdk_of)
        legacy = [n for n in pool if "legacy" in low[n]]
        return (legacy or pool)[0]

    if name == "eden":
        # Eden ships Standard / Genshin-spoof / Legacy; Standard is recommended.
        std = [n for n in pool if "standard" in low[n]]
        if std:
            return std[0]
        pool = [n for n in pool if "legacy" not in low[n]] or pool

    # Generic: prefer non-Play-Store, arm64 / universal Android builds.
    pool.sort(key=lambda n: ("play" in low[n],
                             "arm64" not in low[n] and "aarch64" not in low[n]
                             and "universal" not in low[n], len(n)))
    return pool[0]


def _release_from_assets(key, version, assets):
    apks = [a for a in assets if a["name"].lower().endswith(".apk")]
    if not apks:
        raise RuntimeError("that release has no APK")
    best = choose_apk(key, [a["name"] for a in apks], STATE["sdk"])
    asset = next(a for a in apks if a["name"] == best)
    return Release(version, asset["browser_download_url"], best)


def _url_version(url, pattern=r"(\d+(?:[._]\d+)+|\d{3,})"):
    m = re.findall(pattern, urllib.parse.urlparse(url).path)
    return m[-1].replace("_", ".") if m else "?"


def latest_github(key, repo):
    data = json.loads(http_get(f"https://api.github.com/repos/{repo}/releases/latest"))
    return _release_from_assets(key, data.get("tag_name", "?"), data.get("assets", []))


def latest_retroarch():
    html = http_get("https://buildbot.libretro.com/stable/").decode("utf-8", "replace")
    versions = set(re.findall(r"(\d+\.\d+\.\d+)/", html))
    if not versions:
        raise RuntimeError("no versions listed")
    v = max(versions, key=_ver)
    return Release(v, f"https://buildbot.libretro.com/stable/{v}/android/RetroArch_aarch64.apk",
                   f"RetroArch-{v}-aarch64.apk")


def latest_dolphin():
    errors = []
    for page in ("https://dolphin-emu.org/download/",
                 "https://dolphin-emu.org/update/latest/beta"):   # fallback feed
        try:
            url = first_apk_link(page, prefer=r"/releases/")
            name = posixpath.basename(urllib.parse.urlparse(url).path)
            m = re.search(r"dolphin-([\w.]+?)\.apk", name)
            return Release(m.group(1) if m else _url_version(url), url, name)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    raise RuntimeError("; ".join(errors))


def latest_ppsspp():
    url = first_apk_link("https://www.ppsspp.org/download/")
    return Release(_url_version(url), url, posixpath.basename(urllib.parse.urlparse(url).path))


def latest_eden():
    # Official Eden server (Forgejo). Try /latest, then the newest stable with an APK.
    try:
        data = json.loads(http_get(f"{EDEN_API}/latest"))
        if any(a["name"].lower().endswith(".apk") for a in data.get("assets", [])):
            return _release_from_assets("eden", data.get("tag_name", "?"), data["assets"])
    except Exception:  # noqa: BLE001
        pass
    for rel in json.loads(http_get(f"{EDEN_API}?limit=10")):
        if rel.get("prerelease") or rel.get("draft"):
            continue
        if any(a["name"].lower().endswith(".apk") for a in rel.get("assets", [])):
            return _release_from_assets("eden", rel.get("tag_name", "?"), rel["assets"])
    raise RuntimeError("no stable release with an Android APK found")


def pick_steamdeck(releases):
    """From GitHub release JSON, return (version, asset) for the newest SteamDeck APK."""
    best = None
    for rel in releases:
        m = STEAMDECK_TAG.match(rel.get("tag_name", ""))
        if not m or rel.get("draft") or rel.get("prerelease"):
            continue
        apks = [a for a in rel.get("assets", []) if a["name"].lower().endswith(".apk")]
        if not apks:
            continue
        ver = m.group(1)
        # Releases can carry older APKs "for reference": prefer the one named for this tag.
        asset = next((a for a in apks if ver in a["name"]), None)
        if asset is None:
            def apk_ver(a):
                v = re.search(r"(\d+(?:\.\d+)+)", a["name"])
                return _ver(v.group(1)) if v else (0,)
            asset = max(apks, key=apk_ver)
        if best is None or _ver(ver) > _ver(best[0]):
            best = (ver, asset)
    return best


def latest_steamdeck():
    # The repo also holds unrelated Winlator releases, so GitHub's "latest"
    # can't be used; pick the highest-numbered release tagged "SteamDeck-<ver>".
    releases = []
    for page in range(1, 6):
        batch = json.loads(http_get(
            f"https://api.github.com/repos/{STEAMDECK_REPO}/releases?per_page=100&page={page}"))
        releases += batch
        if len(batch) < 100:
            break
    found = pick_steamdeck(releases)
    if not found:
        raise RuntimeError("no SteamDeck-<version> release with an APK found")
    ver, asset = found
    return Release(ver, asset["browser_download_url"], asset["name"])


class App:
    def __init__(self, key, name, purpose, pattern, latest, page, configure=None):
        self.key, self.name, self.purpose = key, name, purpose
        self.pattern = re.compile(pattern, re.I)
        self.latest, self.page, self.configure = latest, page, configure


APPS = [
    App("iisu", "iiSU", "Frontend / home", r"^com\.iisulauncher$|iisu",
        lambda: latest_github("iisu", "iisu-network/iiSU"),
        "https://github.com/iisu-network/iiSU/releases"),
    App("retroarch", "RetroArch", "Retro consoles", r"^com\.retroarch",
        latest_retroarch, "https://www.retroarch.com/?page=platforms", "retroarch"),
    App("dolphin", "Dolphin", "GameCube / Wii", r"^org\.dolphinemu\.",
        latest_dolphin, "https://dolphin-emu.org/download/", "dolphin"),
    App("ppsspp", "PPSSPP", "PSP", r"^org\.ppsspp\.",
        latest_ppsspp, "https://www.ppsspp.org/download/"),
    App("azahar", "Azahar", "3DS", r"azahar",
        lambda: latest_github("azahar", "azahar-emu/azahar"),
        "https://github.com/azahar-emu/azahar/releases"),
    App("armsx2", "ARMSX2", "PS2", r"armsx2",
        lambda: latest_github("armsx2", "ARMSX2/ARMSX2"),
        "https://github.com/ARMSX2/ARMSX2/releases"),
    App("eden", "Eden", "Switch", r"(^|\.)eden(\.|_|$)",
        latest_eden, "https://git.eden-emu.dev/eden-emu/eden/releases"),
    App("hakux", "hakuX", "Original Xbox", r"hakux",
        lambda: latest_github("hakux", HAKUX_REPO),
        f"https://github.com/{HAKUX_REPO}/releases"),
    App("steamdeck", "SteamDeck", "Steam / PC games", r"steamdeck",
        latest_steamdeck, f"https://github.com/{STEAMDECK_REPO}/releases"),
]
APP_BY_KEY = {a.key: a for a in APPS}


# =====================================================================
#  Remembering which package belongs to which app
# =====================================================================

STATE = {"sd": None, "sdk": 33, "device": None, "latest": {}}


def load_saved():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_package(key, pkg):
    data = load_saved()
    data.setdefault("packages", {})[key] = pkg
    try:
        STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def app_packages(pkgs=None):
    """{app key: installed package or None}."""
    pkgs = installed_packages() if pkgs is None else pkgs
    saved = load_saved().get("packages", {})
    local = {}  # packages named inside APKs we downloaded
    for apk in APK_DIR.glob("*.apk") if APK_DIR.exists() else []:
        key = apk.name.split("-", 1)[0].lower()
        if key in APP_BY_KEY:
            pkg, _ = apk_info(apk)
            if pkg:
                local.setdefault(key, pkg)
    out = {}
    for app in APPS:
        found = None
        for cand in (saved.get(app.key), local.get(app.key)):
            if cand and cand in pkgs:
                found = cand
                break
        if not found:
            found = next((p for p in sorted(pkgs) if app.pattern.search(p)), None)
        out[app.key] = found
    return out


# =====================================================================
#  Config file merging
# =====================================================================

CFG_LINE = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=')


def merge_retroarch_cfg(text, settings):
    """Replace or append key = "value" lines, keeping everything else."""
    remaining = dict(settings)
    out = []
    for line in text.splitlines():
        m = CFG_LINE.match(line)
        if m and m.group(1) in remaining:
            key = m.group(1)
            out.append(f'{key} = "{remaining.pop(key)}"')
        else:
            out.append(line)
    for key, val in remaining.items():
        out.append(f'{key} = "{val}"')
    return "\n".join(out) + "\n"


def merge_ini(text, sections):
    """Case-preserving INI merge (configparser would lowercase keys)."""
    todo = {s: dict(kv) for s, kv in sections.items()}
    out, current = [], None

    def flush(sec):
        for k, v in todo.get(sec, {}).items():
            out.append(f"{k} = {v}")
        todo.pop(sec, None)

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if current is not None:
                # drop trailing blank lines so new keys stay inside the section
                blanks = 0
                while out and not out[-1].strip():
                    out.pop()
                    blanks += 1
                flush(current)
                out.extend([""] * blanks)
            current = s[1:-1]
            out.append(line)
            continue
        m = re.match(r"^\s*([^=;#]+?)\s*=", line)
        if current in todo and m and m.group(1) in todo[current]:
            key = m.group(1)
            out.append(f"{key} = {todo[current].pop(key)}")
        else:
            out.append(line)
    if current is not None:
        flush(current)
    for sec, kv in todo.items():
        if out and out[-1].strip():
            out.append("")
        out.append(f"[{sec}]")
        for k, v in kv.items():
            out.append(f"{k} = {v}")
    return "\n".join(out) + "\n"


def edit_remote_file(remote, transform):
    """Pull a file (or start empty), transform its text, push it back."""
    with tempfile.TemporaryDirectory() as td:
        local = Path(td) / posixpath.basename(remote)
        text = ""
        if remote_exists(remote):
            adb("pull", remote, str(local))
            text = local.read_text(encoding="utf-8", errors="replace")
            shell(f'cp "{remote}" "{remote}.bak"', check=False)
        else:
            shell(f'mkdir -p "{posixpath.dirname(remote)}"')
        local.write_text(transform(text), encoding="utf-8", newline="\n")
        adb("push", str(local), remote)


# =====================================================================
#  Device
# =====================================================================

def list_devices():
    """[(serial, state)] from 'adb devices'."""
    out = subprocess.run([ARGS.adb, "devices"], capture_output=True, text=True).stdout
    return [tuple(l.split()[:2]) for l in out.splitlines()[1:] if len(l.split()) >= 2]


def adb_available():
    return bool(shutil.which(ARGS.adb) or Path(ARGS.adb).exists())


def detect_device():
    """Fill STATE with device info. Returns None, or a message saying what's wrong."""
    devices = list_devices()
    if ARGS.serial:
        devices = [d for d in devices if d[0] == ARGS.serial]
    if not devices:
        return ("No device found. On the Thor: Settings > About device > tap 'Build number' "
                "7 times, then Developer options > USB debugging ON. Plug it in and accept "
                "the prompt.")
    if any(state == "unauthorized" for _, state in devices):
        return "The Thor shows as 'unauthorized': unlock it and tap Allow on the USB debugging prompt."
    if len(devices) > 1:
        return "MULTIPLE"
    out = shell("getprop ro.product.brand; getprop ro.product.model; "
                "getprop ro.build.version.release; getprop ro.build.version.sdk; "
                "echo ---; ls /storage", check=False)
    head, _, storage = out.partition("---")
    lines = (head.splitlines() + ["", "", "", ""])[:4]
    brand, model, release, sdk = (l.strip() for l in lines)
    if sdk.isdigit():
        STATE["sdk"] = int(sdk)
    STATE["device"] = {"brand": brand, "model": model, "android": release,
                       "serial": devices[0][0]}
    sd = next((f"/storage/{n}" for n in storage.split()
               if re.fullmatch(r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}", n)), None)
    STATE["sd"] = sd
    return None


def storage_root():
    return STATE["sd"] or "/storage/emulated/0"


def free_space(path):
    out = shell(f"df -k {shlex.quote(path)} 2>/dev/null | tail -n 1", check=False).split()
    try:
        return int(out[3]) * 1024, int(out[1]) * 1024
    except (IndexError, ValueError):
        return None


def home_app():
    out = shell("cmd package resolve-activity --brief "
                "-a android.intent.action.MAIN -c android.intent.category.HOME", check=False)
    comp = (out.splitlines() or [""])[-1].strip()
    pkg = comp.split("/")[0] if "/" in comp else ""
    return "" if pkg == "android" else pkg   # "android" = the "choose an app" prompt


def lock_screen_off():
    return shell("locksettings get-disabled", check=False).strip() == "true"


# =====================================================================
#  Actions
# =====================================================================

def local_apk(app):
    files = sorted(APK_DIR.glob(f"{app.key}-*.apk")) if APK_DIR.exists() else []
    return files[-1] if files else None


def fetch_apk(app, rel=None):
    """Make sure the newest APK for an app is in apks/. Returns its path, or None."""
    APK_DIR.mkdir(exist_ok=True)
    try:
        rel = rel or app.latest()
        STATE["latest"][app.key] = rel.version
    except Exception as e:  # noqa: BLE001
        have = local_apk(app)
        if have:
            say(f"    couldn't check online ({e}); using {have.name}")
            return have
        say(c(f"    couldn't fetch automatically ({e}).", "red"))
        say(f"    Get it here: {app.page}  - put the APK in apks/ named {app.key}-<anything>.apk")
        return None
    dest = APK_DIR / f"{app.key}-{rel.filename}"
    if dest.exists():
        say(f"    newest version ({rel.version}) already downloaded")
    else:
        say(f"    newest version is {rel.version}")
        try:
            download(rel.url, dest)
        except Exception as e:  # noqa: BLE001
            say(c(f"    download failed ({e}). Get it here: {app.page}", "red"))
            return local_apk(app)
    for old in APK_DIR.glob(f"{app.key}-*.apk"):   # don't keep older copies around
        if old != dest:
            old.unlink()
            say(f"    removed older {old.name}")
    return dest


def grant_storage(pkg):
    shell(f"appops set {pkg} MANAGE_EXTERNAL_STORAGE allow", check=False)


def install_apk(path, label=None):
    """adb install with plain-English handling of common failures. Returns package or None."""
    label = label or path.name
    pkg_in_apk, ver_in_apk = apk_info(path)
    before = installed_packages()
    say(f"  Installing {label}" + (f" ({ver_in_apk})" if ver_in_apk else "") + " ...")
    res = subprocess.run(adb_cmd("install", "-r", "-g", str(path)),
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (res.stdout + res.stderr).strip()
    if res.returncode != 0 or "Failure" in out:
        if "UPDATE_INCOMPATIBLE" in out and pkg_in_apk:
            say(c("    A different build of this app is already installed (it was signed "
                  "by someone else,", "yellow"))
            say(c("    e.g. a Play Store or pre-installed copy). It has to be removed first.",
                  "yellow"))
            say("    Removing it deletes that copy's internal data; saves and games on the "
                "SD card are kept.")
            if confirm("    Remove the existing copy and install this one?", default=False):
                shell(f"pm uninstall {pkg_in_apk}", check=False)
                return install_apk(path, label)
            return None
        if "VERSION_DOWNGRADE" in out:
            say(c("    The installed copy is newer than this APK - leaving it alone.", "yellow"))
            return pkg_in_apk
        say(c(f"    FAILED: {out.splitlines()[-1] if out else 'unknown error'}", "red"))
        return None
    say(c("    installed", "green"))
    new = installed_packages() - before
    pkg = pkg_in_apk or (next(iter(new)) if len(new) == 1 else None)
    if pkg:
        grant_storage(pkg)
    return pkg


def install_app(app, offer_config=True):
    say(c(f"\n  {app.name}", "bold"))
    path = fetch_apk(app)
    if not path:
        return False
    pkg = install_apk(path, app.name)
    if not pkg:
        return False
    save_package(app.key, pkg)
    if offer_config and app.configure and confirm(
            f"  Apply the guide's {app.name} settings now?", default=True):
        CONFIGURE[app.configure]()
    return True


def other_local_apks():
    """APKs in apks/ that aren't one of the managed apps."""
    if not APK_DIR.exists():
        return []
    return sorted(p for p in APK_DIR.glob("*.apk")
                  if p.name.split("-", 1)[0].lower() not in APP_BY_KEY)


def other_emulators(pkgs=None):
    pkgs = installed_packages() if pkgs is None else pkgs
    return sorted(p for p in pkgs if EMU_PATTERN.search(p) and not PROTECTED.match(p))


def remove_package(pkg, label):
    say(f"  Removing {label} ({pkg}) ...")
    out = shell(f"pm uninstall -k --user 0 {pkg}", check=False)
    ok = "Success" in out
    say(c("    removed", "green") if ok else c(f"    {out or 'failed'}", "red"))
    return ok


def removed_packages():
    """Emulators/apps removed for this user that can be restored."""
    everything = shell("pm list packages -u", check=False)
    allp = {l.split(":", 1)[1].strip() for l in everything.splitlines() if ":" in l}
    gone = allp - installed_packages()
    return sorted(p for p in gone
                  if EMU_PATTERN.search(p) or any(a.pattern.search(p) for a in APPS))


def restore_package(pkg):
    out = shell(f"cmd package install-existing {pkg}", check=False)
    say(c(f"  restored {pkg}", "green") if "installed" in out.lower()
        else c(f"  {out or 'failed'}", "red"))


def local_files(root):
    """Every file under root (sub-folders kept), skipping hidden files like .keep."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for f in sorted(filenames):
            if not f.startswith("."):
                p = Path(dirpath) / f
                out.append((p.relative_to(root).as_posix(), p))
    return out


def remote_sizes(remote_dir):
    """{relative path: size} for files already on the device under remote_dir."""
    out = shell(f"cd {shlex.quote(remote_dir)} 2>/dev/null && "
                "find . -type f -exec stat -c '%s %n' {} + 2>/dev/null", check=False)
    sizes = {}
    for line in out.splitlines():
        size, _, name = line.partition(" ")
        if size.isdigit() and name.startswith("./"):
            sizes[name[2:]] = int(size)
    return sizes


def push_tree(local_root, remote_root, label):
    """Copy a folder file by file, showing each file, and skip files already copied."""
    files = local_files(local_root)
    if not files:
        return 0, 0
    on_device = remote_sizes(remote_root)
    todo = [(rel, p, p.stat().st_size) for rel, p in files
            if on_device.get(rel) != p.stat().st_size]
    skipped = len(files) - len(todo)
    total = sum(s for _, _, s in todo)
    say(f"  {label} -> {remote_root}: {len(todo)} to copy ({human_size(total)})"
        + (f", {skipped} already on the card" if skipped else ""))

    made, done, failed = set(), 0, 0
    for i, (rel, p, size) in enumerate(todo, 1):
        remote = f"{remote_root}/{rel}"
        parent = posixpath.dirname(remote)
        if parent not in made:
            shell(f"mkdir -p {shlex.quote(parent)}", check=False)
            made.add(parent)
        say(f"    [{i}/{len(todo)}] {rel}  ({human_size(size)})  "
            f"- {human_size(done)} of {human_size(total)} done")
        # adb's own output isn't captured, so its live percentage shows on big files.
        if subprocess.run(adb_cmd("push", str(p), remote)).returncode != 0:
            failed += 1
            say(c(f"      !! failed to copy {rel} - copy again to retry", "red"))
        done += size
    return len(todo), failed


def rom_summary():
    """[(system, file count, bytes)] for the local roms/ folder."""
    if not ROMS_DIR.exists():
        return []
    out = []
    for d in sorted(ROMS_DIR.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            files = local_files(d)
            if files:
                out.append((d.name, len(files), sum(p.stat().st_size for _, p in files)))
    return out


def copy_games():
    sd = storage_root()
    if BIOS_DIR.exists() and local_files(BIOS_DIR):
        push_tree(BIOS_DIR, f"{sd}/BIOS", "bios")
    else:
        say(f"  No files in {BIOS_DIR} (BIOS files, PS2 BIOS, Switch keys go here).")
    systems = rom_summary()
    if not systems:
        say(f"  No games in {ROMS_DIR} (use iiSU folder names, e.g. roms/snes, roms/ps2).")
        return
    failed = 0
    for name, _, _ in systems:
        failed += push_tree(ROMS_DIR / name, f"{sd}/ROMs/{name}", f"roms/{name}")[1]
    if failed:
        say(c(f"  {failed} file(s) failed. Copy again to retry just those.", "red"))
    say("  Tip: for big libraries, copying with an SD card reader on your PC is much faster.")


def make_folders():
    sd = storage_root()
    for d in ["ROMs", "BIOS", "iiSU", "saves", "states"]:
        shell(f'mkdir -p "{sd}/{d}"')
    # iiSU recognises consoles by folder name, so create them up front.
    shell("cd " + shlex.quote(f"{sd}/ROMs") + " && mkdir -p " + " ".join(COMMON_SYSTEMS),
          check=False)
    say(f"  Created ROMs (with a folder per system), BIOS, iiSU, saves and states in {sd}")


def configure_retroarch():
    pkg = find_pkg("com.retroarch.aarch64", "com.retroarch", pattern=r"retroarch")
    if not pkg:
        say("  RetroArch isn't installed.")
        return
    cfg = f"/storage/emulated/0/Android/data/{pkg}/files/retroarch.cfg"
    if not remote_exists(cfg):
        launch(pkg)
        pause("RetroArch just opened on the Thor. Grant storage permission if asked, wait "
              "for it to finish first-time setup, then Main Menu > Quit RetroArch.")
    shell(f"am force-stop {pkg}", check=False)
    if not remote_exists(cfg):
        say(f"  Couldn't find {cfg} - open RetroArch once on the Thor, then try again.")
        return
    sd = storage_root()
    settings = dict(RETROARCH_SETTINGS)
    settings.update({
        "system_directory": f"{sd}/BIOS",
        "savefile_directory": f"{sd}/saves",
        "savestate_directory": f"{sd}/states",
    })
    edit_remote_file(cfg, lambda t: merge_retroarch_cfg(t, settings))
    say(c(f"  Wrote {len(settings)} settings to retroarch.cfg (backup: retroarch.cfg.bak)",
          "green"))
    say("  Hotkeys: Select = hotkey, +A menu, +Start quit, +R1/L1 save/load,")
    say("           +R2 fast-forward, +L2 slow-mo, +Y screenshot, L3+R3 menu.")


def configure_dolphin():
    pkg = find_pkg("org.dolphinemu.dolphinemu", pattern=r"dolphin")
    if not pkg:
        say("  Dolphin isn't installed.")
        return
    base = f"/storage/emulated/0/Android/data/{pkg}/files/Config"
    if not remote_exists(f"{base}/Dolphin.ini"):
        launch(pkg)
        pause("Dolphin just opened. Let it finish starting (grant permissions), then close it.")
    shell(f"am force-stop {pkg}", check=False)
    edit_remote_file(f"{base}/Dolphin.ini", lambda t: merge_ini(t, DOLPHIN_INI))
    edit_remote_file(f"{base}/GFX.ini", lambda t: merge_ini(t, DOLPHIN_GFX_INI))
    say(c("  Set: Vulkan, 3x internal res, Hybrid Ubershaders, compile shaders first,", "green"))
    say(c("       16:9 + widescreen hack. (Drop to 2x or OpenGL if a game misbehaves.)", "green"))


CONFIGURE = {"retroarch": configure_retroarch, "dolphin": configure_dolphin}


def iisu_first_run():
    if IISU_PKG not in installed_packages():
        say("  iiSU isn't installed.")
        return
    sd = storage_root()
    launch(IISU_PKG)
    pause(
        "On the Thor, iiSU just opened. Go through its setup:\n"
        "      - Allow the permissions it asks for (files / all-files access)\n"
        f"      - ROM folder             -> choose  {sd}/ROMs\n"
        f"      - Media storage location -> choose  {sd}/iiSU\n"
        "      - Let it detect installed emulators and scan your games"
    )


def home_candidates():
    out = shell("cmd package query-activities --brief "
                "-a android.intent.action.MAIN -c android.intent.category.HOME", check=False)
    return [l.strip() for l in out.splitlines()
            if "/" in l and not l.strip().startswith(("priority", "android/"))]


def set_home(pkg):
    comp = next((cmp for cmp in home_candidates() if cmp.startswith(pkg + "/")), None)
    if not comp:
        say(f"  {pkg} can't be a home screen. Set it in Settings > Default Home App.")
        return False
    res = shell(f"cmd package set-home-activity {comp}", check=False)
    say(c(f"  Home screen set to {pkg}", "green") if "Error" not in res
        else c(f"  {res}", "red"))
    return "Error" not in res


def set_lock_screen(disabled):
    out = shell(f"locksettings set-disabled {'true' if disabled else 'false'}", check=False)
    say(f"  Lock screen {'off' if disabled else 'on'}. {out}".rstrip())
    if disabled:
        say("  (If you use a PIN or pattern, set Screen lock to None in Settings instead.)")


CHECKLIST = """
  iiSU (home screen)
    [ ] Settings > check each console uses the right emulator:
          ps2 -> ARMSX2      switch -> Eden       n3ds -> Azahar
          gc / wii -> Dolphin psp -> PPSSPP       everything else -> RetroArch
    [ ] If ARMSX2 shows up as an Android app instead of an emulator, pick it
        by hand for PS2 (known iiSU issue #417); update iiSU if games won't launch
    [ ] Scrape artwork for your library

  RetroArch
    [ ] Main Menu > Online Updater > Core Downloader: download your cores
    [ ] Settings > User Interface > Appearance: pick a Material UI color theme
    [ ] Test Select+A (menu). If D-pad hotkeys don't fire, edit DPAD in this script

  Dolphin
    [ ] Add games: ROMs/gc (and ROMs/wii)
    [ ] Config > General > enable Save States
    [ ] GameCube Input > Controller 1: map buttons
    [ ] In-game: Overlay Controls > Toggle Controls > toggle all off

  ARMSX2 (PS2)
    [ ] First run: pick the BIOS from {sd}/BIOS (a USA one), game folder = ROMs/ps2
    [ ] Controller Port 1: automatic mapping; hide the touchscreen controls
    [ ] Graphics: Vulkan, upscale 3x, widescreen patches on

  Eden (Switch)
    [ ] Install keys: prod.keys (+ title.keys) from {sd}/BIOS
    [ ] Install firmware (dumped from your Switch); add games folder ROMs/switch
    [ ] Graphics: Vulkan; try Turnip GPU drivers if a game has glitches

  hakuX (original Xbox - not Xbox 360)
    [ ] First launch: choose ROMs/xbox as the games folder (no BIOS needed)
    [ ] Games must be ISO or XISO files - extract any .zip / .7z first
    [ ] Optional: a game's menu > Convert to XISO makes a smaller copy
    [ ] iiSU has no Xbox console yet, so start Xbox games from hakuX itself

  SteamDeck (Steam + Proton for Windows PC games)
    [ ] Open it > "Install Linux runtime" (~790 MB, one time - use Wi-Fi)
    [ ] Press Play; first launch downloads Steam, then sign in to your account
    [ ] Optional: Desktop & apps > install Desktop; point its ROMs folder at {sd}/ROMs
    [ ] iiSU may list it under Android apps rather than as a console - that's expected

  PPSSPP
    [ ] Choose PSP folder, browse to ROMs/psp; render res 4x; touch controls off

  Azahar (3DS) - the Thor's two screens work especially well here
    [ ] User data + Applications folder = ROMs/n3ds
    [ ] Graphics: Vulkan, internal res 3x; map controls (L3 = swap, R3 = layout)
    [ ] Layout: try the dual-screen / secondary-display layout
"""


def show_checklist():
    say(CHECKLIST.format(sd=STATE["sd"] or "<SD card>"))


# =====================================================================
#  One-shot steps (used by --only / --skip / --download and Full setup)
# =====================================================================

def step_check():
    header("1. Checking the device")
    if not adb_available():
        sys.exit("adb not found. Install Android platform-tools and put adb on your PATH "
                 "(or pass --adb /path/to/adb).")
    problem = detect_device()
    if problem == "MULTIPLE":
        sys.exit("More than one device connected. Pass --serial <id> (see 'adb devices').")
    if problem:
        sys.exit(problem)
    dev = STATE["device"]
    say(f"  Device: {dev['brand']} {dev['model']} (Android {dev['android']}, API {STATE['sdk']})")
    if "thor" not in f"{dev['brand']} {dev['model']}".lower():
        say("  Note: this doesn't look like an AYN Thor. The script still works on most")
        say("  Android handhelds, but the tuned settings assume a Snapdragon 8 Gen 2.")
    if STATE["sd"]:
        say(f"  SD card found: {STATE['sd']}")
    else:
        say("  No SD card found - everything will go to internal storage (/sdcard).")
        if not confirm("  Continue with internal storage?", default=True):
            sys.exit(0)


def step_remove():
    header("2. Removing pre-installed emulators")
    pkgs = other_emulators()
    if not pkgs:
        say("  No pre-installed emulators found.")
        return
    say("  Found these emulator packages:")
    for p in pkgs:
        say(f"    - {p}")
    say("  They're removed for your user only and can be restored later from the menu")
    say("  (Remove apps > Restore) or with: adb shell cmd package install-existing <package>")
    if ARGS.yes and not ARGS.remove_preinstalled:
        say("  (--yes without --remove-preinstalled: leaving them alone)")
        return
    for p in pkgs:
        if confirm(f"  Remove {p}?", default=True):
            remove_package(p, p)


def step_download():
    header("3. Downloading APKs")
    if not ARGS.download:
        say("  Skipped (pass --download to fetch APKs automatically).")
        say(f"  Otherwise put your APK files in: {APK_DIR}")
        return
    for app in APPS:
        say(f"  {app.name}:")
        fetch_apk(app)
    say("\n  Eden needs your own prod.keys/title.keys + firmware; ARMSX2 needs a PS2 BIOS.")


def step_install():
    header("4. Installing APKs")
    apks = sorted(APK_DIR.glob("*.apk")) if APK_DIR.exists() else []
    if not apks:
        say(f"  No APKs in {APK_DIR} - nothing to install.")
        return
    for apk in apks:
        pkg = install_apk(apk)
        key = apk.name.split("-", 1)[0].lower()
        if pkg and key in APP_BY_KEY:
            save_package(key, pkg)
    for p in other_emulators() + [IISU_PKG]:
        grant_storage(p)
    if IISU_PKG not in installed_packages():
        say("\n  iiSU isn't installed yet. Run with --download, or put its APK in apks/ "
            "and re-run with --only install.")


def step_folders():
    header("5. Creating folders on the SD card")
    make_folders()


def step_push():
    header("6. Copying BIOS files and ROMs")
    copy_games()


def step_retroarch():
    header("7. Configuring RetroArch")
    configure_retroarch()


def step_dolphin():
    header("8. Configuring Dolphin (GameCube / Wii)")
    configure_dolphin()


def step_iisu_first_run():
    header("9. iiSU first run (you'll need to tap through a few screens)")
    iisu_first_run()


def step_launcher():
    header("10. Lock screen + home launcher")
    if confirm("  Turn off the lock screen (so waking the Thor goes straight to iiSU)?"):
        set_lock_screen(True)
    if IISU_PKG not in installed_packages():
        say("  iiSU not installed - can't set it as the home app.")
        return
    if confirm("  Make iiSU the home launcher?"):
        set_home(IISU_PKG)
        say("  Press Home on the Thor to check. To undo: Settings > Default Home App.")


def step_checklist():
    header("Done with the automatic part - finish these on the Thor")
    show_checklist()


STEPS = [
    ("check", step_check),
    ("remove", step_remove),
    ("download", step_download),
    ("install", step_install),
    ("folders", step_folders),
    ("push", step_push),
    ("retroarch", step_retroarch),
    ("dolphin", step_dolphin),
    ("iisu", step_iisu_first_run),
    ("launcher", step_launcher),
    ("checklist", step_checklist),
]


def run_steps():
    for name, fn in STEPS:
        if ARGS.only and name not in ARGS.only and name != "check":
            continue
        if name in ARGS.skip and name != "check":
            continue
        fn()


# =====================================================================
#  Menu (KIAUH-style)
# =====================================================================

W = 74  # total box width


def vlen(s):
    return len(ANSI.sub("", s))


def fit(s, width):
    """Pad (or trim) a possibly-coloured string to a visible width."""
    if vlen(s) > width:
        s = ANSI.sub("", s)[:width - 1] + "~"
    return s + " " * (width - vlen(s))


def box_line(text=""):
    say(c("║", "cyan") + " " + fit(text, W - 4) + " " + c("║", "cyan"))


def box_rule(kind="mid"):
    left, fill, right = {"top": ("╔", "═", "╗"), "mid": ("╟", "─", "╢"),
                         "sep": ("╠", "═", "╣"), "bot": ("╚", "═", "╝")}[kind]
    say(c(left + fill * (W - 2) + right, "cyan"))


def box_title(text):
    box_rule("top")
    pad = (W - 4 - vlen(text)) // 2
    box_line(" " * pad + c(text, "title"))
    box_rule("sep")


def two_cols(left, right, col=14):
    return fit(left, col) + right


def app_rows(pkgs=None):
    """[(app, package, installed version)] for every managed app."""
    mapping = app_packages(pkgs)
    versions = package_versions([p for p in mapping.values() if p])
    return [(a, mapping[a.key], versions.get(mapping[a.key], "") if mapping[a.key] else "")
            for a in APPS]


def status_label(installed_ver, latest):
    if latest is None:
        return ""
    newer = is_newer(latest, installed_ver) if installed_ver else None
    if not installed_ver:
        return c(latest, "dim")
    if newer:
        return c(f"{latest}  update", "yellow")
    if newer is False:
        return c("up to date", "green")
    return c(f"{latest} ?", "dim")


def draw_status(rows):
    dev = STATE["device"]
    box_line(two_cols(c("Device", "bold"),
                      f"{dev['brand']} {dev['model']} - Android {dev['android']}"))
    if STATE["sd"]:
        space = free_space(STATE["sd"])
        extra = f" - {human_size(space[0])} free of {human_size(space[1])}" if space else ""
        box_line(two_cols(c("SD card", "bold"), STATE["sd"] + extra))
    else:
        box_line(two_cols(c("SD card", "bold"), c("none found - using internal storage", "yellow")))
    home = home_app()
    home_name = "iiSU" if home == IISU_PKG else (home or "not set (Android asks)")
    home_txt = c(home_name, "green" if home == IISU_PKG else "yellow")
    lock_txt = c("off", "green") if lock_screen_off() else "on"
    box_line(two_cols(c("Home screen", "bold"), home_txt))
    box_line(two_cols(c("Lock screen", "bold"), lock_txt))
    box_rule()
    box_line(c(fit("App", 11) + fit("For", 18) + fit("Installed", 18) + "Latest", "bold"))
    for app, pkg, ver in rows:
        inst = c(ver or "installed", "green") if pkg else c("not installed", "red")
        latest = STATE["latest"].get(app.key)
        box_line(fit(app.name, 11) + fit(app.purpose, 18) + fit(inst, 18)
                 + status_label(ver, latest))
    if not STATE["latest"]:
        box_line(c("Latest versions show after 'Update apps' checks online.", "dim"))


def menu_prompt(options):
    """options: {key: label}. Returns the chosen key (lower-case)."""
    while True:
        choice = input(c("\n  Choose an option: ", "bold")).strip().lower()
        if choice in options:
            return choice
        say(c("  Please choose one of: " + ", ".join(k.upper() for k in options), "yellow"))


def pick_numbers(text, count):
    """'1,3-5' -> [0, 2, 3, 4]. Returns None if the input isn't a list of numbers."""
    out = []
    for part in text.replace(" ", "").split(","):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            return None
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        for n in range(min(a, b), max(a, b) + 1):
            if not 1 <= n <= count:
                return None
            out.append(n - 1)
    return sorted(set(out))


def back_to_menu():
    input(c("\n  Press Enter to return to the menu... ", "dim"))


def menu_install():
    while True:
        clear()
        box_title("Install apps")
        rows = app_rows()
        for i, (app, pkg, ver) in enumerate(rows, 1):
            state = c(f"installed {ver}", "green") if pkg else c("not installed", "red")
            box_line(f"{i:>2}) " + fit(app.name, 11) + fit(app.purpose, 18) + state)
        others = other_local_apks()
        box_rule()
        box_line(" A) Install everything that's missing")
        if others:
            box_line(f" L) Install the {len(others)} other APK(s) in the apks folder")
        box_line(" B) Back")
        box_rule("bot")
        say(c("  Tip: enter one number, or several like 1,4,6 or 2-5.", "dim"))
        choice = input(c("\n  Choose an option: ", "bold")).strip().lower()
        if choice == "b":
            return
        if choice == "a":
            todo = [app for app, pkg, _ in rows if not pkg]
            if not todo:
                say(c("  Everything is already installed.", "green"))
        elif choice == "l" and others:
            for apk in others:
                install_apk(apk)
            back_to_menu()
            continue
        else:
            idx = pick_numbers(choice, len(rows))
            if idx is None:
                say(c("  Not a valid choice.", "yellow"))
                back_to_menu()
                continue
            todo = [rows[i][0] for i in idx]
        for app in todo:
            install_app(app)
        back_to_menu()


def check_updates(rows):
    say(c("\n  Checking for newer versions online...", "dim"))
    for app, pkg, _ in rows:
        if not pkg:
            continue
        try:
            STATE["latest"][app.key] = app.latest().version
            say(f"    {app.name}: {STATE['latest'][app.key]}")
        except Exception as e:  # noqa: BLE001
            say(c(f"    {app.name}: couldn't check ({e})", "yellow"))


def menu_update():
    rows = app_rows()
    check_updates(rows)
    while True:
        clear()
        box_title("Update apps")
        box_line(c("    " + fit("App", 12) + fit("Installed", 20) + "Latest", "bold"))
        updatable = []
        listed = [(app, pkg, ver) for app, pkg, ver in rows if pkg]
        for i, (app, pkg, ver) in enumerate(listed, 1):
            latest = STATE["latest"].get(app.key)
            if latest and is_newer(latest, ver):
                updatable.append(app)
            box_line(f"{i:>2}) " + fit(app.name, 12) + fit(ver or "?", 20)
                     + (status_label(ver, latest) if latest else c("couldn't check", "dim")))
        if not listed:
            box_line("No apps installed yet - use Install apps first.")
        box_rule()
        if updatable:
            box_line(c(f" A) Update all {len(updatable)} app(s) with a newer version", "yellow"))
        else:
            box_line(c(" Everything that could be checked is up to date.", "green"))
        box_line(" C) Check again")
        box_line(" B) Back")
        box_rule("bot")
        say(c("  Updating keeps your settings and saves. A '?' means the version numbers", "dim"))
        say(c("  couldn't be compared; you can still update that app by its number.", "dim"))
        choice = input(c("\n  Choose an option: ", "bold")).strip().lower()
        if choice == "b":
            return
        if choice == "c":
            rows = app_rows()
            check_updates(rows)
            continue
        if choice == "a" and updatable:
            todo = updatable
        else:
            idx = pick_numbers(choice, len(listed))
            if idx is None:
                continue
            todo = [listed[i][0] for i in idx]
        for app in todo:
            install_app(app, offer_config=False)
        rows = app_rows()
        back_to_menu()


def menu_remove():
    while True:
        clear()
        box_title("Remove apps")
        pkgs = installed_packages()
        rows = [(a, p) for a, p in app_packages(pkgs).items() if p]
        managed = {p for _, p in rows}
        items = [(APP_BY_KEY[k].name, p) for k, p in rows]
        items += [("Other", p) for p in other_emulators(pkgs) if p not in managed]
        for i, (label, pkg) in enumerate(items, 1):
            box_line(f"{i:>2}) " + fit(label, 14) + c(pkg, "dim"))
        if not items:
            box_line("No emulators or frontends installed.")
        box_rule()
        box_line(" R) Restore an app removed earlier")
        box_line(" B) Back")
        box_rule("bot")
        say(c("  Removed apps can be restored from R. Games and saves on the SD card stay.",
              "dim"))
        choice = input(c("\n  Choose an option: ", "bold")).strip().lower()
        if choice == "b":
            return
        if choice == "r":
            gone = removed_packages()
            if not gone:
                say("  Nothing to restore.")
            else:
                for i, p in enumerate(gone, 1):
                    say(f"   {i:>2}) {p}")
                idx = pick_numbers(input("\n  Restore which (numbers, or Enter to cancel)? "),
                                   len(gone))
                for i in idx or []:
                    restore_package(gone[i])
            back_to_menu()
            continue
        idx = pick_numbers(choice, len(items))
        if idx is None:
            continue
        for i in idx:
            label, pkg = items[i]
            label = pkg if label == "Other" else label
            warn = " iiSU is your home screen; Android will ask for another." \
                if pkg == IISU_PKG else ""
            if confirm(f"  Remove {label}?{warn}", default=False):
                remove_package(pkg, label)
        back_to_menu()


def menu_copy():
    clear()
    box_title("Copy games & BIOS")
    systems = rom_summary()
    bios = local_files(BIOS_DIR) if BIOS_DIR.exists() else []
    box_line(two_cols(c("From your PC", "bold"), str(HERE)))
    box_line(two_cols(c("To the Thor", "bold"), storage_root()))
    box_rule()
    box_line(fit("bios", 14) + f"{len(bios)} file(s)")
    for name, count, size in systems:
        box_line(fit(f"roms/{name}", 14) + fit(f"{count} file(s)", 14) + human_size(size))
    if not systems:
        box_line(c("No games in the roms folder (e.g. roms/ps2, roms/snes).", "yellow"))
    box_rule()
    box_line(" C) Copy (only new or changed files)")
    box_line(" B) Back")
    box_rule("bot")
    if menu_prompt({"c": "", "b": ""}) == "c":
        copy_games()
        back_to_menu()


def menu_configure():
    while True:
        clear()
        box_title("Configure emulators")
        box_line(" 1) RetroArch - the guide's settings and hotkeys")
        box_line(" 2) Dolphin   - Vulkan, 3x resolution, widescreen")
        box_line(" 3) iiSU      - first-run setup (choose ROM and media folders)")
        box_line(" 4) Show the finish-up checklist (things done on the Thor)")
        box_rule()
        box_line(" B) Back")
        box_rule("bot")
        choice = menu_prompt({"1": "", "2": "", "3": "", "4": "", "b": ""})
        if choice == "b":
            return
        {"1": configure_retroarch, "2": configure_dolphin,
         "3": iisu_first_run, "4": show_checklist}[choice]()
        back_to_menu()


def menu_device():
    while True:
        clear()
        box_title("Device settings")
        off = lock_screen_off()
        box_line(" 1) Choose the home screen app")
        box_line(f" 2) Turn the lock screen {'on' if off else 'off'}")
        box_line(" 3) Create the SD card folders (ROMs, BIOS, iiSU, saves, states)")
        box_line(" 4) Switch to a different connected device")
        box_rule()
        box_line(" B) Back")
        box_rule("bot")
        choice = menu_prompt({"1": "", "2": "", "3": "", "4": "", "b": ""})
        if choice == "b":
            return
        if choice == "1":
            homes = home_candidates()
            for i, comp in enumerate(homes, 1):
                pkg = comp.split("/")[0]
                say(f"   {i:>2}) {'iiSU' if pkg == IISU_PKG else pkg}")
            idx = pick_numbers(input("\n  Which one (Enter to cancel)? "), len(homes))
            if idx:
                set_home(homes[idx[0]].split("/")[0])
        elif choice == "2":
            set_lock_screen(not off)
        elif choice == "3":
            make_folders()
        elif choice == "4":
            choose_device(force=True)
        back_to_menu()


def menu_full_setup():
    clear()
    box_title("Full guided setup")
    for line in ("Runs every step in order, asking before each change:",
                 "  remove pre-installed emulators, install every app, create folders,",
                 "  copy games & BIOS, configure RetroArch and Dolphin, iiSU first run,",
                 "  home screen + lock screen, then the finish-up checklist."):
        box_line(line)
    box_rule("bot")
    if not confirm("\n  Start the full setup?", default=True):
        return
    step_remove()
    header("3-4. Installing apps")
    for app in APPS:
        install_app(app, offer_config=False)
    for fn in (step_folders, step_push, step_retroarch, step_dolphin,
               step_iisu_first_run, step_launcher, step_checklist):
        fn()
    back_to_menu()


def choose_device(force=False):
    """Handle zero / several devices. Returns False if the user wants to quit."""
    while True:
        if force:
            ARGS.serial = None
        problem = detect_device()
        if problem == "MULTIPLE" or (force and len(list_devices()) > 1):
            devices = [d for d in list_devices() if d[1] == "device"]
            say("\n  Several devices are connected:")
            for i, (serial, _) in enumerate(devices, 1):
                model = subprocess.run([ARGS.adb, "-s", serial, "shell", "getprop",
                                        "ro.product.model"], capture_output=True,
                                       text=True).stdout.strip()
                say(f"   {i}) {model or '?'}  ({serial})")
            idx = pick_numbers(input("\n  Which one? "), len(devices))
            if idx:
                ARGS.serial = devices[idx[0]][0]
            force = False
            continue
        if problem:
            clear()
            box_title(f"AYN Thor Setup Helper {VERSION}")
            box_line(c("Waiting for the Thor", "yellow"))
            for chunk in re.findall(r".{1,68}(?:\s|$)", problem):
                box_line(chunk.strip())
            box_rule("bot")
            if input("\n  Press Enter to try again, or Q to quit: ").strip().lower() == "q":
                return False
            continue
        return True


def main_menu():
    if not adb_available():
        sys.exit("adb not found. Install Android Platform Tools and put adb on your PATH "
                 "(or pass --adb /path/to/adb). See the README.")
    if not choose_device():
        return
    actions = {"1": menu_install, "2": menu_update, "3": menu_remove, "4": menu_copy,
               "5": menu_configure, "6": menu_device, "7": menu_full_setup,
               "8": lambda: (show_checklist(), back_to_menu())}
    while True:
        clear()
        box_title(f"AYN Thor Setup Helper {VERSION}")
        try:
            draw_status(app_rows())
        except RuntimeError:
            box_line(c("Lost the connection to the Thor.", "red"))
        box_rule("sep")
        box_line(c("Main menu", "bold"))
        box_line(two_cols("  1) Install apps", "5) Configure emulators", 34))
        box_line(two_cols("  2) Update apps", "6) Device settings", 34))
        box_line(two_cols("  3) Remove apps", "7) Full guided setup", 34))
        box_line(two_cols("  4) Copy games & BIOS", "8) Finish-up checklist", 34))
        box_line()
        box_line(two_cols("  R) Refresh", "Q) Quit", 34))
        box_rule("bot")
        choice = menu_prompt(dict.fromkeys(list(actions) + ["r", "q"], ""))
        if choice == "q":
            return
        if choice == "r":
            if not choose_device():
                return
            continue
        try:
            actions[choice]()
        except RuntimeError as e:
            say(c(f"\n  Error: {e}", "red"))
            say("  Check the Thor is still plugged in and unlocked.")
            back_to_menu()
            if not choose_device():
                return


# =====================================================================
#  Entry point
# =====================================================================

def main():
    global ARGS
    ap = argparse.ArgumentParser(
        description="Setup helper for the AYN Thor Pro over ADB. Run with no options "
                    "for the menu.",
        epilog="Created with the assistance of Claude (Anthropic). "
               "https://github.com/pdsavas/Thor-Setup-Script")
    ap.add_argument("--download", action="store_true",
                    help="one-shot mode: fetch APKs from official sources")
    ap.add_argument("--yes", action="store_true",
                    help="one-shot mode: accept defaults, don't wait for input")
    ap.add_argument("--remove-preinstalled", action="store_true",
                    help="with --yes, also remove detected pre-installed emulators")
    ap.add_argument("--only", action="append", default=[], metavar="STEP")
    ap.add_argument("--skip", action="append", default=[], metavar="STEP")
    ap.add_argument("--serial", help="adb device serial if several are connected")
    ap.add_argument("--adb", default="adb", help="path to adb")
    ap.add_argument("--no-color", action="store_true", help="plain text output")
    ap.add_argument("--list-steps", action="store_true")
    ARGS = ap.parse_args()
    setup_console(ARGS.no_color)

    names = [n for n, _ in STEPS]
    if ARGS.list_steps:
        say("Steps: " + ", ".join(names))
        return
    for s in ARGS.only + ARGS.skip:
        if s not in names:
            sys.exit(f"Unknown step '{s}'. Steps: {', '.join(names)}")

    one_shot = ARGS.only or ARGS.skip or ARGS.download or ARGS.yes or ARGS.remove_preinstalled
    if one_shot:
        run_steps()
    else:
        main_menu()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        say("\nStopped.")
    except RuntimeError as e:
        sys.exit(f"\nError: {e}")
