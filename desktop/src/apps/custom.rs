//! An app added from the hub with no code: a systemd unit and/or a health address and/or a web page.
use super::{field, open_url, run_unit_action, unit_actions, unit_states, with, App, Ctx};
use crate::net;
use crate::systemd;
use crate::util::{bad, Res};
use serde_json::{json, Map, Value};
use std::sync::{Arc, Mutex};

/// Check a custom app definition from the hub's settings page. Returns a clean object.
pub fn validate(spec: &Value) -> Res<Value> {
    let Some(s) = spec.as_object() else { return Err(bad("expected an object")) };
    let text = |k: &str| match s.get(k) {
        Some(Value::String(x)) => x.clone(),
        Some(Value::Null) | None => String::new(),
        Some(other) => other.to_string(),
    };
    let name: String = text("name").trim().chars().take(40).collect();
    if name.is_empty() {
        return Err(bad("give it a name"));
    }
    let id_re = regex::Regex::new(r"^[a-z0-9][a-z0-9-]{0,39}$").unwrap();
    let derived = regex::Regex::new(r"[^a-z0-9]+").unwrap().replace_all(&name.to_lowercase(), "-").trim_matches('-').to_string();
    let id: String = if text("id").is_empty() { derived } else { text("id") }.chars().take(40).collect();
    if !id_re.is_match(&id) {
        return Err(bad("the id may only use a-z, 0-9 and dashes"));
    }
    let mut unit = text("unit").trim().to_string();
    if !unit.is_empty() && !["service", "socket", "timer", "path", "target"].contains(&unit.rsplit('.').next().unwrap_or("")) {
        unit.push_str(".service");
    }
    if !unit.is_empty() && !systemd::valid_unit(&unit) {
        return Err(bad("that isn't a systemd unit name"));
    }
    let scope = if text("scope").is_empty() { "user".to_string() } else { text("scope") };
    if scope != "user" && scope != "system" {
        return Err(bad("scope is user or system"));
    }
    let mut out = json!({"id": id, "name": name, "unit": unit, "scope": scope});
    for k in ["healthUrl", "openUrl"] {
        let v = text(k).trim().to_string();
        if !v.is_empty() {
            let scheme = url::Url::parse(&v).map(|u| u.scheme().to_string()).unwrap_or_default();
            if scheme != "http" && scheme != "https" {
                return Err(bad(format!("{k} must start with http:// or https://")));
            }
        }
        out[k] = json!(v);
    }
    if unit.is_empty() && out["healthUrl"] == "" && out["openUrl"] == "" {
        return Err(bad("give it a service, a health address or a web page"));
    }
    Ok(out)
}

pub struct CustomApp {
    ctx: Arc<Ctx>,
    spec: Mutex<Value>,
}

impl CustomApp {
    pub fn new(ctx: Arc<Ctx>, spec: Value) -> Option<CustomApp> {
        spec["id"].as_str()?;
        spec["name"].as_str()?;
        Some(CustomApp { ctx, spec: Mutex::new(spec) })
    }

    fn spec(&self) -> Value {
        self.spec.lock().unwrap().clone()
    }

    fn s(&self, k: &str) -> String {
        self.spec()[k].as_str().unwrap_or("").to_string()
    }
}

impl App for CustomApp {
    fn ctx(&self) -> &Ctx {
        &self.ctx
    }
    fn id(&self) -> String {
        self.s("id")
    }
    fn name(&self) -> String {
        self.s("name")
    }
    fn blurb(&self) -> String {
        "Added in Orbit".into()
    }
    fn glyph(&self) -> String {
        let g: String = self.name().chars().take(2).collect::<String>().to_uppercase();
        if g.is_empty() { "··".into() } else { g }
    }
    fn color(&self) -> String {
        "#9aa0a6".into()
    }
    fn custom(&self) -> bool {
        true
    }
    fn units(&self) -> Vec<(String, bool)> {
        let u = self.s("unit");
        if u.is_empty() { vec![] } else { vec![(u, self.s("scope") == "user")] }
    }
    fn open_label(&self) -> String {
        if self.s("openUrl").is_empty() { String::new() } else { "open".into() }
    }

