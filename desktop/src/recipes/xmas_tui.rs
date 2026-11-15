//! xmas-tui (dash1101/xmas-tui): a Christmas village as an interactive live desktop wallpaper, on Linux (KDE Plasma 6,
//! Sway, Hyprland and other layer-shell Wayland desktops, inside kitty's panel) and Windows 10/11. x86-64 builds only.
//!
//! Installs the way its own installers do, just for the user: on Linux its no-sudo installer
//! (xmas-tui-linux-installer.sh: ~/.local/share/xmas-tui and a systemd user service), on Windows its per-user setup
//! (xmas-tui-setup-x86_64.exe, run silently). Both are checked against the release's SHA256SUMS first. xmas-tui updates
//! itself too, so the installed version is found from its program's checksum (GitHub lists one for every release file).
use crate::engine::{Session, Sources};
use crate::host::Host;
use crate::recipe::{need, option, step, Recipe, RecipeBase, StepDef};
use crate::util::{failed, sha256_file, Res};
use serde_json::{json, Map, Value};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::SystemTime;

const REPO: &str = "dash1101/xmas-tui";
const SUMS: &str = "SHA256SUMS";
const LINUX_INSTALLER: &str = "xmas-tui-linux-installer.sh";
const WIN_SETUP: &str = "xmas-tui-setup-x86_64.exe";
const UNIT: &str = "xmas-tui.service";
const RUN_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"; // its setup's (and its own) "run at startup"
const UNINSTALL_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{7F3C2A1E-6B0D-4E9A-9C51-5A2D8E4B1C7F}_is1";

fn any() -> Vec<i32> {
    (0..256).collect()
}

fn sv(p: &std::path::Path) -> String {
    p.to_string_lossy().into_owned()
}

/// kitty's version from `kitty --version` ("kitty 0.39.1 created by Kovid Goyal").
pub fn kitty_version(out: &str) -> Option<(u32, u32)> {
    let v = out.split_whitespace().nth(1)?;
    let mut it = v.split('.').map(|x| x.parse::<u32>().ok());
    Some((it.next()??, it.next()??))
}

pub struct XmasTuiRecipe {
    base: RecipeBase,
    sum: Mutex<Option<(u64, Option<SystemTime>, String)>>, // the program's checksum, kept while the file is the same
}

impl XmasTuiRecipe {
    pub fn new(host: Host, sources: Arc<Sources>) -> XmasTuiRecipe {
        XmasTuiRecipe { base: RecipeBase::new(host, sources), sum: Mutex::new(None) }
    }

    fn h(&self) -> &Host {
        &self.base.host
    }
    fn windows(&self) -> bool {
        self.h().system == "windows"
    }

