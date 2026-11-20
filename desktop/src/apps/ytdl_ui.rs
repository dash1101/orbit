//! YTDL-UI on Orbit's side: whether its page answers, its service (Linux), and the settings Orbit can read from its
//! folder: config.ini, with the changes made in its admin console (data/settings.json) on top, as YTDL-UI itself reads
//! them. Orbit only shows them: YTDL-UI's admin console (behind its own password and two-step sign-in) is where they
//! change, so "YTDL-UI's own settings" opens its page.
use super::{field, release_info, run_unit_action, unit_actions, unit_states, with, App, Ctx};
use crate::net;
use crate::util::{bad, Res};
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

const UNIT: &str = "ytdl-ui.service";

fn env_dir(var: &str) -> Option<PathBuf> {
    std::env::var_os(var).filter(|v| !v.is_empty()).map(PathBuf::from)
}

/// Its folder, where Orbit installs it (see recipes/ytdl_ui.rs): the program, config.ini and data/.
pub fn dir() -> PathBuf {
    let home = crate::config::home();
    if cfg!(windows) {
        env_dir("LOCALAPPDATA").unwrap_or_else(|| home.join("AppData").join("Local")).join("Programs").join("YTDL-UI")
    } else if cfg!(target_os = "macos") {
        home.join("Library").join("Application Support").join("YTDL-UI")
    } else {
        env_dir("XDG_DATA_HOME").unwrap_or_else(|| home.join(".local").join("share")).join("ytdl-ui")
    }
}

pub fn exe() -> PathBuf {
    dir().join(if cfg!(windows) { "ytdl-ui.exe" } else { "ytdl-ui" })
}

pub fn installed() -> bool {
    exe().exists()
}

/// config.ini's values by name (the section doesn't matter: YTDL-UI's names are unique), commented-out lines skipped.
pub fn read_ini(text: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    for line in text.lines() {
        let t = line.trim();
        if t.is_empty() || t.starts_with('#') || t.starts_with(';') || t.starts_with('[') {
            continue;
        }
        if let Some((k, v)) = t.split_once('=') {
            out.insert(k.trim().to_lowercase(), v.trim().to_string());
        }
    }
    out
}

/// What YTDL-UI runs with: its defaults, config.ini, then the admin console's data/settings.json.
pub fn effective(ini: &str, admin: &Value) -> HashMap<String, String> {
    let mut v: HashMap<String, String> = [("host", "127.0.0.1"), ("port", "5000"), ("processing_mode", "choice"),
        ("max_concurrent_jobs", "2"), ("job_ttl_minutes", "30"), ("allowed_sites", ""), ("password", ""),
        ("bandwidth_limit", "10"), ("update_check", "true"), ("data_dir", "data")]
        .iter().map(|(k, d)| (k.to_string(), d.to_string())).collect();
    v.extend(read_ini(ini));
    if let Some(m) = admin.as_object() {
        for (k, x) in m {
            let s = match x {
                Value::String(s) => s.clone(),
                Value::Array(a) => a.iter().filter_map(|i| i.as_str()).collect::<Vec<_>>().join(", "),
                Value::Null => continue,
                other => other.to_string(),
            };
            v.insert(k.to_lowercase(), s);
        }
    }
    v
}

pub struct YtdlUi {
    ctx: Arc<Ctx>,
    version: Mutex<String>, // what its page said last
}

impl YtdlUi {
    pub fn new(ctx: Arc<Ctx>) -> YtdlUi {
        YtdlUi { ctx, version: Mutex::new(String::new()) }
    }

    fn values(&self) -> HashMap<String, String> {
        let ini = std::fs::read_to_string(dir().join("config.ini")).unwrap_or_default();
        let first = effective(&ini, &Value::Null);
        let data = PathBuf::from(first.get("data_dir").cloned().unwrap_or_else(|| "data".into()));
        let data = if data.is_absolute() { data } else { dir().join(data) };
        let admin = std::fs::read_to_string(data.join("settings.json")).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(Value::Null);
        effective(&ini, &admin)
    }

    fn port(&self) -> u16 {
        self.values().get("port").and_then(|p| p.parse().ok()).unwrap_or(5000)
    }

    fn url(&self) -> String {
        format!("http://localhost:{}/", self.port())
    }

    /// Its version from the marker Orbit writes at install (YTDL-UI updating itself changes what its page says).
    fn installed_version(&self) -> String {
        std::fs::read_to_string(dir().join("orbit-install.json")).ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| v["version"].as_str().map(String::from)).unwrap_or_default()
    }
}

impl App for YtdlUi {
    fn ctx(&self) -> &Ctx {
        &self.ctx
    }
    fn id(&self) -> String {
        "ytdl-ui".into()
    }
    fn name(&self) -> String {
        "YTDL-UI".into()
    }
    fn blurb(&self) -> String {
        "Download video and audio from YouTube and 1,800+ other sites, in your browser".into()
    }
    fn glyph(&self) -> String {
        "YT".into()
    }
    fn color(&self) -> String {
        "#22d3ee".into()
    }
    fn repo(&self) -> String {
        "JustAyden/YTDL-UI".into()
    }
    fn homepage(&self) -> String {
        "https://github.com/JustAyden/YTDL-UI".into()
    }
    fn icon(&self) -> String {
        "ytdl-ui.svg".into()
    }
    fn units(&self) -> Vec<(String, bool)> {
        if cfg!(target_os = "linux") { vec![(UNIT.into(), true)] } else { vec![] }
    }
    fn open_label(&self) -> String {
        "open".into()
    }
    fn own_settings(&self) -> Option<Value> {
        Some(json!({"id": "open", "label": "YTDL-UI's own settings", "help": "In its page: ⚙ Settings, then Admin console."}))
    }

