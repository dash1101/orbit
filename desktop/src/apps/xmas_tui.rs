//! xmas-tui on Orbit's side: whether the wallpaper runs, its service (Linux), and its general settings, read from and
//! written to its own config.toml. xmas-tui watches that file and applies changes at once, and writes it itself from its
//! own settings panel, so Orbit changes only the keys it shows, in place: comments and every other key stay as they are.
//! Its full settings stay in its own panel (`xmas-tui --settings-window`).
use super::{field, run_unit_action, unit_actions, unit_states, with, App, Ctx};
use crate::util::{bad, Res};
use serde_json::{json, Map, Value};
use std::path::PathBuf;
use std::sync::Arc;
use toml_edit::{DocumentMut, Item, Table};

const UNIT: &str = "xmas-tui.service";

fn home() -> PathBuf {
    crate::config::home()
}

fn env_dir(var: &str) -> Option<PathBuf> {
    std::env::var_os(var).filter(|v| !v.is_empty()).map(PathBuf::from)
}

/// Where its installers put the program (Linux: ~/.local/share/xmas-tui; Windows: %LOCALAPPDATA%\Programs\xmas-tui).
pub fn exe() -> PathBuf {
    if cfg!(windows) {
        env_dir("LOCALAPPDATA").unwrap_or_else(|| home().join("AppData").join("Local")).join("Programs").join("xmas-tui").join("xmas-tui.exe")
    } else {
        env_dir("XDG_DATA_HOME").unwrap_or_else(|| home().join(".local").join("share")).join("xmas-tui").join("bin").join("xmas-tui")
    }
}

pub fn installed() -> bool {
    !cfg!(target_os = "macos") && exe().exists()
}

/// Its settings file (Linux: $XDG_CONFIG_HOME/xmas-tui/config.toml; Windows: %APPDATA%\xmas-tui\config.toml).
pub fn config_path() -> PathBuf {
    let base = if cfg!(windows) {
        env_dir("APPDATA").unwrap_or_else(|| home().join("AppData").join("Roaming"))
    } else {
        env_dir("XDG_CONFIG_HOME").unwrap_or_else(|| home().join(".config"))
    };
    base.join("xmas-tui").join("config.toml")
}

/// One setting Orbit shows: (section ("" = top level), key, kind, default).
/// Kinds: bool, int, float (shown as a choice of steps), pct (a 0–1 number shown as 0–100 %), text, and a choice list.
#[derive(Clone, Copy)]
enum Kind {
    Bool(bool),
    Int(i64, i64, i64),           // default, min, max
    Float(f64, &'static [(f64, &'static str)]),
    Pct(f64),
    Text(&'static str, usize),    // default, longest
    Choice(&'static str, &'static [(&'static str, &'static str)]),
}

struct Setting {
    section: &'static str,
    key: &'static str,
    label: &'static str,
    help: &'static str,
    kind: Kind,
}

const AMOUNT: &[(f64, &str)] = &[(0.0, "none"), (0.5, "a little"), (1.0, "normal"), (1.5, "more"), (2.0, "a lot")];
const SIZE: &[(f64, &str)] = &[(0.75, "75 %"), (1.0, "100 %"), (1.25, "125 %"), (1.5, "150 %"), (2.0, "200 %")];

