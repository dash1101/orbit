//! Put Orbit in the apps menu (Linux) or the Start menu (Windows). On macOS, dragging Orbit.app to Applications is
//! the way.
use crate::runner::Runner;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

pub const ICON: &[u8] = include_bytes!("../../orbit/static/icon.svg");
/// Orbit's id on Linux: its window's app id (window.rs), and the name of its apps-menu entry and icon, so the
/// desktop shows the open window with Orbit's name and icon.
pub const APP_ID: &str = "app.novalabs.orbit";
/// What the menu entry says, here and in the .deb (tools/make-deb.sh).
const COMMENT: &str = "One place for YTM Starcast and Nova";

fn linux_data() -> PathBuf {
    std::env::var_os("XDG_DATA_HOME").filter(|v| !v.is_empty()).map(PathBuf::from)
        .unwrap_or_else(|| crate::config::home().join(".local").join("share"))
}

fn linux_paths() -> (PathBuf, PathBuf) {
    let data = linux_data();
    (data.join("applications").join(format!("{APP_ID}.desktop")),
     data.join("icons").join("hicolor").join("scalable").join("apps").join(format!("{APP_ID}.svg")))
}

/// The entry and icon Orbit up to 0.5.3 wrote (orbit.desktop, orbit.svg), when they are Orbit's.
fn linux_old_paths() -> Option<(PathBuf, PathBuf)> {
    let data = linux_data();
    let entry = data.join("applications").join("orbit.desktop");
    let text = std::fs::read_to_string(&entry).ok()?;
    text.contains(&format!("Comment={COMMENT}")).then(|| (entry, data.join("icons").join("hicolor").join("scalable").join("apps").join("orbit.svg")))
}

fn windows_link() -> PathBuf {
    let appdata = std::env::var_os("APPDATA").map(PathBuf::from).unwrap_or_else(|| crate::config::home().join("AppData").join("Roaming"));
    appdata.join("Microsoft").join("Windows").join("Start Menu").join("Programs").join("Orbit.lnk")
}

fn program() -> String {
    crate::recipes::orbit::running_program().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|| "orbit".into())
}

/// Where Orbit lives once installed for this user on Linux (no password needed, and it updates itself there).
pub fn linux_home() -> PathBuf {
    linux_data().join("orbit")
}

fn linux_program() -> PathBuf {
    linux_home().join("orbit")
}

/// `orbit` on the command line: ~/.local/bin, which most distributions put on PATH.
fn linux_command() -> PathBuf {
    crate::config::home().join(".local").join("bin").join("orbit")
}

/// Where Orbit lives once installed for this user on Windows, as other per-user apps do (no administrator needed).
fn windows_home() -> PathBuf {
    std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(|| crate::config::home().join("AppData").join("Local"))
        .join("Programs").join("Orbit")
}

/// Where "install orbit" puts Orbit on this system (None where there is no such thing: macOS has Applications).
fn installed_home() -> Option<PathBuf> {
    if cfg!(target_os = "linux") { Some(linux_home()) } else if cfg!(windows) { Some(windows_home()) } else { None }
}

fn installed_program() -> Option<PathBuf> {
    if cfg!(target_os = "linux") { Some(linux_program()) } else if cfg!(windows) { Some(windows_home().join("orbit.exe")) } else { None }
}

/// Orbit in Windows' list of installed apps (Settings → Apps), for this user only; its uninstall runs `orbit --uninstall`.
const UNINSTALL_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\NovaLabs.Orbit";

/// A path as people read it: ~/… for one in the home folder (%LOCALAPPDATA%\… on Windows).
fn shown(p: &Path) -> String {
    if cfg!(windows) {
        if let Some(local) = std::env::var_os("LOCALAPPDATA").map(PathBuf::from) {
            if let Ok(rest) = p.strip_prefix(&local) {
                return format!("%LOCALAPPDATA%\\{}", rest.display());
            }
        }
        return p.display().to_string();
    }
    let home = crate::config::home();
    match p.strip_prefix(&home) {
        Ok(rest) if !home.as_os_str().is_empty() => format!("~/{}", rest.display()),
        _ => p.display().to_string(),
    }
}

fn same_file(a: &Path, b: &Path) -> bool {
    match (std::fs::canonicalize(a), std::fs::canonicalize(b)) {
        (Ok(x), Ok(y)) => x == y,
        _ => false,
    }
}