    /// Where its own installer puts it.
    pub fn dir(&self) -> PathBuf {
        if self.windows() { self.h().local_app_data().join("Programs").join("xmas-tui") } else { self.h().local_app_data().join("xmas-tui") }
    }
    pub fn exe(&self) -> PathBuf {
        if self.windows() { self.dir().join("xmas-tui.exe") } else { self.dir().join("bin").join("xmas-tui") }
    }
    fn settings_dir(&self) -> PathBuf {
        self.h().config_home().join("xmas-tui")
    }
    fn unit_path(&self) -> PathBuf {
        self.h().config_home().join("systemd").join("user").join(UNIT)
    }
    /// The release file the program itself is (what its updates download).
    fn program_asset(&self) -> &'static str {
        if self.windows() { "xmas-tui-windows-x86_64.exe" } else { "xmas-tui-linux-x86_64" }
    }
    fn installer(&self) -> &'static str {
        if self.windows() { WIN_SETUP } else { LINUX_INSTALLER }
    }

    /// Installed system-wide from its .deb (then apt updates it): its version.
    fn deb_version(&self) -> String {
        let Some(dq) = self.h().which("dpkg-query") else { return String::new() };
        let (rc, out) = self.h().probe(&[dq, "-W".into(), "-f=${Status}|${Version}".into(), "xmas-tui".into()]);
        if rc == 0 && out.contains("install ok installed") {
            return out.rsplit('|').next().unwrap_or("").trim().to_string();
        }
        String::new()
    }

    fn kitten(&self) -> bool {
        self.h().which("kitten").is_some() || self.h().bin_dir().join("kitten").exists()
    }

    fn kitty(&self) -> Option<(u32, u32)> {
        let k = self.h().which("kitty").or_else(|| Some(sv(&self.h().bin_dir().join("kitty"))).filter(|p| std::path::Path::new(p).exists()))?;
        kitty_version(&self.h().probe(&[k, "--version".into()]).1)
    }

    /// Windows: whether it starts with Windows (the value its setup and its own switch write).
    fn starts_with_windows(&self) -> bool {
        let (rc, _) = self.h().probe(&["reg".into(), "query".into(), RUN_KEY.into(), "/v".into(), "xmas-tui".into()]);
        rc == 0
    }

    /// The program's SHA-256, worked out again only when the file changed.
    fn program_sum(&self) -> String {
        let exe = self.exe();
        let Ok(meta) = std::fs::metadata(&exe) else { return String::new() };
        let key = (meta.len(), meta.modified().ok());
        if let Some((len, at, sum)) = self.sum.lock().unwrap().clone() {
            if (len, at) == key {
                return sum;
            }
        }
        let sum = sha256_file(&exe).unwrap_or_default();
        *self.sum.lock().unwrap() = Some((key.0, key.1, sum.clone()));
        sum
    }

    /// The release whose program file is the installed one ("" when Orbit hasn't looked at the releases yet).
    fn version_from_sum(&self) -> String {
        let sum = self.program_sum();
        if sum.is_empty() {
            return String::new();
        }
        self.base.sources.cached_all(REPO).iter()
            .find(|r| r["assets"][self.program_asset()]["sha256"].as_str() == Some(sum.as_str()))
            .map(|r| crate::util::version_of(r["tag"].as_str().unwrap_or(""))).unwrap_or_default()
    }

    fn registry_version(&self) -> String {
        let (rc, out) = self.h().probe(&["reg".into(), "query".into(), UNINSTALL_KEY.into(), "/v".into(), "DisplayVersion".into()]);
        if rc != 0 {
            return String::new();
        }
        out.lines().find(|l| l.contains("DisplayVersion")).and_then(|l| l.split_whitespace().last()).unwrap_or("").to_string()
    }

    // ---- steps -----------------------------------------------------------------------------------
    fn find(&self, s: &mut Session) -> Res<bool> {
        let file = self.installer();
        let rel = s.release_with(REPO, &|r| r["assets"].get(file).is_some())?;
        s.asset(&rel, SUMS, REPO)?;
        s.detail(rel["tag"].as_str().unwrap_or(""));
        s.vars.insert("rel".into(), rel);
        Ok(true)
    }

    fn download(&self, s: &mut Session) -> Res<bool> {
        let rel = s.vars["rel"].clone();
        let f = s.download(&rel, self.installer(), REPO, None)?;
        let sums = s.fetch_text(&rel, SUMS, REPO)?;
        s.vars.insert("file".into(), json!(sv(&f)));
        s.vars.insert("sums".into(), json!(sums));
        Ok(true)
    }

    fn verify(&self, s: &mut Session) -> Res<bool> {
        let (file, sums) = (PathBuf::from(s.var_str("file")), s.var_str("sums"));
        s.verify(&file, &sums, self.installer())?;
        Ok(true)
    }

    /// Linux: its own installer (it downloads the program, checks it against the same checksums, and starts the
    /// wallpaper's user service).
    fn linux_install(&self, s: &mut Session) -> Res<bool> {
        s.run(&[self.h().tool("bash"), s.var_str("file")], &[0], false, Some("xmas-tui's installer stopped."),
              "The log below shows what it said.")?;
        self.forget();
        if !s.dry_run && !self.exe().exists() {
            return Err(failed(format!("xmas-tui's installer finished, but the program isn't at {}.", self.exe().display()),
                              "The log below shows what the installer said."));
        }
        Ok(true)
    }

    fn win_stop(&self, s: &mut Session) -> Res<bool> {
        s.run(&[self.h().tool("taskkill"), "/IM".into(), "xmas-tui.exe".into(), "/F".into()], &any(), true, None, "")?;
        s.sleep(0.5)?;
        Ok(true)
    }

    /// Windows: its per-user setup, without its windows (no administrator: it installs just for this user).
    fn win_install(&self, s: &mut Session) -> Res<bool> {
        let tasks = if s.opt_bool("startup", true) { "/MERGETASKS=startup,!desktopicon" } else { "/MERGETASKS=!startup,!desktopicon" };
        s.run(&[s.var_str("file"), "/VERYSILENT".into(), "/SUPPRESSMSGBOXES".into(), "/NORESTART".into(), "/CURRENTUSER".into(),
                tasks.into()], &[0], false, Some("xmas-tui's setup stopped."), "The log below shows what it said.")?;
        if !s.opt_bool("startup", true) {
            // its setup only adds the value: one left by an earlier install goes too
            s.run(&["reg".into(), "delete".into(), RUN_KEY.into(), "/v".into(), "xmas-tui".into(), "/f".into()], &any(), true, None, "")?;
        }
        self.forget();
        if !s.dry_run && !self.exe().exists() {
            return Err(failed(format!("xmas-tui's setup finished, but the program isn't at {}.", self.exe().display()), ""));
        }
        Ok(true)
    }

    fn win_start(&self, s: &mut Session) -> Res<bool> {
        if s.dry_run {
            s.log(&format!("dry run: would start {}", self.exe().display()));
            return Ok(true);
        }
        crate::runner::detached(&[sv(&self.exe())], &[]).map_err(|e| failed(format!("Couldn't start xmas-tui: {e}"), "Start it from the Start menu."))?;
        Ok(true)
    }

    fn linux_remove(&self, s: &mut Session) -> Res<bool> {
        let sc = self.h().tool("systemctl");
        s.run(&[sc.clone(), "--user".into(), "disable".into(), "--now".into(), UNIT.into()], &any(), true, None, "")?;
        s.remove(&[self.unit_path()])?;
        s.run(&[sc, "--user".into(), "daemon-reload".into()], &any(), true, None, "")?;
        s.remove(&[self.dir()])?;
        Ok(true)
    }

    fn win_remove(&self, s: &mut Session) -> Res<bool> {
        let un = self.dir().join("unins000.exe");
        if s.dry_run || un.exists() {
            s.run(&[sv(&un), "/VERYSILENT".into(), "/SUPPRESSMSGBOXES".into(), "/NORESTART".into()], &any(), false, None, "")?;
        }
        // its uninstaller carries on from a copy of itself: wait for the program to go
        for _ in 0..60 {
            if s.dry_run || !self.exe().exists() {
                break;
            }
            s.sleep(0.5)?;
        }
        Ok(true)
    }

    fn settings_too(&self, s: &mut Session) -> Res<bool> {
        if !s.opt_bool("purge", false) {
            return Ok(false);
        }
        s.remove(&[self.settings_dir()])?;
        Ok(true)
    }

    fn gone(&self, s: &mut Session) -> Res<bool> {
        self.forget();
        if !s.dry_run && self.exe().exists() {
            return Err(failed(format!("xmas-tui's program is still at {}.", self.exe().display()),
                              "Something was still using it. Restart the computer, then remove it again."));
        }
        Ok(true)
    }
}

