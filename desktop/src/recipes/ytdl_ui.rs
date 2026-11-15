//! YTDL-UI (JustAyden/YTDL-UI): a web page for downloading video and audio with yt-dlp and ffmpeg, on Windows, macOS and
//! Linux (x64 and ARM).
//!
//! Its ready-made app is one file per system and processor (ytdl-ui-<linux|macos|windows>-<x64|arm64>[.exe]), checked
//! here against the release's SHA256SUMS.txt. Orbit gives it a folder of its own, because the app keeps its settings
//! (config.ini) and data/ next to itself. On Linux with systemd and on macOS it can keep running in the background (a
//! user service or a launch agent); on Windows it runs in its console window while it's open. The app also updates
//! itself; an update from Orbit replaces the file the same way, keeping config.ini and data/.
use crate::engine::{Session, Sources};
use crate::host::Host;
use crate::net;
use crate::recipe::{need, option, step, Recipe, RecipeBase, StepDef};
use crate::util::{failed, Res};
use serde_json::{json, Map, Value};
use std::path::PathBuf;
use std::sync::Arc;

const REPO: &str = "JustAyden/YTDL-UI";
const SUMS: &str = "SHA256SUMS.txt";
const UNIT: &str = "ytdl-ui.service";
const LABEL: &str = "io.github.justayden.ytdl-ui"; // the macOS launch agent
const MARKER: &str = "orbit-install.json";
// an argument the app takes anyway (data/ is its default): started with none, the app also opens the browser
const QUIET: [&str; 2] = ["--data-dir", "data"];

fn any() -> Vec<i32> {
    (0..256).collect()
}

fn sv(p: &std::path::Path) -> String {
    p.to_string_lossy().into_owned()
}

pub struct YtdlUiRecipe {
    base: RecipeBase,
    pub port: u16,
}

impl YtdlUiRecipe {
    pub fn new(host: Host, sources: Arc<Sources>) -> YtdlUiRecipe {
        YtdlUiRecipe { base: RecipeBase::new(host, sources), port: 5000 }
    }

    fn h(&self) -> &Host {
        &self.base.host
    }

    /// Its folder: the program, config.ini and data/.
    pub fn dir(&self) -> PathBuf {
        let h = self.h();
        match h.system.as_str() {
            "windows" => h.local_app_data().join("Programs").join("YTDL-UI"),
            "darwin" => h.local_app_data().join("YTDL-UI"),
            _ => h.local_app_data().join("ytdl-ui"),
        }
    }

    pub fn exe(&self) -> PathBuf {
        self.dir().join(if self.h().system == "windows" { "ytdl-ui.exe" } else { "ytdl-ui" })
    }

    /// The release file for this computer.
    pub fn asset_name(&self) -> String {
        let h = self.h();
        let os = match h.system.as_str() {
            "windows" => "windows",
            "darwin" => "macos",
            _ => "linux",
        };
        let cpu = if h.arch == "arm64" { "arm64" } else { "x64" };
        format!("ytdl-ui-{os}-{cpu}{}", if os == "windows" { ".exe" } else { "" })
    }

    fn unit_path(&self) -> PathBuf {
        self.h().config_home().join("systemd").join("user").join(UNIT)
    }
    fn plist_path(&self) -> PathBuf {
        self.h().home.join("Library").join("LaunchAgents").join(format!("{LABEL}.plist"))
    }
    fn desktop_path(&self) -> PathBuf {
        self.h().local_app_data().join("applications").join("ytdl-ui.desktop")
    }
    fn shortcut_path(&self) -> PathBuf {
        self.h().start_menu().join("YTDL-UI.lnk")
    }
    fn url(&self) -> String {
        format!("http://localhost:{}", self.port)
    }

    /// Can it keep running in the background here (a user service, a launch agent)?
    fn can_background(&self) -> bool {
        match self.h().system.as_str() {
            "linux" => self.h().has_systemd(),
            "darwin" => true,
            _ => false,
        }
    }

