//! Orbit's catalog: packaging/catalog.json in Orbit's repository (tools/make-catalog.py), what can change about an app
//! without a new Orbit: its name, description, colour and icon. Read from GitHub (through api.github.com, at most every
//! 12 hours, with release checks on) and kept in Orbit's settings folder for when GitHub can't be reached; until there
//! is one, the names built into Orbit are used. It never changes how an app is installed or checked.
use crate::engine::Sources;
use base64::Engine;
use serde_json::{json, Map, Value};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, SystemTime};

pub const REPO: &str = "dash1101/orbit";
pub const PATH: &str = "packaging/catalog.json";
const FRESH: Duration = Duration::from_secs(12 * 3600);

pub struct Catalog {
    apps: Mutex<Map<String, Value>>, // id -> {"name", "blurb", "color", "icon" (a data: address)}
}

fn file() -> PathBuf {
    crate::config::config_dir().join("catalog.json")
}

impl Catalog {
    /// The catalog kept from last time (none: an empty one).
    pub fn load() -> Catalog {
        let c = Catalog { apps: Mutex::new(Map::new()) };
        if let Ok(text) = std::fs::read_to_string(file()) {
            c.take(&text);
        }
        c
    }

    /// Take what a catalog says, checked field by field. False when it isn't one (then nothing changes).
    pub fn take(&self, text: &str) -> bool {
        let Ok(root) = serde_json::from_str::<Value>(text) else { return false };
        let Some(list) = root["apps"].as_array() else { return false };
        let text_of = |v: &Value, max: usize| v.as_str().filter(|s| !s.is_empty() && s.chars().count() <= max).map(String::from);
        let mut apps = Map::new();
        for e in list {
            let Some(id) = e["id"].as_str().filter(|i| !i.is_empty() && i.len() <= 40 && i.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')) else {
                continue;
            };
            let mut out = Map::new();
            if let Some(n) = text_of(&e["name"], 60) {
                out.insert("name".into(), json!(n));
            }
            if let Some(b) = text_of(&e["blurb"], 300) {
                out.insert("blurb".into(), json!(b));
            }
            if let Some(c) = e["color"].as_str().filter(|c| c.len() == 7 && c.starts_with('#') && c[1..].chars().all(|x| x.is_ascii_hexdigit())) {
                out.insert("color".into(), json!(c.to_lowercase()));
            }
            if let Some(svg) = e["icon"].as_str().map(str::trim).filter(|s| s.starts_with("<svg") && s.len() <= 65536) {
                // shown as an image (an SVG there can't run anything)
                out.insert("icon".into(), json!(format!("data:image/svg+xml;base64,{}", base64::engine::general_purpose::STANDARD.encode(svg))));
            }
            apps.insert(id.into(), Value::Object(out));
        }
        if apps.is_empty() {
            return false;
        }
        *self.apps.lock().unwrap() = apps;
        true
    }

    /// From GitHub when the kept one is older than 12 hours (or there is none). True when a new one was taken.
    pub fn refresh(&self, sources: &Sources) -> bool {
        let age = std::fs::metadata(file()).and_then(|m| m.modified()).ok().and_then(|t| SystemTime::now().duration_since(t).ok());
        if age.map(|a| a < FRESH).unwrap_or(false) {
            return false;
        }
        let Ok(text) = sources.raw_file(REPO, PATH) else { return false };
        if !self.take(&text) {
            return false;
        }
        let _ = std::fs::create_dir_all(crate::config::config_dir());
        let _ = std::fs::write(file(), text);
        true
    }

    /// An app's description with what the catalog says about it (its "id" says which).
    pub fn apply(&self, d: &mut Value) {
        let Some(id) = d["id"].as_str().map(String::from) else { return };
        if let Some(Value::Object(e)) = self.apps.lock().unwrap().get(&id) {
            for (k, v) in e {
                d[k.as_str()] = v.clone();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_repositorys_catalog_names_every_app_and_bad_values_stay_out() {
        let c = Catalog { apps: Mutex::new(Map::new()) };
        let text = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../packaging/catalog.json")).unwrap();
        assert!(c.take(&text));
        for (id, name) in [("ytm-starcast", "YTM Starcast"), ("nova", "Nova"), ("orbit", "Orbit")] {
            let mut d = json!({"id": id, "name": "built in"});
            c.apply(&mut d);
            assert_eq!(d["name"], name);
            assert!(d["icon"].as_str().unwrap().starts_with("data:image/svg+xml;base64,"));
        }
        assert!(c.take(r#"{"apps": [{"id": "nova", "name": "Nova 2", "color": "red", "icon": "<script>"}]}"#));
        let mut d = json!({"id": "nova", "color": "#5ec4b6"});
        c.apply(&mut d);
        assert_eq!(d["name"], "Nova 2");
        assert_eq!(d["color"], "#5ec4b6"); // "red" isn't a colour here
        assert!(d.get("icon").is_none()); // nor is a script an icon
        assert!(!c.take("not json"));
        assert!(!c.take(r#"{"apps": []}"#));
    }
}
