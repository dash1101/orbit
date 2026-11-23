//! RPCortex Studio (dash1101/rpcortex-studio): the computer's side of an RPCortex device, a full-screen terminal app
//! and the `rpc` command, over USB serial. Linux x86-64, Windows x86-64 and Macs with Apple chips.
//!
//! Each release has one self-contained program per system (rpcortex-studio-<linux-x64|macos-arm64|windows-x64.exe>),
//! checked here against the release's SHA256SUMS. Orbit gives it a folder of its own and, on Linux and macOS, puts
//! `rpcortex-studio` (and, if chosen, `rpc`: the same program, which acts as the command-line tool under that name) in
//! ~/.local/bin. On Windows it gets a Start-menu entry. It has no service, port or web page: it's opened in a terminal.
use crate::engine::Session;
use crate::engine::Sources;
use crate::host::Host;
use crate::recipe::{need, option, step, Recipe, RecipeBase, StepDef};
use crate::util::{failed, Res};
use serde_json::{json, Map, Value};
use std::path::PathBuf;
use std::sync::Arc;

const REPO: &str = "dash1101/rpcortex-studio";
const SUMS: &str = "SHA256SUMS";
const MARKER: &str = "orbit-install.json";

fn sv(p: &std::path::Path) -> String {
    p.to_string_lossy().into_owned()
}

/// The group that may open USB serial ports here: `uucp` where there is no `dialout` (Arch), else `dialout`.
pub fn serial_group(etc_group: &str) -> &'static str {
    let has = |g: &str| etc_group.lines().any(|l| l.split(':').next() == Some(g));
    if !has("dialout") && has("uucp") { "uucp" } else { "dialout" }
}

/// Whether this user may read and write a file (a serial port) now, by the system's own check.
fn can_read_write(p: &std::path::Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        let Ok(c) = std::ffi::CString::new(p.as_os_str().as_bytes()) else { return false };
        unsafe { libc::access(c.as_ptr(), libc::R_OK | libc::W_OK) == 0 }
    }
    #[cfg(not(unix))]
    {
        let _ = p;
        true
    }
}

/// "rpcortex-studio 1.0.0" → "1.0.0".
pub fn version_said(out: &str) -> String {
    out.lines().find_map(|l| l.trim().strip_prefix("rpcortex-studio ")).map(|v| v.trim().trim_start_matches('v').to_string()).unwrap_or_default()
}

pub struct RpcortexStudioRecipe {
    base: RecipeBase,
}

impl RpcortexStudioRecipe {
    pub fn new(host: Host, sources: Arc<Sources>) -> RpcortexStudioRecipe {
        RpcortexStudioRecipe { base: RecipeBase::new(host, sources) }
    }

    fn h(&self) -> &Host {
        &self.base.host
    }

    /// Its folder: the program and the note of which version Orbit put there.
    pub fn dir(&self) -> PathBuf {
        let h = self.h();
        match h.system.as_str() {
            "windows" => h.local_app_data().join("Programs").join("RPCortex Studio"),
            "darwin" => h.local_app_data().join("RPCortex Studio"),
            _ => h.local_app_data().join("rpcortex-studio"),
        }
    }

    pub fn exe(&self) -> PathBuf {
        self.dir().join(if self.h().system == "windows" { "rpcortex-studio.exe" } else { "rpcortex-studio" })
    }

    /// The release file for this computer, or None where there is none (Linux ARM, Intel Macs, Windows on ARM).
    pub fn asset_name(&self) -> Option<String> {
        let h = self.h();
        match (h.system.as_str(), h.arch.as_str()) {
            ("linux", "amd64") => Some("rpcortex-studio-linux-x64".into()),
            ("darwin", "arm64") => Some("rpcortex-studio-macos-arm64".into()),
            ("windows", "amd64") => Some("rpcortex-studio-windows-x64.exe".into()),
            _ => None,
        }
    }

    /// The commands in ~/.local/bin (Linux and macOS): rpcortex-studio, and rpc when chosen.
    fn links(&self) -> Vec<PathBuf> {
        let b = self.h().bin_dir();
        vec![b.join("rpcortex-studio"), b.join("rpc")]
    }

    fn shortcut_path(&self) -> PathBuf {
        self.h().start_menu().join("RPCortex Studio.lnk")
    }

    /// Its settings (only the theme): rpc-tui/config.json in the system's settings folder.
    pub fn config_dir(&self) -> PathBuf {
        self.h().config_home().join("rpc-tui")
    }

