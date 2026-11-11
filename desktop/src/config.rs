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
        let data = Mutex::new(load(&path));
        Config { path, data }
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