const SETTINGS: &[(&str, &[Setting])] = &[
    ("wallpaper", &[
        Setting { section: "", key: "fps", label: "Frames per second", help: "Lower uses less power.", kind: Kind::Int(30, 5, 60) },
        Setting { section: "", key: "scale", label: "Size of everything", help: "", kind: Kind::Float(1.0, SIZE) },
        Setting { section: "", key: "zoom", label: "Zoom", help: "", kind: Kind::Float(1.0, SIZE) },
    ]),
    ("village", &[
        Setting { section: "scene", key: "name", label: "Your villager's name", help: "", kind: Kind::Text("", 24) },
        Setting { section: "scene", key: "time", label: "Time of day", help: "Auto follows your clock.", kind: Kind::Choice("auto", &[
            ("auto", "auto"), ("night", "night"), ("dawn", "dawn"), ("morning", "morning"), ("noon", "noon"), ("afternoon", "afternoon"), ("dusk", "dusk")]) },
        Setting { section: "scene", key: "snow", label: "Snow", help: "", kind: Kind::Float(1.0, AMOUNT) },
        Setting { section: "scene", key: "wind", label: "Wind", help: "", kind: Kind::Float(1.0, AMOUNT) },
        Setting { section: "scene", key: "critters", label: "Critters", help: "", kind: Kind::Float(1.0, AMOUNT) },
        Setting { section: "scene", key: "most_at_once", label: "Most critters at once", help: "", kind: Kind::Int(24, 0, 200) },
        Setting { section: "scene", key: "sleigh_minutes", label: "Sleigh every (minutes)", help: "", kind: Kind::Int(4, 1, 120) },
        Setting { section: "scene", key: "player", label: "Walk around as your villager", help: "", kind: Kind::Bool(true) },
        Setting { section: "scene", key: "autopilot", label: "Your villager wanders by itself", help: "", kind: Kind::Bool(false) },
    ]),
    ("clock", &[
        Setting { section: "clock", key: "enabled", label: "Show the clock", help: "", kind: Kind::Bool(true) },
        Setting { section: "clock", key: "hour24", label: "24-hour clock", help: "", kind: Kind::Bool(false) },
        Setting { section: "clock", key: "seconds", label: "Show seconds", help: "", kind: Kind::Bool(false) },
        Setting { section: "clock", key: "countdown", label: "Countdown to Christmas", help: "", kind: Kind::Bool(true) },
    ]),
    ("widgets", &[
        Setting { section: "widgets", key: "system", label: "System (CPU, memory)", help: "", kind: Kind::Bool(true) },
        Setting { section: "widgets", key: "music", label: "What's playing", help: "", kind: Kind::Bool(true) },
        Setting { section: "widgets", key: "weather", label: "Weather", help: "", kind: Kind::Bool(true) },
        Setting { section: "widgets", key: "map", label: "Map", help: "", kind: Kind::Bool(true) },
        Setting { section: "widgets", key: "visualizer", label: "Music visualizer", help: "", kind: Kind::Bool(false) },
    ]),
    ("weather", &[
        Setting { section: "weather", key: "city", label: "City", help: "Empty: found from your connection.", kind: Kind::Text("", 80) },
        Setting { section: "weather", key: "units", label: "Units", help: "", kind: Kind::Choice("fahrenheit", &[("fahrenheit", "°F"), ("celsius", "°C")]) },
    ]),
    ("sound", &[
        Setting { section: "sound", key: "enabled", label: "Music and sounds", help: "", kind: Kind::Bool(true) },
        Setting { section: "sound", key: "master", label: "Volume (%)", help: "", kind: Kind::Pct(0.5) },
    ]),
];

fn all() -> impl Iterator<Item = &'static Setting> {
    SETTINGS.iter().flat_map(|(_, s)| s.iter())
}

/// The field key Orbit uses for a setting ("scene.snow", "fps").
fn key_of(s: &Setting) -> String {
    if s.section.is_empty() { s.key.to_string() } else { format!("{}.{}", s.section, s.key) }
}

fn get<'a>(doc: &'a DocumentMut, s: &Setting) -> Option<&'a Item> {
    if s.section.is_empty() { doc.get(s.key) } else { doc.get(s.section)?.get(s.key) }
}

fn num(i: Option<&Item>) -> Option<f64> {
    let v = i?.as_value()?;
    v.as_float().or_else(|| v.as_integer().map(|n| n as f64))
}

/// A setting's value as Orbit's field shows it, from the file (its default when the file doesn't say).
fn shown(doc: &DocumentMut, s: &Setting) -> Value {
    let i = get(doc, s);
    match s.kind {
        Kind::Bool(d) => json!(i.and_then(|i| i.as_bool()).unwrap_or(d)),
        Kind::Int(d, lo, hi) => json!(num(i).map(|x| x.round() as i64).unwrap_or(d).clamp(lo, hi)),
        Kind::Float(d, _) => json!(num(i).unwrap_or(d).to_string()),
        Kind::Pct(d) => json!(((num(i).unwrap_or(d)) * 100.0).round().clamp(0.0, 100.0) as i64),
        Kind::Text(d, _) => json!(i.and_then(|i| i.as_str()).unwrap_or(d)),
        Kind::Choice(d, _) => json!(i.and_then(|i| i.as_str()).unwrap_or(d)),
    }
}

