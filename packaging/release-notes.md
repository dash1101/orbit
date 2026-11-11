**Orbit** by NovaLabs: one place for Pocketfader, Nova and YTM Beacon. Each download is the program itself: nothing to unpack, and nothing else to install.

**New in 0.3.1**

- **Orbit updates itself** on Linux and Windows. When a new version is out, the top bar says *update orbit to …*: Orbit downloads it, checks it against the release's checksums, starts it once to make sure it runs and is that version, and switches to it when you press *restart orbit* (or the next time you open Orbit). The old program is kept until the new one has started, so a failed update leaves Orbit as it was. This is checked on every build by updating a real Orbit to a newer one and restarting it, on Linux and on Windows. On a Mac, download the new `.dmg` as before.
- **The overview shows what each app is doing**: the song Pocketfader is playing, what Beacon shows on Discord.
- **Linux: programs Orbit opens use your computer's own libraries.** The standalone Orbit passed its own bundled copies of some system libraries (such as OpenSSL and zlib) on to the browsers, terminals and system tools it started, where older copies can clash with the system's. Now each program gets the computer's own.
- Updating from 0.3.0 or older is still by hand, once: download this release's file in place of the old one.

**New in 0.3.0:** Nova on Windows (a preview), installed, updated and removed by Orbit with one Windows permission prompt; Nova's release signature checked by Orbit itself everywhere, Android included; Nova Server and Nova's setup window opened from Orbit on Linux; open a running app from the overview in one click; and many smaller fixes.

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
