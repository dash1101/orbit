**Orbit** by NovaLabs: one place for YTM Starcast and Nova. Each download is the program itself: nothing to unpack, and nothing else to install.

**New in 0.4.0**

- **Pocketfader and YTM Beacon are now one app: YTM Starcast.** Orbit lists YTM Starcast in their place: the phone remote, your music as your Discord status, and YTM Starcast's improvements to YouTube Music, with one card for all of it. YTM Starcast's page shows your Discord status next to everything else, with the Discord card's settings, *hide for 30 min / 1 h / 3 h* and *show now*, and YTM Starcast's parts (YouTube Music remote, Discord status, Refine, this computer's audio), each with its own switch.
- **Moving over takes one click.** On a computer that still has Pocketfader, YTM Starcast's card offers *update to …*: Orbit puts YTM Starcast in place, stops Pocketfader, removes YTM Beacon with its own uninstaller (a choice in the installer: turn it off to keep YTM Beacon), starts YTM Starcast, and removes Pocketfader's old program files. The installer shows exactly what it will do before it starts. A computer with only YTM Beacon gets *install YTM Starcast (replaces YTM Beacon)*.
- **Your settings carry over.** Orbit never copies or deletes them during the move: YTM Starcast takes over Pocketfader's settings (your phones stay paired) and YTM Beacon's Discord settings by itself when it first starts. Orbit's own settings for Pocketfader (its address and access token) move to YTM Starcast's card, in the same place in the side bar.
- **Remove the YTM Beacon extension from your browser yourself** (browsers only allow that by hand), and add YTM Starcast's extension (`ytm-starcast-extension.zip` in YTM Starcast's release) if you haven't yet.
- **On Android**, the Pocketfader app updates to YTM Starcast in place, with its settings: Orbit for Android lists YTM Starcast instead of Pocketfader.
- **Orbit 0.3.5 and older can't install or update YTM Starcast: update Orbit first.** The files named with the version (`orbit-v0.4.0-…`) are the same files as the plain ones. They're there so that Orbit 0.3.4 and older can update themselves to this release.

**Earlier in 0.3:** updates happen in place, and Orbit restarts on the new version by itself (0.3.5); Orbit updates itself on Linux and Windows (0.3.1) and on a Mac (0.3.4); an app's card offers its update as soon as Orbit opens; one window after a restart; the overview shows what each app is doing; Nova on Windows (a preview), installed, updated and removed by Orbit; Nova's release signature checked by Orbit itself everywhere, Android included.

| System | Download | Then |
|---|---|---|
| **Windows** (10 and 11, 64-bit) | `orbit-windows-x64.exe` | run it |
| **macOS, Apple chips** (M1 and newer) | `orbit-macos-apple-silicon.dmg` | open it, drag **Orbit** onto **Applications** |
| **macOS, Intel** | `orbit-macos-intel.dmg` | the same |
| **Linux** (x86-64) | `orbit-linux-x86_64` | allow it to run once (`chmod +x orbit-linux-x86_64`, or Properties → *Allow executing as program*), then run it |
| **Android** 5.0 and newer | `orbit-android.apk` | install it, then install YTM Starcast and Nova from it |

**First launch on Windows and macOS.** These programs aren't code-signed (that needs a paid certificate), so Windows may show "Windows protected your PC" (choose *More info*, then *Run anyway*) and macOS may say the app can't be opened (right-click **Orbit** in Applications, choose *Open*, then *Open*).

On Linux and Windows, *orbit settings → Orbit in your apps menu* (Start menu on Windows) adds a launcher for the copy you downloaded. Orbit shows itself in an app window of Edge, Chrome or Brave when there is one, otherwise in your browser; closing the window ends it.

Check downloads against `SHA256SUMS`. This is a pre-release.
