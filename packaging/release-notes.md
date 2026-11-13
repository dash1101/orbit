**Orbit** by NovaLabs: one place for YTM Starcast and Nova. Each download is the program itself: nothing to unpack, and nothing else to install.

**New in 0.5.6**

- **YTM Starcast's new icon** in Orbit's list of apps.
- **YTM Starcast's card:** *YouTube Music* and *settings* work with YTM Starcast 0.2.0-alpha.5 and its browser extension 2.3.0. *settings* shows while Refine is switched on (YTM Starcast's settings in YouTube Music are part of it), and if the browser still runs an older extension, Orbit says to reload it.

**New in 0.5.5**

- **Approving a browser for Nova is a click away:** *approve a browser* is on Nova's card, and its code box takes the code however it's written (`zag97v`, `ZAG 97V`), or the whole `sudo nova approve …` line pasted in. With a Nova that reports browsers waiting for approval, Nova's card says so and the button lights up.
- **YTM Starcast's card opens YouTube Music:** with YTM Starcast 0.2.0-alpha.5 and newer, *YouTube Music* brings your open YouTube Music tab to the front (or opens one), and *settings* opens YTM Starcast's settings inside it. The web app stays on YTM Starcast's own page in Orbit.
- **Dialog titles aren't cut off any more** (*confirm*, *approve a browser* and the others).
- **Orbit finds Nova on the computer itself first** (`https://127.0.0.1:8495`, which Nova 0.9.20 and newer answer on Linux), then on your home network, and over Tailscale last.

**New in 0.5.4**

- **Install Orbit on Linux without a password:** *install orbit* at the top moves the program out of your Downloads into `~/.local/share/orbit`, and adds it to your apps menu with its icon, and an `orbit` command. It keeps updating itself there, and *remove* in Orbit's settings takes it all away again (your settings stay).
- **Orbit's window shows Orbit's icon on Linux** (in the taskbar and the window switcher) instead of a generic one, once Orbit is in your apps menu (installed as above, or with the `.deb`). Orbit's menu entry from an earlier version is replaced by the new one when you install Orbit or refresh the entry.
- **The keys at the bottom are buttons too:** click *r refresh*, *t theme*, *i install* and the others, or press the key.
- **Updating by hand right after Orbit starts downloads the update once.** Orbit's own background update could start a second download as well, which then waited as an update to the version just put in place.
- **Windows:** adding Orbit to the Start menu waits longer for PowerShell's first start, which can take over half a minute on a fresh computer.

**New in 0.5.3**

- **Builds for Macs and ARM computers come with milestone releases** (0.6.0, 0.7.0 and so on, and now and then one in between). The releases in between are for Windows and Linux on x86-64, and Android, so they come out sooner. This release still has every build.
- **Orbit on a Mac or an ARM computer updates itself to the newest release that has a build for it**, and passes over the releases without one. Its update offer and its card on the install page say the same.

**New in 0.5.2**

- **A package for Debian and Ubuntu:** `orbit-linux-x86_64.deb` (and `orbit-linux-arm64.deb`). Open it with your software installer (or `sudo apt install ./orbit-linux-x86_64.deb`): Orbit goes in your apps menu with its icon, and the window's web engine comes with it. Orbit installed this way updates itself through apt: *update orbit to …* in the top bar, and the system asks for your password once.
- **Builds for ARM computers:** Windows on ARM (`orbit-windows-arm64.exe`) and 64-bit ARM Linux (`orbit-linux-arm64`, and its `.deb`).
- **One file per download:** the copies named with the version (`orbit-v0.5.1-…`) are gone. They were there so that Orbit 0.3.4 and older could update themselves; an Orbit that old now needs one download by hand.

**New in 0.5.1**

- **Orbit updates itself.** When Orbit starts and a newer version is out, it downloads it, checks it against the release's checksums and starts it once to make sure it works, all in the background. The new version takes Orbit's place the next time you open Orbit, in the same file and folder (or right away: *restart for orbit …* at the top). Nothing restarts while you're using it. Turn it off in Orbit's settings: *Update Orbit by itself*.