    fn written_version(&self) -> String {
        std::fs::read_to_string(self.dir().join(MARKER)).ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| v["version"].as_str().map(String::from)).unwrap_or_default()
    }

    /// Linux: whether this user may open the board's serial port. With a board plugged in, its port is tried itself
    /// (some desktops give the logged-in user access without the group); without one, membership of the serial group.
    fn serial_access(&self) -> (bool, &'static str) {
        let group = serial_group(&std::fs::read_to_string("/etc/group").unwrap_or_default());
        let ports: Vec<PathBuf> = std::fs::read_dir("/dev").map(|r| r.flatten().map(|e| e.path())
            .filter(|p| p.file_name().map(|n| n.to_string_lossy().starts_with("ttyACM")).unwrap_or(false)).collect()).unwrap_or_default();
        if !ports.is_empty() {
            return (ports.iter().any(|p| can_read_write(p)), group);
        }
        let (rc, out) = self.h().probe(&["id".into(), "-nG".into()]);
        (rc == 0 && out.split_whitespace().any(|g| g == group), group)
    }

    // ---- steps -----------------------------------------------------------------------------------
    fn find(&self, s: &mut Session) -> Res<bool> {
        let name = self.asset_name().ok_or_else(|| failed("RPCortex Studio has no program for this computer.", ""))?;
        let rel = s.release_with(REPO, &|r| r["assets"].get(&name).is_some())?;
        s.asset(&rel, SUMS, REPO)?;
        s.detail(rel["tag"].as_str().unwrap_or(""));
        s.vars.insert("rel".into(), rel);
        Ok(true)
    }

    fn download(&self, s: &mut Session) -> Res<bool> {
        let rel = s.vars["rel"].clone();
        let name = self.asset_name().unwrap_or_default();
        let f = s.download(&rel, &name, REPO, None)?;
        let sums = s.fetch_text(&rel, SUMS, REPO)?;
        s.vars.insert("file".into(), json!(sv(&f)));
        s.vars.insert("sums".into(), json!(sums));
        Ok(true)
    }

    fn verify(&self, s: &mut Session) -> Res<bool> {
        let (file, sums) = (PathBuf::from(s.var_str("file")), s.var_str("sums"));
        s.verify(&file, &sums, &self.asset_name().unwrap_or_default())?;
        Ok(true)
    }

    fn put(&self, s: &mut Session) -> Res<bool> {
        s.place(&PathBuf::from(s.var_str("file")), &self.exe())?;
        let v = s.vars["rel"]["tag"].as_str().unwrap_or("").trim_start_matches(['v', 'V']).to_string();
        s.write_file(&self.dir().join(MARKER), &json!({"version": v, "file": self.asset_name()}).to_string(), None)?;
        Ok(true)
    }

    /// Linux and macOS: the commands (links, only over links or nothing). Windows: a Start-menu entry.
    fn commands(&self, s: &mut Session) -> Res<bool> {
        let h = self.h();
        if h.system == "windows" {
            let link = self.shortcut_path();
            let ps = |x: &str| format!("'{}'", x.replace('\'', "''"));
            let script = format!("New-Item -ItemType Directory -Force -Path {} | Out-Null;$s=(New-Object -ComObject WScript.Shell).CreateShortcut({});\
                                  $s.TargetPath={};$s.WorkingDirectory={};$s.Description='Control an RPCortex device';$s.Save()",
                                 ps(&sv(link.parent().unwrap())), ps(&sv(&link)), ps(&sv(&self.exe())), ps(&sv(&self.dir())));
            s.run(&[h.tool("powershell"), "-NoProfile".into(), "-NonInteractive".into(), "-ExecutionPolicy".into(), "Bypass".into(),
                    "-Command".into(), script], &[0], false, Some("Couldn't add RPCortex Studio to the Start menu."), "")?;
            return Ok(true);
        }
        let [studio, rpc] = [self.links()[0].clone(), self.links()[1].clone()];
        s.symlink(&self.exe(), &studio)?;
        if s.opt_bool("rpc", true) {
            s.symlink(&self.exe(), &rpc)?;
        } else if std::fs::read_link(&rpc).map(|t| t == self.exe()).unwrap_or(false) {
            s.remove(&[rpc])?; // turned off: only Orbit's own link goes
        }
        Ok(true)
    }

    /// The program runs and says its version (it never opens a serial port for that).
    fn check(&self, s: &mut Session) -> Res<bool> {
        if s.dry_run {
            s.log("dry run: would run rpcortex-studio --version");
            return Ok(true);
        }
        let (_, out) = s.run(&[sv(&self.exe()), "--version".into()], &[0], false, Some("RPCortex Studio didn't start."),
                             "Its download may be damaged: install it again.")?;
        s.detail(&format!("version {}", version_said(&out)));
        Ok(true)
    }

    fn remove_files(&self, s: &mut Session) -> Res<bool> {
        let mut paths = vec![self.dir(), self.shortcut_path()];
        for l in self.links() {
            // only links to this program (never someone else's rpc)
            if std::fs::read_link(&l).map(|t| t == self.exe()).unwrap_or(false) {
                paths.push(l);
            }
        }
        if s.opt_bool("purge", false) {
            paths.push(self.config_dir());
        }
        s.remove(&paths)?;
        self.forget();
        Ok(true)
    }
}