pub fn state() -> Value {
    let (Some(home), Some(target)) = (installed_home(), installed_program()) else {
        return json!({"supported": false, "where": "", "added": false});
    };
    let linux = cfg!(target_os = "linux");
    let exe = crate::recipes::orbit::running_program();
    let deb = linux && exe.as_deref().map(crate::recipes::orbit::deb_install).unwrap_or(false);
    let installed = exe.as_deref().map(|e| same_file(e, &target)).unwrap_or(false);
    let added = if linux { linux_paths().0.exists() || linux_old_paths().is_some() } else { windows_link().exists() };
    let steps: Vec<Value> = install_steps().into_iter().map(|(id, label)| json!({"id": id, "label": label})).collect();
    json!({"supported": true, "where": if linux { "your apps menu" } else { "the Start menu" }, "added": added,
           "install": !deb, "installed": installed, "deb": deb, "home": shown(&home),
           "elsewhere": !installed && target.exists(), "command": linux, "steps": steps})
}

fn ps_quote(x: &str) -> String {
    format!("'{}'", x.replace('\'', "''"))
}

fn powershell(script: &str) -> (i32, String) {
    crate::runner::run_capture(&["powershell".into(), "-NoProfile".into(), "-NonInteractive".into(), "-Command".into(), script.into()],
                               std::time::Duration::from_secs(90), true) // its first start can be slow
}

/// Rebuild the desktop's list of apps, so a new or changed entry (and the icon of an open Orbit window) shows now.
fn refresh_menu(entry: &Path) {
    let quick = std::time::Duration::from_secs(20);
    if crate::runner::which("update-desktop-database").is_some() {
        let _ = crate::runner::run_capture(&["update-desktop-database".into(), entry.parent().unwrap().to_string_lossy().into()], quick, true);
    }
    // KDE keeps its own list (it notices changes by itself too, a little later)
    for kde in ["kbuildsycoca6", "kbuildsycoca5"] {
        if crate::runner::which(kde).is_some() {
            let _ = crate::runner::run_capture(&[kde.into()], quick, true);
            break;
        }
    }
}

/// Move a file, across file systems too (then copied, keeping its permissions, and the original removed when it can
/// be: a running program on Windows can't). Ok(true) when it was renamed, Ok(false) when copied.
fn move_file(from: &Path, to: &Path) -> std::io::Result<bool> {
    if std::fs::rename(from, to).is_ok() {
        return Ok(true);
    }
    std::fs::copy(from, to)?;
    let _ = std::fs::remove_file(from);
    Ok(false)
}

/// The steps of installing Orbit for this user, in order, for this system: (id, what it does).
pub fn install_steps() -> Vec<(&'static str, String)> {
    let Some(home) = installed_home() else { return vec![] };
    if cfg!(target_os = "linux") {
        vec![("move", format!("Move Orbit to {}", shown(&home))), ("command", "Add the orbit command".into()),
             ("menu", "Add it to your apps menu, with its icon".into())]
    } else {
        vec![("move", format!("Move Orbit to {}", shown(&home))), ("menu", "Add it to the Start menu".into()),
             ("register", "Add it to Windows' list of installed apps".into())]
    }
}

