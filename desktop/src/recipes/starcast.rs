//! YTM Starcast on Linux, Windows and macOS.
//!
//! * Linux: the same layout as YTM Starcast's own install.sh: its own Python environment in ~/.local/share/ytm-starcast
//!   (from the release's Python package), `ytm-starcast` and `ytm-starcast-gui` in ~/.local/bin, an apps-menu entry,
//!   and a systemd user service that starts at login.
//! * Windows: the release's standalone programs in "%LOCALAPPDATA%\Programs\YTM Starcast", a Start-menu entry for the
//!   window, and the release's own install-exe.ps1 to start it at logon (a per-user scheduled task; no administrator).
//! * macOS: the Python package in "~/Library/Application Support/YTM Starcast" and a LaunchAgent (experimental).
use crate::apps::with;
use crate::engine::{Session, Sources};
use crate::host::Host;
use crate::recipe::{need, option, step, Recipe, RecipeBase, StepDef};
use crate::util::{failed, Error, Res};
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub const PORT: u16 = 8777;
const RUN_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"; // a desktop window's "start at login"
/// The desktop window's id (its GTK application id and window class): the apps-menu entry's StartupWMClass.
const WINDOW_ID: &str = "app.novalabs.starcast";
const OTHER_WAY: &str = "installed another way";

/// Where YTM Starcast lives, by name.
#[derive(Clone, Copy)]
pub struct Names {
    pub slug: &'static str,          // commands, settings folder, Linux folder, menu entry, icon and service
    pub module: &'static str,        // Python import package (its icon is in <module>/static)
    pub dists: &'static [&'static str], // Python distribution name as wheels and dist-info folders spell it
    pub win_dir: &'static str,       // folder on Windows and macOS, Start-menu entry
    pub task: &'static str,          // Windows scheduled task (and a desktop window's start-at-login value)
    pub label: &'static str,         // macOS LaunchAgent
    pub display: &'static str,
}

impl Names {
    fn unit(&self) -> String {
        format!("{}.service", self.slug)
    }
}

pub const NEW: Names = Names { slug: "ytm-starcast", module: "starcast", dists: &["ytm_starcast", "ytm-starcast"], win_dir: "YTM Starcast",
                               task: "YTM Starcast", label: "app.novalabs.starcast.server", display: "YTM Starcast" };

/// Options of the server that an update keeps from the install before it (as YTM Starcast's own install.sh does);
/// `--lan` isn't one of them: it follows the installer's "home Wi-Fi" choice.
const KEPT: [&str; 7] = ["--token", "--port", "--bind", "--allow-host", "--audio", "--eq", "--media"];

/// The kept options in a server command line (after the program itself).
/// The --port in a command's words (--port N or --port=N).
pub fn port_of(words: &[String]) -> Option<u16> {
    words.iter().enumerate().find_map(|(i, w)| {
        if w == "--port" { words.get(i + 1).and_then(|p| p.parse().ok()) } else { w.strip_prefix("--port=").and_then(|p| p.parse().ok()) }
    })
}

pub fn kept_options(words: &[String]) -> Vec<String> {
    let mut out = vec![];
    let mut i = 1;
    while i < words.len() {
        let w = &words[i];
        if KEPT.contains(&w.as_str()) && i + 1 < words.len() {
            out.extend([w.clone(), words[i + 1].clone()]);
            i += 2;
            continue;
        }
        if KEPT.iter().any(|k| w.starts_with(&format!("{k}="))) {
            out.push(w.clone());
        }
        i += 1;
    }
    out
}

fn any() -> Vec<i32> {
    (0..256).collect()
}

fn ps_str(x: &str) -> String {
    format!("'{}'", x.replace('\'', "''"))
}

fn sv(p: &Path) -> String {
    p.to_string_lossy().into_owned()
}

/// Is `link` a symbolic link to something inside `folder`?
fn links_into(link: &Path, folder: &Path) -> bool {
    let Ok(target) = std::fs::read_link(link) else { return false };
    let target = if target.is_absolute() { target } else { link.parent().unwrap_or(Path::new("/")).join(target) };
    let norm = |p: &Path| -> PathBuf {
        let mut out = PathBuf::new();
        for c in p.components() {
            match c {
                std::path::Component::ParentDir => {
                    out.pop();
                }
                std::path::Component::CurDir => {}
                other => out.push(other),
            }
        }
        out
    };
    let (t, f) = (norm(&target), norm(folder));
    t.starts_with(&f) && t != f
}

