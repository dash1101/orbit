//! What every app in Orbit looks like.
//!
//! An app adapter answers four questions (how is it doing, what can I do to it, what can I change, what did it
//! say lately) so the hub can show every app the same way. Settings fields are plain objects the web app draws
//! generically: {"key", "label", "type": bool|int|choice|text|words|secret|note, "value", "help"?, "min"?, "max"?,
//! "options"?: [[value, label]], "scope": "app"|"hub"}.
pub mod custom;
pub mod discord;
pub mod nova;
pub mod starcast;

use crate::config::Config;
use crate::net::{self, Releases};
use crate::runner::{terminal_argv, Runner};
use crate::systemd;
use crate::util::{bad, Res};
use serde_json::{json, Map, Value};
use std::sync::Arc;

pub struct Ctx {
    pub config: Arc<Config>,
    pub runner: Arc<Runner>,
    pub releases: Arc<Releases>,
}

impl Ctx {
    pub fn dry_run(&self) -> bool {
        self.runner.dry_run
    }
}

pub fn field(key: &str, label: &str, type_: &str, value: Value, scope: &str) -> Value {
    json!({"key": key, "label": label, "type": type_, "value": value, "scope": scope})
}

pub fn with(mut f: Value, extra: Value) -> Value {
    if let (Some(m), Value::Object(e)) = (f.as_object_mut(), extra) {
        m.extend(e);
    }
    f
}

fn full_match(pattern: &str, v: &str) -> bool {
    regex::Regex::new(&format!("^(?:{pattern})$")).map(|re| re.is_match(v)).unwrap_or(false)
}

/// Check one submitted value against its field.
pub fn coerce(f: &Value, v: &Value) -> Res<Value> {
    let (t, label) = (f["type"].as_str().unwrap_or(""), f["label"].as_str().unwrap_or(""));
    match t {
        "bool" => v.as_bool().map(Value::Bool).ok_or_else(|| bad(format!("{label}: expected on/off"))),
        "int" => {
            let n = match v {
                Value::Number(n) if n.as_i64().is_some() => n.as_i64().unwrap(),
                Value::Number(n) if n.as_f64().map(|x| x.fract() == 0.0).unwrap_or(false) => n.as_f64().unwrap() as i64,
                _ => return Err(bad(format!("{label}: expected a whole number"))),
            };
            let (lo, hi) = (f.get("min").and_then(|x| x.as_i64()), f.get("max").and_then(|x| x.as_i64()));
            if lo.map(|l| n < l).unwrap_or(false) || hi.map(|h| n > h).unwrap_or(false) {
                let s = |x: Option<i64>| x.map(|v| v.to_string()).unwrap_or_else(|| "None".into());
                return Err(bad(format!("{label}: must be between {} and {}", s(lo), s(hi))));
            }
            Ok(json!(n))
        }
        "choice" => {
            let allowed: Vec<Value> = f["options"].as_array().map(|o| o.iter().map(|x| x[0].clone()).collect()).unwrap_or_default();
            if !allowed.contains(v) {
                let names: Vec<String> = allowed.iter().map(|a| a.as_str().map(String::from).unwrap_or_else(|| a.to_string())).collect();
                return Err(bad(format!("{label}: must be one of {}", names.join(", "))));
            }
            Ok(v.clone())
        }
        "text" | "secret" => {
            let max = f.get("maxLen").and_then(|x| x.as_u64()).unwrap_or(300) as usize;
            let s = v.as_str().filter(|s| s.chars().count() <= max).ok_or_else(|| bad(format!("{label}: expected text")))?.trim().to_string();
            if let Some(p) = f.get("pattern").and_then(|p| p.as_str()) {
                if !s.is_empty() && !full_match(p, &s) {
                    return Err(bad(format!("{label}: {}", f.get("patternHelp").and_then(|x| x.as_str()).unwrap_or("that doesn’t look right"))));
                }
            }
            Ok(json!(s))
        }
        "words" => {
            let list: Vec<String> = match v {
                Value::String(s) => s.replace('\n', ",").split(',').map(|w| w.trim().to_string()).filter(|w| !w.is_empty()).collect(),
                Value::Array(a) if a.iter().all(|x| x.is_string()) => a.iter().map(|x| x.as_str().unwrap().to_string()).collect(),
                _ => return Err(bad(format!("{label}: expected a list of words"))),
            };
            let max = f.get("maxItems").and_then(|x| x.as_u64()).unwrap_or(50) as usize;
            if list.len() > max || list.iter().any(|w| w.chars().count() > 80) {
                return Err(bad(format!("{label}: too many or too long")));
            }
            Ok(json!(list))
        }
        _ => Err(bad(format!("{label}: can't be changed here"))),
    }
}

