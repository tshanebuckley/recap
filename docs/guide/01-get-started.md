# 1. Get started

[← Guide](README.md) · Next: [2. Workspace →](02-workspace.md)

## US-01 Install from a flash drive (Windows)

*As a lab member, I install recap from a flash drive on the lab PC without admin rights or internet.*

1. Copy `recap-<version>-win-64.zip` (from the GitHub release) to a USB drive.
2. On the PC: unzip it anywhere, double-click **Install recap.bat**.
3. When it finishes, a **recap** icon is on the desktop. Nothing else needs the internet.

**Done when:** the installer finished without asking for an administrator, the icon opens the
app, and it works with Wi-Fi off.

**Watch out for:** disk space. recap needs about 12 GB free while installing (about 7 GB once
installed). Unzip to a short path such as `C:\Users\you\recap`; very long paths can fail on
Windows.

## US-02 Install on a Mac

*As a Mac user, I install and open recap the same way.*

1. Unzip `recap-<version>-osx-arm64.zip`.
2. Double-click **Install recap.command**. If macOS blocks it: right-click > Open > Open.
3. Open **recap.app** on the desktop.

**Done when:** the app opens. Only Apple-silicon Macs are supported (Apple menu > About This
Mac says "Apple M…"). Write down every security prompt macOS shows, for the README.

**Developers** install from the repository instead: install [pixi](https://pixi.sh), then
`pixi install` and `pixi run recap gui` in the recap folder (see the main README).

## US-03 Open the app

*As a lab member, I open recap from the desktop icon and always get the same single window.*

Double-click the icon (or `recap gui` in a terminal). It opens the last workspace you used; the
very first time, the example workspace if this copy of recap has one (installs from the
repository do), otherwise it asks you to open a folder. Double-clicking again brings the open
window to the front instead of starting a second copy. The window has the image on the left
and four tabs on the right: Images, Count, Fix outlines, Settings (see the
[guide's overview](README.md#the-recap-window-at-a-glance)).

**Done when:** one window, on your last workspace. If an old icon is left over after moving
recap, run `recap desktop-icon` once: it removes recap's old icons and makes one new one.

## US-04 Tell recap where Fiji is

*As a lab member, I tell recap where Fiji is once, so counting in Fiji just works.*

recap opens Fiji (as its own program) for counting by hand. Fiji is the one thing the installer
does not include: download it from [fiji.sc](https://fiji.sc) and unzip it.

| In the app | In a terminal |
|---|---|
| More > **Fiji location...** > **Choose Fiji folder...** and pick `Fiji.app` | `recap fiji set C:/Users/you/Fiji.app` then `recap fiji check` |

The dialog runs checks (folder layout, ImageJ version, launcher, Fiji's own Java). It saves
only if they pass; it asks before saving one that fails.

**Done when:** the dialog shows your Fiji, and **Count in Fiji** (US-09) opens it.

**Watch out for:** on Windows, forward slashes work in paths (`C:/Users/...`). A workspace can
also name its own Fiji (`validation.fiji` in recap.yaml, relative to the workspace); that wins
over your saved choice. `recap fiji show` says which one is used and why.
