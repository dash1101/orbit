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
use std::time::{Duration, Instant, SystemTime};

const REPO: &str = "dash1101/xmas-tui";
const SUMS: &str = "SHA256SUMS";
const LINUX_INSTALLER: &str = "xmas-tui-linux-installer.sh";
const WIN_SETUP: &str = "xmas-tui-setup-x86_64.exe";
const UNIT: &str = "xmas-tui.service";
const KITTY_REPO: &str = "kovidgoyal/kitty";
const KITTY_MIN: (u32, u32) = (0, 36); // kitty's panel as a wallpaper layer
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
    version_in(out.split_whitespace().nth(1)?)
}

/// The first "major.minor" in a text ("0.41.1-2+deb13u2" → (0, 41)).
pub fn version_in(s: &str) -> Option<(u32, u32)> {
    let s = s.trim();
    let s = s.split_once(':').filter(|(e, _)| !e.is_empty() && e.chars().all(|c| c.is_ascii_digit())).map(|(_, v)| v).unwrap_or(s); // a Debian epoch
    let s = s.trim_start_matches(|c: char| !c.is_ascii_digit());
    let mut it = s.split(|c: char| !c.is_ascii_digit()).map(|x| x.parse::<u32>().ok());
    Some((it.next()??, it.next()??))
}

/// The kitty a package manager offers, from what it prints ("Candidate: …", "Version : …", a bare version).
pub fn package_version(manager: &str, out: &str) -> Option<String> {
    let line = match manager {
        "apt" => out.lines().find_map(|l| l.trim().strip_prefix("Candidate:"))?.trim(),
        "pacman" | "zypper" => out.lines().find_map(|l| {
            let (k, v) = l.split_once(':')?;
            (k.trim() == "Version").then_some(v)
        })?.trim(),
        _ => out.lines().map(str::trim).find(|l| l.chars().next().map(|c| c.is_ascii_digit()).unwrap_or(false))?,
    };
    (line != "(none)" && version_in(line).is_some()).then(|| line.to_string())
}

/// A package's version without its epoch and packaging suffix ("1:0.41.1-2+deb13u2" → "0.41.1").
pub fn short_version(v: &str) -> String {
    let v = v.split_once(':').filter(|(e, _)| e.chars().all(|c| c.is_ascii_digit())).map(|(_, r)| r).unwrap_or(v);
    v.split(['-', '+', '~']).next().unwrap_or(v).to_string()
}

/// How Orbit can put kitty on this computer: with the system's packages (when they have a new enough kitty: asks for
/// the password), else with kitty's own build from its GitHub releases (just for the user, no password).
#[derive(Clone, Debug, PartialEq)]
pub enum KittyRoute {
    Package { manager: String, version: String },
    Own { old: Option<String> }, // the packages' kitty, when there is one but it's too old
}

pub struct XmasTuiRecipe {
    base: RecipeBase,
    sum: Mutex<Option<(u64, Option<SystemTime>, String)>>, // the program's checksum, kept while the file is the same
    route: Mutex<Option<(Instant, Option<KittyRoute>)>>,    // how kitty can be installed (asked again after a minute)
}

