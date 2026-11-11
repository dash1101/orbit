//! Put Orbit in the apps menu (Linux) or the Start menu (Windows). On macOS, dragging Orbit.app to Applications is
//! the way.
use crate::runner::Runner;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

pub const ICON: &[u8] = include_bytes!("../../orbit/static/icon.svg");

fn linux_paths() -> (PathBuf, PathBuf) {
    let data = std::env::var_os("XDG_DATA_HOME").filter(|v| !v.is_empty()).map(PathBuf::from)
        .unwrap_or_else(|| crate::config::home().join(".local").join("share"));
    (data.join("applications").join("orbit.desktop"), data.join("icons").join("hicolor").join("scalable").join("apps").join("orbit.svg"))
}

fn windows_link() -> PathBuf {
    let appdata = std::env::var_os("APPDATA").map(PathBuf::from).unwrap_or_else(|| crate::config::home().join("AppData").join("Roaming"));
    appdata.join("Microsoft").join("Windows").join("Start Menu").join("Programs").join("Orbit.lnk")
}

fn program() -> String {
    crate::recipes::orbit::running_program().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|| "orbit".into())
}

pub fn state() -> Value {
    if cfg!(target_os = "linux") {
        return json!({"supported": true, "where": "your apps menu", "added": linux_paths().0.exists()});
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
                               std::time::Duration::from_secs(30), true)
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
        let r = (|| -> std::io::Result<()> {
            std::fs::create_dir_all(icon.parent().unwrap())?;
            std::fs::write(&icon, ICON)?;
            std::fs::create_dir_all(entry.parent().unwrap())?;
            let e = if exe.contains(' ') { format!("\"{exe}\"") } else { exe.clone() };
            std::fs::write(&entry, ["[Desktop Entry]", "Type=Application", "Name=Orbit", "GenericName=App hub",
                                    "Comment=One place for your apps", &format!("Exec={e}"), "Icon=orbit", "Terminal=false",
                                    "Categories=Utility;System;", "StartupWMClass=orbit", ""].join("\n"))
        })();
        if let Err(e) = r {
            return json!({"ok": false, "message": format!("couldn't add Orbit to your apps menu: {e}")});
        }
        if crate::runner::which("update-desktop-database").is_some() {
            let _ = crate::runner::run_capture(&["update-desktop-database".into(), entry.parent().unwrap().to_string_lossy().into()],
                                               std::time::Duration::from_secs(10), true);
        }
        return json!({"ok": true, "message": "Orbit is in your apps menu"});
    }
    if cfg!(windows) {
        let link = windows_link();
        if runner.dry_run {
            runner.record("write", vec![link.to_string_lossy().into()]);
            return json!({"ok": true, "message": "dry run: would add Orbit to the Start menu"});
        }
        let _ = std::fs::create_dir_all(link.parent().unwrap()); // a new or redirected profile may not have it yet
        let dir = Path::new(&exe).parent().map(|p| p.to_string_lossy().into_owned()).unwrap_or_default();
        let script = format!("$s=(New-Object -ComObject WScript.Shell).CreateShortcut({});$s.TargetPath={};$s.Arguments='';$s.WorkingDirectory={};\
                              $s.Description='One place for your apps';$s.Save()",
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
        let (entry, _) = linux_paths();
        let Ok(text) = std::fs::read_to_string(&entry) else { return };
        let e = if new.contains(' ') { format!("\"{new}\"") } else { new.clone() };
        let lines: Vec<String> = text.split('\n').map(|ln| {
            if ln == format!("Exec={old}") || ln == format!("Exec=\"{old}\"") { format!("Exec={e}") } else { ln.to_string() }
        }).collect();
        let changed = lines.join("\n");
        if changed != text {
            let _ = std::fs::write(&entry, changed);
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