/// One step of installing Orbit for this user, with no password or administrator (install_steps):
/// - "move": the program moves to ~/.local/share/orbit (Linux) or %LOCALAPPDATA%\Programs\Orbit (Windows), with an
///   update already waiting next to it. "restart": true when Orbit must start again from there to run from its new
///   place (Windows, where a program renamed while it runs keeps its old path, or when it was copied to another drive),
///   with "program" and "old" (a copy left behind, which the new Orbit deletes once this one has ended).
/// - "command": `orbit` in ~/.local/bin (Linux), unless something else of that name is there.
/// - "menu": the apps-menu entry and icon (Linux), or the Start-menu shortcut (Windows).
/// - "register": Orbit in Windows' installed apps, with its uninstall command.
/// Run after "move", the others work on the installed program. The program goes on updating itself there.
pub fn install_step(runner: &Runner, step: &str) -> Value {
    let (Some(home), Some(target)) = (installed_home(), installed_program()) else {
        return json!({"ok": false, "message": "installing Orbit for you isn't something this system has"});
    };
    let linux = cfg!(target_os = "linux");
    let t = target.to_string_lossy().into_owned();
    match step {
        "move" => {
            let Some(exe) = crate::recipes::orbit::running_program() else {
                return json!({"ok": false, "message": "Orbit can't find its own program"});
            };
            if linux && crate::recipes::orbit::deb_install(&exe) {
                return json!({"ok": false, "message": "This Orbit comes from the Debian package: apt keeps it installed and up to date."});
            }
            if runner.dry_run {
                runner.record("move", vec![exe.to_string_lossy().into(), t.clone()]);
                return json!({"ok": true, "message": format!("dry run: would move Orbit to {}", shown(&home)), "restart": false});
            }
            use crate::recipes::orbit::beside;
            let mut copied = false;
            let r = (|| -> std::io::Result<()> {
                std::fs::create_dir_all(&home)?;
                if !same_file(&exe, &target) {
                    for suffix in ["", ".new", ".new.ok", ".old"] {
                        let _ = std::fs::remove_file(beside(&target, suffix)); // an earlier install's, replaced by this one
                    }
                    copied = !move_file(&exe, &target)?;
                    for suffix in [".new", ".new.ok"] {
                        let waiting = beside(&exe, suffix);
                        if waiting.exists() {
                            let _ = move_file(&waiting, &beside(&target, suffix));
                        }
                    }
                }
                Ok(())
            })();
            if let Err(e) = r {
                return json!({"ok": false, "message": format!("couldn't move Orbit to {}: {e}", shown(&home))});
            }
            let moved = !same_file(&exe, &target) || copied; // (on Linux a renamed program knows its new place)
            let old = if copied && exe.exists() { exe.to_string_lossy().into_owned() } else { String::new() };
            json!({"ok": true, "message": format!("Orbit is in {}", shown(&home)), "copied": copied,
                   "restart": copied || (!linux && moved), "old": old, "program": t})
        }
        "command" if linux => {
            let cmd = linux_command();
            if runner.dry_run {
                runner.record("link", vec![cmd.to_string_lossy().into(), t.clone()]);
                return json!({"ok": true, "message": "dry run: would add the orbit command"});
            }
            let ours = std::fs::read_link(&cmd).map(|x| x == target).unwrap_or(false);
            if !ours && std::fs::symlink_metadata(&cmd).is_ok() {
                return json!({"ok": true, "message": format!("{} is something else's: left as it is", shown(&cmd)), "note": "skipped"});
            }
            let r = (|| -> std::io::Result<()> {
                if !ours {
                    std::fs::create_dir_all(cmd.parent().unwrap())?;
                    #[cfg(unix)]
                    std::os::unix::fs::symlink(&target, &cmd)?;
                }
                Ok(())
            })();
            match r {
                Ok(()) => json!({"ok": true, "message": "orbit starts it from a terminal"}),
                Err(e) => json!({"ok": false, "message": format!("couldn't add the orbit command: {e}")}),
            }
        }
        "menu" => {
            if runner.dry_run {
                return add(runner);
            }
            if linux { add_linux(&t) } else { add_other(runner, &t) }
        }
        "register" if !linux => {
            if runner.dry_run {
                runner.record("reg add", vec![UNINSTALL_KEY.into()]);
                return json!({"ok": true, "message": "dry run: would add Orbit to the installed apps"});
            }
            register_uninstall(&target);
            json!({"ok": true, "message": "Orbit is in Windows' installed apps"})
        }
        _ => json!({"ok": false, "message": format!("{step}: not a step of installing Orbit here")}),
    }
}

/// Install Orbit for this user in one go (every step of install_steps): what `{"install": true}` asks for.
pub fn install(runner: &Runner) -> Value {
    let Some(home) = installed_home() else { return add(runner) };
    let mut moved = json!({});
    let mut trouble = String::new();
    for (step, _) in install_steps() {
        let r = install_step(runner, step);
        if r["ok"] == false {
            if step == "move" {
                return r;
            }
            trouble = format!(", but {}", r["message"].as_str().unwrap_or(""));
        }
        if step == "move" {
            moved = r;
        }
    }
    if runner.dry_run {
        return json!({"ok": true, "message": format!("dry run: would install Orbit for you in {}", shown(&home))});
    }
    let where_ = if cfg!(target_os = "linux") { "your apps menu" } else { "the Start menu" };
    json!({"ok": true, "message": format!("Orbit is installed for you, in {} and {where_}{trouble}", shown(&home)),
           "copied": moved["copied"], "restart": moved["restart"], "old": moved["old"], "program": moved["program"]})
}

