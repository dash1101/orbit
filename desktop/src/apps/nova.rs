//! Nova: secure remote for a home server (system services; its API is signed by a paired phone).
//!
//! Orbit can't, and shouldn't, use Nova's API: every request except /ping must be signed by a paired device. So
//! here Nova gets: reachability and version (public /ping), the state of its systemd units, release checks, the
//! server's health from the file Nova's own monitor writes for dashboards (world-readable), and doors into Nova
//! web and the `sudo nova …` commands. On Windows (a preview there) Orbit shows whether Nova answers and its
//! version, and opens an administrator PowerShell window for each command.
use super::{ask_value, field, open_url, release_info, with, App, Ctx};
use crate::net;
use crate::systemd;
use crate::util::{bad, Res};
use base64::Engine;
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub const PORT: u16 = 8495;
pub const PLAIN_PORT: u16 = 8095; // Nova web without TLS; on Windows the one that answers on the computer itself
const UNITS: [(&str, &str); 7] = [
    ("nova-api.service", "api"), ("nova-gate.service", "access-code gate"), ("nova-helper.socket", "root helper"),
    ("nova-alerts.timer", "alerts"), ("nova-backups.timer", "backups"), ("nova-labs.timer", "labs"),
    ("nova-update-check.timer", "update check"),
];
/// Parts of Nova that are only there when you set them up: listed when present, never counted as missing.
const OPTIONAL_UNITS: [(&str, &str); 3] = [("nova-brain.service", "assistant"), ("nova-ear.service", "assistant: voice"),
                                           ("nova-rgb-anim.service", "lighting")];
/// Oneshot jobs behind the timers: a failed last run is worth showing.
const JOBS: [(&str, &str); 4] = [("nova-update-check.service", "update check"), ("nova-backups.service", "backups"),
                                 ("nova-alerts.service", "alerts"), ("nova-labs.service", "labs")];
const STATUS_FILE: &str = "/var/lib/nova-alerts/www/status.json";
const NOVA_GUI: &str = "/usr/sbin/nova-gui"; // the Nova Server window (Nova 0.9.16 and newer)
const STALE_AFTER: f64 = 15.0 * 60.0;

const TERM: [(&str, &[&str]); 21] = [
    ("status", &["sudo", "nova", "status"]), ("doctor", &["sudo", "nova", "doctor"]), ("log", &["sudo", "nova", "log"]),
    ("journal", &["sudo", "journalctl", "-u", "nova-api.service", "-n", "300", "--no-pager"]),
    ("add", &["sudo", "nova", "add"]), ("add-viewer", &["sudo", "nova", "add", "--viewer"]),
    ("add-remote", &["sudo", "nova", "add", "--remote"]), ("approvals", &["sudo", "nova", "approve"]),
    ("links", &["sudo", "nova", "link"]), ("plugins", &["sudo", "nova", "plugin", "list"]),
    ("devices", &["sudo", "nova", "devices"]), ("tailscale-status", &["tailscale", "status"]),
    ("tailscale-up", &["sudo", "tailscale", "up"]), ("cf-list", &["sudo", "nova", "cloudflare", "list"]),
    ("cf-undo", &["sudo", "nova", "cloudflare", "undo"]), ("setup", &["sudo", "nova", "setup"]),
    ("update-check", &["sudo", "nova", "update", "--check"]), ("update", &["sudo", "nova", "update"]),
    ("backup-now", &["sudo", "nova", "backup", "now"]), ("backup-list", &["sudo", "nova", "backup", "list"]),
    ("restart", &["sudo", "systemctl", "restart", "nova-api.service"]),
];
/// What the Windows server's `nova` command can do (docs/WINDOWS.md in Nova).
const WIN_TERM: [(&str, &[&str]); 10] = [
    ("status", &["status"]), ("doctor", &["doctor"]), ("log", &["log"]), ("add", &["add"]), ("add-viewer", &["add", "--viewer"]),
    ("add-remote", &["add", "--remote"]), ("approvals", &["approve"]), ("links", &["link"]), ("plugins", &["plugin", "list"]),
    ("devices", &["devices"]),
];
/// Saves the Cloudflare API token the way Nova's own root helper does, reading it from a private file so it never
/// appears on a command line.
const CF_SAVE: &str = "import sys; sys.path.insert(0, '/usr/lib/nova-api'); import cfsync; \
                       cfsync.save_token(open(sys.argv[1]).read().strip()); \
                       print('Saved. Nova\\'s Cloudflare auto-setup can use it now (Nova web, Labs).')";

pub struct Nova {
    ctx: Arc<Ctx>,
    found: Mutex<Option<String>>,
    pub status_file: PathBuf,
    waiting: Mutex<u64>, // browsers and actions waiting for approval, as Nova last said (see health)
}