**New in 0.5.0**

- **Orbit is rebuilt as one small program with its own window.** It no longer needs a browser: the window is drawn by the web engine that comes with your system (WebView2 on Windows, WebKit on a Mac, WebKitGTK on Linux), with Orbit's own icon in the taskbar and the Dock. Everything looks and works as before, every animation included.
- **Faster and smaller.** Orbit starts in a fraction of a second (it used to unpack itself first, every time), uses less memory, and the Mac downloads are less than half the size.
- **If your computer lacks the web engine, Orbit offers to install it**: Microsoft's own WebView2 installer on Windows (run only after Orbit checks Microsoft signed it), or your Linux's WebKitGTK package. Say no, and Orbit opens in your browser as before.
- **Updating YTM Starcast keeps its options.** Orbit's installer rewrote YTM Starcast's service with only the options it asks about, so an access token, port or address set for the server was lost at an update. It now keeps them, as YTM Starcast's own installer does; *home Wi-Fi* still follows the switch.
- **YTM Starcast's browser extension:** after installing YTM Starcast, *set up the browser extension* opens YTM Starcast's guide, and its card says when the extension hasn't been added yet or needs reloading (with YTM Starcast 0.2.0a2 and newer).
- **Updating to 0.5.0 is the usual one click** from 0.3.5 and newer: *update orbit to 0.5.0* in the top bar. Your settings stay.

**New in 0.4.0:** Pocketfader and YTM Beacon are now one app, YTM Starcast, and moving over takes one click: Orbit puts YTM Starcast in place, stops Pocketfader, removes YTM Beacon with its own uninstaller (your choice), and your settings and paired phones carry over. Remove the YTM Beacon extension from your browser yourself, and add YTM Starcast's extension (`ytm-starcast-extension.zip` in YTM Starcast's release). On Android, the Pocketfader app updates to YTM Starcast in place.

**Earlier in 0.3:** updates happen in place, and Orbit restarts on the new version by itself (0.3.5); Orbit updates itself on Linux and Windows (0.3.1) and on a Mac (0.3.4); an app's card offers its update as soon as Orbit opens; one window after a restart; the overview shows what each app is doing; Nova on Windows (a preview), installed, updated and removed by Orbit; Nova's release signature checked by Orbit itself everywhere, Android included.

| System | Download | Then |
|---|---|---|
| **Windows** (10 and 11, 64-bit) | `orbit-windows-x64.exe` | run it |
| **Windows on ARM** | `orbit-windows-arm64.exe` | run it |
| **macOS, Apple chips** (M1 and newer) | `orbit-macos-apple-silicon.dmg` | open it, drag **Orbit** onto **Applications** |
| **macOS, Intel** | `orbit-macos-intel.dmg` | open it, drag **Orbit** onto **Applications** |
| **Debian, Ubuntu, Mint, Pop!_OS…** | `orbit-linux-x86_64.deb` | open it with your software installer, or `sudo apt install ./orbit-linux-x86_64.deb`; then Orbit is in your apps menu |
| **Debian, Ubuntu… on ARM** (64-bit) | `orbit-linux-arm64.deb` | the same, with this file |
| **Other Linux** (x86-64) | `orbit-linux-x86_64` | allow it to run once (`chmod +x orbit-linux-x86_64`, or Properties → *Allow executing as program*), run it, then *install orbit* at the top puts it in your apps menu (no password) |
| **Other Linux on ARM** (64-bit) | `orbit-linux-arm64` | the same, with this file |
| **Android** 5.0 and newer | `orbit-android.apk` | install it, then install YTM Starcast and Nova from it |

**First launch on Windows and macOS.** These programs aren't code-signed (that needs a paid certificate), so Windows may show "Windows protected your PC" (choose *More info*, then *Run anyway*) and macOS may say the app can't be opened (right-click **Orbit** in Applications, choose *Open*, then *Open*).

On Windows, *orbit settings → Orbit in the Start menu* adds a launcher for the copy you downloaded. Closing Orbit's window ends it.

Check downloads against `SHA256SUMS`. This is a pre-release.