pub fn plist(label: &str, args: &[String], run_at_load: bool, log: &Path) -> String {
    let esc = |x: &str| x.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;");
    let items: String = args.iter().map(|a| format!("\n        <string>{}</string>", esc(a))).collect();
    let log = esc(&sv(log));
    format!(r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{}</string>
    <key>ProgramArguments</key>
    <array>{items}
    </array>
    <key>RunAtLoad</key>
    <{}/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
</dict>
</plist>
"#, esc(label), if run_at_load { "true" } else { "false" })
}

pub struct StarcastRecipe {
    base: RecipeBase,
    pub port: u16,
    pub wait_seconds: f64,
    pub stop_wait: f64,   // seconds a server started without a service manager gets to end once asked
    pub proc: PathBuf,    // Linux: where a running program's command line is read before it is stopped
}

impl StarcastRecipe {
    pub fn new(host: Host, sources: Arc<Sources>) -> StarcastRecipe {
        StarcastRecipe { base: RecipeBase::new(host, sources), port: PORT, wait_seconds: 30.0, stop_wait: 5.0, proc: "/proc".into() }
    }

    fn h(&self) -> &Host {
        &self.base.host
    }

    // ---- where it lives -------------------------------------------------------------------------
    pub fn app_dir(&self, n: Names) -> PathBuf {
        let h = self.h();
        match h.system.as_str() {
            "windows" => h.local_app_data().join("Programs").join(n.win_dir),
            "darwin" => h.local_app_data().join(n.win_dir),
            _ => h.local_app_data().join(n.slug),
        }
    }
    fn venv(&self, n: Names) -> PathBuf {
        self.app_dir(n).join("venv")
    }
    fn venv_bin(&self, name: &str, n: Names) -> PathBuf {
        self.venv(n).join("bin").join(name)
    }
    fn settings_dir(&self, n: Names) -> PathBuf {
        if self.h().system == "windows" {
            return self.h().config_home().join(n.slug);
        }
        self.h().env.get("XDG_CONFIG_HOME").filter(|v| !v.is_empty()).map(PathBuf::from)
            .unwrap_or_else(|| self.h().home.join(".config")).join(n.slug)
    }
    fn unit_path(&self, n: Names) -> PathBuf {
        self.h().config_home().join("systemd").join("user").join(n.unit())
    }
    fn plist_path(&self, n: Names) -> PathBuf {
        self.h().home.join("Library").join("LaunchAgents").join(format!("{}.plist", n.label))
    }
    fn desktop_path(&self, n: Names) -> PathBuf {
        self.h().local_app_data().join("applications").join(format!("{}.desktop", n.slug))
    }
    fn icon_path(&self, n: Names) -> PathBuf {
        self.h().local_app_data().join("icons/hicolor/scalable/apps").join(format!("{}.svg", n.slug))
    }
    fn shortcut_path(&self, n: Names) -> PathBuf {
        self.h().start_menu().join(format!("{}.lnk", n.win_dir))
    }
    fn links(&self, n: Names) -> Vec<PathBuf> {
        vec![self.h().bin_dir().join(n.slug), self.h().bin_dir().join(format!("{}-gui", n.slug))]
    }
    /// Linux without systemd: the login entry that starts it (what its own install.sh writes then).
    fn autostart_path(&self, n: Names) -> PathBuf {
        self.h().config_home().join("autostart").join(format!("{}.desktop", n.slug))
    }
    fn wheel_name(&self, rel: &Value) -> Res<String> {
        let names: Vec<String> = rel["assets"].as_object().map(|m| m.keys().cloned().collect()).unwrap_or_default();
        names.into_iter().find(|n| NEW.dists.iter().any(|d| n.starts_with(&format!("{d}-"))) && n.ends_with("-py3-none-any.whl"))
            .ok_or_else(|| failed(format!("The {} release doesn't include YTM Starcast's Python package.", rel["tag"].as_str().unwrap_or("")), ""))
    }
    fn zip_name(&self, rel: &Value) -> Res<String> {
        let names: Vec<String> = rel["assets"].as_object().map(|m| m.keys().cloned().collect()).unwrap_or_default();
        names.into_iter().find(|n| n.starts_with(&format!("{}-", NEW.slug)) && n.ends_with("-windows-x64.zip"))
            .ok_or_else(|| failed(format!("The {} release doesn't include the Windows program.", rel["tag"].as_str().unwrap_or("")), ""))
    }

    // ---- what's here ----------------------------------------------------------------------------
    /// (folder, version) of `n` installed in the place Orbit (and the app's own installer) puts it.
    fn layout(&self, n: Names) -> Option<(String, String)> {
        if self.h().system == "windows" {
            if !self.app_dir(n).join(format!("{}.exe", n.slug)).exists() {
                return None;
            }
            let v = std::fs::read_to_string(self.app_dir(n).join("orbit-install.json")).ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok()).and_then(|j| j["version"].as_str().map(String::from)).unwrap_or_default();
            return Some((sv(&self.app_dir(n)), v));
        }
        if !self.venv_bin(n.slug, n).exists() {
            return None;
        }
        Some((sv(&self.app_dir(n)), self.venv_version(n)))
    }

    /// YTM Starcast's version in its environment.
    fn venv_version(&self, n: Names) -> String {
        let dists: Vec<&str> = n.dists.to_vec();
        let lib = self.venv(n).join("lib");
        let pys: Vec<PathBuf> = std::fs::read_dir(&lib).map(|r| r.flatten().map(|e| e.path())
            .filter(|p| p.file_name().map(|x| x.to_string_lossy().starts_with("python")).unwrap_or(false)).collect()).unwrap_or_default();
        for dist in dists {
            let mut found: Vec<String> = vec![];
            for py in &pys {
                if let Ok(rd) = std::fs::read_dir(py.join("site-packages")) {
                    for e in rd.flatten() {
                        let name = e.file_name().to_string_lossy().into_owned();
                        if name.starts_with(&format!("{dist}-")) && name.ends_with(".dist-info") {
                            found.push(name);
                        }
                    }
                }
            }
            found.sort();
            if let Some(last) = found.last() {
                return last[dist.len() + 1..last.len() - ".dist-info".len()].to_string();
            }
        }
        String::new()
    }

    /// The port the installed YTM Starcast answers on (8777 unless it was started with --port).
    fn existing_port(&self) -> u16 {
        port_of(&self.existing_command()).unwrap_or(PORT)
    }

    /// The port this install sets (the option, else the one it had).
    fn chosen_port(&self, s: &Session) -> u16 {
        s.opt_u64("port").and_then(|p| u16::try_from(p).ok()).unwrap_or(self.port)
    }

    fn existing_lan(&self) -> bool {
        let h = self.h();
        if h.system == "windows" {
            let (rc, out) = h.probe(&[h.tool("schtasks"), "/Query".into(), "/TN".into(), NEW.task.into(), "/XML".into()]);
            return rc == 0 && out.contains("--lan");
        }
        let places = if h.system == "linux" {
            vec![self.unit_path(NEW), self.autostart_path(NEW)]
        } else {
            vec![self.plist_path(NEW)]
        };
        for p in places {
            if let Ok(text) = std::fs::read_to_string(&p) {
                return if h.system == "linux" { text.contains("--lan") } else { text.contains("<string>--lan</string>") };
            }
        }
        false
    }

    /// (python, version) for a Python 3.9+ with venv, or (None, why).
    fn python(&self) -> (Option<String>, String) {
        for name in ["python3", "python"] {
            let Some(exe) = self.h().which(name) else { continue };
            let (rc, out) = self.h().probe(&[exe.clone(), "-c".into(), "import sys, venv, ensurepip; print('%d.%d' % sys.version_info[:2])".into()]);
            if rc == 0 {
                let v = out.trim().lines().last().unwrap_or("").to_string();
                let parts: Option<Vec<u32>> = v.split('.').map(|x| x.parse().ok()).collect();
                if let Some(p) = parts {
                    return if p >= vec![3, 9] { (Some(exe), v) } else { (None, format!("Python {v} is too old")) };
                }
            } else {
                return (None, "venv missing".into());
            }
        }
        (None, "not found".into())
    }

    fn tailscale_app(&self) -> bool {
        match self.h().system.as_str() {
            "darwin" => Path::new("/Applications/Tailscale.app").exists(),
            "windows" => self.h().program_files().join("Tailscale").join("tailscale.exe").exists(),
            _ => false,
        }
    }

    // ---- shared steps ----------------------------------------------------------------------------
    fn find(&self, s: &mut Session) -> Res<bool> {
        let rel = s.release(self.repo())?;
        let asset = if self.h().system == "windows" { self.zip_name(&rel)? } else { self.wheel_name(&rel)? };
        s.asset(&rel, "SHA256SUMS.txt", self.repo())?;
        s.detail(&format!("{} · {asset}", rel["tag"].as_str().unwrap_or("")));
        s.vars.insert("asset".into(), json!(asset));
        s.vars.insert("rel".into(), rel);
        let kept = kept_options(&self.existing_command());
        if !kept.is_empty() {
            let shown: Vec<String> = kept.iter().enumerate().map(|(i, w)| if i > 0 && kept[i - 1] == "--token" { "••••••".into() } else if w.starts_with("--token=") { "--token=••••••".into() } else { w.clone() }).collect();
            s.log(&format!("Keeping its options: {}", shown.join(" ")));
        }
        s.vars.insert("kept".into(), json!(kept));
        Ok(true)
    }

    fn download(&self, s: &mut Session) -> Res<bool> {
        let (rel, asset) = (s.vars["rel"].clone(), s.var_str("asset"));
        let f = s.download(&rel, &asset, self.repo(), None)?;
        s.vars.insert("file".into(), json!(sv(&f)));
        Ok(true)
    }

    fn verify(&self, s: &mut Session) -> Res<bool> {
        let rel = s.vars["rel"].clone();
        let sums = s.fetch_text(&rel, "SHA256SUMS.txt", self.repo())?;
        s.verify(&PathBuf::from(s.var_str("file")), &sums, &s.var_str("asset"))?;
        Ok(true)
    }

    /// The server's command line as the install here has it (the service, the login entry or the LaunchAgent), split
    /// into words.
    fn existing_command(&self) -> Vec<String> {
        let h = self.h();
        let words = |line: &str| line.split_whitespace().map(String::from).collect::<Vec<_>>();
        if h.system == "linux" {
            for (path, key) in [(self.unit_path(NEW), "ExecStart="), (self.autostart_path(NEW), "Exec=")] {
                if let Ok(text) = std::fs::read_to_string(&path) {
                    if let Some(line) = text.lines().find_map(|l| l.strip_prefix(key)) {
                        return words(line);
                    }
                }
            }
        } else if h.system == "windows" {
            // the scheduled task's program and arguments
            let (rc, out) = h.probe(&[h.tool("schtasks"), "/Query".into(), "/TN".into(), NEW.task.into(), "/XML".into()]);
            let xml = out.replace('\0', "");
            if rc == 0 {
                let args = xml.split("<Arguments>").nth(1).and_then(|t| t.split("</Arguments>").next()).unwrap_or("");
                let unesc = |x: &str| x.replace("&quot;", "\"").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&");
                let mut w = vec![format!("{}.exe", NEW.slug)];
                w.extend(words(&unesc(args)));
                return w;
            }
        } else if h.system == "darwin" {
            for path in [self.plist_path(NEW)] {
                if let Ok(text) = std::fs::read_to_string(&path) {
                    let Some(arr) = text.split("<key>ProgramArguments</key>").nth(1).and_then(|t| t.split("</array>").next()) else { continue };
                    let unesc = |x: &str| x.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&");
                    return arr.split("<string>").skip(1).filter_map(|x| x.split("</string>").next()).map(unesc).collect();
                }
            }
        }
        vec![]
    }

    fn args(&self, s: &Session) -> Vec<String> {
        // what the install before had (read when the install started: the old service may be gone by now)
        let mut a: Vec<String> = s.vars.get("kept").and_then(|k| k.as_array()).map(|k| k.iter().filter_map(|x| x.as_str().map(String::from)).collect())
            .unwrap_or_default();
        if self.h().system == "darwin" {
            for (k, v) in [("--audio", "none"), ("--media", "none")] {
                if !a.iter().any(|x| x == k || x.starts_with(&format!("{k}="))) {
                    a.extend([k.to_string(), v.to_string()]);
                }
            }
        }
        if s.opt_bool("lan", false) {
            a.push("--lan".into());
        }
        // the port chosen for this install (written only when it isn't the usual one)
        if let Some(p) = s.opt_u64("port") {
            let mut i = 0;
            while i < a.len() {
                if a[i] == "--port" { a.drain(i..(i + 2).min(a.len())); } else if a[i].starts_with("--port=") { a.remove(i); } else { i += 1; }
            }
            if p != PORT as u64 {
                a.extend(["--port".into(), p.to_string()]);
            }
        }
        a
    }

    fn wait(&self, s: &mut Session) -> Res<bool> {
        if !s.wait_http(&format!("http://127.0.0.1:{}/api/info", self.chosen_port(s)), self.wait_seconds, false)? {
            s.vars.insert("slow".into(), json!(true));
            s.log("YTM Starcast hasn't answered yet; it may still be starting. Its log is in Orbit.");
        }
        Ok(true)
    }

    fn remove_files(&self, s: &mut Session) -> Res<bool> {
        let h = self.h();
        let mut paths = vec![self.app_dir(NEW)];
        if h.system == "linux" {
            paths.extend(self.links(NEW).into_iter().filter(|l| links_into(l, &self.app_dir(NEW))));
            paths.extend([self.desktop_path(NEW), self.icon_path(NEW), self.autostart_path(NEW)]);
        }
        if h.system == "windows" {
            paths.push(self.shortcut_path(NEW));
            // the browser extension's folder (on Linux and macOS it's inside the program's folder)
            paths.push(h.local_app_data().join(NEW.slug).join("extension"));
        }
        if s.opt_bool("purge", false) {
            paths.push(self.settings_dir(NEW));
        }
        s.remove(&paths)?;
        Ok(true)
    }

    // ---- Linux and macOS ---------------------------------------------------------------------------
    fn venv_step(&self, s: &mut Session) -> Res<bool> {
        let (py, why) = self.python();
        let Some(py) = py else {
            return Err(failed(format!("Python isn't ready ({why})."), "See “what this computer needs” and try again."));
        };
        if self.venv(NEW).join("bin/python").exists() {
            s.detail("already there");
            return Ok(false);
        }
        s.run(&[py, "-m".into(), "venv".into(), "--system-site-packages".into(), sv(&self.venv(NEW))], &[0], false,
              Some("Couldn't create YTM Starcast's Python environment."), "On Debian/Ubuntu install python3-venv first.")?;
        Ok(true)
    }

    fn pip(&self, s: &mut Session) -> Res<bool> {
        s.detail(&s.var_str("asset"));
        let argv: Vec<String> = [sv(&self.venv_bin("python", NEW)), "-m".into(), "pip".into(), "install".into(), "--quiet".into(),
                                 "--disable-pip-version-check".into(), "--no-input".into(), "--no-deps".into(), "--force-reinstall".into(),
                                 s.var_str("file")].to_vec();
        s.run(&argv, &[0], false, Some("Couldn't install YTM Starcast into its Python environment."), "")?;
        Ok(true)
    }

    fn ytdlp(&self, s: &mut Session) -> Res<bool> {
        s.detail("from PyPI");
        let argv: Vec<String> = [sv(&self.venv_bin("python", NEW)), "-m".into(), "pip".into(), "install".into(), "--quiet".into(),
                                 "--disable-pip-version-check".into(), "--no-input".into(), "--upgrade".into(), "yt-dlp".into()].to_vec();
        match s.run(&argv, &[0], false, None, "") {
            Ok(_) => Ok(true),
            Err(Error::Cancelled) => Err(Error::Cancelled),
            Err(e) => {
                s.log(&format!("Note: couldn't add yt-dlp ({e}); the phone will load songs on its own only."));
                s.detail("skipped: couldn't reach PyPI");
                Ok(false)
            }
        }
    }

    fn menu(&self, s: &mut Session) -> Res<bool> {
        let h = self.h();
        s.symlink(&self.venv_bin(NEW.slug, NEW), &h.bin_dir().join(NEW.slug))?;
        // YTM Starcast's own window (and its menu entry): only when wanted; its marker keeps its own updates agreeing
        let marker = self.app_dir(NEW).join("no-desktop-app");
        if !s.opt_bool("desktop_app", false) {
            for p in [h.bin_dir().join(format!("{}-gui", NEW.slug)), self.desktop_path(NEW)] {
                if p.exists() || std::fs::symlink_metadata(&p).is_ok() {
                    s.remove(std::slice::from_ref(&p))?;
                }
            }
            s.write_file(&marker, "Orbit shows what YTM Starcast's window did (left out when installed from Orbit)\n", None)?;
            return Ok(true);
        }
        if marker.exists() {
            s.remove(std::slice::from_ref(&marker))?;
        }
        s.symlink(&self.venv_bin(&format!("{}-gui", NEW.slug), NEW), &h.bin_dir().join(format!("{}-gui", NEW.slug)))?;
        if !s.dry_run {
            let lib = self.venv(NEW).join("lib");
            let icon = std::fs::read_dir(&lib).ok().and_then(|rd| rd.flatten()
                .map(|e| e.path().join("site-packages").join(NEW.module).join("static/icon.svg")).find(|p| p.exists()));
            if let Some(icon) = icon {
                let text = std::fs::read_to_string(&icon).unwrap_or_default();
                s.write_file(&self.icon_path(NEW), &text, None)?;
            }
        }
        // the same entry as YTM Starcast's own install.sh writes
        let entry = ["[Desktop Entry]".to_string(), "Type=Application".into(), format!("Name={}", NEW.display), "GenericName=YouTube Music companion".into(),
                     "Comment=A remote for your phone, your music as your Discord status, and a better YouTube Music".into(),
                     format!("Exec={}", sv(&h.bin_dir().join(format!("{}-gui", NEW.slug)))), format!("Icon={}", NEW.slug), "Terminal=false".into(),
                     "Categories=AudioVideo;Audio;Utility;".into(), "Keywords=YouTube Music;Discord;remote;".into(),
                     format!("StartupWMClass={WINDOW_ID}"), String::new()].join("\n");
        s.write_file(&self.desktop_path(NEW), &entry, None)?;
        Ok(true)
    }

    /// No systemd here, and YTM Starcast already started at login through a login entry: it keeps one.
    fn xdg_autostart(&self, s: &mut Session) -> Res<bool> {
        let exe = std::iter::once(sv(&self.venv_bin(NEW.slug, NEW))).chain(self.args(s)).collect::<Vec<_>>().join(" ");
        let entry = ["[Desktop Entry]".to_string(), "Type=Application".into(), format!("Name={}", NEW.display), format!("Exec={exe}"),
                     "Terminal=false".into(), String::new()].join("\n");
        s.write_file(&self.autostart_path(NEW), &entry, None)?;
        Ok(true)
    }

    fn systemd_step(&self, s: &mut Session) -> Res<bool> {
        let sc = self.h().tool("systemctl");
        let exec_start = std::iter::once(sv(&self.venv_bin(NEW.slug, NEW))).chain(self.args(s)).collect::<Vec<_>>().join(" ");
        let unit = ["[Unit]".to_string(), format!("Description={} - YouTube Music companion", NEW.display), "After=network-online.target sound.target".into(),
                    String::new(), "[Service]".into(), format!("ExecStart={exec_start}"), "Restart=on-failure".into(), "RestartSec=3".into(),
                    String::new(), "[Install]".into(), "WantedBy=default.target".into(), String::new()].join("\n");
        s.write_file(&self.unit_path(NEW), &unit, None)?;
        s.run(&[sc.clone(), "--user".into(), "daemon-reload".into()], &[0], false, None, "")?;
        let verb = if s.opt_bool("autostart", true) { "enable" } else { "disable" };
        s.run(&[sc.clone(), "--user".into(), verb.into(), NEW.unit()], &[0], false, None, "")?;
        s.run(&[sc, "--user".into(), "restart".into(), NEW.unit()], &[0], false, Some("YTM Starcast's service didn't start."),
              "Open YTM Starcast in Orbit to read its log.")?;
        Ok(true)
    }

    fn launchd(&self, s: &mut Session) -> Res<bool> {
        let args: Vec<String> = std::iter::once(sv(&self.venv_bin(NEW.slug, NEW))).chain(self.args(s)).collect();
        let log = self.h().home.join("Library/Logs").join(format!("{}.log", NEW.slug));
        s.write_file(&self.plist_path(NEW), &plist(NEW.label, &args, s.opt_bool("autostart", true), &log), None)?;
        let (lc, dom) = (self.h().tool("launchctl"), format!("gui/{}", self.h().uid()));
        let p = sv(&self.plist_path(NEW));
        s.run(&[lc.clone(), "bootout".into(), dom.clone(), p.clone()], &any(), true, None, "")?;
        s.run(&[lc.clone(), "bootstrap".into(), dom.clone(), p], &[0], false, Some("macOS didn't start YTM Starcast."), "")?;
        s.run(&[lc, "kickstart".into(), format!("{dom}/{}", NEW.label)], &any(), true, None, "")?; // now, even when not at login
        Ok(true)
    }

    fn unix_unservice(&self, s: &mut Session) -> Res<bool> {
        if self.h().system == "linux" {
            if self.h().has_systemd() {
                let sc = self.h().tool("systemctl");
                s.run(&[sc.clone(), "--user".into(), "disable".into(), "--now".into(), NEW.unit()], &any(), false, None, "")?;
                s.remove(&[self.unit_path(NEW)])?;
                s.run(&[sc, "--user".into(), "daemon-reload".into()], &any(), false, None, "")?;
            }
            if self.stop_running(s, &[NEW])?.is_empty() && !self.h().has_systemd() {
                return Ok(false);
            }
        } else {
            s.run(&[self.h().tool("launchctl"), "bootout".into(), format!("gui/{}", self.h().uid()), sv(&self.plist_path(NEW))], &any(), true, None, "")?;
            s.remove(&[self.plist_path(NEW)])?;
        }
        Ok(true)
    }

    // ---- a server no service manager stops (Linux) ---------------------------------------------
    fn cmdline(&self, pid: i64) -> Vec<u8> {
        std::fs::read(self.proc.join(pid.to_string()).join("cmdline")).unwrap_or_default()
    }

    /// Is this command line the server of one of `names`: `<program> …`, `python …/<program> …` or `python -m <module> …`?
    fn is_server(cmdline: &[u8], names: &[Names]) -> bool {
        let mut argv: Vec<String> = cmdline.split(|b| *b == 0).map(|a| String::from_utf8_lossy(a).into_owned()).collect();
        argv.extend([String::new(), String::new(), String::new()]);
        let base = |a: &str| Path::new(a).file_name().map(|x| x.to_string_lossy().into_owned()).unwrap_or_default();
        names.iter().any(|n| n.slug == base(&argv[0]) || n.slug == base(&argv[1]) || (argv[1] == "-m" && argv[2] == n.module))
    }

    /// The servers of `names` running here without a service manager: one a desktop window started (it writes
    /// `server.pid` in the settings folder), and one a login entry or a terminal started from the program in its
    /// own folder. A pid file counts only when that process's command line is the server's.
    fn running(&self, names: &[Names]) -> Vec<(i64, Option<PathBuf>, Vec<u8>)> {
        if self.h().system != "linux" || !self.proc.is_dir() {
            return vec![];
        }
        let mut found: std::collections::BTreeMap<i64, (Option<PathBuf>, Vec<u8>)> = Default::default();
        for n in [NEW] {
            let pidfile = self.settings_dir(n).join("server.pid");
            let Ok(text) = std::fs::read(&pidfile) else { continue };
            let digits: String = String::from_utf8_lossy(&text).chars().filter(|c| c.is_ascii_digit()).take(9).collect();
            let pid: i64 = digits.parse().unwrap_or(0);
            let cmd = if pid > 1 { self.cmdline(pid) } else { vec![] };
            if !cmd.is_empty() && Self::is_server(&cmd, names) && !found.contains_key(&pid) {
                found.insert(pid, (Some(pidfile), cmd));
            }
        }
        // the program in its folder
        let real = |p: &Path| std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
        let programs: Vec<PathBuf> = names.iter().map(|n| real(&self.venv_bin(n.slug, *n))).collect();
        #[cfg(unix)]
        let uid = self.h().uid();
        for d in std::fs::read_dir(&self.proc).map(|r| r.flatten().collect::<Vec<_>>()).unwrap_or_default() {
            let name = d.file_name().to_string_lossy().into_owned();
            let Ok(pid) = name.parse::<i64>() else { continue };
            if found.contains_key(&pid) {
                continue;
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::MetadataExt;
                if d.metadata().map(|m| m.uid() != uid).unwrap_or(true) {
                    continue;
                }
            }
            let Ok(cmd) = std::fs::read(d.path().join("cmdline")) else { continue };
            let first: Vec<&[u8]> = cmd.split(|b| *b == 0).take(2).collect();
            if first.iter().any(|a| !a.is_empty() && programs.contains(&real(Path::new(&*String::from_utf8_lossy(a))))) {
                found.insert(pid, (None, cmd));
            }
        }
        found.remove(&(std::process::id() as i64));
        found.into_iter().map(|(p, (f, c))| (p, f, c)).collect()
    }

    /// Stop what running() finds the way the desktop window stops it (SIGTERM), and wait for it to end.
    fn stop_running(&self, s: &mut Session, names: &[Names]) -> Res<Vec<(i64, Option<PathBuf>, Vec<u8>)>> {
        let found = self.running(names);
        for (pid, pidfile, cmd) in &found {
            let what = format!("process {pid}{}", pidfile.as_ref().map(|f| format!(" (started by its desktop window: {})", f.display())).unwrap_or_default());
            if s.dry_run {
                s.log(&format!("dry run: would stop {what}"));
                continue;
            }
            s.log(&format!("Stopping {what}"));
            #[cfg(unix)]
            unsafe {
                if libc::kill(*pid as i32, libc::SIGTERM) != 0 {
                    let e = std::io::Error::last_os_error();
                    if e.raw_os_error() != Some(libc::ESRCH) {
                        s.log(&format!("Couldn't stop process {pid}: {e}"));
                    }
                }
            }
            for _ in 0..(self.stop_wait * 4.0) as i64 {
                if &self.cmdline(*pid) != cmd {
                    break; // ended (a process that has ended shows no command line)
                }
                s.sleep(0.25)?;
            }
            if &self.cmdline(*pid) == cmd {
                s.vars.insert("still_running".into(), json!(pid));
                s.log(&format!("Note: process {pid} is still running. Stop it with `kill {pid}`, or restart the computer."));
            } else if let Some(f) = pidfile {
                s.remove(&[f.clone()])?; // so nothing later stops another program that gets the same number
            }
        }
        Ok(found)
    }

    // ---- Windows ------------------------------------------------------------------------------------
    fn ps(&self, s: &Session, script: &str, fail: Option<&str>) -> Res<(i32, String)> {
        s.run(&[self.h().tool("powershell"), "-NoProfile".into(), "-NonInteractive".into(), "-ExecutionPolicy".into(), "Bypass".into(),
                "-Command".into(), script.into()], &[0], false, fail, "")
    }

    fn unpack(&self, s: &mut Session) -> Res<bool> {
        let d = s.extract(&PathBuf::from(s.var_str("file")))?;
        let exe = s.find(&d, &format!("{}.exe", NEW.slug))?;
        s.vars.insert("src".into(), json!(sv(exe.parent().unwrap_or(&d))));
        Ok(true)
    }

    /// Stop YTM Starcast wherever it was installed.
    fn win_stop(&self, s: &mut Session) -> Res<bool> {
        s.run(&[self.h().tool("schtasks"), "/End".into(), "/TN".into(), NEW.task.into()], &any(), true, None, "")?;
        for exe in [format!("{}.exe", NEW.slug), format!("{}-gui.exe", NEW.slug)] {
            s.run(&[self.h().tool("taskkill"), "/IM".into(), exe, "/F".into()], &any(), true, None, "")?;
        }
        s.sleep(0.5)?;
        Ok(true)
    }

    fn win_copy(&self, s: &mut Session) -> Res<bool> {
        s.copy_tree(&PathBuf::from(s.var_str("src")), &self.app_dir(NEW))?;
        let v = s.vars["rel"]["tag"].as_str().unwrap_or("").trim_start_matches('v').to_string();
        s.write_file(&self.app_dir(NEW).join("orbit-install.json"), &json!({"version": v}).to_string(), None)?;
        Ok(true)
    }

    fn win_shortcut(&self, s: &mut Session) -> Res<bool> {
        let target = self.app_dir(NEW).join(format!("{}-gui.exe", NEW.slug));
        let link = self.shortcut_path(NEW);
        if !s.opt_bool("desktop_app", false) {
            if link.exists() {
                s.remove(std::slice::from_ref(&link))?;
            }
            s.detail("left out: Orbit shows what YTM Starcast's window did");
            return Ok(true);
        }
        // the Start menu's folder can be missing (a new or redirected profile): make it first
        let script = format!("New-Item -ItemType Directory -Force -Path {} | Out-Null;$s=(New-Object -ComObject WScript.Shell).CreateShortcut({});\
                              $s.TargetPath={};$s.WorkingDirectory={};$s.Description='YouTube Music companion';$s.Save()",
                             ps_str(&sv(link.parent().unwrap())), ps_str(&sv(&link)), ps_str(&sv(&target)), ps_str(&sv(&self.app_dir(NEW))));
        self.ps(s, &script, Some("Couldn't add YTM Starcast to the Start menu."))?;
        Ok(true)
    }

    fn win_task(&self, s: &mut Session) -> Res<bool> {
        let script = self.app_dir(NEW).join("install-exe.ps1");
        if s.opt_bool("autostart", true) {
            // -Lan is always given: without it the script keeps the --lan its task had before
            let lan = if s.opt_bool("lan", false) { "-Lan" } else { "-Lan:$false" };
            // YTM Starcast 0.2.0a10 and newer: its script takes -NoDesktopApp and -Port (and keeps them at updates)
            let text = if s.dry_run { String::new() } else { std::fs::read_to_string(&script).unwrap_or_default() };
            let port = self.chosen_port(s);
            let mut more = String::new();
            if text.contains("NoDesktopApp") {
                more.push_str(if s.opt_bool("desktop_app", false) { " -NoDesktopApp:$false" } else { " -NoDesktopApp" });
            }
            let takes_port = text.contains("$Port");
            if takes_port {
                more.push_str(&format!(" -Port {port}"));
            }
            self.ps(s, &format!("& {} {lan}{more}; exit 0", ps_str(&sv(&script))), Some("Couldn't set YTM Starcast to start at logon."))?;
            if takes_port {
                return Ok(true);
            }
            // an older YTM Starcast: its port goes into the task's arguments here (8777 needs none)
            let add = if port == PORT { String::new() } else { format!(" --port {port}") };
            self.ps(s, &format!("$t = Get-ScheduledTask -TaskName {}; $a = $t.Actions[0]; \
                                 $a.Arguments = (($a.Arguments -replace '\\s*--port(=|\\s+)\\d+', '') + '{add}').Trim(); \
                                 Set-ScheduledTask -TaskName {} -Action $a | Out-Null", ps_str(NEW.task), ps_str(NEW.task)),
                    Some("Couldn't set YTM Starcast's port."))?;
        } else {
            // only the scheduled task: install-exe.ps1 -Uninstall removes the whole program as well
            s.run(&[self.h().tool("schtasks"), "/Delete".into(), "/TN".into(), NEW.task.into(), "/F".into()], &any(), true, None, "")?;
        }
        Ok(true)
    }

    fn win_start(&self, s: &mut Session) -> Res<bool> {
        // Started here with no window, also when its scheduled task starts it at logon: the task runs the program in
        // the user's session, where a console program shows a console window (closing it would end YTM Starcast).
        // YTM Starcast's installer starts its task too: that copy ends here, and the task's own command starts hidden.
        let a = self.args(s).join(" ");
        let args = if a.is_empty() { format!("--port {PORT}") } else { a };
        let (task, exe, dir) = (ps_str(NEW.task), ps_str(&sv(&self.app_dir(NEW).join(format!("{}.exe", NEW.slug)))), ps_str(&sv(&self.app_dir(NEW))));
        self.ps(s, &format!("$t = Get-ScheduledTask -TaskName {task} -ErrorAction SilentlyContinue; \
                             if ($t) {{ Stop-ScheduledTask -TaskName {task} -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 800; \
                               $a = $t.Actions[0]; $p = @{{ WindowStyle = 'Hidden'; FilePath = $a.Execute.Trim('\"'); WorkingDirectory = $a.WorkingDirectory }}; \
                               if ($a.Arguments) {{ $p.ArgumentList = $a.Arguments }}; Start-Process @p }} \
                             else {{ Start-Process -WindowStyle Hidden -FilePath {exe} -ArgumentList {} -WorkingDirectory {dir} }}", ps_str(&args)),
                Some("Couldn't start YTM Starcast."))?;
        self.wait(s)
    }

    fn win_untask(&self, s: &mut Session) -> Res<bool> {
        let script = self.app_dir(NEW).join("install-exe.ps1");
        if script.exists() {
            s.run(&[self.h().tool("powershell"), "-NoProfile".into(), "-NonInteractive".into(), "-ExecutionPolicy".into(), "Bypass".into(),
                    "-File".into(), sv(&script), "-Uninstall".into()], &any(), false, None, "")?;
        } else {
            s.run(&[self.h().tool("schtasks"), "/Delete".into(), "/TN".into(), NEW.task.into(), "/F".into()], &any(), true, None, "")?;
        }
        s.run(&[self.h().tool("reg"), "delete".into(), RUN_KEY.into(), "/v".into(), NEW.task.into(), "/f".into()], &any(), true, None, "")?;
        Ok(true)
    }
}