fn field_for(doc: &DocumentMut, s: &Setting) -> Value {
    let k = key_of(s);
    let v = shown(doc, s);
    let f = match s.kind {
        Kind::Bool(_) => field(&k, s.label, "bool", v, "app"),
        Kind::Int(_, lo, hi) => with(field(&k, s.label, "int", v, "app"), json!({"min": lo, "max": hi})),
        Kind::Pct(_) => with(field(&k, s.label, "int", v, "app"), json!({"min": 0, "max": 100})),
        Kind::Text(_, max) => with(field(&k, s.label, "text", v, "app"), json!({"maxLen": max})),
        Kind::Float(_, steps) => {
            let cur = v.as_str().unwrap_or("").to_string();
            let mut opts: Vec<Value> = steps.iter().map(|(x, l)| json!([x.to_string(), l])).collect();
            if !steps.iter().any(|(x, _)| x.to_string() == cur) {
                opts.push(json!([cur.clone(), cur])); // a value set in xmas-tui's own panel stays choosable
            }
            with(field(&k, s.label, "choice", v, "app"), json!({"options": opts}))
        }
        Kind::Choice(_, list) => {
            let cur = v.as_str().unwrap_or("").to_string();
            let mut opts: Vec<Value> = list.iter().map(|(x, l)| json!([x, l])).collect();
            if !list.iter().any(|(x, _)| *x == cur) {
                opts.push(json!([cur.clone(), cur]));
            }
            with(field(&k, s.label, "choice", v, "app"), json!({"options": opts}))
        }
    };
    if s.help.is_empty() { f } else { with(f, json!({"help": s.help})) }
}

/// Change some settings in xmas-tui's file, keeping everything else in it as it is.
pub fn edit(text: &str, patch: &Map<String, Value>) -> Res<String> {
    let mut doc: DocumentMut = text.parse().map_err(|e| bad(format!("xmas-tui's config.toml can't be read: {e}")))?;
    for (k, v) in patch {
        let s = all().find(|s| key_of(s) == *k).ok_or_else(|| bad(format!("unknown setting: {k}")))?;
        let value: toml_edit::Value = match s.kind {
            Kind::Bool(_) => v.as_bool().ok_or_else(|| bad("expected on/off"))?.into(),
            Kind::Int(_, lo, hi) => {
                let n = v.as_i64().filter(|n| (lo..=hi).contains(n)).ok_or_else(|| bad(format!("{}: {lo}–{hi}", s.label)))?;
                // a number xmas-tui reads as a decimal stays one
                if get(&doc, s).and_then(|i| i.as_float()).is_some() || s.key == "sleigh_minutes" {
                    (n as f64).into()
                } else {
                    n.into()
                }
            }
            Kind::Pct(_) => {
                let n = v.as_i64().filter(|n| (0..=100).contains(n)).ok_or_else(|| bad(format!("{}: 0–100", s.label)))?;
                (n as f64 / 100.0).into()
            }
            Kind::Float(..) => {
                let x: f64 = v.as_str().and_then(|t| t.parse().ok()).filter(|x: &f64| x.is_finite() && (0.0..=10.0).contains(x))
                    .ok_or_else(|| bad(format!("{}: expected a number", s.label)))?;
                x.into()
            }
            Kind::Text(_, max) => v.as_str().filter(|t| t.chars().count() <= max).ok_or_else(|| bad(format!("{}: too long", s.label)))?.into(),
            Kind::Choice(..) => v.as_str().ok_or_else(|| bad(format!("{}: expected a choice", s.label)))?.into(),
        };
        let table: &mut Table = if s.section.is_empty() {
            doc.as_table_mut()
        } else {
            let t = doc.entry(s.section).or_insert(Item::Table(Table::new()));
            t.as_table_mut().ok_or_else(|| bad(format!("[{}] in xmas-tui's config.toml isn't a table", s.section)))?
        };
        match table.get_mut(s.key).and_then(|i| i.as_value_mut()) {
            Some(old) => {
                let decor = old.decor().clone(); // the comment after the value stays
                *old = value;
                *old.decor_mut() = decor;
            }
            None => {
                table.insert(s.key, Item::Value(value));
            }
        }
    }
    Ok(doc.to_string())
}

