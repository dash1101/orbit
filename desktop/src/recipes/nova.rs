//! Nova's server: on Linux with systemd, and on Windows (a preview there). Not on macOS.
//!
//! Mirrors Nova's own get.sh / get.ps1: read the release's SHA256SUMS, check its Ed25519 signature against Nova's
//! release key (pinned here, not fetched), download the package, check its checksum, then install it as the
//! administrator. Linux: the .deb on Debian/Ubuntu (so apt keeps track of it), the one-file installer elsewhere.
//! Windows: nova-windows.zip, whose installer Windows runs after asking for permission once.
use crate::engine::{Session, Sources};
use crate::host::Host;
use crate::net;
use crate::recipe::{need, option, step, Recipe, RecipeBase, StepDef};
use crate::util::{failed, Res};
use base64::Engine;
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// packaging/release-key.pub in the Nova repository
pub const RELEASE_KEY: &str = "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAGk70hAkKUc/oE8yLsTv49yeIkSJkCeuDG0IqxnF69WI=\n-----END PUBLIC KEY-----\n";
const VERSION_FILE: &str = "/usr/lib/nova-api/VERSION";
const WIN_ZIP: &str = "nova-windows.zip";
const SETUP_GUI: &str = "/usr/lib/nova-api/setup-desktop"; // Nova's own setup window (asks for the password itself)
const NOVA_GUI: &str = "/usr/sbin/nova-gui"; // the Nova Server window: status, pairing, devices, approvals

/// The Ed25519 key in a PEM public key (SubjectPublicKeyInfo).
pub fn key_from_pem(pem: &str) -> Option<[u8; 32]> {
    let b64: String = pem.lines().filter(|l| !l.starts_with("-----")).collect();
    let der = base64::engine::general_purpose::STANDARD.decode(b64.trim()).ok()?;
    const PREFIX: [u8; 12] = [0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00];
    if der.len() != 44 || der[..12] != PREFIX {
        return None;
    }
    der[12..].try_into().ok()
}

/// SHA256SUMS signed with Nova's release key (Ed25519, base64 in SHA256SUMS.sig)?
pub fn check_signature(sums_text: &str, sig_text: &str, release_key: &str) -> Res<()> {
    let raw = base64::engine::general_purpose::STANDARD.decode(sig_text.trim())
        .map_err(|_| failed("The release's signature file is damaged, so it won't be installed.", ""))?;
    let bad_sig = || failed("The release's signature doesn't match Nova's release key, so it won't be installed.",
                            "Don't install this download. Check the Nova repository for news.");
    let key = key_from_pem(release_key).and_then(|k| ed25519_dalek::VerifyingKey::from_bytes(&k).ok()).ok_or_else(bad_sig)?;
    let sig = ed25519_dalek::Signature::from_slice(&raw).map_err(|_| bad_sig())?;
    key.verify_strict(sums_text.as_bytes(), &sig).map_err(|_| bad_sig())
}

pub struct NovaRecipe {
    base: RecipeBase,
    pub release_key: String,
    pub version_file: PathBuf,
    pub port: u16,
    pub plain_port: u16,
    pub start_tries: u32,
}

impl NovaRecipe {
    pub fn new(host: Host, sources: Arc<Sources>) -> NovaRecipe {
        NovaRecipe { base: RecipeBase::new(host, sources), release_key: RELEASE_KEY.into(), version_file: VERSION_FILE.into(),
                     port: 8495, plain_port: 8095, start_tries: 30 }
    }

    fn host(&self) -> &Host {
        &self.base.host
    }

    fn windows(&self) -> bool {
        self.host().system == "windows"
    }

    pub fn win_dir(&self) -> PathBuf {
        self.host().program_files().join("Nova")
    }

    /// The "Nova Server" window in everyone's Start menu.
    pub fn win_shortcut(&self) -> PathBuf {
        self.host().program_data().join("Microsoft").join("Windows").join("Start Menu").join("Programs").join("Nova Server.lnk")
    }

    fn dpkg_version(&self) -> String {
        let Some(dq) = self.host().which("dpkg-query") else { return String::new() };
        let (rc, out) = self.host().probe(&[dq, "-W".into(), "-f=${Status}|${Version}".into(), "nova-server".into()]);
        if rc == 0 && out.contains("install ok installed") {
            return out.rsplit('|').next().unwrap_or("").trim().to_string();
        }
        String::new()
    }