pub trait App: Send + Sync {
    fn ctx(&self) -> &Ctx;
    fn id(&self) -> String;
    fn name(&self) -> String;
    fn blurb(&self) -> String;
    fn glyph(&self) -> String;
    fn color(&self) -> String;
    fn repo(&self) -> String {
        String::new()
    }
    fn homepage(&self) -> String {
        String::new()
    }
    /// (unit, user?) pairs this app runs as.
    fn units(&self) -> Vec<(String, bool)> {
        vec![]
    }
    /// A file in static/apps (svg or png); the glyph is used when empty.
    fn icon(&self) -> String {
        String::new()
    }
    /// Its "open" action's name, for a button on the overview card while it runs.
    fn open_label(&self) -> String {
        String::new()
    }
    /// More of its actions as buttons on the overview card while it runs (after "open"; each one from actions()).
    fn card_actions(&self) -> Vec<Value> {
        vec![]
    }
    /// Windows: the scheduled task it runs from at logon, and its program's name (YTM Starcast: "YTM Starcast",
    /// ytm-starcast.exe). Orbit starts, stops and switches it there, as it does with the systemd service on Linux.
    fn windows_task(&self) -> Option<(String, String)> {
        None
    }
    /// Whether it answered when last looked at (for the start/restart button where the task can't say so).
    fn answering(&self) -> bool {
        false
    }
    /// (browsers, browser actions) waiting for the owner's approval (Nova), for a desktop notification.
    fn approvals_waiting(&self) -> (u64, u64) {
        (0, 0)
    }
    fn custom(&self) -> bool {
        false
    }

    fn hub(&self) -> Map<String, Value> {
        self.ctx().config.app(&self.id())
    }
    fn hub_fields(&self) -> Vec<Value> {
        vec![]
    }

    fn status(&self) -> Value;
    /// [{"title", "fields": [...]}]
    fn settings(&self) -> Vec<Value> {
        let f = self.hub_fields();
        if f.is_empty() { vec![] } else { vec![json!({"title": "connection", "fields": f})] }
    }
    fn apply_app_settings(&self, _patch: Map<String, Value>) -> Res<()> {
        Err(bad("this app has no settings that can be changed from here"))
    }
    fn apply_hub_settings(&self, patch: Map<String, Value>) -> Res<()> {
        self.ctx().config.update_app(&self.id(), patch);
        Ok(())
    }
    fn actions(&self) -> Vec<Value> {
        vec![]
    }
    /// For an action with "term": the command it runs in Orbit's terminal:
    /// {"argv": [...], "title", "cleanup": [files to delete afterwards], "mask": [secrets to hide], "shown"?}.
    fn term_command(&self, _action: &str, _args: &Map<String, Value>) -> Res<Option<Value>> {
        Ok(None)
    }
    fn run_action(&self, _action: &str, _args: &Map<String, Value>) -> Res<Value> {
        Err(bad("unknown action"))
    }
    fn logs(&self) -> String {
        match self.units().first() {
            Some((unit, user)) => systemd::journal(&self.ctx().runner, unit, *user, 80),
            None => String::new(),
        }
    }
    /// After an install changed it: look for it again.
    fn forget_found(&self) {}

    fn describe(&self) -> Value {
        let icon = self.icon();
        json!({"id": self.id(), "name": self.name(), "blurb": self.blurb(), "glyph": self.glyph(), "color": self.color(),
               "homepage": self.homepage(), "custom": self.custom(), "icon": if icon.is_empty() { String::new() } else { format!("/apps/{icon}") }})
    }
}