/// Remove Orbit installed for this user: the program, its command (Linux), menu entry or Start-menu shortcut and
/// icon, and its entry in Windows' installed apps. Its settings stay. On Windows a running program's file can't be
/// deleted, so when this Orbit runs from there its folder goes once it has ended ("closing": the caller ends it).
pub fn remove(runner: &Runner) -> Value {
    let (Some(home), Some(target)) = (installed_home(), installed_program()) else {
        return json!({"ok": false, "message": "Orbit isn't installed for you here"});
    };
    if !target.exists() {
        return json!({"ok": false, "message": "Orbit isn't installed for you here"});
    }
    let settings = shown(&crate::config::config_dir());
    if runner.dry_run {
        runner.record("remove", vec![home.to_string_lossy().into()]);
        return json!({"ok": true, "message": "dry run: would remove Orbit"});
    }
    if cfg!(windows) {
        let _ = std::fs::remove_file(windows_link());
        unregister_uninstall();
        if crate::recipes::orbit::running_program().map(|e| same_file(&e, &target)).unwrap_or(false) {
            delete_after_exit(&home);
            return json!({"ok": true, "closing": true, "message": format!("Orbit is removed (its settings stay in {settings}); it closes now")});
        }
        return match std::fs::remove_dir_all(&home) {
            Ok(()) => json!({"ok": true, "message": format!("Orbit is removed (its settings stay in {settings})")}),
            Err(e) => json!({"ok": false, "message": format!("couldn't remove {}: {e}", shown(&home))}),
        };
    }
    let (cmd, (entry, icon)) = (linux_command(), linux_paths());
    if std::fs::read_link(&cmd).map(|t| t == target).unwrap_or(false) {
        let _ = std::fs::remove_file(&cmd);
    }
    let t = target.display();
    let menu_is_ours = std::fs::read_to_string(&entry)
        .map(|text| text.lines().any(|l| l == format!("Exec={t}") || l == format!("Exec=\"{t}\""))).unwrap_or(false);
    if menu_is_ours {
        let _ = std::fs::remove_file(&entry);
        let _ = std::fs::remove_file(&icon);
        refresh_menu(&entry);
    }
    if let Err(e) = std::fs::remove_dir_all(&home) {
        return json!({"ok": false, "message": format!("couldn't remove {}: {e}", shown(&home))});
    }
    json!({"ok": true, "message": format!("Orbit is removed (its settings stay in {settings}); close this window to end it")})
}

/// `orbit --uninstall` (what Settings → Apps → Orbit → Uninstall runs on Windows; works on Linux too): other Orbits
/// running from the installed program end first (its file can't go while they run), then the same as "remove".
pub fn uninstall_cli() -> i32 {
    let Some(target) = installed_program() else {
        eprintln!("orbit: there is no installing Orbit for you on this system");
        return 1;
    };
    let me = std::process::id();
    if cfg!(windows) {
        // the installed program is orbit.exe; downloaded copies keep their own names (orbit-windows-x64.exe …)
        let _ = crate::runner::run_capture(&["taskkill".into(), "/F".into(), "/FI".into(), format!("PID ne {me}"), "/IM".into(), "orbit.exe".into()],
                                           std::time::Duration::from_secs(20), true);
    } else if let Ok(dir) = std::fs::read_dir("/proc") {
        for pid in dir.flatten().filter_map(|e| e.file_name().to_str().and_then(|n| n.parse::<u32>().ok())) {
            let exe = std::fs::read_link(format!("/proc/{pid}/exe")).ok();
            if pid != me && exe.map(|e| e == target).unwrap_or(false) {
                let _ = crate::runner::run_capture(&["kill".into(), pid.to_string()], std::time::Duration::from_secs(5), true);
            }
        }
    }
    let r = remove(&Runner::new(false));
    // (what follows the ";" is about this window, which a command line doesn't have)
    println!("{}", r["message"].as_str().unwrap_or("").split("; ").next().unwrap_or(""));
    if r["ok"] == true { 0 } else { 1 }
}