impl Recipe for StarcastRecipe {
    fn base(&self) -> &RecipeBase {
        &self.base
    }
    fn id(&self) -> &str {
        "ytm-starcast"
    }
    fn name(&self) -> &str {
        "YTM Starcast"
    }
    fn repo(&self) -> &str {
        "dash1101/ytm-starcast"
    }
    fn blurb(&self) -> &str {
        "YouTube Music companion: a remote for your phone, your Discord status and a better YouTube Music"
    }
    fn glyph(&self) -> &str {
        "S"
    }
    fn color(&self) -> &str {
        "#38d9c4"
    }
    fn platform(&self, system: &str) -> (String, String) {
        match system {
            "linux" | "windows" => ("full".into(), String::new()),
            "darwin" => ("limited".into(), "Experimental: YTM Starcast isn't tested on macOS yet. Its YouTube Music remote and your Discord status \
                                            are meant to work there; changing the Mac's volume and controlling other players aren't available on macOS.".into()),
            _ => ("none".into(), "YTM Starcast isn't available on this system yet.".into()),
        }
    }

    fn detect(&self) -> Value {
        if let Some((w, v)) = self.layout(NEW) {
            return json!({"installed": true, "version": v, "where": w,
                          "how": if self.h().system == "windows" { "standalone program" } else { "its own Python environment" }});
        }
        match self.h().which(NEW.slug) {
            Some(other) => json!({"installed": true, "version": "", "where": other, "how": OTHER_WAY}),
            None => json!({"installed": false, "version": "", "where": "", "how": ""}),
        }
    }