/// The approval code in what was typed or pasted: "ZAG97V", "zag 97v", "ZAG-97V", or the whole
/// "sudo nova approve ZAG97V" line as the browser or the Nova app shows it (options after the code are left out).
pub fn approval_code(typed: &str) -> Res<String> {
    let lower = typed.to_ascii_lowercase();
    let rest = match lower.rfind("approve") {
        Some(i) => &typed[i + "approve".len()..],
        None => typed,
    };
    let code: String = rest.split_whitespace().take_while(|w| !w.starts_with("--")).collect::<String>().replace('-', "").to_ascii_uppercase();
    if (4..=12).contains(&code.len()) && code.chars().all(|c| c.is_ascii_alphanumeric()) {
        Ok(code)
    } else {
        Err(bad("Code shown in the browser: that isn't one (a code is six letters and numbers, like ZAG97V)"))
    }
}

impl Nova {
    pub fn new(ctx: Arc<Ctx>) -> Nova {
        // (Orbit's own tests can name another status file)
        let status = std::env::var("ORBIT_TEST_NOVA_STATUS").ok().filter(|p| !p.is_empty()).unwrap_or_else(|| STATUS_FILE.into());
        Nova { ctx, found: Mutex::new(None), status_file: PathBuf::from(status), waiting: Mutex::new(0) }
    }

    fn windows() -> bool {
        cfg!(windows)
    }