    fn installed_version(&self) -> String {
        std::fs::read_to_string(&self.version_file).map(|s| s.trim().to_string()).unwrap_or_default()
    }

    fn admin_way(&self) -> &'static str {
        if self.host().which("pkexec").is_some() && self.host().has_display() {
            return "window";
        }
        if self.host().which("sudo").is_some() && crate::runner::terminal_argv(&["true".into()], "Orbit").is_some() {
            return "terminal";
        }
        ""
    }

    /// (name in SHA256SUMS, file name on GitHub).
    fn package(&self, sums_names: &[String]) -> (String, String) {
        if self.windows() {
            return (WIN_ZIP.into(), WIN_ZIP.into());
        }
        if self.host().debian_like() {
            let re = regex::Regex::new(r"^nova-server_.*_all\.deb$").unwrap();
            if let Some(n) = sums_names.iter().find(|n| re.is_match(n)) {
                return (n.clone(), n.replace('~', "."));
            }
        }
        ("nova-installer.run".into(), "nova-installer.run".into())
    }

    // ---- steps -----------------------------------------------------------------------------------
    fn find(&self, s: &mut Session) -> Res<bool> {
        let rel = s.release(self.repo())?;
        s.asset(&rel, "SHA256SUMS", self.repo())?;
        s.detail(rel["tag"].as_str().unwrap_or(""));
        s.vars.insert("rel".into(), rel);
        Ok(true)
    }

    fn signature(&self, s: &mut Session) -> Res<bool> {
        let rel = s.vars["rel"].clone();
        let sums = s.fetch_text(&rel, "SHA256SUMS", self.repo())?;
        let sig = if rel["assets"].get("SHA256SUMS.sig").is_some() { s.fetch_text(&rel, "SHA256SUMS.sig", self.repo())? } else { String::new() };
        let pkg = if s.dry_run {
            let names: Vec<String> = rel["assets"].as_object().map(|m| m.keys().map(|n| n.replace(".alpha", "~alpha")).collect()).unwrap_or_default();
            self.package(&names)
        } else {
            let names: Vec<String> = sums.lines().filter_map(|ln| {
                let mut it = ln.splitn(2, char::is_whitespace);
                let (_, n) = (it.next()?, it.next()?);
                Some(n.trim().trim_start_matches('*').to_string())
            }).collect();
            self.package(&names)
        };
        s.vars.insert("sums".into(), json!(sums));
        s.vars.insert("pkg".into(), json!([pkg.0, pkg.1]));
        if s.dry_run {
            s.detail("Nova's release key (pinned in Orbit)");
            s.pause(0.3)?;
            return Ok(true);
        }
        if sig.is_empty() {
            return Err(failed("This Nova release isn't signed, so Orbit won't install it.", ""));
        }
        check_signature(&sums, &sig, &self.release_key)?;
        s.log("Release signature checks out (Nova's release key).");
        s.detail("signed with Nova's release key");
        Ok(true)
    }

    fn download(&self, s: &mut Session) -> Res<bool> {
        let gh = s.vars["pkg"][1].as_str().unwrap_or("").to_string();
        let rel = s.vars["rel"].clone();
        let f = s.download(&rel, &gh, self.repo(), Some(&gh))?;
        s.vars.insert("file".into(), json!(f.to_string_lossy()));
        Ok(true)
    }

    fn verify(&self, s: &mut Session) -> Res<bool> {
        let sum_name = s.vars["pkg"][0].as_str().unwrap_or("").to_string();
        let (file, sums) = (PathBuf::from(s.var_str("file")), s.var_str("sums"));
        s.verify(&file, &sums, &sum_name)?;
        Ok(true)
    }

    fn win_unpack(&self, s: &mut Session) -> Res<bool> {
        let dest = s.extract(&PathBuf::from(s.var_str("file")))?;
        let mut script = dest.join("Nova").join("windows").join("install.ps1");
        if !s.dry_run && !script.is_file() {
            script = s.find(&dest, "install.ps1")?; // the zip's layout changed: look for it
        }
        s.vars.insert("script".into(), json!(script.to_string_lossy()));
        Ok(true)
    }

    fn win_install(&self, s: &mut Session) -> Res<bool> {
        let args: Vec<String> = if s.opt_bool("firewall", true) { vec![] } else { vec!["-NoFirewall".into()] };
        let script = PathBuf::from(s.var_str("script"));
        s.admin_windows(&script, &args, "install Nova",
                        "The log below shows what Nova's installer said. Its own logs are in C:\\ProgramData\\Nova\\log.")?;
        self.forget();
        if !s.dry_run && !self.detect()["installed"].as_bool().unwrap_or(false) {
            return Err(failed("Nova's installer finished, but Nova isn't in its folder.", "The log below shows what the installer said."));
        }
        Ok(true)
    }

    fn win_remove(&self, s: &mut Session) -> Res<bool> {
        let args: Vec<String> = if s.opt_bool("purge", false) { vec!["-Purge".into()] } else { vec![] };
        s.admin_windows(&self.win_dir().join("uninstall.ps1"), &args, "remove Nova", "")?;
        Ok(true)
    }

    fn win_gone(&self, s: &mut Session) -> Res<bool> {
        self.forget();
        if !s.dry_run && self.win_dir().join("lib").join("VERSION").exists() {
            return Err(failed(format!("Some of Nova is still in {}.", self.win_dir().display()),
                              "Something was still using it. Restart the computer, then delete that folder."));
        }
        Ok(true)
    }

    fn install(&self, s: &mut Session) -> Res<bool> {
        let f = s.var_str("file");
        let target = s.vars["rel"]["tag"].as_str().unwrap_or("").trim_start_matches(['v', 'V']).replace('-', "~");
        let before = self.installed_version();
        let done = || {
            let v = self.installed_version();
            !v.is_empty() && (v == target || v != before)
        };
        let argv: Vec<String> = if f.ends_with(".deb") {
            ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", &f].iter().map(|x| x.to_string()).collect()
        } else {
            vec!["sh".into(), f.clone()]
        };
        s.admin(&argv, "install Nova", Some(&done), "The log below shows what the installer said.")?;
        Ok(true)
    }

    fn started(&self, s: &mut Session) -> Res<bool> {
        if s.dry_run {
            s.pause(0.4)?;
            return Ok(true);
        }
        s.detail("waiting for Nova to answer");
        let mut urls = vec![format!("https://127.0.0.1:{}/api/v1/ping", self.port)];
        if self.windows() {
            urls.insert(0, format!("http://127.0.0.1:{}/api/v1/ping", self.plain_port));
        }
        for _ in 0..self.start_tries {
            for url in &urls {
                if let Ok(v) = net::get_json(url, &net::Req { timeout: 1.0, headers: vec![], insecure: true }) {
                    if v["ok"].as_bool().unwrap_or(false) {
                        return Ok(true);
                    }
                }
            }
            s.sleep(1.0)?;
        }
        s.vars.insert("slow".into(), json!(true));
        s.log("Nova hasn't answered on this computer yet. It starts answering once it's set up.");
        Ok(true)
    }

    fn remove(&self, s: &mut Session) -> Res<bool> {
        let verb = if s.opt_bool("purge", false) { "purge" } else { "remove" };
        let argv: Vec<String> = ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", verb, "-y", "nova-server"].iter().map(|x| x.to_string()).collect();
        let gone = || self.dpkg_version().is_empty();
        s.admin(&argv, "remove Nova", Some(&gone), "")?;
        Ok(true)
    }

    fn gone(&self, s: &mut Session) -> Res<bool> {
        self.forget();
        if !s.dry_run && !self.dpkg_version().is_empty() {
            return Err(failed("apt still lists nova-server as installed.", "Run `sudo apt remove nova-server` in a terminal."));
        }
        Ok(true)
    }

    fn win_summary(&self, op: &str, opts: &Map<String, Value>, rel: Option<&Value>) -> Vec<String> {
        let purge = opts.get("purge").and_then(|v| v.as_bool()).unwrap_or(false);
        if op == "uninstall" {
            return vec![
                format!("Stop Nova and remove it with its own uninstaller ({}\\uninstall.ps1)", self.win_dir().display()),
                if purge { "Delete its settings and paired devices too".into() }
                else { "Keep its settings and paired devices (in C:\\ProgramData\\Nova) for a reinstall".into() },
                "Your containers and files stay as they are".into(),
            ];
        }
        let v = rel.and_then(|r| r["tag"].as_str()).unwrap_or("the latest release");
        let first = !self.installed()["installed"].as_bool().unwrap_or(false);
        let mut out = vec![
            format!("Download Nova {v} for Windows (nova-windows.zip) from GitHub"),
            "Check the release's signature with Nova's release key, then the download's checksum".into(),
            format!("Run Nova's installer as the administrator: Nova goes to {}, gets its own Python environment (Python itself is \
                     installed with winget if it's missing) and three background tasks that start with Windows", self.win_dir().display()),
        ];
        if opts.get("firewall").and_then(|v| v.as_bool()).unwrap_or(true) {
            out.push("Let your home network and Tailscale reach Nova through Windows Firewall".into());
        }
        out.push(format!("Start Nova{}", if first { "; then pair your phone from Nova Server in the Start menu" }
                                          else { " again, keeping its settings, paired devices and certificate" }));
        out
    }
}