    fn can_uninstall(&self) -> (bool, String) {
        let inst = self.installed();
        if inst["installed"].as_bool().unwrap_or(false) && inst["how"] == OTHER_WAY {
            return (false, format!("YTM Starcast at {} was installed another way (with pip, or run from its source folder), so Orbit leaves it \
                                    alone. Remove it the way it was installed.", inst["where"].as_str().unwrap_or("")));
        }
        (true, String::new())
    }

    fn requirements(&self, op: &str) -> Vec<Value> {
        if op == "uninstall" {
            return vec![];
        }
        let h = self.h();
        let discord = need("Discord desktop app (for your Discord status)", true, "", false,
                           "YTM Starcast shows your status through the Discord app and reconnects whenever Discord starts, so Discord can be installed later.");
        if h.system == "windows" {
            return vec![need("Windows 10 or 11 (64-bit)", h.arch == "amd64",
                             "The Windows program is built for 64-bit Intel/AMD processors. Windows on ARM isn't supported yet.", true, ""), discord];
        }
        let (py, v) = self.python();
        let detail = if py.is_some() { format!("Python {v}") } else { String::new() };
        let mut out = vec![];
        if h.system == "linux" {
            let hint = match v.as_str() {
                "venv missing" => "Install python3-venv (Debian/Ubuntu: sudo apt install python3-venv).".to_string(),
                "not found" => "Install Python 3 from your distribution (sudo apt install python3 python3-venv).".to_string(),
                other => format!("{other}; install a newer Python 3."),
            };
            out.push(need("Python 3.9 or newer with venv", py.is_some(), &hint, true, &detail));
            out.push(need("pactl (sound control)", h.which("pactl").is_some(),
                          "Install pulseaudio-utils; it works with PipeWire too (Debian/Ubuntu: sudo apt install pulseaudio-utils).", true, ""));
            out.push(need("busctl (control other music players)", h.which("busctl").is_some(),
                          "Part of systemd. Without it YTM Starcast only controls volume and YouTube Music.", false, ""));
            out.push(need("systemd (start at login)", h.has_systemd(),
                          "Without systemd YTM Starcast installs, but you start it yourself with `ytm-starcast`.", false, ""));
        } else {
            out.push(need("Python 3.9 or newer", py.is_some(), "Install Python 3 from python.org (or run `xcode-select --install`).", true, &detail));
        }
        out.push(need("Tailscale (reach it from your phone anywhere)", h.which("tailscale").is_some() || self.tailscale_app(),
                      "Optional. Without it, turn on “home Wi-Fi” below, or use YTM Starcast on this computer only.", false, ""));
        out.push(discord);
        out
    }

