//! The web engine Orbit's window is drawn with: part of the system, not the person's browser.
//!
//! * Windows: Microsoft Edge WebView2. It comes with Windows 11 and was installed on Windows 10 by Windows Update,
//!   but a few computers don't have it: Orbit offers to install it (Microsoft's own installer, checked to be signed
//!   by Microsoft before it runs).
//! * macOS: WebKit, part of macOS itself. Always there.
//! * Linux: WebKitGTK (libwebkit2gtk-4.1), a system library most desktops have: Orbit offers to install it with the
//!   system's package manager (the system asks for the password).
//! Anyone who says no gets Orbit in their browser instead, as before.
#[allow(unused_imports)]
use std::path::{Path, PathBuf};
#[allow(unused_imports)]
use std::time::Duration;

/// Ask a yes/no question in the system's own dialog. None when there's no way to ask.
#[allow(dead_code)]
pub fn ask(title: &str, text: &str) -> Option<bool> {
    #[cfg(windows)]
    {
        use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, IDYES, MB_ICONQUESTION, MB_SETFOREGROUND, MB_YESNO};
        let w = |s: &str| s.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
        let r = unsafe { MessageBoxW(std::ptr::null_mut(), w(text).as_ptr(), w(title).as_ptr(), MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND) };
        return Some(r == IDYES);
    }
    #[cfg(not(windows))]
    {
        let run = |argv: &[&str]| std::process::Command::new(argv[0]).args(&argv[1..]).status().ok().map(|s| s.success());
        if crate::runner::which("kdialog").is_some() {
            return run(&["kdialog", "--title", title, "--yesno", text]);
        }
        if crate::runner::which("zenity").is_some() {
            return run(&["zenity", "--question", "--no-wrap", "--title", title, "--text", text]);
        }
        if std::io::IsTerminal::is_terminal(&std::io::stdin()) {
            use std::io::Write;
            print!("{text}\n[y/N] ");
            let _ = std::io::stdout().flush();
            let mut line = String::new();
            let _ = std::io::stdin().read_line(&mut line);
            return Some(matches!(line.trim().to_lowercase().as_str(), "y" | "yes"));
        }
        None
    }
}

/// Tell the person something in the system's own dialog (or the terminal).
#[allow(dead_code)]
pub fn tell(title: &str, text: &str) {
    #[cfg(windows)]
    {
        use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONINFORMATION, MB_OK, MB_SETFOREGROUND};
        let w = |s: &str| s.encode_utf16().chain(Some(0)).collect::<Vec<u16>>();
        unsafe { MessageBoxW(std::ptr::null_mut(), w(text).as_ptr(), w(title).as_ptr(), MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND) };
    }
    #[cfg(not(windows))]
    {
        let ok = if crate::runner::which("kdialog").is_some() {
            std::process::Command::new("kdialog").args(["--title", title, "--msgbox", text]).status().is_ok()
        } else if crate::runner::which("zenity").is_some() {
            std::process::Command::new("zenity").args(["--info", "--no-wrap", "--title", title, "--text", text]).status().is_ok()
        } else {
            false
        };
        if !ok {
            eprintln!("{text}");
        }
    }
}

// ---- Windows ---------------------------------------------------------------------------------------
#[cfg(all(windows, feature = "window"))]
pub fn windows_engine() -> Option<String> {
    tauri::webview_version().ok().filter(|v| !v.is_empty())
}

/// Microsoft's WebView2 installer ("Evergreen bootstrapper"): Microsoft's own address for it.
#[cfg(windows)]
const WEBVIEW2_BOOTSTRAPPER: &str = "https://go.microsoft.com/fwlink/p/?LinkId=2124703";

/// WebView2 is there, or the person had it installed now. False: use the browser.
#[cfg(all(windows, feature = "window"))]
pub fn ensure_windows() -> bool {
    if windows_engine().is_some() {
        return true;
    }
    let yes = ask("Orbit", "Orbit shows its window with Microsoft Edge WebView2, which isn't on this computer yet.\n\n\
                           Install it now? It's a small download from Microsoft, and Windows keeps it up to date afterwards.\n\n\
                           Choose No to open Orbit in your browser instead.").unwrap_or(false);
    if !yes {
        return false;
    }
    match install_webview2() {
        Ok(()) if windows_engine().is_some() => true,
        Ok(()) => {
            tell("Orbit", "The WebView2 installer finished, but Windows doesn't report it yet. Orbit opens in your browser this time; \
                           next time it should have its own window.");
            false
        }
        Err(e) => {
            tell("Orbit", &format!("WebView2 couldn't be installed: {e}\n\nOrbit opens in your browser instead. You can also get WebView2 \
                                    from Microsoft's website (search for \"WebView2 Runtime\")."));
            false
        }
    }
}