impl Recipe for NovaRecipe {
    fn base(&self) -> &RecipeBase {
        &self.base
    }
    fn id(&self) -> &str {
        "nova"
    }
    fn name(&self) -> &str {
        "Nova"
    }
    fn repo(&self) -> &str {
        "dash1101/Nova"
    }
    fn blurb(&self) -> &str {
        "Secure remote for your home server"
    }
    fn glyph(&self) -> &str {
        "NV"
    }
    fn color(&self) -> &str {
        "#5ec4b6"
    }

    fn platform(&self, system: &str) -> (String, String) {
        match system {
            "linux" => ("full".into(), String::new()),
            "darwin" => ("none".into(), "Nova's server isn't available on macOS yet; it runs on Linux. From a Mac you can still use Nova web \
                                         in your browser, or the Nova app on Android, to reach a Nova server.".into()),
            "windows" => ("limited".into(), "A preview on Windows 10 and 11 and Windows Server 2019 or newer: pairing, devices, approvals, live \
                                             graphs, alerts, the task manager, power and Docker Desktop work; programs and updates, files, the \
                                             terminal and backups aren't there yet.".into()),
            _ => ("none".into(), "Nova isn't available on this system yet.".into()),
        }
    }

    fn support(&self) -> Value {
        let (level, note) = self.platform(&self.host().system);
        if level != "none" && !(self.host().arch == "amd64" || self.host().arch == "arm64") {
            return json!({"level": "none", "note": format!("Nova isn't available for this processor ({}) yet.", self.host().arch)});
        }
        if level != "none" && self.host().system == "linux" && !self.host().has_systemd() {
            return json!({"level": "none", "note": "Nova runs as a systemd service, and this Linux doesn't use systemd."});
        }
        json!({"level": level, "note": note})
    }