/// Windows: the installed Orbit's folder is deleted once this Orbit has ended, by a PowerShell with a hidden console
/// that waits for it, then tries for a while (a virus scanner may still be reading the program it just saw written).
fn delete_after_exit(dir: &Path) {
    let d = ps_quote(&dir.to_string_lossy());
    let script = format!("Wait-Process -Id {} -Timeout 300 -ErrorAction SilentlyContinue; \
                          for ($i = 0; $i -lt 60 -and (Test-Path -LiteralPath {d}); $i++) {{ Start-Sleep -Milliseconds 700; \
                          Remove-Item -LiteralPath {d} -Recurse -Force -ErrorAction SilentlyContinue }}", std::process::id());
    let mut c = std::process::Command::new("powershell");
    c.args(["-NoProfile", "-NonInteractive", "-Command", &script])
        .stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000 | 0x0000_0200); // CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP: outlives Orbit, no window
    }
    let _ = c.spawn();
}

/// Windows: Orbit in the list of installed apps, with its version, icon, size and uninstall command.
fn register_uninstall(program: &Path) {
    if !cfg!(windows) {
        return;
    }
    let p = program.to_string_lossy();
    let dir = program.parent().map(|d| d.to_string_lossy().into_owned()).unwrap_or_default();
    let kb = std::fs::metadata(program).map(|m| m.len() / 1024).unwrap_or(0);
    let values = [("DisplayName", "REG_SZ", "Orbit".to_string()), ("DisplayVersion", "REG_SZ", crate::VERSION.to_string()),
                  ("Publisher", "REG_SZ", "NovaLabs".into()), ("DisplayIcon", "REG_SZ", format!("{p},0")),
                  ("InstallLocation", "REG_SZ", dir), ("UninstallString", "REG_SZ", format!("\"{p}\" --uninstall")),
                  ("QuietUninstallString", "REG_SZ", format!("\"{p}\" --uninstall")),
                  ("URLInfoAbout", "REG_SZ", "https://github.com/dash1101/orbit".into()),
                  ("NoModify", "REG_DWORD", "1".into()), ("NoRepair", "REG_DWORD", "1".into()), ("EstimatedSize", "REG_DWORD", kb.to_string())];
    for (name, kind, data) in values {
        let _ = crate::runner::run_capture(&["reg".into(), "add".into(), UNINSTALL_KEY.into(), "/v".into(), name.into(), "/t".into(), kind.into(),
                                              "/d".into(), data, "/f".into()], std::time::Duration::from_secs(10), true);
    }
}

fn unregister_uninstall() {
    let _ = crate::runner::run_capture(&["reg".into(), "delete".into(), UNINSTALL_KEY.into(), "/f".into()], std::time::Duration::from_secs(10), true);
}

/// At start (Windows): Orbit installed for this user, after an update, says its new version in the list of installed apps.
pub fn installed_started() {
    if !cfg!(windows) {
        return;
    }
    let (Some(target), Some(exe)) = (installed_program(), crate::recipes::orbit::running_program()) else { return };
    if same_file(&exe, &target) {
        std::thread::spawn(move || register_uninstall(&target));
    }
}

/// Create (or refresh) the launcher. Writes files, so dry-run only records it.
pub fn add(runner: &Runner) -> Value {
    let exe = program();
    if cfg!(target_os = "linux") {
        let (entry, icon) = linux_paths();
        if runner.dry_run {
            runner.record("write", vec![entry.to_string_lossy().into(), icon.to_string_lossy().into()]);
            return json!({"ok": true, "message": format!("dry run: would add Orbit to your apps menu ({})", entry.display())});
        }
        return add_linux(&exe);
    }
    add_other(runner, &exe)
}

fn add_linux(exe: &str) -> Value {
    let exe = exe.to_string();
    {
        let (entry, icon) = linux_paths();
        let r = (|| -> std::io::Result<()> {
            std::fs::create_dir_all(icon.parent().unwrap())?;
            std::fs::write(&icon, ICON)?;
            std::fs::create_dir_all(entry.parent().unwrap())?;
            let e = if exe.contains(' ') { format!("\"{exe}\"") } else { exe.clone() };
            std::fs::write(&entry, ["[Desktop Entry]", "Type=Application", "Name=Orbit", "GenericName=App hub",
                                    &format!("Comment={COMMENT}"), &format!("Exec={e}"), &format!("Icon={APP_ID}"), "Terminal=false",
                                    "Categories=Utility;System;", &format!("StartupWMClass={APP_ID}"), ""].join("\n"))?;
            if let Some((old_entry, old_icon)) = linux_old_paths() {
                let _ = std::fs::remove_file(old_entry); // replaced by the entry above (its name is the window's id)
                let _ = std::fs::remove_file(old_icon);
            }
            Ok(())
        })();
        if let Err(e) = r {
            return json!({"ok": false, "message": format!("couldn't add Orbit to your apps menu: {e}")});
        }
        refresh_menu(&entry);
        json!({"ok": true, "message": "Orbit is in your apps menu"})
    }
}