    pub fn win_dir() -> PathBuf {
        std::env::var_os("ProgramFiles").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("C:\\Program Files")).join("Nova")
    }

    fn program_data() -> PathBuf {
        std::env::var_os("ProgramData").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("C:\\ProgramData"))
    }

    fn win_version(&self) -> String {
        std::fs::read_to_string(Self::win_dir().join("lib").join("VERSION")).map(|s| s.trim().to_string()).unwrap_or_default()
    }

    /// An administrator PowerShell window running `nova <words>` (or a script), left open to read. The command
    /// travels base64-encoded (-EncodedCommand), so nothing in it can be misread as more commands.
    fn win_admin_window(&self, title: &str, words: &[String]) -> Res<Value> {
        let q = |s: &str| format!("'{}'", s.replace('\'', "''"));
        let body = if words.first().map(|w| w == "@script").unwrap_or(false) {
            words[1].clone()
        } else {
            format!("& {}{}", q(&Self::win_dir().join("nova.cmd").to_string_lossy()), words.iter().map(|w| format!(" {}", q(w))).collect::<String>())
        };
        let inner = format!("$host.UI.RawUI.WindowTitle = {}; {body}", q(title));
        let utf16: Vec<u8> = inner.encode_utf16().flat_map(|u| u.to_le_bytes()).collect();
        let enc = base64::engine::general_purpose::STANDARD.encode(utf16);
        let outer = format!("Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{enc}'");
        let argv: Vec<String> = ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", &outer].iter().map(|s| s.to_string()).collect();
        if !self.ctx.runner.launch(&argv) {
            return Err(bad("couldn't start PowerShell"));
        }
        Ok(json!({"ok": true, "message": format!("opened an administrator window: {title} (Windows asks for permission)")}))
    }

    fn candidates(&self) -> Vec<String> {
        if !cfg!(target_os = "linux") {
            return vec!["127.0.0.1".into()];
        }
        let (rc, out) = self.ctx.runner.read(&["hostname".into(), "-I".into()], Duration::from_secs(2));
        let mut hosts = vec!["127.0.0.1".to_string()];
        if rc == 0 {
            // the computer itself, then the home network, other addresses, and Tailscale last (as Nova itself suggests)
            let mut more: Vec<(u8, String)> = out.split_whitespace().filter_map(|h| h.parse::<std::net::Ipv4Addr>().ok()).map(|ip| {
                let o = ip.octets();
                let rank = if ip.is_private() { 0 } else if o[0] == 100 && (64..128).contains(&o[1]) { 2 } else { 1 };
                (rank, ip.to_string())
            }).collect();
            more.sort_by_key(|(rank, _)| *rank); // stable: each group keeps the system's order
            hosts.extend(more.into_iter().map(|(_, h)| h));
        }
        hosts
    }

    /// (port, scheme) to look for Nova web on: on Windows the plain port answers on the computer itself.
    fn web_ports() -> Vec<(u16, &'static str)> {
        if Self::windows() { vec![(PLAIN_PORT, "http"), (PORT, "https")] } else { vec![(PORT, "https")] }
    }

    pub fn address(&self) -> Option<String> {
        let url = self.hub().get("url").and_then(|u| u.as_str()).unwrap_or("").trim_end_matches('/').to_string();
        if !url.is_empty() {
            if let Ok(u) = url::Url::parse(&url) {
                if u.scheme() == "http" || u.scheme() == "https" {
                    return Some(url);
                }
            }
        }
        // Nova on this computer itself first (on Linux, Nova 0.9.20 and newer answer there too), over an address found before
        if cfg!(target_os = "linux") && net::port_open("127.0.0.1", PORT, Duration::from_millis(300)) {
            let f = format!("https://127.0.0.1:{PORT}");
            *self.found.lock().unwrap() = Some(f.clone());
            return Some(f);
        }
        if let Some(f) = self.found.lock().unwrap().clone() {
            if let Ok(u) = url::Url::parse(&f) {
                if net::port_open(u.host_str().unwrap_or(""), u.port().unwrap_or(0), Duration::from_millis(300)) {
                    return Some(f);
                }
            }
        }
        for h in self.candidates() {
            for (port, scheme) in Self::web_ports() {
                if net::port_open(&h, port, Duration::from_millis(300)) {
                    let f = format!("{scheme}://{h}:{port}");
                    *self.found.lock().unwrap() = Some(f.clone());
                    return Some(f);
                }
            }
        }
        *self.found.lock().unwrap() = None;
        None
    }

    fn ping(&self, addr: &str, s: &mut Map<String, Value>) -> Option<Value> {
        match net::get_json(&format!("{addr}/api/v1/ping"), &net::Req { timeout: 2.0, headers: vec![], insecure: true }) {
            Ok(v) => Some(v),
            Err(e) => {
                s.insert("detail".into(), json!(e.message));
                None
            }
        }
    }

    /// What Nova's monitor last wrote about the server, or None.
    pub fn health(&self) -> Option<Value> {
        let d: Value = serde_json::from_str(&std::fs::read_to_string(&self.status_file).ok()?).ok()?;
        let level = d["level"].as_str()?;
        if !["ok", "info", "warning", "critical"].contains(&level) {
            return None;
        }
        let age = d["updated"].as_str().and_then(parse_time).map(|t| (net::now() - t).max(0.0));
        let lead = regex::Regex::new(r"^[^\w/~]+\s*").unwrap();
        let clean = |t: &Value| -> String { lead.replace(t.as_str().unwrap_or(""), "").trim().chars().take(120).collect() };
        let issues: Vec<String> = d["active"].as_array().cloned().unwrap_or_default().iter()
            .filter(|a| a["level"] == "warning" || a["level"] == "critical")
            .map(|a| clean(&a["title"])).filter(|t| !t.is_empty()).collect();
        let m = if d["metrics"].is_object() { d["metrics"].clone() } else { json!({}) };
        // what waits for approval (counts only: the code is read off the browser that asks)
        let count = |k: &str| d["waiting"][k].as_u64().unwrap_or(0).min(99);
        let waiting = json!({"browsers": count("browsers"), "actions": count("actions")});
        Some(json!({"level": level, "headline": clean(&d["headline"]), "issues": issues, "metrics": m, "age": age, "waiting": waiting,
                    "stale": age.map(|a| a > STALE_AFTER).unwrap_or(true)}))
    }

    fn ago(seconds: f64) -> String {
        let s = seconds as i64;
        if s >= 86400 { format!("{}d ago", s / 86400) } else if s >= 3600 { format!("{}h ago", s / 3600) } else { format!("{} min ago", (s / 60).max(1)) }
    }

    /// Rows for the status card: what needs attention first, then the server at a glance.
    pub fn health_facts(&self, hl: &Value) -> Vec<Value> {
        let (m, mut out) = (&hl["metrics"], vec![]);
        if hl["stale"].as_bool().unwrap_or(true) {
            let when = hl["age"].as_f64().map(Self::ago).unwrap_or_else(|| "a while ago".into());
            out.push(json!(["monitor", format!("last checked {when}: Nova's alerts aren't running"), "warn"]));
            return out;
        }
        let issues: Vec<String> = hl["issues"].as_array().cloned().unwrap_or_default().iter().filter_map(|x| x.as_str().map(String::from)).collect();
        for (i, t) in issues.iter().take(4).enumerate() {
            out.push(json!([if i == 0 { "needs attention" } else { "" }, t, "warn"]));
        }
        if issues.len() > 4 {
            out.push(json!(["", format!("and {} more (open Nova)", issues.len() - 4), "muted"]));
        }
        let text = |k: &str| m[k].as_str().filter(|s| !s.trim().is_empty()).map(String::from);
        for (key, label) in [("containers", "containers"), ("websites", "websites")] {
            if let Some(t) = text(key) {
                if !t.starts_with("0/0") {
                    out.push(json!([label, t]));
                }
            }
        }
        let drives: Vec<Value> = m["storage"].as_array().cloned().unwrap_or_default().into_iter().filter(|d| d["pct"].is_number()).collect();
        if let Some(d) = drives.iter().max_by(|a, b| a["pct"].as_f64().unwrap().partial_cmp(&b["pct"].as_f64().unwrap()).unwrap()) {
            let pct = d["pct"].as_f64().unwrap_or(0.0);
            let left = d["free"].as_f64().map(|f| format!(" · {:.0} GB free", f / 1e9)).unwrap_or_default();
            let more = if drives.len() > 1 { format!(" (fullest of {})", drives.len()) } else { String::new() };
            let name = d["name"].as_str().filter(|s| !s.is_empty()).or(d["mount"].as_str().filter(|s| !s.is_empty())).unwrap_or("drive");
            out.push(json!(["storage", format!("{name} {pct:.0}%{left}{more}"), if pct >= 90.0 { json!("warn") } else { Value::Null }]));
        }
        if let Some(t) = text("data_backup") {
            out.push(json!(["last backup", t]));
        }
        if let Some(t) = text("uptime") {
            out.push(json!(["up for", t]));
        }
        out
    }

    fn win_status(&self) -> Value {
        let version = self.win_version();
        let mut s = Map::new();
        s.insert("units".into(), json!([]));
        let mut f: Vec<Value> = vec![];
        let addr = self.address();
        let ping = addr.as_ref().and_then(|a| self.ping(a, &mut s));
        if version.is_empty() && ping.is_none() {
            s.insert("state".into(), json!("off"));
            s.insert("headline".into(), json!("Not installed on this computer"));
            if self.hub().get("url").and_then(|u| u.as_str()).unwrap_or("").is_empty() {
                s.insert("detail".into(), json!("Nova on another computer? Set its address in settings (connection)."));
            }
            s.insert("facts".into(), json!(f));
            return Value::Object(s);
        }
        if let Some(a) = &addr {
            f.push(json!(["web", a]));
        }
        if !version.is_empty() {
            f.push(json!(["installed", format!("{} (a preview on Windows)", Self::win_dir().display())]));
        }
        match ping.filter(|p| p["ok"].as_bool().unwrap_or(false)) {
            Some(p) => {
                let v = p["api"].as_str().filter(|x| !x.is_empty()).map(String::from).unwrap_or(version.clone());
                s.insert("version".into(), json!(v));
                f.push(json!(["api level", format!("{} (oldest served {})", vstr(&p["api_level"]), vstr(&p["min_level"]))]));
                s.insert("state".into(), json!("ok"));
                s.insert("headline".into(), json!("Running"));
            }
            None => {
                s.insert("version".into(), json!(version));
                s.insert("state".into(), json!("down"));
                s.insert("headline".into(), json!("Nova isn't answering"));
                s.insert("detail".into(), json!("Its three tasks are in Task Scheduler (folder \\Nova\\); its logs are in C:\\ProgramData\\Nova\\log."));
            }
        }
        if crate::runner::Runner::available("tailscale") {
            f.extend(self.remote_facts().into_iter().filter(|x| x[0] == "tailscale"));
        }
        s.insert("facts".into(), json!(f));
        let v = s.get("version").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if let Some(rel) = release_info(self, &v, false) {
            s.insert("update".into(), rel);
        }
        Value::Object(s)
    }

    /// Away-from-home: Tailscale and the Cloudflare tunnel, read without root.
    fn remote_facts(&self) -> Vec<Value> {
        let mut out = vec![];
        let r = &self.ctx.runner;
        if crate::runner::Runner::available("tailscale") {
            let (rc, js) = r.read(&["tailscale".into(), "status".into(), "--json".into(), "--peers=false".into()], Duration::from_secs(3));
            let st: Value = if rc == 0 { serde_json::from_str(&js).unwrap_or(json!({})) } else { json!({}) };
            let state = st["BackendState"].as_str().unwrap_or("");
            let ips: Vec<String> = st["Self"]["TailscaleIPs"].as_array().cloned().unwrap_or_default().iter()
                .filter_map(|i| i.as_str()).filter(|i| !i.contains(':')).map(String::from).collect();
            let name = st["Self"]["DNSName"].as_str().unwrap_or("").trim_end_matches('.').to_string();
            if state == "Running" {
                let who = if name.is_empty() { ips.first().cloned().unwrap_or_default() } else { name };
                out.push(json!(["tailscale", format!("connected · {who}")]));
            } else {
                let t = match state { "NeedsLogin" => "not signed in", "Stopped" => "turned off", "" => "not running", s => s };
                out.push(json!(["tailscale", t, "warn"]));
            }
        } else {
            out.push(json!(["tailscale", "not installed", "muted"]));
        }
        let cf = systemd::show(r, "cloudflared.service", false);
        if let Some(state) = cf["state"].as_str().filter(|s| *s != "missing") {
            out.push(json!(["cloudflare tunnel", state, if state == "running" { Value::Null } else { json!("warn") }]));
        }
        out
    }

    fn win_actions(&self) -> Vec<Value> {
        let w = |id: &str, label: &str, group: &str| json!({"id": format!("win:{id}"), "label": label, "group": group});
        let mut acts = vec![json!({"id": "open", "label": "open nova web", "group": "open", "primary": true})];
        let installed = !self.win_version().is_empty();
        if installed {
            acts.push(json!({"id": "gui", "label": "nova server", "group": "open"}));
        }
        acts.push(json!({"id": "releases", "label": "releases", "group": "open"}));
        if !installed {
            return acts;
        }
        let linux = self.linux_actions();
        let ask = |id: &str| linux.iter().find(|a| a["id"] == id).map(|a| a["ask"].clone()).unwrap_or(Value::Null);
        acts.extend([
            w("status", "status", "check"), w("doctor", "doctor", "check"), w("log", "recent actions", "check"),
            w("journal", "api log", "check"),
            w("add", "pair a phone", "phones & browsers"), w("add-viewer", "pair a view-only device", "phones & browsers"),
            w("add-remote", "pair a phone that's away from home", "phones & browsers"),
            w("devices", "paired devices", "phones & browsers"), w("approvals", "waiting for approval", "phones & browsers"),
            with(w("approve", "approve a browser", "phones & browsers"), json!({"ask": ask("term:approve")})),
            with(w("remove", "remove a device", "phones & browsers"), json!({"danger": true, "ask": ask("term:remove")})),
            w("links", "linked servers", "more"), w("plugins", "plugins", "more"),
        ]);
        acts
    }

    fn linux_actions(&self) -> Vec<Value> {
        let t = |id: &str, label: &str, group: &str, changes: bool, extra: Value| {
            with(json!({"id": id, "label": label, "group": group, "term": true, "changes": changes}), extra)
        };
        let mut acts = vec![json!({"id": "open", "label": "open nova web", "group": "open", "primary": true})];
        if Path::new(NOVA_GUI).exists() {
            acts.push(json!({"id": "gui", "label": "nova server", "group": "open"}));
        }
        acts.push(json!({"id": "releases", "label": "releases", "group": "open"}));
        acts.extend([
            t("term:status", "status", "check", false, json!({})),
            t("term:doctor", "doctor", "check", false, json!({})),
            t("term:log", "recent actions", "check", false, json!({})),
            t("term:journal", "api log", "check", false, json!({})),
            t("term:add", "pair a phone", "phones & browsers", true, json!({})),
            t("term:add-viewer", "pair a view-only device", "phones & browsers", true, json!({})),
            t("term:add-remote", "pair a phone that's away from home", "phones & browsers", true, json!({})),
            t("term:devices", "paired devices", "phones & browsers", false, json!({})),
            t("term:approvals", "waiting for approval", "phones & browsers", false, json!({})),
            t("term:approve", "approve a browser", "phones & browsers", true, json!({"ask": {"label": "Code shown in the browser",
                "help": "Six letters and numbers, like ZAG97V. Pasting the whole “sudo nova approve …” line works too.",
                "pattern": "[A-Za-z0-9 -]{4,80}", "patternHelp": "letters and numbers, as shown in the browser"}})),
            t("term:remove", "remove a device", "phones & browsers", true, json!({"danger": true, "ask": {
                "label": "Name or ID of the device (see paired devices)", "pattern": r"[\w.'(][\w .'()-]{0,59}",
                "patternHelp": "its name or ID, as listed in paired devices",
                "help": "It can never connect again; pair it anew to bring it back."}})),
            t("term:tailscale-status", "tailscale status", "away from home", false, json!({})),
            t("term:tailscale-up", "connect to tailscale", "away from home", true,
              json!({"confirm": "Sign this computer in to Tailscale? A sign-in link appears in the terminal."})),
            t("term:cf-token", "cloudflare api token", "away from home", true, json!({"ask": {"label": "Cloudflare API token", "secret": true,
                "pattern": "[A-Za-z0-9_-]{30,200}", "patternHelp": "that doesn't look like a Cloudflare API token",
                "help": "Used by Nova's Cloudflare auto-setup (Nova web → Labs). Create it in Cloudflare → My Profile → API Tokens with: \
                         Account · Cloudflare Tunnel · Edit, Account · Access: Apps and Policies · Edit, Zone · DNS · Edit. Saved root-only on this server."}})),
            t("term:cf-list", "what nova changed in cloudflare", "away from home", false, json!({})),
            t("term:cf-undo", "undo nova's cloudflare changes", "away from home", true, json!({"danger": true})),
            t("term:setup", "run setup again", "away from home", true, json!({})),
            t("term:update-check", "check for update", "maintenance", false, json!({})),
            t("term:update", "update nova", "maintenance", true, json!({"confirm": "Install the newest Nova release?"})),
            t("term:backup-now", "back up now", "maintenance", true, json!({})),
            t("term:backup-list", "list backups", "maintenance", false, json!({})),
            t("term:restart", "restart api", "maintenance", true, json!({"confirm": "Restart Nova's API? Phones reconnect by themselves."})),
            t("term:links", "linked servers", "more", false, json!({})),
            t("term:plugins", "plugins", "more", false, json!({})),
        ]);
        acts
    }
}

