**Orbit** by NovaLabs: one place for YTM Starcast and Nova. Each download is the program itself: nothing to unpack, and nothing else to install.

**New in 0.5.0**

- **Orbit is rebuilt as one small program with its own window.** It no longer needs a browser: the window is drawn by the web engine that comes with your system (WebView2 on Windows, WebKit on a Mac, WebKitGTK on Linux), with Orbit's own icon in the taskbar and the Dock. Everything looks and works as before, every animation included.
- **Faster and smaller.** Orbit starts in a fraction of a second (it used to unpack itself first, every time), uses less memory, and the Mac downloads are less than half the size.
- **If your computer lacks the web engine, Orbit offers to install it**: Microsoft's own WebView2 installer on Windows (run only after Orbit checks Microsoft signed it), or your Linux's WebKitGTK package. Say no, and Orbit opens in your browser as before.
- **Updating to 0.5.0 is the usual one click** from 0.3.5 and newer: *update orbit to 0.5.0* in the top bar. Your settings stay.

**New in 0.4.0:** Pocketfader and YTM Beacon are now one app, YTM Starcast, and moving over takes one click: Orbit puts YTM Starcast in place, stops Pocketfader, removes YTM Beacon with its own uninstaller (your choice), and your settings and paired phones carry over. Remove the YTM Beacon extension from your browser yourself, and add YTM Starcast's extension (`ytm-starcast-extension.zip` in YTM Starcast's release). On Android, the Pocketfader app updates to YTM Starcast in place.

The files named with the version (`orbit-v0.5.0-…`) are the same files as the plain ones, there so that Orbit 0.3.4 and older can update themselves.

**Earlier in 0.3:** updates happen in place, and Orbit restarts on the new version by itself (0.3.5); Orbit updates itself on Linux and Windows (0.3.1) and on a Mac (0.3.4); an app's card offers its update as soon as Orbit opens; one window after a restart; the overview shows what each app is doing; Nova on Windows (a preview), installed, updated and removed by Orbit; Nova's release signature checked by Orbit itself everywhere, Android included.

| System | Download | Then |
|---|---|---|
| **Windows** (10 and 11, 64-bit) | `orbit-windows-x64.exe` | run it |
| **macOS, Apple chips** (M1 and newer) | `orbit-macos-apple-silicon.dmg` | open it, drag **Orbit** onto **Applications** |
| **macOS, Intel** | `orbit-macos-intel.dmg` | the same |
| **Linux** (x86-64) | `orbit-linux-x86_64` | allow it to run once (`chmod +x orbit-linux-x86_64`, or Properties → *Allow executing as program*), then run it |
| **Android** 5.0 and newer | `orbit-android.apk` | install it, then install YTM Starcast and Nova from it |

**First launch on Windows and macOS.** These programs aren't code-signed (that needs a paid certificate), so Windows may show "Windows protected your PC" (choose *More info*, then *Run anyway*) and macOS may say the app can't be opened (right-click **Orbit** in Applications, choose *Open*, then *Open*).

On Linux and Windows, *orbit settings → Orbit in your apps menu* (Start menu on Windows) adds a launcher for the copy you downloaded. Closing Orbit's window ends it.

Check downloads against `SHA256SUMS`. This is a pre-release.