impl Recipe for XmasTuiRecipe {
    fn base(&self) -> &RecipeBase {
        &self.base
    }
    fn id(&self) -> &str {
        "xmas-tui"
    }
    fn name(&self) -> &str {
        "xmas-tui"
    }
    fn repo(&self) -> &str {
        REPO
    }
    fn blurb(&self) -> &str {
        "A cozy, glowing Christmas village as your desktop wallpaper: walk around, chat with the neighbours, feed the deer"
    }
    fn glyph(&self) -> &str {
        "XM"
    }
    fn color(&self) -> &str {
        "#e0483e"
    }

    fn platform(&self, system: &str) -> (String, String) {
        match system {
            "linux" => ("full".into(), "x86-64, on KDE Plasma 6, Sway, Hyprland or another Wayland desktop with layers (not GNOME), with kitty 0.36 \
                                        or newer installed.".into()),
            "windows" => ("full".into(), "Windows 10 and 11 (x86-64).".into()),
            "darwin" => ("none".into(), "xmas-tui is for Linux and Windows desktops; there's no macOS version.".into()),
            _ => ("none".into(), "xmas-tui isn't available on this system.".into()),
        }
    }

    fn support(&self) -> Value {
        let (level, note) = self.platform(&self.h().system);
        if level == "none" {
            return json!({"level": level, "note": note});
        }
        if self.h().arch != "amd64" {
            if self.windows() && self.h().arch == "arm64" {
                return json!({"level": "limited", "note": "Built for x86-64: Windows 11 on ARM runs it through its x64 emulation (not tested there)."});
            }
            return json!({"level": "none", "note": format!("xmas-tui is built for x86-64 only, and this computer has {}.", self.h().arch)});
        }
        if self.h().system == "linux" && !self.h().has_systemd() {
            return json!({"level": "none", "note": "xmas-tui runs from a systemd user service, and this Linux doesn't use systemd."});
        }
        json!({"level": level, "note": note})
    }