    /// The version a running YTDL-UI says it is (it updates itself, so this beats what Orbit wrote down).
    fn running_version(&self) -> String {
        let req = net::Req { timeout: 0.4, headers: vec![], insecure: false };
        match net::get_json(&format!("http://127.0.0.1:{}/api/health", self.port), &req) {
            Ok(v) if v["ok"].as_bool() == Some(true) && v.get("ytdlp").is_some() => v["version"].as_str().unwrap_or("").to_string(),
            _ => String::new(),
        }
    }

    fn written_version(&self) -> String {
        std::fs::read_to_string(self.dir().join(MARKER)).ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| v["version"].as_str().map(String::from)).unwrap_or_default()
    }

    // ---- steps -----------------------------------------------------------------------------------
    fn find(&self, s: &mut Session) -> Res<bool> {
        let name = self.asset_name();
        let rel = s.release_with(REPO, &|r| r["assets"].get(&name).is_some())?;
        s.asset(&rel, SUMS, REPO)?;
        s.detail(rel["tag"].as_str().unwrap_or(""));
        s.vars.insert("rel".into(), rel);
        Ok(true)
    }

    fn download(&self, s: &mut Session) -> Res<bool> {
        let rel = s.vars["rel"].clone();
        let name = self.asset_name();
        let f = s.download(&rel, &name, REPO, None)?;
        let sums = s.fetch_text(&rel, SUMS, REPO)?;
        s.vars.insert("file".into(), json!(sv(&f)));
        s.vars.insert("sums".into(), json!(sums));
        Ok(true)
    }

    fn verify(&self, s: &mut Session) -> Res<bool> {
        let (file, sums) = (PathBuf::from(s.var_str("file")), s.var_str("sums"));
        s.verify(&file, &sums, &self.asset_name())?;
        Ok(true)
    }

    /// Stop it wherever it runs from, so its file can be replaced (or removed).
    fn stop(&self, s: &mut Session) -> Res<bool> {
        let h = self.h();
        match h.system.as_str() {
            "windows" => {
                s.run(&[h.tool("taskkill"), "/IM".into(), "ytdl-ui.exe".into(), "/F".into()], &any(), true, None, "")?;
                s.sleep(0.5)?;
            }
            "darwin" => {
                if self.plist_path().exists() {
                    s.run(&[h.tool("launchctl"), "bootout".into(), format!("gui/{}", h.uid()), sv(&self.plist_path())], &any(), true, None, "")?;
                }
            }
            _ => {
                if h.has_systemd() && self.unit_path().exists() {
                    s.run(&[h.tool("systemctl"), "--user".into(), "stop".into(), UNIT.into()], &any(), true, None, "")?;
                }
            }
        }
        Ok(true)
    }

    fn put(&self, s: &mut Session) -> Res<bool> {
        s.place(&PathBuf::from(s.var_str("file")), &self.exe())?;
        let v = s.vars["rel"]["tag"].as_str().unwrap_or("").trim_start_matches(['v', 'V']).to_string();
        s.write_file(&self.dir().join(MARKER), &json!({"version": v, "file": self.asset_name()}).to_string(), None)?;
        Ok(true)
    }

    /// A menu entry (Linux) or a Start-menu shortcut (Windows). Macs open it from Orbit or the browser.
    fn menu(&self, s: &mut Session) -> Res<bool> {
        let h = self.h();
        if h.system == "windows" {
            let link = self.shortcut_path();
            let ps = |x: &str| format!("'{}'", x.replace('\'', "''"));
            let script = format!("New-Item -ItemType Directory -Force -Path {} | Out-Null;$s=(New-Object -ComObject WScript.Shell).CreateShortcut({});\
                                  $s.TargetPath={};$s.WorkingDirectory={};$s.Description='Download video and audio with yt-dlp';$s.Save()",
                                 ps(&sv(link.parent().unwrap())), ps(&sv(&link)), ps(&sv(&self.exe())), ps(&sv(&self.dir())));
            s.run(&[h.tool("powershell"), "-NoProfile".into(), "-NonInteractive".into(), "-ExecutionPolicy".into(), "Bypass".into(),
                    "-Command".into(), script], &[0], false, Some("Couldn't add YTDL-UI to the Start menu."), "")?;
            return Ok(true);
        }
        if h.system != "linux" {
            return Ok(false);
        }
        // kept running: the entry opens its page; otherwise it starts the app in a terminal (it opens the page itself)
        let background = s.opt_bool("background", true) && self.can_background();
        let (exec, terminal) = if background { (format!("xdg-open {}", self.url()), "false") } else { (format!("\"{}\"", sv(&self.exe())), "true") };
        let entry = ["[Desktop Entry]".to_string(), "Type=Application".into(), "Name=YTDL-UI".into(),
                     "GenericName=Video and audio downloader".into(), "Comment=Download video and audio with yt-dlp and ffmpeg".into(),
                     format!("Exec={exec}"), format!("Terminal={terminal}"), "Icon=folder-download".into(),
                     "Categories=AudioVideo;Network;Utility;".into(), "Keywords=yt-dlp;youtube;download;ffmpeg;".into(), String::new()].join("\n");
        s.write_file(&self.desktop_path(), &entry, None)?;
        Ok(true)
    }

    /// Linux: a user service; macOS: a launch agent. Started now, and at every login.
    fn background(&self, s: &mut Session) -> Res<bool> {
        let h = self.h();
        if !s.opt_bool("background", true) {
            // asked not to: whatever an earlier install set up goes
            self.unservice(s)?;
            s.detail("left out: start it from the menu when you need it");
            return Ok(false);
        }
        let args: Vec<String> = std::iter::once(sv(&self.exe())).chain(QUIET.iter().map(|x| x.to_string())).collect();
        if h.system == "darwin" {
            let log = h.home.join("Library/Logs/ytdl-ui.log");
            s.write_file(&self.plist_path(), &crate::recipes::starcast::plist(LABEL, &args, true, &log), None)?;
            let (lc, dom, p) = (h.tool("launchctl"), format!("gui/{}", h.uid()), sv(&self.plist_path()));
            s.run(&[lc.clone(), "bootout".into(), dom.clone(), p.clone()], &any(), true, None, "")?;
            s.run(&[lc.clone(), "bootstrap".into(), dom.clone(), p], &[0], false, Some("macOS didn't start YTDL-UI."), "")?;
            s.run(&[lc, "kickstart".into(), format!("{dom}/{LABEL}")], &any(), true, None, "")?;
            return Ok(true);
        }
        let sc = h.tool("systemctl");
        let exec = args.iter().map(|a| format!("\"{}\"", a.replace('\\', "\\\\").replace('"', "\\\""))).collect::<Vec<_>>().join(" ");
        let unit = ["[Unit]".to_string(), "Description=YTDL-UI - video and audio downloader".into(), "After=network-online.target".into(),
                    String::new(), "[Service]".into(), format!("WorkingDirectory={}", sv(&self.dir())), format!("ExecStart={exec}"),
                    "Restart=on-failure".into(), "RestartSec=3".into(), String::new(), "[Install]".into(), "WantedBy=default.target".into(),
                    String::new()].join("\n");
        s.write_file(&self.unit_path(), &unit, None)?;
        s.run(&[sc.clone(), "--user".into(), "daemon-reload".into()], &[0], false, None, "")?;
        s.run(&[sc.clone(), "--user".into(), "enable".into(), UNIT.into()], &[0], false, None, "")?;
        s.run(&[sc, "--user".into(), "restart".into(), UNIT.into()], &[0], false, Some("YTDL-UI's service didn't start."),
              "Run `journalctl --user -u ytdl-ui` in a terminal to see why.")?;
        Ok(true)
    }

    fn answers(&self, s: &mut Session) -> Res<bool> {
        if !(s.opt_bool("background", true) && self.can_background()) {
            return Ok(false);
        }
        if !s.wait_http(&format!("http://127.0.0.1:{}/api/health", self.port), 40.0, false)? {
            s.vars.insert("slow".into(), json!(true));
            s.log("YTDL-UI hasn't answered yet. The first start unpacks the app and, on Linux, downloads ffmpeg, which takes a while.");
        }
        Ok(true)
    }

    /// The service or launch agent goes (removing, or installing without it).
    fn unservice(&self, s: &mut Session) -> Res<bool> {
        let h = self.h();
        if h.system == "darwin" && self.plist_path().exists() {
            s.run(&[h.tool("launchctl"), "bootout".into(), format!("gui/{}", h.uid()), sv(&self.plist_path())], &any(), true, None, "")?;
            s.remove(&[self.plist_path()])?;
        }
        if h.system == "linux" && self.unit_path().exists() {
            let sc = h.tool("systemctl");
            s.run(&[sc.clone(), "--user".into(), "disable".into(), "--now".into(), UNIT.into()], &any(), true, None, "")?;
            s.remove(&[self.unit_path()])?;
            s.run(&[sc, "--user".into(), "daemon-reload".into()], &any(), true, None, "")?;
        }
        Ok(true)
    }

    fn remove_files(&self, s: &mut Session) -> Res<bool> {
        self.unservice(s)?;
        let mut paths = vec![self.desktop_path(), self.shortcut_path()];
        if s.opt_bool("purge", false) {
            paths.push(self.dir());
        } else {
            // config.ini and data/ stay for a reinstall
            for n in ["ytdl-ui", "ytdl-ui.exe", "ytdl-ui.old", "ytdl-ui.exe.old", "ytdl-ui.new", "ytdl-ui.exe.new", MARKER] {
                paths.push(self.dir().join(n));
            }
        }
        s.remove(&paths)?;
        self.forget();
        if !s.dry_run && self.exe().exists() {
            return Err(failed(format!("YTDL-UI's program is still at {}.", self.exe().display()),
                              "Something was still using it. Close YTDL-UI's window, then try again."));
        }
        Ok(true)
    }
}