pub struct XmasTui {
    ctx: Arc<Ctx>,
}

impl XmasTui {
    pub fn new(ctx: Arc<Ctx>) -> XmasTui {
        XmasTui { ctx }
    }

    fn doc(&self) -> DocumentMut {
        std::fs::read_to_string(config_path()).ok().and_then(|t| t.parse().ok()).unwrap_or_default()
    }

    fn running_windows(&self) -> bool {
        let (rc, out) = crate::runner::run_capture(&["tasklist".into(), "/FI".into(), "IMAGENAME eq xmas-tui.exe".into(), "/NH".into()],
                                                   std::time::Duration::from_secs(5), true);
        rc == 0 && out.to_lowercase().contains("xmas-tui.exe")
    }
}

impl App for XmasTui {
    fn ctx(&self) -> &Ctx {
        &self.ctx
    }
    fn id(&self) -> String {
        "xmas-tui".into()
    }
    fn name(&self) -> String {
        "xmas-tui".into()
    }
    fn blurb(&self) -> String {
        "A cozy, glowing Christmas village as your wallpaper".into()
    }
    fn glyph(&self) -> String {
        "XT".into()
    }
    fn color(&self) -> String {
        "#e0483e".into()
    }
    fn repo(&self) -> String {
        "dash1101/xmas-tui".into()
    }
    fn homepage(&self) -> String {
        "https://github.com/dash1101/xmas-tui".into()
    }
    fn icon(&self) -> String {
        "xmas-tui.svg".into()
    }
    fn units(&self) -> Vec<(String, bool)> {
        if cfg!(target_os = "linux") { vec![(UNIT.into(), true)] } else { vec![] }
    }
    fn own_settings(&self) -> Option<Value> {
        Some(json!({"id": "open", "label": "xmas-tui's own settings", "help": "Its own settings window: keys, villagers, updates and the rest."}))
    }

    fn status(&self) -> Value {
        let units = unit_states(self);
        let running = if cfg!(windows) { self.running_windows() } else {
            units.first().map(|u| u["state"] == "running" || u["state"] == "starting").unwrap_or(false)
        };
        let failed = units.first().map(|u| u["state"] == "failed").unwrap_or(false);
        let doc = self.doc();
        let mut facts = vec![json!(["settings", config_path().display().to_string()])];
        if doc.get("sound").and_then(|s| s.get("enabled")).and_then(|v| v.as_bool()) == Some(false) {
            facts.push(json!(["sound", "off"]));
        }
        let (state, headline) = if failed {
            ("down", "The wallpaper stopped with an error: see the log")
        } else if running {
            ("ok", "Running as your wallpaper")
        } else {
            ("off", "Not running")
        };
        json!({"units": units, "facts": facts, "state": state, "headline": headline})
    }

    fn settings(&self) -> Vec<Value> {
        let doc = self.doc();
        let mut out: Vec<Value> = SETTINGS.iter().map(|(title, list)| json!({"title": title, "fields": list.iter().map(|s| field_for(&doc, s)).collect::<Vec<_>>()})).collect();
        out.push(json!({"title": "more", "fields": [field("more", "more", "note", json!("Everything else (keys, villagers, updates…) is in xmas-tui's own settings: “xmas-tui's own settings” above. Changes here apply at once."), "app")]}));
        out
    }