    fn options(&self, op: &str) -> Vec<Value> {
        let h = self.h();
        if op == "uninstall" {
            return vec![option("purge", "Also delete YTM Starcast's settings", "bool", json!(false),
                               &format!("Your settings are in {}. Leave it off to keep them.",
                                        self.settings_dir(NEW).display()))];
        }
        let mut opts = vec![];
        if h.system != "linux" || h.has_systemd() {
            opts.push(option("autostart", "Start when I log in", "bool", json!(true), "Keeps the remote and your Discord status going without opening anything."));
        }
        opts.push(with(option("port", "Port", "int", json!(self.existing_port()),
                              "Where YTM Starcast answers on this computer (usually 8777). Phones and the browser extension use it too: \
                               change it only when another program needs this one."), json!({"min": 1024, "max": 65535})));
        opts.push(option("desktop_app", "YTM Starcast's own window too", "bool", json!(false),
                         "Orbit shows what it did (the phone addresses with a QR code, the switches, the log), so it's left out \
                          unless you want it. Turning this off removes it."));
        opts.push(option("lan", "Also reach it on my home Wi-Fi", "bool", json!(self.existing_lan()),
                         "Lets phones on the same network connect. It then asks for an access token, which YTM Starcast shows you. Not needed with Tailscale."));
        if h.system != "windows" {
            opts.push(option("ytdlp", "Let this computer fetch songs for the phone", "bool", json!(true),
                             "Installs yt-dlp into YTM Starcast's own environment, used when the phone can't load a song by itself."));
        }
        opts
    }