    fn detect(&self) -> Value {
        let none = json!({"installed": false, "version": "", "where": "", "how": ""});
        if self.windows() {
            return match std::fs::read_to_string(self.win_dir().join("lib").join("VERSION")) {
                Ok(v) => json!({"installed": true, "version": v.trim(), "where": self.win_dir().to_string_lossy(), "how": "Nova's Windows installer"}),
                Err(_) => none,
            };
        }
        if self.host().system != "linux" {
            return none;
        }
        let deb = self.dpkg_version();
        let v = self.installed_version();
        if v.is_empty() && deb.is_empty() {
            return none;
        }
        json!({"installed": true, "version": if v.is_empty() { deb.clone() } else { v }, "where": "/usr/lib/nova-api",
               "how": if deb.is_empty() { "Nova's installer" } else { "Debian package" }})
    }

    fn can_uninstall(&self) -> (bool, String) {
        if self.windows() {
            if self.win_dir().join("uninstall.ps1").is_file() {
                return (true, String::new());
            }
            return (false, format!("Nova's uninstaller isn't in {}, so Orbit can't remove it.", self.win_dir().display()));
        }
        if !self.dpkg_version().is_empty() {
            return (true, String::new());
        }
        (false, "Nova was installed with its one-file installer, so there's no package to remove. Run `sudo nova help uninstall` in a \
                 terminal for the steps.".into())
    }

    fn needs_admin(&self, _op: &str) -> Value {
        if self.windows() {
            return json!("Windows asks once");
        }
        json!(match self.admin_way() { "window" => "in the system's password window", "terminal" => "in a terminal window (sudo)", _ => "the system will ask" })
    }