    fn apply_app_settings(&self, patch: Map<String, Value>) -> Res<()> {
        let path = config_path();
        let text = std::fs::read_to_string(&path).unwrap_or_default();
        let new = edit(&text, &patch)?;
        if self.ctx.dry_run() {
            return Ok(());
        }
        if let Some(d) = path.parent() {
            std::fs::create_dir_all(d).map_err(|e| bad(format!("Couldn't write {}: {e}", d.display())))?;
        }
        // written whole and then put in place, so xmas-tui never reads half a file
        let tmp = path.with_extension("toml.orbit-new");
        std::fs::write(&tmp, new).and_then(|_| std::fs::rename(&tmp, &path))
            .map_err(|e| bad(format!("Couldn't write {}: {e}", path.display())))
    }

    fn actions(&self) -> Vec<Value> {
        let mut acts = vec![
            json!({"id": "open", "label": "xmas-tui's own settings", "group": "open", "primary": true}),
            json!({"id": "window", "label": "play in a window", "group": "open"}),
        ];
        acts.extend(unit_actions(self));
        acts
    }

    fn run_action(&self, action: &str, _args: &Map<String, Value>) -> Res<Value> {
        let exe = exe().to_string_lossy().into_owned();
        let launch = |flag: &str, said: &str| -> Res<Value> {
            if self.ctx.dry_run() {
                return Ok(json!({"ok": true, "message": format!("{said} (dry run)")}));
            }
            if !self.ctx.runner.launch(&[exe.clone(), flag.into()]) {
                return Err(bad("Couldn't start xmas-tui"));
            }
            Ok(json!({"ok": true, "message": said}))
        };
        match action {
            "open" => launch("--settings-window", "opened xmas-tui's settings"),
            "window" => launch("--windowed", "opened xmas-tui in a window"),
            a if a.starts_with("svc:") && !self.units().is_empty() => run_unit_action(self, a),
            _ => Err(bad("unknown action")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn edits_keep_the_rest_of_the_file() {
        let text = "# xmas-tui settings\nfps = 30\n\n[scene]\n# how much snow\nsnow = 1.0 # 0 = none\nname = \"Ada\"\nfuture_key = [1, 2]\n\n[keybinds]\nup = \"w\"\n";
        let mut p = Map::new();
        p.insert("scene.snow".into(), json!("1.5"));
        p.insert("fps".into(), json!(24));
        p.insert("sound.master".into(), json!(80));
        p.insert("scene.player".into(), json!(false));
        let out = edit(text, &p).unwrap();
        assert!(out.contains("# xmas-tui settings"));
        assert!(out.contains("# how much snow"));
        assert!(out.contains("snow = 1.5 # 0 = none"), "{out}");
        assert!(out.contains("fps = 24"));
        assert!(out.contains("future_key = [1, 2]"));
        assert!(out.contains("[keybinds]\nup = \"w\""));
        let doc: DocumentMut = out.parse().unwrap();
        assert_eq!(doc["sound"]["master"].as_float(), Some(0.8));
        assert_eq!(doc["scene"]["player"].as_bool(), Some(false));
        assert_eq!(doc["scene"]["name"].as_str(), Some("Ada"));
    }

    #[test]
    fn bad_values_change_nothing() {
        let mut p = Map::new();
        p.insert("fps".into(), json!(500));
        assert!(edit("fps = 30\n", &p).is_err());
        let mut p = Map::new();
        p.insert("nope".into(), json!(1));
        assert!(edit("", &p).is_err());
        assert!(edit("this is = = not toml", &Map::new()).is_err());
    }

    #[test]
    fn fields_show_the_file_or_the_defaults() {
        let doc: DocumentMut = "[scene]\nsnow = 1.25\n[sound]\nmaster = 0.3\n".parse().unwrap();
        let snow = all().find(|s| s.key == "snow").unwrap();
        let f = field_for(&doc, snow);
        assert_eq!(f["value"], json!("1.25"));
        assert!(f["options"].as_array().unwrap().iter().any(|o| o[0] == "1.25"));
        assert_eq!(field_for(&doc, all().find(|s| s.key == "master").unwrap())["value"], json!(30));
        assert_eq!(field_for(&doc, all().find(|s| s.key == "fps").unwrap())["value"], json!(30));
    }
}