fn add_other(runner: &Runner, exe: &str) -> Value {
    let exe = exe.to_string();
    if cfg!(windows) {
        let link = windows_link();
        if runner.dry_run {
            runner.record("write", vec![link.to_string_lossy().into()]);
            return json!({"ok": true, "message": "dry run: would add Orbit to the Start menu"});
        }
        let _ = std::fs::create_dir_all(link.parent().unwrap()); // a new or redirected profile may not have it yet
        let dir = Path::new(&exe).parent().map(|p| p.to_string_lossy().into_owned()).unwrap_or_default();
        let script = format!("$s=(New-Object -ComObject WScript.Shell).CreateShortcut({});$s.TargetPath={};$s.Arguments='';$s.WorkingDirectory={};\
                              $s.Description='One place for YTM Starcast and Nova';$s.Save()",
                             ps_quote(&link.to_string_lossy()), ps_quote(&exe), ps_quote(&dir));
        let (rc, out) = powershell(&script);
        if rc != 0 {
            let why: String = out.trim().chars().take(200).collect();
            return json!({"ok": false, "message": format!("couldn't add Orbit to the Start menu: {}", if why.is_empty() { "unknown error".into() } else { why })});
        }
        return json!({"ok": true, "message": "Orbit is in the Start menu"});
    }
    json!({"ok": false, "message": "on macOS, drag Orbit.app into Applications"})
}

/// An update moved the program (orbit-v0.3.4-linux-x86_64 → orbit-linux-x86_64): Orbit's own launcher, when it
/// starts the old file, now starts the new one. Quietly; a launcher pointing anywhere else is left alone.
pub fn follow(old: &Path, new: &Path) {
    let (old, new) = (old.to_string_lossy().into_owned(), new.to_string_lossy().into_owned());
    if cfg!(target_os = "linux") {
        let entries = [Some(linux_paths().0), linux_old_paths().map(|p| p.0)];
        for entry in entries.into_iter().flatten() {
            let Ok(text) = std::fs::read_to_string(&entry) else { continue };
            let e = if new.contains(' ') { format!("\"{new}\"") } else { new.clone() };
            let lines: Vec<String> = text.split('\n').map(|ln| {
                if ln == format!("Exec={old}") || ln == format!("Exec=\"{old}\"") { format!("Exec={e}") } else { ln.to_string() }
            }).collect();
            let changed = lines.join("\n");
            if changed != text {
                let _ = std::fs::write(&entry, changed);
            }
        }
    } else if cfg!(windows) {
        let link = windows_link();
        if !link.exists() {
            return;
        }
        let shortcut = format!("$s=(New-Object -ComObject WScript.Shell).CreateShortcut({});", ps_quote(&link.to_string_lossy()));
        let (_, target) = powershell(&format!("{shortcut}$s.TargetPath"));
        let target = target.trim();
        let (t, o) = (Path::new(target), Path::new(&old));
        // The same folder can be written two ways (C:\Users\RUNNER~1\… and C:\Users\runneradmin\…): compare the
        // folders themselves rather than the text.
        let same_name = t.file_name().map(|n| n.to_string_lossy().to_lowercase()) == o.file_name().map(|n| n.to_string_lossy().to_lowercase());
        let same_dir = match (t.parent().and_then(|p| std::fs::canonicalize(p).ok()), o.parent().and_then(|p| std::fs::canonicalize(p).ok())) {
            (Some(a), Some(b)) => a == b,
            _ => false,
        };
        if target.is_empty() || !same_name || !same_dir {
            return;
        }
        let dir = Path::new(&new).parent().map(|p| p.to_string_lossy().into_owned()).unwrap_or_default();
        let _ = powershell(&format!("{shortcut}$s.TargetPath={};$s.WorkingDirectory={};$s.Save()", ps_quote(&new), ps_quote(&dir)));
    }
}