    fn wants(&self, rel: &Value) -> bool {
        rel["assets"].get(self.installer()).is_some()
    }

    fn detect(&self) -> Value {
        if !self.windows() {
            let deb = self.deb_version();
            if !deb.is_empty() && !self.exe().exists() {
                return json!({"installed": true, "version": deb, "where": "/usr", "how": "Debian package"});
            }
        }
        if !self.exe().exists() {
            return json!({"installed": false, "version": "", "where": "", "how": ""});
        }
        let mut v = self.version_from_sum();
        if v.is_empty() && self.windows() {
            v = self.registry_version();
        }
        json!({"installed": true, "version": v, "where": sv(&self.dir()), "how": if self.windows() { "its setup" } else { "its installer" }})
    }

    fn can_uninstall(&self) -> (bool, String) {
        if !self.windows() && !self.deb_version().is_empty() && !self.exe().exists() {
            return (false, "xmas-tui is installed system-wide from its .deb. Remove it with `sudo apt remove xmas-tui`.".into());
        }
        (true, String::new())
    }

    fn requirements(&self, op: &str) -> Vec<Value> {
        let h = self.h();
        if op != "install" || self.windows() {
            return vec![];
        }
        let mut out = vec![];
        if !self.deb_version().is_empty() && !self.exe().exists() {
            out.push(need("Not installed from its .deb already", false,
                          "xmas-tui is installed system-wide from its .deb, and apt keeps it updated. To let Orbit look after it, remove that \
                           first: `sudo apt remove xmas-tui`.", true, ""));
        }
        let kitty = self.kitty();
        let new_enough = kitty.map(|v| v >= (0, 36)).unwrap_or(false);
        out.push(need("kitty 0.36 or newer (the wallpaper runs in its panel)", self.kitten() && new_enough,
                      if self.kitten() { "Your kitty is older than 0.36: get the current one from https://sw.kovidgoyal.net/kitty/binary/." }
                      else { "Install kitty (for example `sudo apt install kitty`, or https://sw.kovidgoyal.net/kitty/binary/), then try again." },
                      true, &kitty.map(|(a, b)| format!("kitty {a}.{b}")).unwrap_or_default()));
        let desktop = h.env.get("XDG_CURRENT_DESKTOP").cloned().unwrap_or_default();
        out.push(need("A desktop that supports wallpaper layers (not GNOME)", !desktop.to_lowercase().contains("gnome"),
                      "GNOME doesn't offer the layer xmas-tui sits in. KDE Plasma 6, Sway and Hyprland do.", true, &desktop));
        let wayland = h.env.get("WAYLAND_DISPLAY").map(|v| !v.is_empty()).unwrap_or(false)
            || h.env.get("XDG_SESSION_TYPE").map(|v| v == "wayland").unwrap_or(false);
        out.push(need("A Wayland session", wayland, "It's a Wayland wallpaper: log in to a Wayland session (on KDE: Plasma (Wayland)).", false, ""));
        for tool in ["curl", "sha256sum"] {
            out.push(need(&format!("{tool} (its installer uses it)"), h.which(tool).is_some(), &format!("Install {tool}, then try again."), true, ""));
        }
        out
    }