impl XmasTuiRecipe {
    pub fn new(host: Host, sources: Arc<Sources>) -> XmasTuiRecipe {
        XmasTuiRecipe { base: RecipeBase::new(host, sources), sum: Mutex::new(None), route: Mutex::new(None) }
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

    /// The newest kitty here: on PATH, in ~/.local/bin, or kitty's own build in ~/.local/kitty.app (where xmas-tui looks too).
    fn kitty(&self) -> Option<(u32, u32)> {
        let found = [self.h().which("kitty"), Some(sv(&self.h().bin_dir().join("kitty"))), Some(sv(&self.kitty_app().join("bin").join("kitty")))];
        found.into_iter().flatten().filter(|p| std::path::Path::new(p).exists())
            .filter_map(|k| kitty_version(&self.h().probe(&[k, "--version".into()]).1)).max()
    }

    fn kitty_app(&self) -> PathBuf {
        self.h().home.join(".local").join("kitty.app")
    }

    /// kitty is here and new enough (with its kitten command).
    fn kitty_ok(&self) -> bool {
        self.kitty().map(|v| v >= KITTY_MIN).unwrap_or(false)
            && (self.kitten() || self.kitty_app().join("bin").join("kitten").exists())
    }

    /// The system's package manager: (name, its install program), when Orbit knows it.
    fn manager(&self) -> Option<(String, String)> {
        let h = self.h();
        [("apt", "apt-cache", "apt-get"), ("dnf", "dnf", "dnf"), ("pacman", "pacman", "pacman"), ("zypper", "zypper", "zypper")].iter()
            .find_map(|(m, ask, tool)| h.which(ask).and(h.which(tool)).map(|bin| (m.to_string(), bin)))
    }

    /// The command that installs these packages with the package manager (run as the administrator).
    fn install_argv(manager: &str, bin: &str, pkgs: &[String]) -> Vec<String> {
        let flags: &[&str] = match manager {
            "apt" | "dnf" => &["install", "-y"],
            "pacman" => &["-S", "--noconfirm", "--needed"],
            _ => &["--non-interactive", "install"],
        };
        std::iter::once(bin.to_string()).chain(flags.iter().map(|s| s.to_string())).chain(pkgs.iter().cloned()).collect()
    }

    /// Linux, installing, and kitty missing or too old: what Orbit can do about it (None: nothing).
    fn kitty_route(&self) -> Option<KittyRoute> {
        if let Some((at, r)) = self.route.lock().unwrap().clone() {
            if at.elapsed() < Duration::from_secs(60) {
                return r;
            }
        }
        let h = self.h();
        let mut old = None;
        let mut found = None;
        if let Some((manager, _)) = self.manager() {
            let ask: Vec<&str> = match manager.as_str() {
                "apt" => vec!["apt-cache", "policy", "kitty"],
                "dnf" => vec!["dnf", "-q", "--cacheonly", "repoquery", "--latest-limit", "1", "--qf", "%{version}\\n", "kitty"],
                "pacman" => vec!["pacman", "-Si", "kitty"],
                _ => vec!["zypper", "--no-refresh", "--non-interactive", "info", "kitty"],
            };
            let (rc, out) = h.probe(&ask.iter().map(|s| s.to_string()).collect::<Vec<_>>());
            if let Some(version) = (rc == 0).then(|| package_version(&manager, &out)).flatten() {
                if version_in(&version).map(|v| v >= KITTY_MIN).unwrap_or(false) {
                    found = Some(KittyRoute::Package { manager, version: short_version(&version) });
                } else {
                    old = Some(short_version(&version));
                }
            }
        }
        // kitty's own build: the wallpaper is x86-64 only, and so is what Orbit offers here
        let r = found.or_else(|| (h.arch == "amd64").then_some(KittyRoute::Own { old }));
        *self.route.lock().unwrap() = Some((Instant::now(), r.clone()));
        r
    }

    /// kitty needs installing first (Linux, installing).
    fn needs_kitty(&self, op: &str) -> bool {
        op == "install" && !self.windows() && !self.kitty_ok()
    }

    /// ffmpeg is missing and Orbit can install it (Linux, installing): xmas-tui's moving lock screen needs it.
    fn needs_ffmpeg(&self, op: &str) -> bool {
        op == "install" && !self.windows() && self.h().which("ffmpeg").is_none() && self.manager().is_some()
    }

    /// ffmpeg's package: Fedora has "ffmpeg" only with RPM Fusion, else "ffmpeg-free".
    fn ffmpeg_package(&self, manager: &str) -> String {
        if manager == "dnf" {
            let (rc, out) = self.h().probe(&["dnf", "-q", "--cacheonly", "repoquery", "--qf", "%{name}\\n", "ffmpeg"].map(String::from));
            if !(rc == 0 && out.lines().any(|l| l.trim() == "ffmpeg")) {
                return "ffmpeg-free".into();
            }
        }
        "ffmpeg".into()
    }

    /// What xmas-tui needs that isn't here, installed as chosen: kitty and ffmpeg from the system's packages together (one
    /// password), else kitty's own build. Nothing runs as the administrator without the system asking for the password.
    fn prerequisites(&self, s: &mut Session) -> Res<bool> {
        let kitty = self.needs_kitty("install");
        if kitty && !s.opt_bool("kitty", true) {
            return Err(failed("xmas-tui needs kitty 0.36 or newer, and it isn't installed.",
                              "Tick “Install kitty” and try again, or install kitty yourself (https://sw.kovidgoyal.net/kitty/binary/)."));
        }
        let route = if kitty { self.kitty_route() } else { None };
        if kitty && route.is_none() {
            return Err(failed("Orbit can't install kitty on this computer.",
                              "Install kitty 0.36 or newer yourself (https://sw.kovidgoyal.net/kitty/binary/), then try again."));
        }
        let mut pkgs = vec![];
        if let Some(KittyRoute::Package { version, .. }) = &route {
            s.log(&format!("kitty {version} from the system's packages"));
            pkgs.push("kitty".to_string());
        }
        let manager = self.manager();
        if self.needs_ffmpeg("install") && s.opt_bool("ffmpeg", true) {
            if let Some((m, _)) = &manager {
                pkgs.push(self.ffmpeg_package(m));
            }
        }
        if let (false, Some((m, bin))) = (pkgs.is_empty(), &manager) {
            let argv = Self::install_argv(m, bin, &pkgs);
            let with_kitty = pkgs.iter().any(|p| p == "kitty");
            let check = || if with_kitty { self.kitty_ok() } else { self.h().which("ffmpeg").is_some() };
            let what = format!("install {}", pkgs.join(" and "));
            match s.admin(&argv, &what, Some(&check), "Or install them yourself, then try again.") {
                Ok(_) => {}
                Err(e) if with_kitty => return Err(e),
                Err(e) => s.log(&format!("ffmpeg wasn't installed ({e}): the lock screen stays a still picture until it is")),
            }
        }
        if let Some(KittyRoute::Own { .. }) = route {
            self.kitty_own(s)?;
        }
        *self.route.lock().unwrap() = None;
        if kitty && !s.dry_run && !self.kitty_ok() {
            return Err(failed("kitty was installed, but Orbit doesn't find a new enough kitty.", "Open a new terminal and run kitty --version."));
        }
        Ok(true)
    }

    fn kitty_option(&self) -> Vec<Value> {
        match self.kitty_route() {
            Some(KittyRoute::Package { manager, version, .. }) => vec![option("kitty", "Install kitty", "bool", json!(true),
                &format!("From your system's packages ({manager}: kitty {version}). Orbit asks for your password in the system's window \
                          first; nothing is installed without it."))],
            Some(KittyRoute::Own { old }) => vec![option("kitty", "Install kitty", "bool", json!(true),
                &(format!("kitty's own build from its GitHub releases, checked against GitHub's checksum, just for you in {} (no password \
                           needed).", self.kitty_app().display())
                  + &old.map(|v| format!(" Your system's packages only have kitty {v}, too old for the wallpaper.")).unwrap_or_default()))],
            None => vec![],
        }
    }

    /// kitty's own build (kitty-<version>-x86_64.txz from its GitHub releases), checked against GitHub's SHA-256 for it,
    /// unpacked to ~/.local/kitty.app with kitty and kitten linked into ~/.local/bin: what kitty's own installer does.
    fn kitty_own(&self, s: &mut Session) -> Res<()> {
        let pick = |r: &Value| -> Option<String> {
            r["assets"].as_object()?.keys().find(|n| n.starts_with("kitty-") && n.ends_with("-x86_64.txz")).cloned()
        };
        let rel = s.release_with(KITTY_REPO, &|r| pick(r).is_some())?;
        let name = pick(&rel).unwrap_or_default();
        let expected = s.asset(&rel, &name, KITTY_REPO)?["sha256"].as_str().unwrap_or("").to_string();
        if expected.is_empty() && !s.dry_run {
            return Err(failed(format!("GitHub doesn't list a checksum for {name}, so it won't be installed."),
                              "Install kitty yourself (https://sw.kovidgoyal.net/kitty/binary/), then try again."));
        }
        let file = s.download(&rel, &name, KITTY_REPO, None)?;
        s.detail(&format!("SHA-256 of {name}"));
        if s.dry_run {
            s.log(&format!("dry run: would check {name} against GitHub's checksum"));
        } else {
            let got = match s.sha.get(&file) { Some(x) => x.clone(), None => sha256_file(&file).map_err(|e| failed(format!("Couldn't read {name}: {e}"), ""))? };
            if got != expected {
                return Err(failed(format!("{name} doesn't match its published checksum, so it won't be installed."),
                                  "The download may be damaged. Try again."));
            }
            s.log(&format!("Checksum OK: {name} {}…", &got[..16]));
        }
        let app = self.kitty_app();
        let new = app.with_extension("app.orbit-new");
        s.detail("unpacking kitty");
        if s.dry_run {
            s.log(&format!("dry run: would unpack {name} to {}", app.display()));
        } else {
            let _ = std::fs::remove_dir_all(&new);
            std::fs::create_dir_all(&new).map_err(|e| failed(format!("Couldn't write {}: {e}", new.display()), ""))?;
            s.run(&[self.h().tool("tar"), "-xJf".into(), sv(&file), "-C".into(), sv(&new)], &[0], false,
                  Some("Couldn't unpack kitty."), "tar and xz are needed to unpack it.")?;
            let old = app.with_extension("app.orbit-old");
            let _ = std::fs::remove_dir_all(&old);
            if app.exists() {
                std::fs::rename(&app, &old).map_err(|e| failed(format!("Couldn't replace {}: {e}", app.display()), ""))?;
            }
            std::fs::rename(&new, &app).map_err(|e| failed(format!("Couldn't put kitty at {}: {e}", app.display()), ""))?;
            let _ = std::fs::remove_dir_all(&old);
        }
        for b in ["kitty", "kitten"] {
            // a link of its own, or none yet: never over a file that's there already
            let link = self.h().bin_dir().join(b);
            if std::fs::symlink_metadata(&link).map(|m| m.file_type().is_symlink()).unwrap_or(true) {
                s.symlink(&app.join("bin").join(b), &link)?;
            }
        }
        s.log(&format!("kitty {} is in {}", rel["tag"].as_str().unwrap_or(""), app.display()));
        Ok(())
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
        // the xmas-tui command (its installer does this too from 1.16.0 on): never over a file that isn't a link
        let link = self.command_link();
        if std::fs::symlink_metadata(&link).map(|m| m.file_type().is_symlink()).unwrap_or(true) {
            s.symlink(&self.exe(), &link)?;
        }
        Ok(true)
    }

    fn command_link(&self) -> PathBuf {
        self.h().bin_dir().join("xmas-tui")
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
        // the xmas-tui command, when it's the link to this install
        let link = self.command_link();
        if std::fs::read_link(&link).map(|t| t == self.exe()).unwrap_or(false) {
            s.remove(&[link])?;
        }
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
        let ok = self.kitty_ok();
        let can = !ok && self.kitty_route().is_some();
        out.push(need("kitty 0.36 or newer (the wallpaper runs in its panel)", ok,
                      if can { "Orbit can install it for you: see “Install kitty” in the next step." }
                      else if kitty.is_some() { "Your kitty is older than 0.36: get the current one from https://sw.kovidgoyal.net/kitty/binary/." }
                      else { "Install kitty (for example `sudo apt install kitty`, or https://sw.kovidgoyal.net/kitty/binary/), then try again." },
                      !can, &kitty.map(|(a, b)| format!("kitty {a}.{b}")).unwrap_or_default()));
        out.push(need("ffmpeg (for the moving lock screen)", h.which("ffmpeg").is_some(),
                      if self.manager().is_some() { "Recommended. Orbit can install it for you: see “Install ffmpeg” in the next step." }
                      else { "Recommended: install ffmpeg with your package manager. Without it the lock screen is a still picture." },
                      false, ""));
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
        let mut out = vec![];
        if self.needs_kitty(op) {
            out.extend(self.kitty_option());
        }
        if self.needs_ffmpeg(op) {
            let m = self.manager().map(|m| m.0).unwrap_or_default();
            out.push(option("ffmpeg", "Install ffmpeg", "bool", json!(true),
                &format!("For xmas-tui's moving lock screen (without it the lock screen is a still picture). From your system's packages \
                          ({m}): Orbit asks for your password in the system's window first.")));
        }
        out
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
        let mut out = vec![];
        if self.needs_ffmpeg(op) && opts.get("ffmpeg").and_then(|v| v.as_bool()).unwrap_or(true) {
            out.push(format!("Install ffmpeg with {}, as the administrator, for the moving lock screen: Orbit asks for your password first",
                             self.manager().map(|m| m.0).unwrap_or_default()));
        }
        if self.needs_kitty(op) {
            out.insert(0, match (opts.get("kitty").and_then(|v| v.as_bool()).unwrap_or(true), self.kitty_route()) {
                (false, _) | (_, None) => "kitty isn't installed: install it first, or tick “Install kitty”".to_string(),
                (true, Some(KittyRoute::Package { manager, version, .. })) =>
                    format!("Install kitty {version} with {manager}, as the administrator: Orbit asks for your password first"),
                (true, Some(KittyRoute::Own { .. })) =>
                    format!("Download kitty's own build from GitHub, check it, and put it in {} (just for you)", self.kitty_app().display()),
            });
        }
        out.extend([
            format!("Download xmas-tui {v}'s installer ({LINUX_INSTALLER}) from GitHub and check it against the release's checksums"),
            format!("Run it just for you (no sudo): the program goes to {}, its settings to {}", self.dir().display(), self.settings_dir().display()),
            "It adds a systemd user service that keeps the wallpaper running and starts it now (with its music and sounds: they're in its settings)".into(),
        ]);
        out
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
        let mut out = vec![];
        if self.needs_kitty(op) || self.needs_ffmpeg(op) {
            out.push(step("prereqs", "Install what xmas-tui needs", 3.0, me(Self::prerequisites)));
        }
        out.extend([
            step("find", "Find the latest release", 0.5, me(Self::find)),
            step("download", "Download", 2.0, me(Self::download)),
            step("verify", "Check the download", 0.5, me(Self::verify)),
        ]);
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
    fn package_managers_kitty() {
        assert_eq!(package_version("apt", "kitty:\n  Installed: (none)\n  Candidate: 0.41.1-2+deb13u2\n"), Some("0.41.1-2+deb13u2".into()));
        assert_eq!(package_version("apt", "kitty:\n  Installed: (none)\n  Candidate: (none)\n"), None);
        assert_eq!(package_version("pacman", "Repository      : extra\nName            : kitty\nVersion         : 0.44.0-1\n"), Some("0.44.0-1".into()));
        assert_eq!(package_version("zypper", "Name           : kitty\nVersion        : 0.39.1-1.2\n"), Some("0.39.1-1.2".into()));
        assert_eq!(package_version("dnf", "0.42.2\n"), Some("0.42.2".into()));
        assert_eq!(version_in("0.41.1-2+deb13u2"), Some((0, 41)));
        assert_eq!(version_in("0.26.5"), Some((0, 26)));
        assert_eq!(version_in("1:0.26.5-5"), Some((0, 26)));
        assert_eq!(short_version("1:0.41.1-2+deb13u2"), "0.41.1");
        assert_eq!(short_version("0.44.0-1"), "0.44.0");
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
