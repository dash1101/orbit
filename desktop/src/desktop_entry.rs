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

/// A path as people read it: ~/… for one in the home folder.
fn shown(p: &Path) -> String {
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
    if cfg!(target_os = "linux") {
        let exe = crate::recipes::orbit::running_program();
        let deb = exe.as_deref().map(crate::recipes::orbit::deb_install).unwrap_or(false);
        let installed = exe.as_deref().map(|e| same_file(e, &linux_program())).unwrap_or(false);
        return json!({"supported": true, "where": "your apps menu", "added": linux_paths().0.exists() || linux_old_paths().is_some(),
                      "install": !deb, "installed": installed, "deb": deb, "home": shown(&linux_home()),
                      "elsewhere": !installed && linux_program().exists()});
    }
    if cfg!(windows) {
        return json!({"supported": true, "where": "the Start menu", "added": windows_link().exists()});
    }
    json!({"supported": false, "where": "", "added": false})
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

/// Move a file, across file systems too (then copied and the original removed, keeping its permissions).
fn move_file(from: &Path, to: &Path) -> std::io::Result<()> {
    if std::fs::rename(from, to).is_ok() {
        return Ok(());
    }
    std::fs::copy(from, to)?;
    std::fs::remove_file(from)
}

/// Install Orbit for this user (Linux): the program moves to ~/.local/share/orbit (an update already waiting next
/// to it comes along), `orbit` starts it from ~/.local/bin, and it gets its apps-menu entry and icon. Nothing needs a
/// password, and the program goes on updating itself there. The running Orbit keeps running from its new place.
pub fn install(runner: &Runner) -> Value {
    if !cfg!(target_os = "linux") {
        return add(runner);
    }
    let Some(exe) = crate::recipes::orbit::running_program() else {
        return json!({"ok": false, "message": "Orbit can't find its own program"});
    };
    if crate::recipes::orbit::deb_install(&exe) {
        return json!({"ok": false, "message": "This Orbit comes from the Debian package: apt keeps it installed and up to date."});
    }
    let (home, target, cmd) = (linux_home(), linux_program(), linux_command());
    if runner.dry_run {
        runner.record("move", vec![exe.to_string_lossy().into(), target.to_string_lossy().into()]);
        runner.record("link", vec![cmd.to_string_lossy().into(), target.to_string_lossy().into()]);
        return json!({"ok": true, "message": format!("dry run: would install Orbit for you in {}", shown(&home))});
    }
    use crate::recipes::orbit::beside;
    let r = (|| -> std::io::Result<()> {
        std::fs::create_dir_all(&home)?;
        if !same_file(&exe, &target) {
            for suffix in ["", ".new", ".new.ok", ".old"] {
                let _ = std::fs::remove_file(beside(&target, suffix)); // an earlier install's, replaced by this one
            }
            move_file(&exe, &target)?;
            for suffix in [".new", ".new.ok"] {
                let waiting = beside(&exe, suffix);
                if waiting.exists() {
                    let _ = move_file(&waiting, &beside(&target, suffix));
                }
            }
        }
        // `orbit` on the command line, unless something else of that name is there already
        let ours = std::fs::read_link(&cmd).map(|t| t == target).unwrap_or(false);
        if !ours && std::fs::symlink_metadata(&cmd).is_err() {
            std::fs::create_dir_all(cmd.parent().unwrap())?;
            #[cfg(unix)]
            std::os::unix::fs::symlink(&target, &cmd)?;
        }
        Ok(())
    })();
    if let Err(e) = r {
        return json!({"ok": false, "message": format!("couldn't install Orbit in {}: {e}", shown(&home))});
    }
    let menu = add_linux(&target.to_string_lossy());
    if menu["ok"] == false {
        return menu;
    }
    json!({"ok": true, "message": format!("Orbit is installed for you, in {} and your apps menu", shown(&home))})
}

/// Remove Orbit installed for this user (Linux): the program, its command, menu entry and icon. Its settings stay.
pub fn remove(runner: &Runner) -> Value {
    let (home, target, cmd) = (linux_home(), linux_program(), linux_command());
    if !cfg!(target_os = "linux") || !target.exists() {
        return json!({"ok": false, "message": "Orbit isn't installed for you here"});
    }
    let (entry, icon) = linux_paths();
    if runner.dry_run {
        runner.record("remove", vec![home.to_string_lossy().into(), entry.to_string_lossy().into(), icon.to_string_lossy().into()]);
        return json!({"ok": true, "message": "dry run: would remove Orbit"});
    }
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
    json!({"ok": true, "message": format!("Orbit is removed (its settings stay in {}); close this window to end it",
                                          shown(&crate::config::config_dir()))})
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