fn vstr(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

fn parse_time(s: &str) -> Option<f64> {
    if let Ok(t) = chrono::DateTime::parse_from_rfc3339(s) {
        return Some(t.timestamp_millis() as f64 / 1000.0);
    }
    for fmt in ["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S%.f", "%Y-%m-%d %H:%M:%S"] {
        if let Ok(n) = chrono::NaiveDateTime::parse_from_str(s, fmt) {
            // without a zone, Python reads it as local time
            use chrono::TimeZone;
            return chrono::Local.from_local_datetime(&n).single().map(|t| t.timestamp_millis() as f64 / 1000.0);
        }
    }
    None
}

impl App for Nova {
    fn ctx(&self) -> &Ctx {
        &self.ctx
    }
    fn id(&self) -> String {
        "nova".into()
    }
    fn name(&self) -> String {
        "Nova".into()
    }
    fn blurb(&self) -> String {
        "Secure remote for your home server".into()
    }
    fn glyph(&self) -> String {
        "NV".into()
    }
    fn color(&self) -> String {
        "#5ec4b6".into()
    }
    fn repo(&self) -> String {
        "dash1101/Nova".into()
    }
    fn homepage(&self) -> String {
        "https://github.com/dash1101/Nova".into()
    }
    fn icon(&self) -> String {
        "nova.svg".into()
    }
    fn open_label(&self) -> String {
        "nova web".into()
    }
    fn approvals_waiting(&self) -> (u64, u64) {
        self.health().filter(|h| !h["stale"].as_bool().unwrap_or(true))
            .map(|h| (h["waiting"]["browsers"].as_u64().unwrap_or(0), h["waiting"]["actions"].as_u64().unwrap_or(0)))
            .unwrap_or((0, 0))
    }
    /// "approve a browser" on the card (the button to press when something is waiting).
    fn card_actions(&self) -> Vec<Value> {
        let waiting = *self.waiting.lock().unwrap() > 0;
        self.actions().into_iter().filter(|a| a["id"].as_str().map(|i| i.ends_with(":approve")).unwrap_or(false))
            .map(|mut a| { a["primary"] = json!(waiting); a }).collect()
    }
    fn units(&self) -> Vec<(String, bool)> {
        UNITS.iter().map(|(u, _)| (u.to_string(), false)).collect()
    }
    fn forget_found(&self) {
        *self.found.lock().unwrap() = None;
    }

    fn hub_fields(&self) -> Vec<Value> {
        let url = self.hub().get("url").and_then(|u| u.as_str()).unwrap_or("").to_string();
        vec![with(field("url", "Nova web address", "text", json!(url), "hub"),
                  json!({"help": "Leave empty to find Nova on this computer. Example: https://myserver:8495"}))]
    }

    fn status(&self) -> Value {
        if Self::windows() {
            return self.win_status();
        }
        let r = &self.ctx.runner;
        let mut units = vec![];
        for (u, label) in UNITS {
            let mut st = systemd::show(r, u, false);
            st["label"] = json!(label);
            units.push(st);
        }
        for (u, label) in OPTIONAL_UNITS {
            let mut st = systemd::show(r, u, false);
            if st["state"].as_str().map(|s| s != "missing").unwrap_or(false) {
                st["label"] = json!(label);
                units.push(st);
            }
        }
        let installed = units.iter().any(|u| u["state"].as_str().map(|s| s != "missing").unwrap_or(false));
        let mut s = Map::new();
        let failed_jobs: Vec<&str> = JOBS.iter().filter(|(u, _)| systemd::show(r, u, false)["state"] == "failed").map(|(_, l)| *l).collect();
        let addr = self.address();
        let ping = addr.as_ref().and_then(|a| self.ping(a, &mut s));
        let mut f: Vec<Value> = vec![];
        if let Some(a) = &addr {
            f.push(json!(["web", a]));
        }
        s.insert("units".into(), json!(units));
        if !installed && ping.is_none() {
            s.insert("state".into(), json!("off"));
            s.insert("headline".into(), json!("Not installed on this computer"));
            if self.hub().get("url").and_then(|u| u.as_str()).unwrap_or("").is_empty() {
                s.insert("detail".into(), json!("Nova on another computer? Set its address in settings (connection)."));
            }
            s.insert("facts".into(), json!(f));
            return Value::Object(s);
        }
        let label_of = |u: &str| UNITS.iter().find(|x| x.0 == u).map(|x| x.1.to_string()).unwrap_or_else(|| u.to_string());
        let core_bad: Vec<String> = units.iter()
            .filter(|u| u["unit"] == "nova-api.service" || u["unit"] == "nova-helper.socket")
            .filter(|u| !matches!(u["state"].as_str(), Some("running") | Some("missing")))
            .map(|u| label_of(u["unit"].as_str().unwrap_or(""))).collect();
        let ping = ping.filter(|p| !p.is_null());
        if let Some(p) = ping.as_ref().filter(|p| p["ok"].as_bool().unwrap_or(false)) {
            s.insert("version".into(), json!(p["api"].as_str().unwrap_or("")));
            f.push(json!(["api level", format!("{} (oldest served {})", vstr(&p["api_level"]), vstr(&p["min_level"]))]));
        }
        if !failed_jobs.is_empty() {
            f.push(json!(["last run failed", failed_jobs.join(", "), "warn"]));
        }
        let hl = if installed { self.health() } else { None };
        if let Some(h) = &hl {
            f.extend(self.health_facts(h));
        }
        f.extend(self.remote_facts());
        let issues: Vec<String> = hl.as_ref().map(|h| h["issues"].as_array().cloned().unwrap_or_default().iter()
            .filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default();
        let stale = hl.as_ref().map(|h| h["stale"].as_bool().unwrap_or(true)).unwrap_or(false);
        let (browsers, actions) = hl.as_ref().filter(|_| !stale)
            .map(|h| (h["waiting"]["browsers"].as_u64().unwrap_or(0), h["waiting"]["actions"].as_u64().unwrap_or(0))).unwrap_or((0, 0));
        *self.waiting.lock().unwrap() = browsers + actions;
        let (state, headline) = if ping.is_none() {
            ("down", format!("Nova isn't answering{}", if core_bad.is_empty() { String::new() } else { format!(" ({} not running)", core_bad.join(", ")) }))
        } else if !core_bad.is_empty() {
            ("warn", format!("Answering, but {} isn't running", core_bad.join(", ")))
        } else if browsers + actions > 0 {
            ("warn", match (browsers, actions) {
                (1, 0) => "A browser is waiting for your approval: approve it with the code it shows".to_string(),
                (b, 0) => format!("{b} browsers are waiting for your approval"),
                (0, 1) => "A browser is waiting for you to approve what it asked: use the code it shows".to_string(),
                (b, a) => format!("{} things are waiting for your approval", b + a),
            })
        } else if !failed_jobs.is_empty() {
            ("warn", format!("Running · the last {} failed", failed_jobs[0]))
        } else if hl.is_some() && stale {
            ("warn", "Running, but its alerts have stopped checking the server".to_string())
        } else if !issues.is_empty() {
            let n = issues.len();
            ("warn", format!("Running · {n} need{} attention: {}", if n == 1 { "s" } else { "" }, issues[0]))
        } else if hl.is_some() {
            ("ok", "Running · all systems normal".to_string())
        } else {
            ("ok", "Running".to_string())
        };
        s.insert("state".into(), json!(state));
        s.insert("headline".into(), json!(headline));
        s.insert("facts".into(), json!(f));
        let v = s.get("version").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if let Some(rel) = release_info(self, &v, false) {
            s.insert("update".into(), rel);
        }
        Value::Object(s)
    }

    fn settings(&self) -> Vec<Value> {
        let how = if Self::windows() { "“nova server” in actions" } else { "“sudo nova setup”" };
        vec![
            json!({"title": "nova", "fields": [field("_note", "", "note", json!(format!(
                "Nova's own settings live in Nova web and the Nova app; risky changes are approved on your phone, so they aren't changed \
                 from here. Use “open nova web” or {how}.")), "app")]}),
            json!({"title": "connection", "fields": self.hub_fields()}),
        ]
    }

    fn actions(&self) -> Vec<Value> {
        if Self::windows() { self.win_actions() } else { self.linux_actions() }
    }

    fn term_command(&self, action: &str, args: &Map<String, Value>) -> Res<Option<Value>> {
        let key = action.strip_prefix("term:").unwrap_or("");
        if key == "approve" {
            let code = ask_value(self, action, args)?;
            return Ok(Some(json!({"argv": ["sudo", "nova", "approve", approval_code(&code)?], "title": "Nova · approve a browser"})));
        }
        if key == "remove" {
            let who = ask_value(self, action, args)?;
            return Ok(Some(json!({"argv": ["sudo", "nova", "remove", who], "title": "Nova · remove a device"})));
        }
        if key == "cf-token" {
            let tok = ask_value(self, action, args)?;
            if !Path::new("/usr/lib/nova-api/cfsync.py").exists() {
                return Err(bad("This Nova doesn't have Cloudflare auto-setup; update Nova first."));
            }
            let path = std::env::temp_dir().join(format!("orbit-cf-{}", crate::util::new_id()));
            #[cfg(unix)]
            {
                use std::io::Write;
                use std::os::unix::fs::OpenOptionsExt;
                let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&path)
                    .map_err(|e| bad(format!("couldn't save the token for a moment: {e}")))?;
                f.write_all(tok.as_bytes()).map_err(|e| bad(e.to_string()))?;
            }
            #[cfg(not(unix))]
            std::fs::write(&path, &tok).map_err(|e| bad(e.to_string()))?;
            let p = path.to_string_lossy().into_owned();
            return Ok(Some(json!({"argv": ["sudo", "/usr/bin/python3", "-c", CF_SAVE, p.clone()], "title": "Nova · Cloudflare token",
                                  "cleanup": [p], "mask": [tok],
                                  "shown": "sudo python3 (Nova's cfsync.save_token) — save the Cloudflare API token, root-only"})));
        }
        if let Some((_, argv)) = TERM.iter().find(|(k, _)| *k == key) {
            if key.starts_with("tailscale") && !crate::runner::Runner::available("tailscale") {
                return Err(bad("Tailscale isn't installed. Get it from tailscale.com/download, or run Nova's setup again and choose Tailscale."));
            }
            let label = self.actions().into_iter().find(|a| a["id"] == action).and_then(|a| a["label"].as_str().map(String::from)).unwrap_or(key.into());
            return Ok(Some(json!({"argv": argv, "title": format!("Nova · {label}")})));
        }
        Ok(None)
    }

    fn run_action(&self, action: &str, args: &Map<String, Value>) -> Res<Value> {
        if action == "gui" && !Self::windows() {
            if !Path::new(NOVA_GUI).exists() || !self.ctx.runner.launch(&[NOVA_GUI.to_string()]) {
                return Err(bad("Nova Server's window isn't installed (it comes with Nova 0.9.16 and newer)"));
            }
            return Ok(json!({"ok": true, "message": "opened Nova Server (it asks for your password)"}));
        }
        if action == "gui" {
            let lnk = Self::program_data().join("Microsoft").join("Windows").join("Start Menu").join("Programs").join("Nova Server.lnk");
            if !lnk.exists() || !self.ctx.runner.open_file(&lnk.to_string_lossy()) {
                return Err(bad("Nova Server isn't in the Start menu; reinstall Nova to bring it back"));
            }
            return Ok(json!({"ok": true, "message": "opened Nova Server"}));
        }
        if let Some(key) = action.strip_prefix("win:").filter(|_| Self::windows()) {
            let label = self.win_actions().into_iter().find(|a| a["id"] == action).and_then(|a| a["label"].as_str().map(String::from)).unwrap_or(key.into());
            return match key {
                "approve" => self.win_admin_window("Nova · approve a browser", &["approve".into(), approval_code(&ask_value(self, action, args)?)?]),
                "remove" => self.win_admin_window("Nova · remove a device", &["remove".into(), ask_value(self, action, args)?]),
                "journal" => {
                    let log = Self::program_data().join("Nova").join("log").join("api.log");
                    self.win_admin_window("Nova · api log", &["@script".into(),
                        format!("Get-Content -Tail 300 '{}'", log.to_string_lossy().replace('\'', "''"))])
                }
                k => match WIN_TERM.iter().find(|(w, _)| *w == k) {
                    Some((_, words)) => self.win_admin_window(&format!("Nova · {label}"), &words.iter().map(|s| s.to_string()).collect::<Vec<_>>()),
                    None => Err(bad("unknown action")),
                },
            };
        }
        if action == "open" {
            let addr = self.address().ok_or_else(|| bad("Nova isn't reachable; set its address in settings"))?;
            return open_url(self, &format!("{addr}/"));
        }
        if action == "releases" {
            return open_url(self, &format!("{}/releases", self.homepage()));
        }
        Err(bad("unknown action"))
    }

    fn logs(&self) -> String {
        if Self::windows() {
            return "(Nova's logs are in C:\\ProgramData\\Nova\\log, which only the administrator can read.\n \
                    Use “api log” under actions to open the newest lines in an administrator window.)".into();
        }
        let out = systemd::journal(&self.ctx.runner, "nova-api.service", false, 80);
        if out.trim().is_empty() || out.contains("No entries") {
            return "(Nothing here that your account may read. Nova runs as a system service, and its log is only readable by the \
                    administrator or members of the 'adm' or 'systemd-journal' groups.\n Use “api log” under actions to read it with sudo.)".into();
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_code_is_found_in_what_was_typed_or_pasted() {
        for typed in ["ZAG97V", "zag97v", " ZAG 97V ", "ZAG-97V", "sudo nova approve ZAG97V", "  sudo nova approve zag97v --role admin"] {
            assert_eq!(approval_code(typed).unwrap(), "ZAG97V", "{typed:?}");
        }
        for wrong in ["", "sudo nova approve", "ZA", "sudo nova approve ZAG97V; rm -rf /"] {
            assert!(approval_code(wrong).is_err(), "{wrong:?}");
        }
    }

    #[test]
    fn approvals_waiting_show_on_the_card() {
        let dir = std::env::temp_dir().join(format!("orbit-nova-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let ctx = Arc::new(Ctx { config: Arc::new(crate::config::Config::new(Some(dir.join("config.json")))),
                                 runner: Arc::new(crate::runner::Runner::new(true)), releases: Arc::new(net::Releases::new()) });
        let mut nova = Nova::new(ctx);
        nova.status_file = dir.join("status.json");
        let now = chrono::Utc::now().to_rfc3339();
        let write = |waiting: Value| std::fs::write(dir.join("status.json"),
            json!({"level": "ok", "headline": "all good", "updated": now, "active": [], "waiting": waiting}).to_string()).unwrap();
        write(json!({"browsers": 1, "actions": 0}));
        let h = nova.health().unwrap();
        assert_eq!(h["waiting"], json!({"browsers": 1, "actions": 0}));
        write(json!(null)); // a Nova that doesn't say
        assert_eq!(nova.health().unwrap()["waiting"], json!({"browsers": 0, "actions": 0}));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