    fn assets(&self, rel: &Value) -> Vec<String> {
        let r = if self.h().system == "windows" { self.zip_name(rel) } else { self.wheel_name(rel) };
        r.map(|n| vec![n]).unwrap_or_default()
    }

    fn summary(&self, op: &str, opts: &Map<String, Value>, rel: Option<&Value>) -> Vec<String> {
        let h = self.h();
        let opt = |k: &str| opts.get(k).and_then(|v| v.as_bool());
        if op == "uninstall" {
            let mut lines = vec!["Stop YTM Starcast and stop it starting at login".to_string()];
            if let Some((w, _)) = self.layout(NEW) {
                lines.push(format!("Remove {w}"));
            }
            if h.system == "linux" {
                lines.push("Remove its apps-menu entry and the ytm-starcast commands".into());
            }
            if h.system == "windows" {
                lines.push("Remove its Start-menu entry".into());
            }
            lines.push(if opt("purge").unwrap_or(false) { "Delete your settings too".to_string() }
                       else { format!("Keep your settings ({})", self.settings_dir(NEW).display()) });
            lines.push("Remove YTM Starcast's browser extension yourself afterwards, if you added it (browsers only allow that by hand)".into());
            return lines;
        }
        let v = rel.and_then(|r| r["tag"].as_str()).unwrap_or("the latest release");
        let mut lines = vec![];
        lines.push(format!("Download YTM Starcast {v} from GitHub and check its checksum"));
        let window = opt("desktop_app").unwrap_or(false);
        if h.system == "windows" {
            lines.push(format!("Put the program in {}{}", self.app_dir(NEW).display(),
                               if window { ", with YTM Starcast's own window in the Start menu" } else { " (YTM Starcast's own window left out: Orbit shows what it did)" }));
        } else {
            lines.push(format!("Install it with its own Python in {}", self.app_dir(NEW).display()));
            if opt("ytdlp").unwrap_or(false) {
                lines.push("Add yt-dlp so this computer can fetch songs for the phone (from PyPI)".into());
            }
            if h.system == "linux" && window {
                lines.push("Add YTM Starcast's own window to your apps menu".into());
            }
        }
        if opt("autostart").unwrap_or(h.system == "linux" && h.has_systemd()) {
            lines.push("Start it now and whenever you log in".into());
        } else if h.system == "linux" && !h.has_systemd() && self.autostart_path(NEW).exists() {
            lines.push("Start it when you log in, as before".into());
        } else if h.system == "linux" && !h.has_systemd() {
            lines.push("You start it yourself with `ytm-starcast` (no systemd here)".into());
        } else {
            lines.push("Start it now (not at login)".into());
        }
        lines.push(if opt("lan").unwrap_or(false) { "Listen on this computer, Tailscale and your home Wi-Fi (with an access token)" }
                   else { "Listen on this computer and Tailscale only" }.into());
        lines.push("No administrator password needed".into());
        lines
    }