/// Download Microsoft's installer, make sure Microsoft signed it, and run it (it shows its own progress).
#[cfg(windows)]
pub fn install_webview2() -> Result<(), String> {
    use std::io::Read;
    let dir = std::env::temp_dir().join(format!("orbit-webview2-{}", crate::util::new_id()));
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let exe = dir.join("MicrosoftEdgeWebview2Setup.exe");
    let resp = crate::net::agent(Duration::from_secs(60), false).get(WEBVIEW2_BOOTSTRAPPER).call().map_err(|e| format!("download: {e}"))?;
    let host = url::Url::parse(resp.get_url()).ok().and_then(|u| u.host_str().map(String::from)).unwrap_or_default();
    if !(host == "microsoft.com" || host.ends_with(".microsoft.com")) {
        return Err(format!("the download came from {host}, not Microsoft"));
    }
    let mut data = Vec::new();
    resp.into_reader().take(50 << 20).read_to_end(&mut data).map_err(|e| format!("download: {e}"))?;
    std::fs::write(&exe, data).map_err(|e| e.to_string())?;
    // Only a file signed by Microsoft runs.
    let check = format!("$s = Get-AuthenticodeSignature -LiteralPath '{}'; if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -like '*O=Microsoft Corporation*') {{ exit 0 }} else {{ Write-Output $s.Status; exit 1 }}",
                        exe.to_string_lossy().replace('\'', "''"));
    let (rc, out) = crate::runner::run_capture(&["powershell".into(), "-NoProfile".into(), "-NonInteractive".into(), "-Command".into(), check],
                                               Duration::from_secs(60), true);
    if rc != 0 {
        let _ = std::fs::remove_dir_all(&dir);
        return Err(format!("the installer isn't signed by Microsoft ({})", out.trim()));
    }
    let st = std::process::Command::new(&exe).arg("/install").status().map_err(|e| e.to_string())?;
    let _ = std::fs::remove_dir_all(&dir);
    if !st.success() {
        return Err(format!("Microsoft's installer stopped with code {}", st.code().unwrap_or(-1)));
    }
    Ok(())
}

// ---- Linux -----------------------------------------------------------------------------------------
#[cfg(target_os = "linux")]
pub fn linux_engine() -> bool {
    unsafe { libloading::Library::new("libwebkit2gtk-4.1.so.0").is_ok() }
}

/// The package that has WebKitGTK 4.1, for this Linux's package manager: (manager, its arguments).
#[cfg(target_os = "linux")]
fn linux_package() -> Option<(String, Vec<&'static str>)> {
    let w = |n: &str| crate::runner::which(n).map(|p| p.to_string_lossy().into_owned());
    if let Some(p) = w("apt-get") {
        return Some((p, vec!["install", "-y", "libwebkit2gtk-4.1-0"]));
    }
    if let Some(p) = w("dnf") {
        return Some((p, vec!["install", "-y", "webkit2gtk4.1"]));
    }
    if let Some(p) = w("pacman") {
        return Some((p, vec!["-S", "--needed", "--noconfirm", "webkit2gtk-4.1"]));
    }
    if let Some(p) = w("zypper") {
        return Some((p, vec!["--non-interactive", "install", "libwebkit2gtk-4_1-0"]));
    }
    None
}

/// WebKitGTK is there, or the person had it installed now. False: use the browser.
#[cfg(target_os = "linux")]
pub fn ensure_linux() -> bool {
    if linux_engine() {
        return true;
    }
    let Some((pm, args)) = linux_package() else {
        eprintln!("orbit: the window needs WebKitGTK 4.1 (libwebkit2gtk-4.1); install it with your package manager. Opening the browser instead.");
        return false;
    };
    let pkg = args.last().copied().unwrap_or("");
    let shown = format!("{} {}", Path::new(&pm).file_name().unwrap_or_default().to_string_lossy(), args.join(" "));
    let yes = ask("Orbit", &format!("Orbit draws its window with WebKitGTK ({pkg}), which isn't on this computer yet.\n\n\
                                     Install it now? Orbit runs: {shown}\nThe system asks for your password.\n\n\
                                     Choose No to open Orbit in your browser instead.")).unwrap_or(false);
    if !yes {
        return false;
    }
    let graphical = std::env::var_os("DISPLAY").is_some() || std::env::var_os("WAYLAND_DISPLAY").is_some();
    let mut argv: Vec<String> = if crate::runner::which("pkexec").is_some() && graphical {
        vec!["pkexec".into(), pm]
    } else if std::io::IsTerminal::is_terminal(&std::io::stdin()) && crate::runner::which("sudo").is_some() {
        vec!["sudo".into(), pm]
    } else {
        tell("Orbit", &format!("Orbit can't ask for the administrator password here. Run this in a terminal, then open Orbit again:\n\nsudo {shown}"));
        return false;
    };
    argv.extend(args.iter().map(|a| a.to_string()));
    let ok = std::process::Command::new(&argv[0]).args(&argv[1..]).status().map(|s| s.success()).unwrap_or(false);
    if ok && linux_engine() {
        return true;
    }
    tell("Orbit", &format!("WebKitGTK wasn't installed, so Orbit opens in your browser this time. To install it yourself: sudo {shown}"));
    false
}
