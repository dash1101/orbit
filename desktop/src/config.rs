//! Orbit's own settings: ~/.config/orbit/config.json (or $XDG_CONFIG_HOME/orbit; %APPDATA%\Orbit on Windows).
//! The same file and format as the Python Orbit, so an update keeps everything.
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub fn defaults() -> Map<String, Value> {
    let v = json!({
        "theme": "tui",
        "refreshSec": 3,
        "checkUpdates": true,   // ask GitHub (at most every 12 hours) whether a newer release exists
        "autoUpdate": true,     // a newer Orbit is downloaded and checked by itself, and takes over at the next start
        "notifyApprovals": true, // a desktop notification when Nova has something waiting for approval (Linux)
        "port": 8790,           // where Orbit answers on this computer (127.0.0.1); --port overrides it
        "apps": {},             // per-app hub settings: {"<id>": {"enabled": true, ...}}
        "custom": [],           // apps added from the hub
        "order": [],            // app ids in the order they appear in the side bar
        "github": {},           // Orbit's own GitHub sign-in: {"token", "login", "since"} (readable by you only)
        "themes": [],           // custom themes
        "window": [],           // the window's last size, [width, height]
    });
    match v {
        Value::Object(m) => m,
        _ => unreachable!(),
    }
}

pub fn config_dir() -> PathBuf {
    if cfg!(windows) {
        let base = std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| home().join("AppData").join("Roaming"));
        return base.join("Orbit");
    }
    let base = std::env::var_os("XDG_CONFIG_HOME")
        .filter(|v| !v.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(".config"));
    base.join("orbit")
}

pub fn home() -> PathBuf {
    let var = if cfg!(windows) { "USERPROFILE" } else { "HOME" };
    std::env::var_os(var).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

fn same_kind(a: &Value, b: &Value) -> bool {
    matches!(
        (a, b),
        (Value::String(_), Value::String(_))
            | (Value::Number(_), Value::Number(_))
            | (Value::Bool(_), Value::Bool(_))
            | (Value::Object(_), Value::Object(_))
            | (Value::Array(_), Value::Array(_))
    )
}

pub struct Config {
    pub path: PathBuf,
    data: Mutex<Map<String, Value>>,
}

impl Config {
    pub fn new(path: Option<PathBuf>) -> Config {
        let path = path.unwrap_or_else(|| config_dir().join("config.json"));
        let mut d = load(&path);
        if migrate(&mut d) {
            save(&path, &d);
        }
        Config { path, data: Mutex::new(d) }
    }

    /// Everything, as one object.
    pub fn all(&self) -> Value {
        Value::Object(self.data.lock().unwrap().clone())
    }

    pub fn get(&self, key: &str) -> Value {
        self.data.lock().unwrap().get(key).cloned().unwrap_or(Value::Null)
    }

    pub fn update(&self, patch: Map<String, Value>) -> Value {
        let mut d = self.data.lock().unwrap();
        for (k, v) in patch {
            d.insert(k, v);
        }
        save(&self.path, &d);
        Value::Object(d.clone())
    }

    pub fn app(&self, app_id: &str) -> Map<String, Value> {
        let d = self.data.lock().unwrap();
        d.get("apps").and_then(|a| a.get(app_id)).and_then(|v| v.as_object()).cloned().unwrap_or_default()
    }

    pub fn update_app(&self, app_id: &str, patch: Map<String, Value>) -> Map<String, Value> {
        let mut d = self.data.lock().unwrap();
        let apps = d.entry("apps").or_insert_with(|| json!({}));
        if !apps.is_object() {
            *apps = json!({});
        }
        let cur = apps.as_object_mut().unwrap().entry(app_id).or_insert_with(|| json!({}));
        if !cur.is_object() {
            *cur = json!({});
        }
        let m = cur.as_object_mut().unwrap();
        for (k, v) in patch {
            m.insert(k, v);
        }
        let out = m.clone();
        save(&self.path, &d);
        out
    }
}

// Built-in apps that changed: Pocketfader is now YTM Starcast, and YTM Beacon is part of YTM Starcast.
const RENAMED: [(&str, &str); 1] = [("pocketfader", "ytm-starcast")]; // old id -> new id (its address, token and on/off carry over)
const RETIRED: [&str; 1] = ["ytm-beacon"]; // folded into another app: its tile goes

/// Bring settings saved by an older Orbit up to date. Returns true when something changed.
pub fn migrate(d: &mut Map<String, Value>) -> bool {
    let mut changed = false;
    let mut apps = d.get("apps").and_then(|a| a.as_object()).cloned().unwrap_or_default();
    let mut order: Vec<Value> = d.get("order").and_then(|o| o.as_array()).cloned().unwrap_or_default();
    for (old, new) in RENAMED {
        if let Some(was) = apps.remove(old) {
            let mut merged = was.as_object().cloned().unwrap_or_default();
            if let Some(Value::Object(own)) = apps.get(new) {
                merged.extend(own.clone()); // the new id's own values win
            }
            apps.insert(new.into(), Value::Object(merged));
            changed = true;
        }
        if let Some(i) = order.iter().position(|x| x == old) {
            order.retain(|x| x != old);
            if !order.iter().any(|x| x == new) {
                order.insert(i.min(order.len()), json!(new)); // the tile keeps its place
            }
            changed = true;
        }
    }
    for gone in RETIRED {
        if apps.remove(gone).is_some() {
            changed = true;
        }
        if order.iter().any(|x| x == gone) {
            order.retain(|x| x != gone);
            changed = true;
        }
    }
    if changed {
        d.insert("apps".into(), Value::Object(apps));
        d.insert("order".into(), Value::Array(order));
    }
    changed
}

fn load(path: &Path) -> Map<String, Value> {
    let mut data = defaults();
    if let Ok(text) = std::fs::read_to_string(path) {
        if let Ok(Value::Object(raw)) = serde_json::from_str::<Value>(&text) {
            for (k, v) in raw {
                if let Some(d) = data.get(&k) {
                    if same_kind(d, &v) {
                        data.insert(k, v);
                    }
                }
            }
        }
    }
    data
}

fn save(path: &Path, data: &Map<String, Value>) {
    let _ = (|| -> std::io::Result<()> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let tmp = path.with_extension("tmp");
        std::fs::write(&tmp, serde_json::to_string_pretty(data).unwrap_or_default())?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600))?;
        }
        std::fs::rename(&tmp, path) // atomic: a crash can't leave half a file
    })();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn settings_from_before_the_move_carry_over() {
        let mut d = defaults();
        d.insert("apps".into(), json!({"pocketfader": {"url": "http://127.0.0.1:8777", "token": "t"}, "ytm-beacon": {"enabled": false},
                                       "ytm-starcast": {"enabled": true}}));
        d.insert("order".into(), json!(["nova", "pocketfader", "ytm-beacon"]));
        assert!(migrate(&mut d));
        assert_eq!(d["apps"], json!({"ytm-starcast": {"url": "http://127.0.0.1:8777", "token": "t", "enabled": true}}));
        assert_eq!(d["order"], json!(["nova", "ytm-starcast"]));
        assert!(!migrate(&mut d));
    }
}