    fn requirements(&self, op: &str) -> Vec<Value> {
        if self.windows() {
            return vec![need("Windows PowerShell (it comes with Windows)", self.host().which("powershell").is_some(),
                             "Orbit runs Nova's installer with PowerShell, and can't find powershell.exe.", true, "")];
        }
        let way = self.admin_way();
        let mut out = vec![
            need("Linux with systemd", true, "", true, &self.host().distro()),
            need("A way to ask for the administrator password", !way.is_empty(),
                 "Install pkexec (polkit), or run the installer from a desktop session with a terminal app.", true,
                 match way { "window" => "the system's password window", "terminal" => "sudo in a terminal", _ => "" }),
        ];
        if op == "install" && !self.host().debian_like() {
            out.push(need("Debian or Ubuntu (apt keeps Nova updated)", false,
                          "Other distributions use Nova's one-file installer; it installs what Nova needs with dnf, pacman or zypper.", false, ""));
        }
        out
    }

    fn options(&self, op: &str) -> Vec<Value> {
        if op == "uninstall" {
            return vec![option("purge", "Also delete Nova's settings and paired devices", "bool", json!(false),
                               "Leave this off to keep them for a later reinstall. Your containers, files and backups are never touched either way.")];
        }
        if self.windows() {
            return vec![option("firewall", "Let your other devices reach Nova", "bool", json!(true),
                               "Adds two Windows Firewall rules that let in your home network and Tailscale, nothing else. Without them only this \
                                computer can reach Nova, and a phone can't pair over Wi-Fi.")];
        }
        vec![]
    }

    fn assets(&self, rel: &Value) -> Vec<String> {
        let names: Vec<String> = rel["assets"].as_object().map(|m| m.keys().cloned().collect()).unwrap_or_default();
        let (_, gh) = self.package(&names);
        vec![gh, "SHA256SUMS".into(), "SHA256SUMS.sig".into()]
    }

    fn summary(&self, op: &str, opts: &Map<String, Value>, rel: Option<&Value>) -> Vec<String> {
        if self.windows() {
            return self.win_summary(op, opts, rel);
        }
        let purge = opts.get("purge").and_then(|v| v.as_bool()).unwrap_or(false);
        if op == "uninstall" {
            return vec![
                format!("Remove the Nova server with apt ({} nova-server)", if purge { "purge" } else { "remove" }),
                if purge { "Delete its settings and paired devices too".into() } else { "Keep its settings and paired devices".into() },
                "Your containers, files and backups stay as they are".into(),
                "Used Cloudflare auto-setup? Run `sudo nova cloudflare undo` first".into(),
            ];
        }
        let v = rel.and_then(|r| r["tag"].as_str()).unwrap_or("the latest release");
        let pkg = if self.host().debian_like() { "the Debian package (apt keeps it updated)" } else { "Nova's one-file installer" };
        let first = !self.installed()["installed"].as_bool().unwrap_or(false);
        vec![format!("Download Nova {v} from GitHub: {pkg}"),
             "Check the release's signature with Nova's release key, then the download's checksum".into(),
             "Install it as the administrator (system-wide, as a service)".into(),
             format!("Start Nova's services{}", if first { "; then you set it up and pair your phone" } else { "" })]
    }

    fn steps(self: Arc<Self>, op: &str, _opts: &Map<String, Value>) -> Vec<StepDef> {
        let me = |f: fn(&NovaRecipe, &mut Session) -> Res<bool>| {
            let r = self.clone();
            move |s: &mut Session| f(&r, s)
        };
        if self.windows() {
            if op == "uninstall" {
                return vec![step("remove", "Remove Nova", 3.0, me(Self::win_remove)), step("check", "Make sure it's gone", 0.5, me(Self::win_gone))];
            }
            return vec![
                step("find", "Find the latest release", 0.5, me(Self::find)),
                step("signature", "Check the release's signature", 1.0, me(Self::signature)),
                step("download", "Download", 2.0, me(Self::download)),
                step("verify", "Check the download", 0.5, me(Self::verify)),
                step("unpack", "Unpack", 0.5, me(Self::win_unpack)),
                step("install", "Install as administrator", 6.0, me(Self::win_install)),
                step("start", "Check that Nova started", 1.0, me(Self::started)),
            ];
        }
        if op == "uninstall" {
            return vec![step("remove", "Remove Nova", 3.0, me(Self::remove)), step("check", "Make sure it's gone", 1.0, me(Self::gone))];
        }
        vec![
            step("find", "Find the latest release", 0.5, me(Self::find)),
            step("signature", "Check the release's signature", 1.0, me(Self::signature)),
            step("download", "Download", 3.0, me(Self::download)),
            step("verify", "Check the download", 0.5, me(Self::verify)),
            step("install", "Install as administrator", 4.0, me(Self::install)),
            step("start", "Check that Nova started", 1.0, me(Self::started)),
        ]
    }