// ---- shared helpers -----------------------------------------------------------------------------------
pub fn apply_settings(app: &dyn App, patch: &Value) -> Res<Value> {
    let Some(patch) = patch.as_object().filter(|p| !p.is_empty()) else { return Err(bad("nothing to change")) };
    let mut fields = Map::new();
    for g in app.settings() {
        for f in g["fields"].as_array().cloned().unwrap_or_default() {
            if let Some(k) = f["key"].as_str() {
                fields.insert(k.into(), f.clone());
            }
        }
    }
    let (mut app_patch, mut hub_patch) = (Map::new(), Map::new());
    for (k, v) in patch {
        let f = fields.get(k).filter(|f| f["type"] != "note" && !f.get("readonly").and_then(|r| r.as_bool()).unwrap_or(false))
            .ok_or_else(|| bad(format!("unknown setting: {k}")))?;
        let val = coerce(f, v)?;
        if f["scope"] == "app" { app_patch.insert(k.clone(), val) } else { hub_patch.insert(k.clone(), val) };
    }
    let app_changed = !app_patch.is_empty();
    if !hub_patch.is_empty() {
        app.apply_hub_settings(hub_patch)?;
    }
    if app_changed {
        app.apply_app_settings(app_patch)?;
    }
    Ok(json!({"ok": true, "dryRun": app.ctx().dry_run() && app_changed}))
}

/// The value typed for an action with "ask" (checked against its pattern).
pub fn ask_value(app: &dyn App, action: &str, args: &Map<String, Value>) -> Res<String> {
    let spec = app.actions().into_iter().find(|a| a["id"] == action).and_then(|a| a.get("ask").cloned());
    let Some(spec) = spec else { return Ok(String::new()) };
    let label = spec["label"].as_str().unwrap_or("");
    let v = args.get("value").and_then(|v| v.as_str()).filter(|v| !v.trim().is_empty() && v.chars().count() <= 400)
        .ok_or_else(|| bad(format!("{label}: please fill it in")))?.trim().to_string();
    if let Some(p) = spec.get("pattern").and_then(|p| p.as_str()) {
        if !full_match(p, &v) {
            return Err(bad(format!("{label}: {}", spec.get("patternHelp").and_then(|x| x.as_str()).unwrap_or("that doesn’t look right"))));
        }
    }
    Ok(v)
}

pub fn unit_states(app: &dyn App) -> Vec<Value> {
    if let Some((task, _)) = app.windows_task().filter(|_| cfg!(windows)) {
        return vec![crate::wintask::show(&app.ctx().runner, &task)];
    }
    app.units().iter().map(|(u, user)| systemd::show(&app.ctx().runner, u, *user)).collect()
}

/// Start/stop/restart/autostart buttons for the main *user* unit.
pub fn unit_actions(app: &dyn App) -> Vec<Value> {
    let mut out = vec![];
    if let Some((task, _)) = app.windows_task().filter(|_| cfg!(windows)) {
        let st = crate::wintask::show(&app.ctx().runner, &task);
        if st["state"] == "missing" {
            return out;
        }
        let running = app.answering();
        out.push(json!({"id": if running { "svc:restart" } else { "svc:start" }, "label": if running { "restart" } else { "start" }, "group": "service"}));
        if running {
            out.push(json!({"id": "svc:stop", "label": "stop", "group": "service", "confirm": format!("Stop {}?", app.name())}));
        }
        let on = st["enabled"] == "enabled";
        out.push(json!({"id": if on { "svc:disable" } else { "svc:enable" }, "group": "service",
                        "label": if on { "start at login: on" } else { "start at login: off" }, "toggle": on}));
        return out;
    }
    if let Some((unit, user)) = app.units().first() {
        if !*user {
            return out;
        }
        let st = systemd::show(&app.ctx().runner, unit, true);
        if st["state"] == "missing" {
            return out;
        }
        let running = st["state"] == "running" || st["state"] == "starting";
        out.push(json!({"id": if running { "svc:restart" } else { "svc:start" }, "label": if running { "restart" } else { "start" }, "group": "service"}));
        if running {
            out.push(json!({"id": "svc:stop", "label": "stop", "group": "service", "confirm": format!("Stop {}?", app.name())}));
        }
        let on = st["enabled"] == "enabled";
        out.push(json!({"id": if on { "svc:disable" } else { "svc:enable" }, "group": "service",
                        "label": if on { "start at login: on" } else { "start at login: off" }, "toggle": on}));
    }
    out
}

pub fn run_unit_action(app: &dyn App, action: &str) -> Res<Value> {
    let verb = action.split_once(':').map(|x| x.1).unwrap_or("");
    if let Some((task, program)) = app.windows_task().filter(|_| cfg!(windows)) {
        let (ok, msg) = crate::wintask::act(&app.ctx().runner, &task, &program, verb);
        if !ok {
            return Err(bad(msg));
        }
        return Ok(json!({"ok": true, "message": format!("{msg}{}", if app.ctx().dry_run() { " (dry run)" } else { "" })}));
    }
    let (unit, user) = app.units().first().cloned().ok_or_else(|| bad("unknown action"))?;
    let (ok, msg) = systemd::act(&app.ctx().runner, &unit, verb, user);
    if !ok {
        return Err(bad(msg));
    }
    Ok(json!({"ok": true, "message": format!("{verb}: {unit}{}", if app.ctx().dry_run() { " (dry run)" } else { "" })}))
}