impl Recipe for YtdlUiRecipe {
    fn base(&self) -> &RecipeBase {
        &self.base
    }
    fn id(&self) -> &str {
        "ytdl-ui"
    }
    fn name(&self) -> &str {
        "YTDL-UI"
    }
    fn repo(&self) -> &str {
        REPO
    }
    fn blurb(&self) -> &str {
        "Download video and audio from YouTube and 1,800+ other sites, in your browser (yt-dlp and ffmpeg)"
    }
    fn glyph(&self) -> &str {
        "DL"
    }
    fn color(&self) -> &str {
        "#22d3ee"
    }

    fn platform(&self, system: &str) -> (String, String) {
        match system {
            "linux" => ("full".into(), "x64 and ARM64 (Raspberry Pi 4 and 5 too). It finds or downloads ffmpeg itself.".into()),
            "windows" => ("full".into(), "Windows 10 and 11, x64 and ARM. It runs in its own console window while it's open, and finds or downloads \
                                          ffmpeg itself.".into()),
            "darwin" => ("full".into(), "Apple Silicon and Intel Macs. Converting needs ffmpeg: `brew install ffmpeg`.".into()),
            _ => ("none".into(), "YTDL-UI isn't available on this system.".into()),
        }
    }

    fn wants(&self, rel: &Value) -> bool {
        rel["assets"].get(self.asset_name()).is_some()
    }