impl Recipe for RpcortexStudioRecipe {
    fn base(&self) -> &RecipeBase {
        &self.base
    }
    fn id(&self) -> &str {
        "rpcortex-studio"
    }
    fn name(&self) -> &str {
        "RPCortex Studio"
    }
    fn repo(&self) -> &str {
        REPO
    }
    fn blurb(&self) -> &str {
        "Control an RPCortex device from your computer: files, packages, firmware, logs and a terminal, over USB serial"
    }
    fn glyph(&self) -> &str {
        "RC"
    }
    fn color(&self) -> &str {
        "#2dd4be"
    }

    fn platform(&self, system: &str) -> (String, String) {
        match system {
            "linux" => ("full".into(), "Linux on x86-64.".into()),
            "windows" => ("full".into(), "Windows 10 and 11 on x86-64. It opens in Windows Terminal or the console.".into()),
            "darwin" => ("full".into(), "Macs with Apple chips (no Intel build).".into()),
            _ => ("none".into(), "RPCortex Studio isn't available on this system.".into()),
        }
    }

    fn support(&self) -> Value {
        if self.asset_name().is_none() && ["linux", "darwin", "windows"].contains(&self.h().system.as_str()) {
            let why = match self.h().system.as_str() {
                "darwin" => "RPCortex Studio's Mac program is for Apple chips only.",
                _ => "RPCortex Studio's program is for x86-64 processors only on this system.",
            };
            return json!({"level": "none", "note": why});
        }
        let (level, note) = self.platform(&self.h().system);
        json!({"level": level, "note": note})
    }

    fn wants(&self, rel: &Value) -> bool {
        self.asset_name().map(|n| rel["assets"].get(&n).is_some()).unwrap_or(false)
    }

    fn detect(&self) -> Value {
        if !self.exe().is_file() {
            return json!({"installed": false, "version": "", "where": "", "how": ""});
        }
        let (rc, out) = self.h().probe(&[sv(&self.exe()), "--version".into()]);
        let mut v = if rc == 0 { version_said(&out) } else { String::new() };
        if v.is_empty() {
            v = self.written_version();
        }
        json!({"installed": true, "version": v, "where": sv(&self.dir()), "how": "its ready-made program"})
    }

    fn requirements(&self, op: &str) -> Vec<Value> {
        if op != "install" {
            return vec![];
        }
        let mut out = vec![need("A board running RPCortex v2, on a USB cable", true, "", false,
                                "Needed to use it, not to install it. Close other programs that use the board's port first (PuTTY, screen, the browser tool).")];
        if self.h().system == "linux" {
            let (ok, group) = self.serial_access();
            out.push(need(&format!("Access to USB serial ports (the {group} group)"), ok,
                          &format!("Run `sudo usermod -aG {group} $USER`, then log out and back in. Until then RPCortex Studio can't open the board."),
                          false, ""));
        }
        out
    }

    fn options(&self, op: &str) -> Vec<Value> {
        if op == "uninstall" {
            return vec![option("purge", "Also delete its settings", "bool", json!(false),
                               &format!("Its settings (the theme) are in {}. Leave this off to keep them.", self.config_dir().display()))];
        }
        if self.h().system == "windows" {
            return vec![];
        }
        vec![option("rpc", "Also the rpc command", "bool", json!(true),
                    "The same program under the name rpc, the command-line tool (`rpc exec \"ver\"` …). Never replaces an rpc that isn't RPCortex Studio's.")]
    }

    fn assets(&self, _rel: &Value) -> Vec<String> {
        self.asset_name().map(|n| vec![n, SUMS.into()]).unwrap_or_default()
    }

