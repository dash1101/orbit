//! RPCortex Studio on Orbit's side: whether it's installed and a board is plugged in, opening it in a terminal (in
//! Orbit's theme, when that is one of the themes they share), and its one setting, the theme, read from and written to
//! its own config.json ({"theme": "<name>"}; the file holds nothing else). It has no service, port or web page.
use super::{field, release_info, with, App, Ctx};
use crate::recipes::rpcortex_studio::version_said;
use crate::util::{bad, Res};
use serde_json::{json, Map, Value};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

/// Its themes: the same names as Orbit's built-in ones.
pub const THEMES: [(&str, &str); 9] = [("tui", "dusk"), ("phosphor", "phosphor"), ("amber", "amber"), ("ember", "ember"), ("frost", "frost"),
                                        ("lilac", "lilac"), ("mono", "mono"), ("ocean", "ocean"), ("paper", "paper")];

fn env_dir(var: &str) -> Option<PathBuf> {
    std::env::var_os(var).filter(|v| !v.is_empty()).map(PathBuf::from)
}

/// Where Orbit installs it (see recipes/rpcortex_studio.rs).
pub fn exe() -> PathBuf {
    let home = crate::config::home();
    if cfg!(windows) {
        env_dir("LOCALAPPDATA").unwrap_or_else(|| home.join("AppData").join("Local")).join("Programs").join("RPCortex Studio").join("rpcortex-studio.exe")
    } else if cfg!(target_os = "macos") {
        home.join("Library/Application Support/RPCortex Studio/rpcortex-studio")
    } else {
        env_dir("XDG_DATA_HOME").unwrap_or_else(|| home.join(".local").join("share")).join("rpcortex-studio").join("rpcortex-studio")
    }
}

pub fn installed() -> bool {
    exe().is_file()
}

/// Its settings file (only the theme).
pub fn config_path() -> PathBuf {
    let home = crate::config::home();
    let base = if cfg!(windows) {
        env_dir("APPDATA").unwrap_or_else(|| home.join("AppData").join("Roaming"))
    } else if cfg!(target_os = "macos") {
        home.join("Library/Application Support")
    } else {
        env_dir("XDG_CONFIG_HOME").unwrap_or_else(|| home.join(".config"))
    };
    base.join("rpc-tui").join("config.json")
}

/// The board's serial ports present now (Linux: /dev/ttyACM*; macOS: /dev/cu.usbmodem*). Windows: not looked at.
fn boards() -> Vec<String> {
    let prefix = if cfg!(target_os = "macos") { "cu.usbmodem" } else if cfg!(target_os = "linux") { "ttyACM" } else { return vec![] };
    let mut out: Vec<String> = std::fs::read_dir("/dev").map(|r| r.flatten().map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.starts_with(prefix)).map(|n| format!("/dev/{n}")).collect()).unwrap_or_default();
    out.sort();
    out
}

pub struct RpcortexStudio {
    ctx: Arc<Ctx>,
    version: Mutex<String>,
}

impl RpcortexStudio {
    pub fn new(ctx: Arc<Ctx>) -> RpcortexStudio {
        RpcortexStudio { ctx, version: Mutex::new(String::new()) }
    }

    fn theme(&self) -> String {
        std::fs::read_to_string(config_path()).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| v["theme"].as_str().map(String::from)).unwrap_or_default()
    }

    /// The theme to open it in: Orbit's own, when "follow Orbit's theme" is on and Orbit's is one of its themes.
    fn launch_theme(&self) -> Option<String> {
        if !self.hub().get("followTheme").and_then(|v| v.as_bool()).unwrap_or(true) {
            return None;
        }
        let t = self.ctx.config.get("theme").as_str().unwrap_or("").to_string();
        THEMES.iter().any(|(id, _)| *id == t).then_some(t)
    }

    fn version(&self) -> String {
        let mut v = self.version.lock().unwrap();
        if v.is_empty() {
            let (rc, out) = crate::runner::run_capture(&[exe().to_string_lossy().into_owned(), "--version".into()],
                                                       std::time::Duration::from_secs(5), true);
            if rc == 0 {
                *v = version_said(&out);
            }
        }
        v.clone()
    }
}

impl App for RpcortexStudio {
    fn ctx(&self) -> &Ctx {
        &self.ctx
    }
    fn id(&self) -> String {
        "rpcortex-studio".into()
    }
    fn name(&self) -> String {
        "RPCortex Studio".into()
    }
    fn blurb(&self) -> String {
        "Control an RPCortex device from your computer, over USB serial".into()
    }
    fn glyph(&self) -> String {
        "RC".into()
    }
    fn color(&self) -> String {
        "#2dd4be".into()
    }
    fn repo(&self) -> String {
        "dash1101/rpcortex-studio".into()
    }
    fn homepage(&self) -> String {
        "https://github.com/dash1101/rpcortex-studio".into()
    }
    fn icon(&self) -> String {
        "rpcortex-studio.svg".into()
    }
    fn open_label(&self) -> String {
        "open".into()
    }
    fn own_settings(&self) -> Option<Value> {
        Some(json!({"id": "open", "label": "open RPCortex Studio", "help": "Its theme can be changed inside it too."}))
    }
    fn forget_found(&self) {
        self.version.lock().unwrap().clear(); // an update: ask the new program
    }