    fn detect(&self) -> Value {
        if !self.exe().is_file() {
            return json!({"installed": false, "version": "", "where": "", "how": ""});
        }
        let mut v = self.running_version();
        if v.is_empty() {
            v = self.written_version();
        }
        json!({"installed": true, "version": v, "where": sv(&self.dir()), "how": "its ready-made app"})
    }

    fn requirements(&self, op: &str) -> Vec<Value> {
        let h = self.h();
        let mut out = vec![];
        if op != "install" {
            return out;
        }
        if h.system == "darwin" {
            out.push(need("ffmpeg (for merging and converting)", h.which("ffmpeg").is_some(),
                          "Install it with Homebrew: `brew install ffmpeg`. YTDL-UI works without it, but can't merge video with audio or make MP3s.",
                          false, ""));
        }
        if h.which("deno").is_none() && h.which("node").is_none() {
            out.push(need("Deno or Node.js (for YouTube's higher qualities)", false,
                          "yt-dlp needs one of them to unlock YouTube's higher qualities. Install Deno from https://deno.land, or Node.js, \
                           then restart YTDL-UI.", false, ""));
        }
        out
    }

    fn options(&self, op: &str) -> Vec<Value> {
        if op == "uninstall" {
            return vec![option("purge", "Also delete its settings and downloads", "bool", json!(false),
                               "Leave this off to keep config.ini and data/ (settings, admin login, cached tools) for a reinstall.")];
        }
        if self.can_background() {
            return vec![option("background", "Keep it running in the background", "bool", json!(true),
                               "Starts YTDL-UI now and whenever you log in, so its page is always at http://localhost:5000. Off: start it \
                                from the menu when you need it.")];
        }
        vec![]
    }

