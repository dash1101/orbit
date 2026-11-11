**Orbit** by NovaLabs: one place for Pocketfader, Nova and YTM Beacon. Each download is the program itself: nothing to unpack, and nothing else to install.

**New in 0.3.0**

- **Nova on Windows.** Nova's server now runs on Windows 10 and 11 and Windows Server 2019 or newer (a preview: pairing, devices, approvals, live graphs, alerts, the task manager, power and Docker Desktop work; programs and updates, files, the terminal and backups aren't there yet). Orbit installs, updates and removes it, with one Windows permission prompt and the installer's progress in Orbit's own window. Nova's page shows whether it answers and opens an administrator window for its commands. This was checked by installing, updating and removing Nova through Orbit on real Windows Server 2022 and 2025 machines.
- **Nova's release signature is checked everywhere.** Orbit checks it itself now, so it no longer depends on openssl (which Windows doesn't have), and the Android app checks it on every Android version (Android only checks these signatures itself from version 13).
- **Nova Server and Nova's setup window** (Nova 0.9.16 and newer on Linux) open from Orbit instead of a terminal.
- **Open an app from the overview** in one click while it runs: Pocketfader's web app, Beacon's dashboard, Nova web.
- **Smaller things:** versions are written the same way everywhere, an app you haven't installed is shown as "Not installed" in grey instead of red, a page shows straight away when you switch apps, commands in hints are shown as code, YTM Beacon's buttons follow what it's doing (show now when hidden, the hide times when showing, update only when there is one), Nova's log explains why it can be empty and *api log* reads it, simpler theme names, links in *about*, and the Linux window remembers its size.

| System | Download | Then |
|---|---|---|
| **Windows** (10 and 11, 64-bit) | `orbit-*-windows-x64.exe` | run it |
| **macOS, Apple chips** (M1 and newer) | `orbit-*-macos-apple-silicon.dmg` | open it, drag **Orbit** onto **Applications** |
| **macOS, Intel** | `orbit-*-macos-intel.dmg` | the same |
| **Linux** (x86-64) | `orbit-*-linux-x86_64` | allow it to run once (`chmod +x orbit-*-linux-x86_64`, or Properties → *Allow executing as program*), then run it |
| **Android** 5.0 and newer | `orbit-*-android.apk` | install it, then install Pocketfader and Nova from it |

**First launch on Windows and macOS.** These programs aren't code-signed (that needs a paid certificate), so Windows may show "Windows protected your PC" (choose *More info*, then *Run anyway*) and macOS may say the app can't be opened (right-click **Orbit** in Applications, choose *Open*, then *Open*).

On Linux and Windows, *orbit settings → Orbit in your apps menu* (Start menu on Windows) adds a launcher for the copy you downloaded. Orbit shows itself in an app window of Edge, Chrome or Brave when there is one, otherwise in your browser; closing the window ends it.

Check downloads against `SHA256SUMS`. This is a pre-release.
