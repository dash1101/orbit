**Orbit** by NovaLabs: one place for YTM Starcast and Nova. Each download is the program itself: nothing to unpack, and nothing else to install.

**New in 0.6.0**, a milestone: it comes for every system, Macs and ARM computers included.

- **No console windows on Windows.** Orbit runs everything in the background without a window, and starts YTM Starcast hidden (also right after installing it), so no window can end it when closed. Checked on a real Windows desktop with every build.
- **Start, stop and restart YTM Starcast on Windows**, and switch *start at login*, from its page in Orbit, as on Linux.
- **YTM Starcast's page shows where to open it on your phone:** its addresses, and a QR code to scan (with YTM Starcast 0.2.0-alpha.10 and newer). YTM Starcast's own window is left out when Orbit installs it (a choice in the install brings it back), and on Windows its log is on its page.
- **Nova's page shows where Nova answers** (this computer, your home network, your tailnet), says when it still needs setting up (with *set up nova* on its card), and lists what its check-up found, with Nova 0.9.23 and newer. Orbit follows Nova to another port, and on Windows shows its health too.
- **An app that isn't installed says so**, with its install button and nothing to open. An installed app that isn't running offers *start*.
- **Choose the ports:** Orbit's own in its settings (from its next start), and YTM Starcast's when you install or update it.
- **Installing Orbit for you shows each step** as it's done, and asks before Orbit starts again from its new place.
- **The apps' names, descriptions and icons come from Orbit's repository**, kept for when GitHub can't be reached: a renamed app or a new icon shows in the Orbit you have, without an update. On Android, new apps can arrive this way too.
- **Android: Orbit offers the newest release that has the Android app**, and says when a newer one is out for other systems only. Orbit 0.3.4 and older on Android can update itself to this release.
- **Updating YTM Starcast on Windows keeps its options** (an access token, a port).

**Earlier in 0.5:** when GitHub's limit for requests is used up, Orbit says so and when it resets (0.5.9); Orbit installs for you on Windows too, and `orbit --uninstall` (0.5.8); a desktop notification when Nova waits for your approval (Linux), the window keeps its side bar at any size, and the paper theme is easier to read (0.5.7); Nova's card approves a browser, and its code box takes a pasted `sudo nova approve …` line (0.5.5); YTM Starcast's card brings up YouTube Music and YTM Starcast's settings in it (0.5.5, with YTM Starcast 0.2.0-alpha.5); Orbit installs on Linux without a password, and its window shows Orbit's icon (0.5.4); the keys at the bottom are buttons too (0.5.4); Macs and ARM computers get builds at milestones, and Orbit there updates itself to the newest release with a build for it (0.5.3); a `.deb` for Debian and Ubuntu, and builds for ARM (0.5.2); Orbit updates itself in the background (0.5.1); Orbit is one small, fast program with its own window, and offers to install the web engine it needs (0.5.0).

**New in 0.4.0:** Pocketfader and YTM Beacon are now one app, YTM Starcast, and moving over takes one click: Orbit puts YTM Starcast in place, stops Pocketfader, removes YTM Beacon with its own uninstaller (your choice), and your settings and paired phones carry over. Remove the YTM Beacon extension from your browser yourself, and add YTM Starcast's extension (`ytm-starcast-extension.zip` in YTM Starcast's release). On Android, the Pocketfader app updates to YTM Starcast in place.

**Earlier in 0.3:** updates happen in place, and Orbit restarts on the new version by itself (0.3.5); Orbit updates itself on Linux and Windows (0.3.1) and on a Mac (0.3.4); an app's card offers its update as soon as Orbit opens; one window after a restart; the overview shows what each app is doing; Nova on Windows (a preview), installed, updated and removed by Orbit; Nova's release signature checked by Orbit itself everywhere, Android included.

| System | Download | Then |
|---|---|---|
| **Windows** (10 and 11, 64-bit) | `orbit-windows-x64.exe` | run it, then *install orbit* at the top puts it in the Start menu and the installed apps (no administrator) |
| **Windows on ARM** | `orbit-windows-arm64.exe` | run it |
| **macOS, Apple chips** (M1 and newer) | `orbit-macos-apple-silicon.dmg` | open it, drag **Orbit** onto **Applications** |
| **macOS, Intel** | `orbit-macos-intel.dmg` | open it, drag **Orbit** onto **Applications** |
| **Debian, Ubuntu, Mint, Pop!_OS…** | `orbit-linux-x86_64.deb` | open it with your software installer, or `sudo apt install ./orbit-linux-x86_64.deb`; then Orbit is in your apps menu |
| **Debian, Ubuntu… on ARM** (64-bit) | `orbit-linux-arm64.deb` | the same, with this file |
| **Other Linux** (x86-64) | `orbit-linux-x86_64` | allow it to run once (`chmod +x orbit-linux-x86_64`, or Properties → *Allow executing as program*), run it, then *install orbit* at the top puts it in your apps menu (no password) |
| **Other Linux on ARM** (64-bit) | `orbit-linux-arm64` | the same, with this file |
| **Android** 5.0 and newer | `orbit-android.apk` | install it, then install YTM Starcast and Nova from it |

**First launch on Windows and macOS.** These programs aren't code-signed (that needs a paid certificate), so Windows may show "Windows protected your PC" (choose *More info*, then *Run anyway*) and macOS may say the app can't be opened (right-click **Orbit** in Applications, choose *Open*, then *Open*).

Closing Orbit's window ends it.

Check downloads against `SHA256SUMS`. This is a pre-release.