    fn assets(&self, _rel: &Value) -> Vec<String> {
        vec![self.asset_name(), SUMS.into()]
    }

    fn summary(&self, op: &str, opts: &Map<String, Value>, rel: Option<&Value>) -> Vec<String> {
        let purge = opts.get("purge").and_then(|v| v.as_bool()).unwrap_or(false);
        if op == "uninstall" {
            return vec![
                "Stop YTDL-UI and remove its program, menu entry and start at login".into(),
                if purge { format!("Delete {} with its settings and data", self.dir().display()) }
                else { format!("Keep its settings (config.ini) and data/ in {} for a reinstall", self.dir().display()) },
                "Files you downloaded with it stay where you saved them".into(),
            ];
        }
        let v = rel.and_then(|r| r["tag"].as_str()).unwrap_or("the latest release");
        let background = opts.get("background").and_then(|v| v.as_bool()).unwrap_or(true) && self.can_background();
        let mut out = vec![
            format!("Download YTDL-UI {v} ({}) from GitHub and check it against the release's checksums", self.asset_name()),
            format!("Put it in {} (just for you, no administrator needed)", self.dir().display()),
        ];
        match self.h().system.as_str() {
            "windows" => out.push("Add YTDL-UI to the Start menu: it opens its page in your browser when started".into()),
            "linux" => out.push("Add YTDL-UI to the app menu".into()),
            _ => {}
        }
        if background {
            out.push(format!("Keep it running in the background (starts at login) at {}", self.url()));
        }
        out.push("Its own admin console has the rest: on the first start the admin password is in data/initial-admin-password.txt".into());
        out
    }

    fn steps(self: Arc<Self>, op: &str, _opts: &Map<String, Value>) -> Vec<StepDef> {
        let me = |f: fn(&YtdlUiRecipe, &mut Session) -> Res<bool>| {
            let r = self.clone();
            move |s: &mut Session| f(&r, s)
        };
        if op == "uninstall" {
            return vec![step("stop", "Stop YTDL-UI", 0.5, me(Self::stop)), step("remove", "Remove its files", 1.0, me(Self::remove_files))];
        }
        let mut out = vec![
            step("find", "Find the latest release", 0.5, me(Self::find)),
            step("download", "Download", 4.0, me(Self::download)),
            step("verify", "Check the download", 0.5, me(Self::verify)),
            step("stop", "Stop the running copy", 0.5, me(Self::stop)),
            step("put", "Put it in its folder", 0.5, me(Self::put)),
        ];
        if self.h().system != "darwin" {
            out.push(step("menu", "Add it to the menu", 0.5, me(Self::menu)));
        }
        if self.can_background() {
            out.push(step("background", "Keep it running", 1.0, me(Self::background)));
            out.push(step("answer", "Check that it answers", 2.0, me(Self::answers)));
        }
        out
    }