    fn status(&self) -> Value {
        let units = unit_states(self);
        let mut facts: Vec<Value> = vec![];
        let unit = units.first().cloned();
        let mut health = None;
        let h = self.s("healthUrl");
        if !h.is_empty() {
            match net::request_text("GET", &h, None, &net::Req { timeout: 2.0, ..Default::default() }) {
                Ok(_) => {
                    health = Some(true);
                    facts.push(json!(["health", "answering"]));
                }
                Err(e) => {
                    health = Some(false);
                    facts.push(json!(["health", if e.message.is_empty() { "no answer".to_string() } else { e.message }, "warn"]));
                }
            }
        }
        let st = unit.as_ref().map(|u| u["state"].as_str().unwrap_or("").to_string()).unwrap_or_default();
        let (state, headline) = if unit.is_some() && st == "failed" {
            ("down", "The service failed — see the log".to_string())
        } else if unit.is_some() && st == "missing" {
            ("unknown", format!("No unit called {}", unit.as_ref().unwrap()["unit"].as_str().unwrap_or("")))
        } else if health == Some(false) {
            ("down", "Not answering".into())
        } else if unit.is_some() && st == "stopped" {
            ("off", "Not running".into())
        } else if unit.is_some() || health == Some(true) {
            ("ok", "Running".into())
        } else {
            ("unknown", "Nothing to check — add a service or a health address".into())
        };
        json!({"units": units, "facts": facts, "state": state, "headline": headline})
    }

    fn settings(&self) -> Vec<Value> {
        let sp = self.spec();
        let g = |k: &str| sp[k].as_str().unwrap_or("").to_string();
        vec![json!({"title": "definition", "fields": [
            with(field("name", "Name", "text", json!(g("name")), "hub"), json!({"maxLen": 40})),
            field("unit", "systemd unit", "text", json!(g("unit")), "hub"),
            with(field("scope", "Unit type", "choice", json!(if g("scope").is_empty() { "user".into() } else { g("scope") }), "hub"),
                 json!({"options": [["user", "user (systemctl --user)"], ["system", "system (read-only here)"]]})),
            with(field("healthUrl", "Health address", "text", json!(g("healthUrl")), "hub"), json!({"help": "Any page that answers when the app is up."})),
            field("openUrl", "Web page", "text", json!(g("openUrl")), "hub"),
        ]})]
    }

    fn apply_hub_settings(&self, patch: Map<String, Value>) -> Res<()> {
        let mut merged = self.spec();
        for (k, v) in patch {
            merged[k] = v;
        }
        merged["id"] = json!(self.id());
        let spec = validate(&merged)?;
        let custom: Vec<Value> = self.ctx.config.get("custom").as_array().cloned().unwrap_or_default().into_iter()
            .map(|c| if c["id"] == spec["id"] { spec.clone() } else { c }).collect();
        let mut m = Map::new();
        m.insert("custom".into(), json!(custom));
        self.ctx.config.update(m);
        *self.spec.lock().unwrap() = spec;
        Ok(())
    }

    fn actions(&self) -> Vec<Value> {
        let mut acts = vec![];
        if !self.s("openUrl").is_empty() {
            acts.push(json!({"id": "open", "label": "open", "group": "open", "primary": true}));
        }
        acts.extend(unit_actions(self));
        acts.push(json!({"id": "remove", "label": "remove from orbit", "group": "orbit", "danger": true,
                         "confirm": format!("Remove {} from Orbit? (The app itself is not touched.)", self.name())}));
        acts
    }

    fn run_action(&self, action: &str, _args: &Map<String, Value>) -> Res<Value> {
        if action == "open" && !self.s("openUrl").is_empty() {
            return open_url(self, &self.s("openUrl"));
        }
        if action.starts_with("svc:") && !self.units().is_empty() {
            return run_unit_action(self, action);
        }
        if action == "remove" {
            let id = self.id();
            let custom: Vec<Value> = self.ctx.config.get("custom").as_array().cloned().unwrap_or_default().into_iter()
                .filter(|c| c["id"] != id.as_str()).collect();
            let mut m = Map::new();
            m.insert("custom".into(), json!(custom));
            self.ctx.config.update(m);
            return Ok(json!({"ok": true, "message": format!("removed {}", self.name()), "removed": true}));
        }
        Err(bad("unknown action"))
    }
}