    fn options(&self, op: &str) -> Vec<Value> {
        if op == "uninstall" {
            return vec![option("purge", "Also delete its settings", "bool", json!(false),
                               "Leave this off to keep config.toml (your widgets, villager and choices) for a reinstall.")];
        }
        if self.windows() {
            let on = !self.installed()["installed"].as_bool().unwrap_or(false) || self.starts_with_windows();
            return vec![option("startup", "Run at startup", "bool", json!(on),
                               "Makes the village your wallpaper whenever you sign in. It's the same switch as in its settings (Display tab).")];
        }
        vec![]
    }

    fn assets(&self, _rel: &Value) -> Vec<String> {
        // on Linux the installer then downloads the program itself: its size is what the download is
        let mut out = vec![self.installer().to_string(), SUMS.to_string()];
        if !self.windows() {
            out.push(self.program_asset().into());
        }
        out
    }

    fn summary(&self, op: &str, opts: &Map<String, Value>, rel: Option<&Value>) -> Vec<String> {
        let purge = opts.get("purge").and_then(|v| v.as_bool()).unwrap_or(false);
        if op == "uninstall" {
            return vec![
                if self.windows() { "Stop the wallpaper and remove xmas-tui with its own uninstaller".into() }
                else { "Stop the wallpaper, remove its user service and its folder".into() },
                if purge { format!("Delete its settings ({}) too", self.settings_dir().display()) }
                else { format!("Keep its settings in {} for a reinstall", self.settings_dir().display()) },
            ];
        }
        let v = rel.and_then(|r| r["tag"].as_str()).unwrap_or("the latest release");
        if self.windows() {
            let startup = opts.get("startup").and_then(|v| v.as_bool()).unwrap_or(true);
            return vec![
                format!("Download xmas-tui {v}'s setup ({WIN_SETUP}) from GitHub and check it against the release's checksums"),
                format!("Run it just for you (no administrator): xmas-tui goes to {}, with Start menu entries and an uninstaller", self.dir().display()),
                if startup { "Run it at startup, as your desktop wallpaper".into() } else { "Leave run at startup off".into() },
                "Start the wallpaper (with its music and sounds: they're in its settings)".into(),
            ];
        }
        vec![
            format!("Download xmas-tui {v}'s installer ({LINUX_INSTALLER}) from GitHub and check it against the release's checksums"),
            format!("Run it just for you (no sudo): the program goes to {}, its settings to {}", self.dir().display(), self.settings_dir().display()),
            "It adds a systemd user service that keeps the wallpaper running and starts it now (with its music and sounds: they're in its settings)".into(),
        ]
    }