    fn steps(self: Arc<Self>, op: &str, opts: &Map<String, Value>) -> Vec<StepDef> {
        let me = |f: fn(&StarcastRecipe, &mut Session) -> Res<bool>| {
            let r = self.clone();
            move |s: &mut Session| f(&r, s)
        };
        let sysname = self.h().system.clone();
        let opt = |k: &str| opts.get(k).and_then(|v| v.as_bool()).unwrap_or(false);
        if op == "uninstall" {
            let mut v = if sysname == "windows" {
                vec![step("stop", "Stop YTM Starcast", 1.0, me(Self::win_stop)),
                     step("task", "Stop it starting at logon", 1.0, me(Self::win_untask))]
            } else {
                vec![step("stop", "Stop YTM Starcast and its start-at-login", 1.0, me(Self::unix_unservice))]
            };
            v.push(step("files", "Remove the program", 1.0, me(Self::remove_files)));
            return v;
        }
        let mut steps = vec![step("find", "Find the latest release", 0.5, me(Self::find))];
        if sysname == "windows" {
            steps.extend([
                step("download", "Download", 5.0, me(Self::download)),
                step("verify", "Check the download", 0.5, me(Self::verify)),
                step("unpack", "Unpack", 1.0, me(Self::unpack)),
                step("stop", "Stop the running copy", 0.5, me(Self::win_stop)),
                step("copy", "Install the program", 1.0, me(Self::win_copy)),
                step("shortcut", "Its own window in the Start menu (if chosen)", 0.5, me(Self::win_shortcut)),
            ]);
            steps.push(step("task", if opt("autostart") { "Start at logon" } else { "Turn off start at logon" }, 1.0, me(Self::win_task)));
            steps.push(step("start", "Start YTM Starcast", 1.5, me(Self::win_start)));
            return steps;
        }
        steps.extend([
            step("download", "Download", 1.5, me(Self::download)),
            step("verify", "Check the download", 0.5, me(Self::verify)),
            step("venv", "Set up its own Python", 2.0, me(Self::venv_step)),
            step("pip", "Install YTM Starcast", 2.0, me(Self::pip)),
        ]);
        if opt("ytdlp") {
            steps.push(step("ytdlp", "Add yt-dlp", 3.0, me(Self::ytdlp)));
        }
        if sysname == "linux" {
            steps.push(step("menu", "The ytm-starcast command (and its window, if chosen)", 0.5, me(Self::menu)));
            if !self.h().has_systemd() && self.autostart_path(NEW).exists() {
                steps.push(step("login", "Start it when you log in, as before", 0.5, me(Self::xdg_autostart)));
            }
            if self.h().has_systemd() {
                steps.push(step("service", if opt("autostart") { "Start it now and at login" } else { "Start it now" }, 1.0, me(Self::systemd_step)));
                steps.push(step("check", "Check that it answers", 1.0, me(Self::wait)));
            }
        } else {
            steps.push(step("agent", if opt("autostart") { "Start it now and at login" } else { "Start it now" }, 1.0, me(Self::launchd)));
            steps.push(step("check", "Check that it answers", 1.0, me(Self::wait)));
        }
        steps
    }

