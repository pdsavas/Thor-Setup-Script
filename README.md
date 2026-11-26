# Thor Setup Script

A setup helper for the **AYN Thor Pro** Android handheld that runs on your PC. Plug the Thor in over
USB and use a menu (modelled on [KIAUH](https://github.com/dw-0/kiauh)) to:

- install, update and remove emulators and a few helper apps
- copy your games and BIOS files to the SD card
- apply tuned emulator settings
- set the **iiSU** frontend as the home screen

It started as an automation of the TechDweeb "How to set up an Android handheld" guide and grew from
there. It was built with the help of Claude, Anthropic's AI assistant (see [Credits](#credits)). It also works on most other Android handhelds, but its settings are tuned for the Thor Pro's
Snapdragon 8 Gen 2.

> **Why not an APK?** Android won't let an ordinary app silently install or remove other apps, or
> edit their settings files. A PC connected with USB debugging (ADB) can, so a PC script can
> automate far more.

## What it installs

| App | For | Downloaded from | Build it picks |
|---|---|---|---|
| [iiSU](https://github.com/iisu-network/iiSU) | Frontend / home screen | GitHub releases | latest APK |
| [RetroArch](https://www.retroarch.com) | NES to PS1, N64, GBA and more | buildbot.libretro.com | latest stable, 64-bit |
| [Dolphin](https://dolphin-emu.org) | GameCube / Wii | dolphin-emu.org | latest stable release |
| [PPSSPP](https://www.ppsspp.org) | PSP | ppsspp.org | latest |
| [Azahar](https://github.com/azahar-emu/azahar) | 3DS | GitHub releases | non-Play-Store ("vanilla") APK |
| [ARMSX2](https://github.com/ARMSX2/ARMSX2) | PS2 | GitHub releases | the build for your Android version |
| [Eden](https://eden-emu.dev) | Switch | git.eden-emu.dev | latest stable, Standard APK |
| [hakuX](https://github.com/rfandango/hakuX) | Original Xbox | GitHub releases | latest APK |
| [DroidDeck](https://github.com/Droid-Deck/DroidDeck) | Steam / PC games, SteamOS-style | GitHub releases | latest APK (the plain one, not `-antutu`, `-ludashi` or `-pubg`) |
| [Obtainium](https://github.com/ImranR98/Obtainium) | Update alerts for apps, from their official pages | GitHub releases | latest 64-bit APK (not the `fdroid` flavour) |
| [JamesDSP](https://github.com/timschneeb/RootlessJamesDSP) | System-wide audio effects / EQ | F-Droid | RootlessJamesDSP, F-Droid's current build |

Every app is downloaded fresh from its official source when you install or update it. This
repository doesn't contain any APKs.

> **Look-alike warning:** the official Eden builds are only on eden-emu.dev, and the official
> hakuX builds are only on [rfandango/hakuX's releases](https://github.com/rfandango/hakuX/releases).
> DroidDeck's are only on [Droid-Deck/DroidDeck's releases](https://github.com/Droid-Deck/DroidDeck/releases)
> (its official website is droid-deck.dev; droiddeck.org, droid-deck.com and droiddeck.wiki are
> unofficial). Other GitHub repos with those names are forks, fakes or unrelated apps.

## Requirements

- A Windows or Mac computer with:
  - **Python 3** ([python.org](https://www.python.org/downloads/); on Windows, tick *Add python.exe to PATH*)
  - **Android Platform Tools** ([SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools)),
    with `adb` on your PATH
- The Thor with **USB debugging** on: Settings → About device → tap *Build number* 7 times, then
  Settings → System → Developer options → *USB debugging*
- A microSD card in the Thor (recommended)
- Your own BIOS files, Switch keys and firmware, and game dumps

## Quick start

1. Click **Code → Download ZIP** on this page and unzip it.
2. Put your files in the folders:

   ```
   apks/   optional: extra APKs to install (the script downloads the apps itself)
   bios/   BIOS files, PS2 BIOS (ARMSX2), Switch prod.keys / title.keys (Eden)
   roms/   one folder per system, named the way iiSU expects:
           nes snes n64 gb gbc gba nds n3ds gc wii switch
           psx ps2 psp genesis saturn dreamcast arcade pico8 xbox
   ```

3. Plug in the Thor and double-click **`Thor Setup.bat`** (Windows) or **`Thor Setup.command`**
   (Mac), or run `python thor_setup.py`.
4. The first time, choose **7) Full guided setup**.

**Mac:** the first time, right-click `Thor Setup.command` → **Open** → **Open**. If it says you
don't have permission, run `chmod +x "Thor Setup.command"` in Terminal once, or use
`python3 thor_setup.py` instead.

For full step-by-step instructions, see the
[setup guide (PDF)](docs/AYN%20Thor%20Pro%20Setup%20Script%20Guide.pdf).

## The menu

```
╔════════════════════════════════════════════════════════════════════════╗
║                       AYN Thor Setup Helper 4.3                        ║
╠════════════════════════════════════════════════════════════════════════╣
║ Device        AYN Thor - Android 13                                    ║
║ SD card       /storage/1A2B-3C4D - 409.0 GB free of 476.7 GB           ║
║ Home screen   iiSU                                                     ║
║ Lock screen   off                                                      ║
╟────────────────────────────────────────────────────────────────────────╢
║ App        For               Installed         Latest                  ║
║ iiSU       Frontend / home   0.0.7.3           up to date              ║
║ RetroArch  Retro consoles    1.18.0            1.22.2  update          ║
║ hakuX      Original Xbox     1.3.0             up to date              ║
║ ...                                                                    ║
╠════════════════════════════════════════════════════════════════════════╣
║ Main menu                                                              ║
║   1) Install apps                 5) Configure emulators               ║
║   2) Update apps                  6) Device settings                   ║
║   3) Remove apps                  7) Full guided setup                 ║
║   4) Copy games & BIOS            8) Finish-up checklist               ║
║   R) Refresh                      Q) Quit                              ║
╚════════════════════════════════════════════════════════════════════════╝
```

| Option | What it does |
|---|---|
| 1) Install apps | Install apps by number (`1,4,6` or `2-5`), or **A** for everything missing. **L** installs other APKs in `apks/` |
| 2) Update apps | Checks each app online, shows installed vs latest, and **A** updates everything out of date. Settings and saves are kept |
| 3) Remove apps | Removes any emulator, including pre-installed ones. **R** restores a removed app |
| 4) Copy games & BIOS | Copies only new or changed files, listing each one with progress |
| 5) Configure emulators | Applies the RetroArch settings and hotkeys, or the Dolphin settings, or runs iiSU's first-run setup |
| 6) Device settings | Choose the home screen app, turn the lock screen on or off, create the SD card folders, or switch devices |
| 7) Full guided setup | Everything above, start to finish, asking before each change |
| 8) Finish-up checklist | The steps Android only lets you do by hand on the device |

### Command-line mode

The original one-shot options still work, and passing any of them skips the menu:

| Option | What it does |
|---|---|
| `--download` | Full setup, start to finish, downloading the newest apps |
| `--only push` | Runs one step (repeatable). `--list-steps` shows them all |
| `--skip remove` | Skips a step |
| `--yes` | Accepts the defaults without asking (add `--remove-preinstalled` to also remove pre-installed emulators) |
| `--serial ID` | Picks a device when several are connected (works with the menu too) |
| `--adb PATH` | Uses an `adb` that isn't on your PATH |
| `--no-color` | Plain text output |

## What's automated and what isn't

| Step | Automated? |
|---|---|
| Remove pre-installed emulators | ✅ reversible |
| Download, install and update apps | ✅ with storage permission granted |
| SD card folders (ROMs per system, BIOS, iiSU, saves, states) | ✅ |
| Copy BIOS and games | ✅ only new or changed files |
| RetroArch: the guide's UI, input, hotkey and save settings | ✅ written to `retroarch.cfg` (original kept as `.bak`) |
| Dolphin: Vulkan, 3x resolution, Hybrid Ubershaders, widescreen | ✅ |
| iiSU as the home screen, lock screen off | ✅ |
| iiSU first run (choose ROM and media folders) | ⚠️ guided; Android requires taps on the device |
| Cores, controller mapping, PS2 BIOS, Switch keys and firmware, Steam sign-in | ❌ printed as a checklist |

## Notes

- **iiSU** is alpha software. If it lists ARMSX2 as an Android app rather than an emulator
  ([iiSU #417](https://github.com/iisu-network/iiSU/issues/417)), choose it for PS2 by hand.
- **hakuX** emulates the *original* Xbox, not the Xbox 360. It needs no BIOS. Games must be ISO or
  XISO files in `roms/xbox`. iiSU doesn't list original Xbox yet, so start those games from hakuX.
- **DroidDeck** is another way to play Steam and PC games, with a SteamOS-style Gaming Mode and
  a Linux desktop. It replaces SteamDeck, which was renamed DroidDeck; if SteamDeck is still on your
  Thor, you can remove it in *Remove apps*. DroidDeck needs about 4 GB
  free. On Android 14 and newer, first turn on *Disable child process restrictions* in Developer
  options. Then open DroidDeck, install the Linux runtime, press *Play* and sign in to Steam.
- **Obtainium** isn't needed for the Setup Helper, which already updates every app it installs.
  It's useful on the Thor itself: add an app's GitHub releases link and Obtainium tells you when
  there's an update, even when the Thor isn't plugged in to your PC.
- **JamesDSP** is [RootlessJamesDSP](https://github.com/timschneeb/RootlessJamesDSP), the version of
  JamesDSP for devices that aren't rooted.
  [james34602/JamesDSPManager](https://github.com/james34602/JamesDSPManager) needs root (it's a
  Magisk module), so the script doesn't install it. RootlessJamesDSP has no APK on GitHub, so the
  script gets it from [F-Droid](https://f-droid.org/packages/me.timschneeberger.rootlessjamesdsp/).
  The script grants the permissions it would otherwise ask you to set up with a computer. Apps that
  block audio capture, such as Spotify and Chrome, aren't affected by it, and it adds a little audio
  delay. On Android 15 and newer, turn on *Disable screen share protections* in Developer options.
  If you installed it from Google Play, the script offers to replace that copy with F-Droid's,
  because the two are signed differently.
- **Eden** needs your own `prod.keys` and firmware installed from inside Eden.
- If D-pad hotkeys don't work in RetroArch, change `DPAD` near the top of `thor_setup.py` to
  `"19", "20", "21", "22"` and reapply the RetroArch settings.

## Legal

This project doesn't include or download any games, BIOS files, keys or firmware. Only use
files you've dumped from hardware and games you own. Each app it installs is a separate project
under its own license.

## Credits

This project was created by [pdsavas](https://github.com/pdsavas) with the assistance of
[Claude](https://claude.ai), Anthropic's AI assistant. That includes the script, this README and
the setup guide. It started from the TechDweeb Android handheld setup guide. Each version was
tested against a simulated device, and earlier versions were used on its author's AYN Thor Pro.

## License

[MIT](LICENSE)
