**Orbit** by NovaLabs: one place for Pocketfader, Nova and YTM Beacon. Each download is the program itself: nothing to unpack, and nothing else to install.

**New in 0.3.5**

- **Updates happen in place, in one step.** The downloads no longer have the version in their names (`orbit-windows-x64.exe`, `orbit-linux-x86_64`, …), so Orbit keeps its name and folder from one version to the next, and shortcuts keep working. After *update orbit to …*, Orbit restarts on the new version by itself after a few seconds (close the update window to wait instead). It doesn't restart while an app is still installing.
- **A copy downloaded before 0.3.5, named with its version (`orbit-v0.3.4-windows-x64.exe`), takes the plain name at its next update**, and Orbit's own apps-menu or Start-menu entry follows it. A taskbar pin you made yourself on Windows still points at the old name: pin Orbit again once.
- **A new icon, on the same grid as Nova's.** It fills its tile and is centred, so it's the same size as Nova's next to it. On Android 13 and newer it also follows the system's themed icons.
- The files named with the version (`orbit-v0.3.5-…`) are the same files as the plain ones. They're there so that Orbit 0.3.4 and older can update themselves to this release.

**Earlier in 0.3:** Orbit updates itself on Linux and Windows (0.3.1) and on a Mac (0.3.4), checked on every build by updating a real Orbit to a newer one; an app's card offers its update as soon as Orbit opens; one window after a restart; Windows fixes for installing Pocketfader and removing YTM Beacon; the overview shows what each app is doing; Nova on Windows (a preview), installed, updated and removed by Orbit; Nova's release signature checked by Orbit itself everywhere, Android included.

| System | Download | Then |
|---|---|---|
| **Windows** (10 and 11, 64-bit) | `orbit-windows-x64.exe` | run it |
| **macOS, Apple chips** (M1 and newer) | `orbit-macos-apple-silicon.dmg` | open it, drag **Orbit** onto **Applications** |
| **macOS, Intel** | `orbit-macos-intel.dmg` | the same |
| **Linux** (x86-64) | `orbit-linux-x86_64` | allow it to run once (`chmod +x orbit-linux-x86_64`, or Properties → *Allow executing as program*), then run it |
| **Android** 5.0 and newer | `orbit-android.apk` | install it, then install Pocketfader and Nova from it |

**First launch on Windows and macOS.** These programs aren't code-signed (that needs a paid certificate), so Windows may show "Windows protected your PC" (choose *More info*, then *Run anyway*) and macOS may say the app can't be opened (right-click **Orbit** in Applications, choose *Open*, then *Open*).

On Linux and Windows, *orbit settings → Orbit in your apps menu* (Start menu on Windows) adds a launcher for the copy you downloaded. Orbit shows itself in an app window of Edge, Chrome or Brave when there is one, otherwise in your browser; closing the window ends it.

Check downloads against `SHA256SUMS`. This is a pre-release.