    fn status(&self) -> Value {
        let port = self.port();
        let health = net::get_json(&format!("http://127.0.0.1:{port}/api/health"), &net::Req { timeout: 2.0, ..Default::default() }).ok();
        let up = health.as_ref().map(|h| h["ok"].as_bool().unwrap_or(false)).unwrap_or(false);
        let mut v = health.as_ref().and_then(|h| h["version"].as_str()).map(String::from).unwrap_or_default();
        if v.is_empty() {
            v = self.installed_version();
        }
        *self.version.lock().unwrap() = v.clone();
        let units = unit_states(self);
        let failed = units.first().map(|u| u["state"] == "failed").unwrap_or(false);
        let (state, headline) = if up {
            ("ok", format!("Answering at {}", self.url()))
        } else if failed {
            ("down", "It stopped with an error: see the log".to_string())
        } else {
            ("off", "Not running".to_string())
        };
        let mut s = json!({"state": state, "headline": headline, "version": v, "units": units, "running": up,
                           "facts": [["folder", dir().display().to_string()]]});
        if let Some(h) = health.filter(|_| up) {
            s["facts"].as_array_mut().unwrap().push(json!(["yt-dlp", if h["ytdlp"].as_bool().unwrap_or(false) { "ready" } else { "missing" }]));
        }
        if let Some(rel) = release_info(self, &v, false) {
            s["update"] = rel;
        }
        s
    }

    fn settings(&self) -> Vec<Value> {
        let v = self.values();
        let get = |k: &str| v.get(k).cloned().unwrap_or_default();
        let ro = |key: &str, label: &str, value: String| with(field(key, label, "text", json!(value), "app"), json!({"readonly": true}));
        let on_off = |k: &str| if matches!(get(k).to_lowercase().as_str(), "true" | "1" | "yes" | "on") { "on" } else { "off" }.to_string();
        let mode = match get("processing_mode").as_str() {
            "server" => "on this computer".to_string(),
            "browser" => "in the visitor's browser".to_string(),
            "choice" => "the visitor chooses".to_string(),
            m => m.to_string(),
        };
        let mb = get("bandwidth_limit");
        vec![
            json!({"title": "server", "fields": [
                ro("address", "Address", format!("http://{}:{}/", if get("host") == "0.0.0.0" { "every network".to_string() } else { get("host") }, get("port"))),
                ro("password", "Password for visitors", if get("password").is_empty() { "off".into() } else { "on".into() }),
                ro("sites", "Sites allowed", if get("allowed_sites").is_empty() { "all that yt-dlp supports".into() } else { get("allowed_sites") }),
            ]}),
            json!({"title": "downloads", "fields": [
                ro("mode", "Where downloads are processed", mode),
                ro("jobs", "Downloads at the same time", get("max_concurrent_jobs")),
                ro("ttl", "Finished files kept (minutes)", get("job_ttl_minutes")),
                ro("bandwidth", "Speed limit (MB/s)", if mb == "0" { "none".into() } else { mb }),
                ro("updates", "Checks for updates", on_off("update_check")),
            ]}),
            json!({"title": "more", "fields": [field("more", "more", "note", json!(
                "These come from its config.ini and its admin console. They change in YTDL-UI's own settings: “YTDL-UI's own settings”, then ⚙ Settings → Admin console."), "app")]}),
        ]
    }

    fn actions(&self) -> Vec<Value> {
        let mut acts = vec![json!({"id": "open", "label": "open", "group": "open", "primary": true, "ext": true})];
        acts.extend(unit_actions(self));
        acts
    }

    fn run_action(&self, action: &str, _args: &Map<String, Value>) -> Res<Value> {
        match action {
            "open" => super::open_url(self, &self.url()),
            a if a.starts_with("svc:") && !self.units().is_empty() => run_unit_action(self, a),
            _ => Err(bad("unknown action")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_its_settings_like_it_does() {
        let ini = "[server]\n# port = 9999\nport = 5055\n[security]\npassword = secret\n[limits]\n; x\nmax_concurrent_jobs=4\n";
        let v = effective(ini, &json!({"max_concurrent_jobs": 6, "allowed_sites": ["youtube.com", "youtu.be"], "x": null}));
        assert_eq!(v["port"], "5055");
        assert_eq!(v["password"], "secret");
        assert_eq!(v["max_concurrent_jobs"], "6"); // the admin console wins over config.ini
        assert_eq!(v["allowed_sites"], "youtube.com, youtu.be");
        assert_eq!(v["host"], "127.0.0.1");
        assert!(!v.contains_key("x"));
    }
}