    fn finish(&self, sess: &Session, op: &str, opts: &Map<String, Value>) -> Value {
        let purge = opts.get("purge").and_then(|v| v.as_bool()).unwrap_or(false);
        let first = !sess.vars.get("before").and_then(|b| b["installed"].as_bool()).unwrap_or(false);
        if self.windows() {
            if op == "uninstall" {
                return json!({"headline": "Nova is removed.", "body": format!("Its tasks, firewall rules and Start menu entry are gone.{}",
                    if purge { " Its settings and paired devices too." } else { " Its settings and paired devices are kept in C:\\ProgramData\\Nova for a reinstall." }),
                    "next": []});
            }
            let mut body = if first { "Next, pair your phone: open Nova Server, press “Pair a phone or tablet” and scan the code with the Nova app.".to_string() }
                           else { "Its settings, paired devices and certificate are kept.".to_string() };
            if sess.vars.get("slow").and_then(|v| v.as_bool()).unwrap_or(false) {
                body.push_str(" Nova hasn't answered yet; give it a minute, or look in C:\\ProgramData\\Nova\\log.");
            }
            return json!({"headline": if first { "Nova is installed." } else { "Nova is updated." }, "body": body, "next": [
                {"id": "gui", "label": "open nova server", "kind": "shortcut", "path": self.win_shortcut().to_string_lossy(), "primary": first},
                {"id": "app", "label": "open in orbit", "kind": "app", "app": "nova", "primary": !first}]});
        }
        if op == "uninstall" {
            return json!({"headline": "Nova is removed.", "body": format!("Your containers, files and backups are untouched.{}",
                if purge { "" } else { " Its settings are kept for a reinstall." }), "next": []});
        }
        let mut nxt = vec![];
        if first {
            // Nova 0.9.16 and newer have windows for both; older ones are set up in a terminal
            nxt.push(if Path::new(SETUP_GUI).exists() {
                json!({"id": "setup", "label": "set up nova", "kind": "program", "argv": [SETUP_GUI], "primary": true})
            } else {
                json!({"id": "setup", "label": "set up nova", "kind": "terminal", "argv": ["sudo", "nova", "setup"], "primary": true})
            });
            nxt.push(if Path::new(NOVA_GUI).exists() {
                json!({"id": "pair", "label": "pair a phone", "kind": "program", "argv": [NOVA_GUI]})
            } else {
                json!({"id": "pair", "label": "pair a phone", "kind": "terminal", "argv": ["sudo", "nova", "add"]})
            });
        }
        nxt.push(json!({"id": "app", "label": "open in orbit", "kind": "app", "app": "nova", "primary": !first}));
        json!({"headline": if first { "Nova is installed." } else { "Nova is updated." },
               "body": if first { "Next, set it up (a few questions in a terminal), then pair your phone with the Nova app." }
                       else { "Nova's services were restarted on the new version." },
               "next": nxt})
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_release_key_reads() {
        assert!(key_from_pem(RELEASE_KEY).is_some());
        assert!(key_from_pem("-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----").is_none());
    }

    #[test]
    fn signatures_are_checked() {
        use ed25519_dalek::{Signer, SigningKey};
        let sk = SigningKey::from_bytes(&[7u8; 32]);
        let mut der = vec![0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00];
        der.extend(sk.verifying_key().to_bytes());
        let pem = format!("-----BEGIN PUBLIC KEY-----\n{}\n-----END PUBLIC KEY-----\n", base64::engine::general_purpose::STANDARD.encode(&der));
        let sums = "abc  nova-windows.zip\n";
        let sig = base64::engine::general_purpose::STANDARD.encode(sk.sign(sums.as_bytes()).to_bytes());
        assert!(check_signature(sums, &sig, &pem).is_ok());
        assert!(check_signature("tampered", &sig, &pem).is_err());
        assert!(check_signature(sums, &sig, RELEASE_KEY).is_err());
        assert!(check_signature(sums, "not base64!", &pem).is_err());
    }
}