    fn status(&self) -> Value {
        let v = self.version();
        let found = boards();
        let mut facts = vec![json!(["program", exe().display().to_string()])];
        let (state, headline) = if !cfg!(windows) && found.is_empty() {
            ("ok", "Ready · no board plugged in".to_string())
        } else if cfg!(windows) {
            ("ok", "Ready · open it with your board plugged in".to_string())
        } else {
            facts.push(json!(["board", found.join(", ")]));
            ("ok", if found.len() == 1 { "Ready · a board is plugged in".to_string() } else { format!("Ready · {} boards plugged in", found.len()) })
        };
        let t = self.theme();
        facts.push(json!(["theme", if t.is_empty() { "its default".to_string() } else { t }]));
        let mut s = json!({"state": state, "headline": headline, "version": v, "facts": facts});
        if let Some(rel) = release_info(self, &v, false) {
            s["update"] = rel;
        }
        s
    }

    fn settings(&self) -> Vec<Value> {
        let cur = self.theme();
        let options: Vec<Value> = THEMES.iter().map(|(id, label)| json!([id, label])).collect();
        let follow = self.hub().get("followTheme").and_then(|v| v.as_bool()).unwrap_or(true);
        vec![json!({"title": "look", "fields": [
            with(field("followTheme", "Open it in Orbit's theme", "bool", json!(follow), "hub"),
                 json!({"help": "When Orbit's theme is one RPCortex Studio has too (all the built-in ones), it opens in it."})),
            with(field("theme", "Its own theme", "choice", json!(if cur.is_empty() { "tui".to_string() } else { cur }), "app"),
                 json!({"options": options, "help": "Used when it isn't opened in Orbit's theme. Kept in its own settings file."})),
        ]})]
    }

    fn apply_app_settings(&self, patch: Map<String, Value>) -> Res<()> {
        let Some(t) = patch.get("theme").and_then(|v| v.as_str()) else { return Ok(()) };
        if self.ctx.dry_run() {
            return Ok(());
        }
        let path = config_path();
        let mut doc = std::fs::read_to_string(&path).ok().and_then(|x| serde_json::from_str::<Value>(&x).ok())
            .filter(|v| v.is_object()).unwrap_or_else(|| json!({}));
        doc["theme"] = json!(t);
        if let Some(d) = path.parent() {
            std::fs::create_dir_all(d).map_err(|e| bad(format!("Couldn't write {}: {e}", d.display())))?;
        }
        // written whole and then put in place, as RPCortex Studio writes it itself
        let tmp = path.with_extension("json.orbit-new");
        std::fs::write(&tmp, serde_json::to_string_pretty(&doc).unwrap_or_default()).and_then(|_| std::fs::rename(&tmp, &path))
            .map_err(|e| bad(format!("Couldn't write {}: {e}", path.display())))
    }

    fn actions(&self) -> Vec<Value> {
        vec![
            json!({"id": "open", "label": "open RPCortex Studio", "group": "open", "primary": true}),
            json!({"id": "releases", "label": "releases", "group": "open"}),
        ]
    }

    fn run_action(&self, action: &str, _args: &Map<String, Value>) -> Res<Value> {
        match action {
            "open" => {
                let mut argv = vec![exe().to_string_lossy().into_owned()];
                if let Some(t) = self.launch_theme() {
                    argv.extend(["tui".into(), "--theme".into(), t]);
                }
                if self.ctx.dry_run() {
                    return Ok(json!({"ok": true, "message": format!("dry run: would open a terminal: {}", argv.join(" "))}));
                }
                super::open_terminal(self, &argv, Some("RPCortex Studio"))
            }
            "releases" => super::open_url(self, &format!("{}/releases", self.homepage())),
            _ => Err(bad("unknown action")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn its_themes_are_orbits_built_in_ones() {
        let ids: Vec<&str> = THEMES.iter().map(|(id, _)| *id).collect();
        for t in ["tui", "phosphor", "amber", "ember", "frost", "lilac", "mono", "ocean", "paper"] {
            assert!(ids.contains(&t), "{t}");
        }
        assert!(config_path().ends_with("rpc-tui/config.json") || config_path().ends_with("rpc-tui\\config.json"));
    }
}
