# Orbit

By NovaLabs. Standalone downloads for Linux, Windows, macOS and Android are on the [releases page](../../releases): nothing needs installing first.

One place for all your apps: install them, see at a glance whether each one is running, start and stop it,
change its settings, read its log and update it when a new release is out. It comes with support for
[Pocketfader](https://github.com/dash1101/pocketfader), [Nova](https://github.com/dash1101/Nova) and
[YTM Beacon](https://github.com/dash1101/ytm-beacon), and you can add any other app from Orbit's settings.

Orbit is a small Python program (standard library only) with its own window. It runs on Linux, Windows and
macOS, only listens on `127.0.0.1`, and only asks for the administrator password when an app has to be installed
system-wide (Nova), through your system's own password window or Windows' permission prompt.

## Install

```bash
./install.sh            # adds Orbit to your apps menu and an `orbit` command
orbit                   # opens the window
```

For the native window you need `python3-gi` and `gir1.2-webkit2-4.1` (Debian/Ubuntu). Without them Orbit opens in
a browser window instead. Remove it with `./install.sh --uninstall`.

Run from the source folder without installing: `python3 -m orbit` (on Windows: `py -m orbit`). On Windows and
macOS, Orbit opens as an app window in Edge, Chrome or Brave, or as a tab in your default browser.

| Option | |
|---|---|
| `--dry-run` | Read everything, change nothing: actions and the apps' settings are recorded instead of applied, and installs play out step by step without downloading, writing or running anything. Orbit's own settings (its theme, the order of the apps) are still saved. |
| `--no-window` | Only run the server and print its address. |
| `--port N` | Use another port (default 8790). |

## Installing apps

Open **install** in the side bar (or press `i`). Each app shows the systems it runs on and whether it's installed
here. Installing, updating and removing all go through the same five steps:

1. **check**: what this computer is, what is already installed, the latest release, and anything the app still
   needs (with how to get it);
2. **choose**: a few plain choices, such as starting at login (skipped when there's nothing to choose);
3. **review**: exactly what will happen, before anything does;
4. **install**: each step ticks off as it finishes, with the download's progress; *show details* has the full log;
   you can cancel, or hide the window and let it carry on;
5. **done**: what to do next (open the app, set it up, add its browser extension), or what went wrong and how to fix it.

| | Linux | Windows | macOS |
|---|---|---|---|
| **Pocketfader** | its own Python environment in `~/.local/share/pocketfader`, apps-menu entry, systemd user service | the standalone program in `%LOCALAPPDATA%\Programs\Pocketfader`, Start-menu entry, starts at logon | its own Python environment and a LaunchAgent. The YouTube Music remote works; controlling the Mac's volume and other players isn't available on macOS yet |
| **YTM Beacon** | Beacon's own installer (`ytm-beacon install`): program, browser extension, start at login | same | same |
| **Nova** | the `.deb` on Debian/Ubuntu (so apt keeps it updated), Nova's one-file installer elsewhere; needs systemd | a preview: Nova's Windows installer (`nova-windows.zip`) puts it in `C:\Program Files\Nova` with three background tasks that start with Windows, and its own uninstaller removes it. Pairing, devices, approvals, live graphs, alerts, the task manager, power and Docker Desktop work; programs and updates, files, the terminal and backups aren't there yet | not available |

Only Nova needs the administrator password, and Orbit never sees it: on Linux your desktop's password window asks for
it (`pkexec`), or a terminal with `sudo` when there's no such window; on Windows, Windows asks for permission once and
Nova's installer runs without a window of its own, its output in Orbit's progress log.

**What Orbit checks.** Downloads only come from the app's own GitHub releases. Each file is checked against the
SHA-256 checksums published with the release before it is unpacked or run. Nova's checksums are also checked against
Nova's release signing key, which is built into Orbit rather than downloaded; Orbit checks the Ed25519 signature
itself, so this works the same on every system with nothing else installed. Archives can't write outside their
folder.

**Private repositories** need a GitHub sign-in. Orbit uses, in this order: its own (orbit settings → *github* →
*sign in with GitHub*), `GITHUB_TOKEN`/`GH_TOKEN`, or the GitHub command line's (`gh auth login`). Signing in from
Orbit uses GitHub's device flow: Orbit shows a short code, you enter it at github.com/login/device, and no password
passes through Orbit. The login belongs to the NovaLabs Orbit GitHub App, which can only *read* repository contents.
The token is kept in Orbit's settings file (readable by you only) and only sent to `api.github.com`, never to the file
host a download is redirected to. *check access* shows what the current sign-in can read, repository by repository.

**Removing** keeps your settings unless you tick the box to delete them. An app installed some other way (with pip,
or run from its source folder) is shown but left alone.

## Orbit for Android

`android/` is Orbit for phones and tablets (Android 5.0 and newer). It installs, updates, opens and removes the
Android apps of **Pocketfader** and **Nova**, and updates **Orbit** itself. YTM Beacon has no separate Android app:
its Discord status controls are built into Pocketfader.

Installing works like the desktop installer: review what will happen, then each step ticks off with the download's
progress, then a result. The APK comes from the app's GitHub release and is checked before Android sees it: its
SHA-256 against the release's checksums, Nova's checksums against Nova's release key (built into Orbit, and checked
by Orbit itself, so on every Android version), and that the file really is that app. Android's own installer then asks you to
confirm.

Android only lets an app install others once you allow it ("install unknown apps"). Orbit checks this first and says
so on the review screen, before anything is downloaded, with a button that opens the right Settings page.

A private repository's releases need a GitHub sign-in: *sign in with GitHub* in Orbit's settings on the phone (the
same device flow as the desktop), or a fine-grained token with **Repository access → Only select repositories** and
**Contents: Read-only** (the default, *Public repositories*, can't see private ones). *check access* shows what it can
read. It stays in Orbit's private storage and is only sent to `api.github.com`. The app has the same themes as the
desktop.

Build it with Gradle 8.9 and JDK 17 (`cd android && gradle :app:assembleRelease`), or let the `build` workflow in
`.github/workflows` build it. A `v*` tag publishes a release. The app id is `app.novalabs.orbit`. To install updates over
an earlier copy, give the workflow a signing key as repository secrets: `ORBIT_KEYSTORE_B64`,
`ORBIT_KEYSTORE_PASSWORD` and `ORBIT_KEY_ALIAS`. Without them a release leaves the Android app out.

## The standalone programs

Each download on the releases page is the program itself, with nothing to unpack and nothing else to install: the `.exe`
on Windows, a `.dmg` on macOS (open it and drag Orbit onto Applications), and the program file on Linux (allow it to
run once with `chmod +x`, since browsers don't keep that). On Linux and Windows, *orbit settings → Orbit in your apps
menu* adds a launcher for the copy you downloaded.

**Orbit updates itself** on Linux, Windows and macOS. When a new release is out, the top bar says *update orbit to …*
(and Orbit's own card on the install page offers it). The new program is downloaded from Orbit's GitHub release,
checked against the release's checksums, and started once to make sure it runs and is that version; only then does it
wait next to the running one. Orbit then restarts on it by itself after a few seconds (the window closes and opens
again on the new version), unless an app is still installing; close the update window to wait instead, and Orbit
switches the next time it starts. The old program is kept until the new one has started, so a failed update leaves
Orbit as it was. On a Mac the whole app is replaced: the new Orbit.app comes out of the release's `.dmg`. Running from
the source folder, update with `git pull`.

The update replaces the program **in place**: the downloads are named without the version (`orbit-windows-x64.exe`,
`orbit-linux-x86_64`, `orbit-macos-apple-silicon.dmg`, …), so the program keeps its name and folder from version to
version, and launchers and shortcuts keep working. A copy downloaded before 0.3.5, named with its version
(`orbit-v0.3.4-windows-x64.exe`), takes the plain name at its next update, and Orbit's own apps-menu or Start-menu entry
follows it.

## What each app gets

| | Status | Settings | Actions |
|---|---|---|---|
| **Pocketfader** | running or not, output device, volume, what's playing, YouTube Music extension, EQ, release | *Pause other players*; address and access token | open web app, desktop window, start/stop/restart, start at login |
| **YTM Beacon** | what Discord shows and why, Discord connection, extension last seen, what's playing, release; details: Discord user and app (Discord, Vesktop…), application ID, extension build, version, uptime, files | everything on the Discord card: show/hide, private mode, name in member list, album art, progress, badge, button, when paused, words never to show, and the Discord application ID | dashboard, hide for 30 min / 1 h / 3 h, show now, doctor, check for update, update, extension folders, start/stop/restart, start at login |
| **Nova** | answering or not, version and API level; how the server itself is doing: what needs attention, containers, websites, the fullest drive, the last backup, uptime; every Nova service and timer (the assistant and lighting when set up), failed background jobs, Tailscale and Cloudflare tunnel, release | Nova web address | open Nova web and the Nova Server window (Nova 0.9.16 and newer); status, doctor, recent actions, the API's log; pair a phone, a view-only device or a phone that's away from home, paired devices, what's waiting for approval, approve a browser, remove a device; Tailscale status and sign-in, Cloudflare API token, what Nova changed in Cloudflare and undoing it, setup; check for and install updates, back up now, list backups, restart the API; linked servers, plugins |
| **Your own apps** | the service's state and/or a health address | name, service, health address, web page | open, start/stop/restart, start at login, remove |

**Nova on Windows** (a preview) shows whether Nova answers, its version and API level; its health file and logs are only readable by the administrator there, so *api log* and Nova's commands open in an administrator PowerShell window, and *nova server* opens the Nova Server window from the Start menu.

**Why Nova is mostly read-only here.** Every Nova request except `/ping` must be signed by a paired phone, and risky
ones are approved on it. Orbit doesn't get around that. It shows Nova's health and opens Nova web or a terminal for
`sudo nova …` instead. The server's own health (what needs attention, containers, drives, backups) is read from the
status file Nova's monitor writes for dashboards (`/var/lib/nova-alerts/www/status.json`, readable by anyone on that
computer), so it shows up when Orbit runs on the server itself; if that file stops being updated, Orbit says so
rather than showing old numbers.

**Commands run inside Orbit.** Actions such as `sudo nova status` or `ytm-beacon doctor` open in Orbit's own terminal
(a drawer at the bottom): type straight into it, including `sudo`'s password and any questions; `ctrl+c` stops a
command, and you can hide the drawer while commands keep running. Only the commands an app offers can be run; the page
can't send its own. In a dry run, commands that would change something are shown instead of run.

**The Cloudflare API token** for Nova's auto-setup is saved the way Nova saves it itself (root-only), passed through a
private temporary file that is deleted afterwards, so it never appears on a command line.

**System services** (like Nova's) are never started or stopped directly by Orbit; their actions go through `sudo` in
Orbit's terminal, so you approve each one with your password. On Windows, Nova's commands open in an administrator
PowerShell window instead (Windows asks for permission each time); what you type for them, such as a device's name,
travels encoded, so it can never be read as another command.

## Themes

Orbit's look is a theme. All of the first ones are text-interface themes: square boxes with the title set into the
border, monospace type and a small square for each app's status. Pick one in **orbit settings**, or press `t` to cycle.

- **dusk** (default): charcoal with a pink accent
- **phosphor**: green screen
- **amber**: amber screen
- **paper**: light
- **frost**, **ember**, **ocean**, **lilac**, **mono**

**Your own themes:** *+ new theme* (or *copy & edit* on any theme) opens an editor with every colour, the corner
rounding and glow, and the whole window previews your changes as you make them. Custom themes are saved in Orbit's
settings.

A theme is a block of colour and shape tokens in `orbit/static/index.html` (`[data-theme="…"]`), plus an entry in
`THEMES` in `orbit/server.py`.

## Keys

`j`/`k` or `↓`/`↑` move · `1`–`9` jump to an app · `o` overview · `i` install · `,` settings · `r` refresh · `t` next theme · `?` the list of keys

In the installer: `Enter` continues, `Esc` closes (an install keeps going in the background).

Drag an app's tile in the side bar to reorder your apps. Apps that don't run on this computer's system stay in the
list, marked "not available on … yet".

## Adding an app to Orbit

- **Without code:** orbit settings → *add an app*, with a systemd service, a health address (any page that answers
  while the app is up) and/or a web page.
- **Built in:** write a subclass of `orbit.apps.base.App` (see `pocketfader.py`, the simplest full one) and list it in
  `BUILTIN` in `orbit/apps/__init__.py`. An app answers four things: `status()`, `settings()` (fields the window draws
  for you), `actions()` and `logs()`.
- **Installable:** add a recipe in `orbit/install/recipes/` (see `beacon.py`, the simplest) with the same `id` and list
  it in `ALL`. A recipe says which systems the app runs on, how to tell it's installed, what the computer needs, the
  choices to offer, and the steps; the engine does the downloading, checking, progress and dry run.

## Privacy and safety

- Orbit's server listens on `127.0.0.1` only. Requests must use a loopback host name, come from Orbit's own page (or no
  page at all) and send JSON, so websites you visit can't use it.
- It talks to your apps on this computer only. The exceptions are the release check (at most every 12 hours, one
  request per app to `api.github.com`), which you can turn off, and installing or updating an app, which downloads
  that app's release from GitHub.
- Settings are kept in `~/.config/orbit/config.json` (`%APPDATA%\Orbit\config.json` on Windows), readable by you only.

## Tests

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -t .
```

The tests use fakes and small stub servers, so they never touch real services. The installer tests run real
downloads, checksum and signature checks against a stub GitHub, with fake `pkexec`, `apt-get`, `python3` and
`systemctl` in a temporary home folder, and the Windows and macOS installs as dry runs.

The tests run on Linux, Windows and macOS. Two more checks run on real machines in CI: `tools/ci-nova-windows.py`
installs the newest Nova, Pocketfader and YTM Beacon through the built `orbit.exe` on GitHub's Windows machines,
checks each one answers, and removes them again (and installs Nova over itself through Orbit's Python code); and
`tools/ci-selfupdate.py` has a real standalone Orbit update itself to a newer build and restart on it, on Linux,
Windows and both kinds of Mac.