pub fn open_url(app: &dyn App, url: &str) -> Res<Value> {
    if !(url.starts_with("http://") || url.starts_with("https://")) {
        return Err(bad("not a web address"));
    }
    if !app.ctx().runner.open_url(url) {
        return Err(bad("couldn't open a browser"));
    }
    Ok(json!({"ok": true, "message": format!("opened {url}")}))
}

pub fn open_terminal(app: &dyn App, command: &[String], title: Option<&str>) -> Res<Value> {
    let shown = command.join(" ");
    let argv = terminal_argv(command, title.unwrap_or(&app.name())).ok_or_else(|| bad(format!("no terminal program found; run: {shown}")))?;
    if !app.ctx().runner.launch(&argv) {
        return Err(bad(format!("couldn't open a terminal; run: {shown}")));
    }
    Ok(json!({"ok": true, "message": format!("opened a terminal: {shown}")}))
}

/// {'latest', 'url', 'available', 'error'} or None when checks are off / not yet run.
pub fn release_info(app: &dyn App, current: &str, force: bool) -> Option<Value> {
    let repo = app.repo();
    if repo.is_empty() {
        return None;
    }
    let rel = if force {
        Some(app.ctx().releases.latest(&repo, true))
    } else if app.ctx().config.get("checkUpdates").as_bool().unwrap_or(true) {
        Some(app.ctx().releases.latest(&repo, false))
    } else {
        app.ctx().releases.cached(&repo)
    }?;
    if !rel["ok"].as_bool().unwrap_or(false) {
        return Some(json!({"error": rel["error"]}));
    }
    let r = &rel["release"];
    let tag = r["tag"].as_str().unwrap_or("");
    Some(json!({"latest": r["tag"], "url": r["url"], "available": net::is_newer(tag, current)}))
}

/// All apps, in the person's order (built-ins first by default), keyed by id.
pub fn load(ctx: &Arc<Ctx>) -> Vec<Arc<dyn App>> {
    let mut apps: Vec<Arc<dyn App>> = vec![Arc::new(starcast::Starcast::new(ctx.clone())), Arc::new(nova::Nova::new(ctx.clone()))];
    for spec in ctx.config.get("custom").as_array().cloned().unwrap_or_default() {
        let id = spec["id"].as_str().unwrap_or("").to_string();
        if !id.is_empty() && !apps.iter().any(|a| a.id() == id) {
            if let Some(c) = custom::CustomApp::new(ctx.clone(), spec) {
                apps.push(Arc::new(c));
            }
        }
    }
    let order: Vec<String> = ctx.config.get("order").as_array().cloned().unwrap_or_default()
        .iter().filter_map(|v| v.as_str().map(String::from)).collect();
    let mut out: Vec<Arc<dyn App>> = vec![];
    for id in &order {
        if let Some(a) = apps.iter().find(|a| &a.id() == id) {
            if !out.iter().any(|o| &o.id() == id) {
                out.push(a.clone());
            }
        }
    }
    for a in apps {
        if !out.iter().any(|o| o.id() == a.id()) {
            out.push(a);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn settings_are_checked_like_before() {
        let f = json!({"key": "n", "label": "Count", "type": "int", "min": 1, "max": 60});
        assert_eq!(coerce(&f, &json!(5)).unwrap(), json!(5));
        assert!(coerce(&f, &json!(0)).is_err());
        assert!(coerce(&f, &json!(true)).is_err());
        let t = json!({"key": "c", "label": "Code", "type": "text", "pattern": "[A-Z]{4}", "patternHelp": "four capitals"});
        assert_eq!(coerce(&t, &json!(" ABCD ")).unwrap(), json!("ABCD"));
        assert!(coerce(&t, &json!("ABCDE")).unwrap_err().to_string().contains("four capitals"));
        let w = json!({"key": "w", "label": "Words", "type": "words"});
        assert_eq!(coerce(&w, &json!("a, b\nc")).unwrap(), json!(["a", "b", "c"]));
    }
}