    fn finish(&self, sess: &Session, op: &str, opts: &Map<String, Value>) -> Value {
        let v = |k: &str| sess.vars.get(k).cloned().unwrap_or(Value::Null);
        let purge = opts.get("purge").and_then(|x| x.as_bool()).unwrap_or(false);
        if op == "uninstall" {
            let mut body = vec![];
            if !purge {
                body.push(format!("Your settings are kept in {}.", self.settings_dir(NEW).display()));
            }
            body.push("If you added YTM Starcast's browser extension, remove it from your browser's extensions page.".into());
            return json!({"headline": "YTM Starcast is removed.", "body": body.join(" "), "next": []});
        }
        let mut note = String::new();
        if let Some(pid) = v("still_running").as_i64() {
            note = format!(" An older copy is still running (process {pid}) and keeps YTM Starcast's port: stop it with `kill {pid}`, or restart the computer.");
        }
        let h = self.h();
        let port = opts.get("port").and_then(|p| p.as_u64()).unwrap_or(PORT as u64);
        let nxt = json!([{"id": "app", "label": "open in orbit", "kind": "app", "app": self.id(), "primary": true},
                         {"id": "web", "label": "open ytm starcast", "kind": "url", "url": format!("http://127.0.0.1:{port}/")},
                         {"id": "extension", "label": "set up the browser extension", "kind": "url", "url": format!("http://127.0.0.1:{port}/#extension")}]);
        let (mut head, mut body) = if h.system == "linux" && !h.has_systemd() {
            ("YTM Starcast is installed.".to_string(), "Start it with `ytm-starcast` in a terminal; it prints the address to open on your phone.".to_string())
        } else {
            ("YTM Starcast is installed and running.".to_string(),
             "On your phone, open this computer's address on port 8777 (YTM Starcast's window shows it, with a QR code), then add it to your home screen.".to_string())
        };
        if v("slow") == json!(true) {
            head = "YTM Starcast is installed.".into();
            body = format!("It hasn't answered yet; give it a few seconds, or read its log in Orbit. {body}");
        }
        if h.system == "darwin" {
            body.push_str(" YTM Starcast isn't tested on macOS yet; if something doesn't work, its log is in ~/Library/Logs/ytm-starcast.log.");
        }
        json!({"headline": head, "body": format!("{body}{note}"), "next": nxt})
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn servers_are_recognised_by_their_command_line() {
        assert!(StarcastRecipe::is_server(b"/home/u/.local/share/ytm-starcast/venv/bin/ytm-starcast\0--port\08777", &[NEW]));
        assert!(StarcastRecipe::is_server(b"python3\0-m\0starcast\0", &[NEW]));
        assert!(!StarcastRecipe::is_server(b"python3\0-m\0something\0", &[NEW]));
        assert!(!StarcastRecipe::is_server(b"/usr/bin/bash\0", &[NEW]));
    }

    #[test]
    fn an_update_keeps_the_servers_options_but_not_lan() {
        let w = |s: &str| s.split_whitespace().map(String::from).collect::<Vec<_>>();
        assert_eq!(port_of(&w("/x/ytm-starcast --lan --port 8778")), Some(8778));
        assert_eq!(port_of(&w("ytm-starcast.exe --audio windows --port=9000")), Some(9000));
        assert_eq!(port_of(&w("/x/ytm-starcast --lan")), None);
        assert_eq!(kept_options(&w("/x/ytm-starcast --lan --token abc --port=8778 --bind 0.0.0.0 --verbose --audio none")),
                   w("--token abc --port=8778 --bind 0.0.0.0 --audio none"));
        assert!(kept_options(&w("/x/ytm-starcast --lan")).is_empty());
        assert!(kept_options(&w("/x/ytm-starcast --token")).is_empty()); // a flag without its value isn't kept
    }

    #[test]
    fn the_launch_agent_is_escaped() {
        let p = plist("app.x", &["/a b/c&d".into(), "--lan".into()], true, Path::new("/l<og"));
        assert!(p.contains("<string>/a b/c&amp;d</string>") && p.contains("<true/>") && p.contains("/l&lt;og"));
    }
}