    fn steps(self: Arc<Self>, op: &str, _opts: &Map<String, Value>) -> Vec<StepDef> {
        let me = |f: fn(&XmasTuiRecipe, &mut Session) -> Res<bool>| {
            let r = self.clone();
            move |s: &mut Session| f(&r, s)
        };
        if op == "uninstall" {
            let mut out = if self.windows() {
                vec![step("stop", "Stop the wallpaper", 0.5, me(Self::win_stop)), step("remove", "Remove xmas-tui", 2.0, me(Self::win_remove))]
            } else {
                vec![step("remove", "Stop it and remove it", 1.0, me(Self::linux_remove))]
            };
            out.push(step("settings", "Delete its settings", 0.3, me(Self::settings_too)));
            out.push(step("check", "Make sure it's gone", 0.3, me(Self::gone)));
            return out;
        }
        let mut out = vec![
            step("find", "Find the latest release", 0.5, me(Self::find)),
            step("download", "Download", 2.0, me(Self::download)),
            step("verify", "Check the download", 0.5, me(Self::verify)),
        ];
        if self.windows() {
            out.extend([step("stop", "Stop the running copy", 0.5, me(Self::win_stop)),
                        step("install", "Run its setup", 3.0, me(Self::win_install)),
                        step("start", "Start the wallpaper", 0.5, me(Self::win_start))]);
        } else {
            out.push(step("install", "Run its installer", 4.0, me(Self::linux_install)));
        }
        out
    }

    fn finish(&self, sess: &Session, op: &str, opts: &Map<String, Value>) -> Value {
        if op == "uninstall" {
            let purge = opts.get("purge").and_then(|v| v.as_bool()).unwrap_or(false);
            return json!({"headline": "xmas-tui is removed.", "body": if purge { "Its settings are gone too." }
                          else { "Its settings are kept for a reinstall." }, "next": []});
        }
        let first = !sess.vars.get("before").and_then(|b| b["installed"].as_bool()).unwrap_or(false);
        let body = if first {
            "The village is your wallpaper now. Click the bare desktop, then walk with WASD or the arrow keys and press E to talk to people. \
             Right-click for what you can do, and ⚙ (top right) for its settings. It looks for updates itself."
        } else {
            "Its settings are kept. It looks for updates itself, too."
        };
        let exe = sv(&self.exe());
        json!({"headline": if first { "xmas-tui is installed." } else { "xmas-tui is updated." }, "body": body, "next": [
            {"id": "settings", "label": "open its settings", "kind": "program", "argv": [exe.clone(), "--settings-window"], "primary": true},
            {"id": "window", "label": "play in a window", "kind": "program", "argv": [exe, "--windowed"]}]})
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
    fn kittys_version_is_read() {
        assert_eq!(kitty_version("kitty 0.39.1 created by Kovid Goyal"), Some((0, 39)));
        assert_eq!(kitty_version("kitty 0.36.0"), Some((0, 36)));
        assert_eq!(kitty_version("nonsense"), None);
    }

    #[test]
    fn only_x86_linux_and_windows() {
        let r = |s, a| XmasTuiRecipe::new(host(s, a), Arc::new(Sources::github())).support()["level"].as_str().unwrap().to_string();
        assert_eq!(r("linux", "amd64"), "full");
        assert_eq!(r("windows", "amd64"), "full");
        assert_eq!(r("windows", "arm64"), "limited");
        assert_eq!(r("linux", "arm64"), "none");
        assert_eq!(r("darwin", "arm64"), "none");
        let mut h = host("linux", "amd64");
        h.systemd = Some(false);
        assert_eq!(XmasTuiRecipe::new(h, Arc::new(Sources::github())).support()["level"], "none");
    }

    #[test]
    fn the_version_comes_from_the_programs_checksum() {
        let d = std::env::temp_dir().join(format!("orbit-xmas-{}", crate::util::new_id()));
        let mut h = host("linux", "amd64");
        h.home = d.clone();
        let r = XmasTuiRecipe::new(h, Arc::new(Sources::github()));
        std::fs::create_dir_all(r.exe().parent().unwrap()).unwrap();
        std::fs::write(r.exe(), b"village").unwrap();
        assert_eq!(r.program_sum(), sha256_file(&r.exe()).unwrap());
        assert_eq!(r.version_from_sum(), ""); // no releases looked at yet
        assert_eq!(r.wants(&json!({"assets": {LINUX_INSTALLER: {}}})), true);
        let _ = std::fs::remove_dir_all(d);
    }
}