    fn finish(&self, sess: &Session, op: &str, opts: &Map<String, Value>) -> Value {
        if op == "uninstall" {
            let purge = opts.get("purge").and_then(|v| v.as_bool()).unwrap_or(false);
            return json!({"headline": "YTDL-UI is removed.", "body": if purge { "Its settings and data are gone too." }
                          else { "Its settings and data are kept for a reinstall." }, "next": []});
        }
        let first = !sess.vars.get("before").and_then(|b| b["installed"].as_bool()).unwrap_or(false);
        let background = opts.get("background").and_then(|v| v.as_bool()).unwrap_or(true) && self.can_background();
        let mut body = String::from(if first {
            "On the first visit, open ⚙ Settings → Admin console with the password in data/initial-admin-password.txt (in its folder): it \
             asks you to turn on two-factor login and pick a new password. It keeps yt-dlp up to date by itself."
        } else {
            "Its settings and data are kept."
        });
        if sess.vars.get("slow").and_then(|v| v.as_bool()).unwrap_or(false) {
            body.push_str(" It hasn't answered yet: the first start takes a little while. Give it a minute.");
        }
        let next = if background {
            vec![json!({"id": "open", "label": "open ytdl-ui", "kind": "url", "url": format!("http://localhost:{}", self.port), "primary": true})]
        } else if self.h().system == "windows" {
            vec![json!({"id": "start", "label": "start ytdl-ui", "kind": "shortcut", "path": sv(&self.shortcut_path()), "primary": true})]
        } else {
            vec![json!({"id": "start", "label": "start ytdl-ui", "kind": "program", "argv": [sv(&self.exe())], "primary": true})]
        };
        json!({"headline": if first { "YTDL-UI is installed." } else { "YTDL-UI is updated." }, "body": body, "next": next})
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn host(system: &str, arch: &str) -> Host {
        let mut h = Host::real();
        h.system = system.into();
        h.arch = arch.into();
        h.home = PathBuf::from("/home/u");
        h.env.clear();
        h.systemd = Some(true);
        h
    }

    #[test]
    fn each_computer_gets_its_own_file() {
        let r = |s, a| YtdlUiRecipe::new(host(s, a), Arc::new(Sources::github())).asset_name();
        assert_eq!(r("linux", "amd64"), "ytdl-ui-linux-x64");
        assert_eq!(r("linux", "arm64"), "ytdl-ui-linux-arm64");
        assert_eq!(r("darwin", "arm64"), "ytdl-ui-macos-arm64");
        assert_eq!(r("darwin", "amd64"), "ytdl-ui-macos-x64");
        assert_eq!(r("windows", "amd64"), "ytdl-ui-windows-x64.exe");
        assert_eq!(r("windows", "arm64"), "ytdl-ui-windows-arm64.exe");
        let rec = YtdlUiRecipe::new(host("linux", "arm64"), Arc::new(Sources::github()));
        assert!(rec.wants(&json!({"assets": {"ytdl-ui-linux-arm64": {}}})));
        assert!(!rec.wants(&json!({"assets": {"ytdl-ui-linux-x64": {}}})));
        assert_eq!(rec.exe(), PathBuf::from("/home/u/.local/share/ytdl-ui/ytdl-ui"));
    }

    #[test]
    fn it_runs_in_the_background_only_where_something_keeps_it_running() {
        let mut h = host("linux", "amd64");
        assert!(YtdlUiRecipe::new(h.clone(), Arc::new(Sources::github())).can_background());
        h.systemd = Some(false);
        assert!(!YtdlUiRecipe::new(h, Arc::new(Sources::github())).can_background());
        assert!(!YtdlUiRecipe::new(host("windows", "amd64"), Arc::new(Sources::github())).can_background());
        assert!(YtdlUiRecipe::new(host("darwin", "arm64"), Arc::new(Sources::github())).options("install")[0]["key"] == "background");
    }
}