    fn summary(&self, op: &str, opts: &Map<String, Value>, rel: Option<&Value>) -> Vec<String> {
        let purge = opts.get("purge").and_then(|v| v.as_bool()).unwrap_or(false);
        if op == "uninstall" {
            return vec![
                format!("Remove {} and its commands or Start-menu entry", self.dir().display()),
                if purge { format!("Delete its settings ({})", self.config_dir().display()) }
                else { format!("Keep its settings ({})", self.config_dir().display()) },
            ];
        }
        let v = rel.and_then(|r| r["tag"].as_str()).unwrap_or("the latest release");
        let mut out = vec![
            format!("Download RPCortex Studio {v} ({}) from GitHub and check it against the release's checksums", self.asset_name().unwrap_or_default()),
            format!("Put it in {} (just for you, no administrator needed)", self.dir().display()),
        ];
        if self.h().system == "windows" {
            out.push("Add RPCortex Studio to the Start menu".into());
        } else if opts.get("rpc").and_then(|v| v.as_bool()).unwrap_or(true) {
            out.push("Add the rpcortex-studio and rpc commands (in ~/.local/bin)".into());
        } else {
            out.push("Add the rpcortex-studio command (in ~/.local/bin)".into());
        }
        out.push("Check that it starts and says its version".into());
        out
    }

    fn steps(self: Arc<Self>, op: &str, _opts: &Map<String, Value>) -> Vec<StepDef> {
        let me = |f: fn(&RpcortexStudioRecipe, &mut Session) -> Res<bool>| {
            let r = self.clone();
            move |s: &mut Session| f(&r, s)
        };
        if op == "uninstall" {
            return vec![step("remove", "Remove its files", 1.0, me(Self::remove_files))];
        }
        vec![
            step("find", "Find the latest release", 0.5, me(Self::find)),
            step("download", "Download", 4.0, me(Self::download)),
            step("verify", "Check the download", 0.5, me(Self::verify)),
            step("put", "Put it in its folder", 0.5, me(Self::put)),
            step("commands", if self.h().system == "windows" { "Add it to the Start menu" } else { "Add its commands" }, 0.5, me(Self::commands)),
            step("check", "Check that it starts", 0.5, me(Self::check)),
        ]
    }

    fn finish(&self, sess: &Session, op: &str, opts: &Map<String, Value>) -> Value {
        if op == "uninstall" {
            let purge = opts.get("purge").and_then(|v| v.as_bool()).unwrap_or(false);
            return json!({"headline": "RPCortex Studio is removed.", "body": if purge { "Its settings are gone too." } else { "Its settings are kept." }, "next": []});
        }
        let first = !sess.vars.get("before").and_then(|b| b["installed"].as_bool()).unwrap_or(false);
        let body = "Plug in your RPCortex board, then open RPCortex Studio: it finds the board by itself. It opens in a terminal \
                    (80×24 or larger).";
        let next = vec![json!({"id": "app", "label": "open in orbit", "kind": "app", "app": self.id(), "primary": true})];
        json!({"headline": if first { "RPCortex Studio is installed." } else { "RPCortex Studio is updated." }, "body": body, "next": next})
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
        h
    }

    #[test]
    fn each_computer_gets_its_own_file_or_none() {
        let r = |s, a| RpcortexStudioRecipe::new(host(s, a), Arc::new(Sources::github()));
        assert_eq!(r("linux", "amd64").asset_name().as_deref(), Some("rpcortex-studio-linux-x64"));
        assert_eq!(r("darwin", "arm64").asset_name().as_deref(), Some("rpcortex-studio-macos-arm64"));
        assert_eq!(r("windows", "amd64").asset_name().as_deref(), Some("rpcortex-studio-windows-x64.exe"));
        assert_eq!(r("linux", "arm64").asset_name(), None);
        assert_eq!(r("darwin", "amd64").support()["level"], "none");
        assert_eq!(r("linux", "amd64").exe(), PathBuf::from("/home/u/.local/share/rpcortex-studio/rpcortex-studio"));
        assert!(r("linux", "amd64").wants(&json!({"assets": {"rpcortex-studio-linux-x64": {}}})));
        assert!(!r("linux", "amd64").wants(&json!({"assets": {"rpcortex-studio-macos-arm64": {}}})));
    }

    #[test]
    fn versions_and_serial_groups() {
        assert_eq!(version_said("rpcortex-studio 1.0.0\n"), "1.0.0");
        assert_eq!(version_said("something else"), "");
        assert_eq!(serial_group("root:x:0:\ndialout:x:20:u\n"), "dialout");
        assert_eq!(serial_group("root:x:0:\nuucp:x:14:\n"), "uucp");
        assert_eq!(serial_group(""), "dialout");
        assert!(!can_read_write(std::path::Path::new("/nonexistent/ttyACM9")));
    }
}
