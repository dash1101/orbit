//! What an install recipe looks like.
//!
//! A recipe knows, for one app: which systems it runs on, whether (and which version of) it is installed here,
//! what this computer still needs, the few choices worth asking about, and the steps to install, update or remove
//! it. The installer window is drawn entirely from `info()`; `plan()` turns the same answers into steps.
use crate::apps::coerce;
use crate::engine::{check_op, Session, Sources};
use crate::host::Host;
use crate::net;
use crate::util::{bad, failed, fmt_size, version_of, Res};
use serde_json::{json, Map, Value};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub type StepFn = Box<dyn Fn(&mut Session) -> Res<bool> + Send + Sync>; // Ok(true) = done, Ok(false) = skipped

pub struct StepDef {
    pub id: &'static str,
    pub label: String,
    pub weight: f64,
    pub f: StepFn,
}

pub fn step(id: &'static str, label: &str, weight: f64, f: impl Fn(&mut Session) -> Res<bool> + Send + Sync + 'static) -> StepDef {
    StepDef { id, label: label.into(), weight, f: Box::new(f) }
}

pub fn option(key: &str, label: &str, type_: &str, value: Value, help: &str) -> Value {
    json!({"key": key, "label": label, "type": type_, "value": value, "help": help})
}

/// One line of "what this computer needs": ok, or (blocking or not) with how to fix it.
pub fn need(label: &str, ok: bool, hint: &str, blocking: bool, detail: &str) -> Value {
    json!({"label": label, "ok": ok, "hint": if ok { "" } else { hint }, "blocking": blocking && !ok, "detail": detail})
}

/// Shared state every recipe has.
pub struct RecipeBase {
    pub host: Host,
    pub sources: Arc<Sources>,
    installed: Mutex<Option<(Instant, Value)>>,
}

impl RecipeBase {
    pub fn new(host: Host, sources: Arc<Sources>) -> RecipeBase {
        RecipeBase { host, sources, installed: Mutex::new(None) }
    }
}

pub trait Recipe: Send + Sync {
    fn base(&self) -> &RecipeBase;
    fn id(&self) -> &str;
    fn name(&self) -> &str;
    fn repo(&self) -> &str;
    fn blurb(&self) -> &str;
    fn glyph(&self) -> &str;
    fn color(&self) -> &str;
    /// system -> what installing gives you there: ("full" | "limited" | "none", note)
    fn platform(&self, system: &str) -> (String, String);
    fn self_app(&self) -> bool {
        false
    }

    /// {"installed", "version", "where", "how"}: looked at, never changed.
    fn detect(&self) -> Value {
        json!({"installed": false, "version": "", "where": "", "how": ""})
    }
    fn requirements(&self, _op: &str) -> Vec<Value> {
        vec![]
    }
    fn options(&self, _op: &str) -> Vec<Value> {
        vec![]
    }
    /// Release files this install downloads (for the size shown before starting).
    fn assets(&self, _rel: &Value) -> Vec<String> {
        vec![]
    }
    fn summary(&self, _op: &str, _opts: &Map<String, Value>, _rel: Option<&Value>) -> Vec<String> {
        vec![]
    }
    fn steps(self: Arc<Self>, op: &str, opts: &Map<String, Value>) -> Vec<StepDef>;
    fn finish(&self, _sess: &Session, _op: &str, _opts: &Map<String, Value>) -> Value {
        json!({})
    }
    /// (ok, reason): whether Orbit knows how to remove what is installed here.
    fn can_uninstall(&self) -> (bool, String) {
        (true, String::new())
    }
    fn needs_admin(&self, _op: &str) -> Value {
        json!(false)
    }
    /// Whether this recipe can use a release (Orbit's own: one with a file for this computer).
    fn wants(&self, _rel: &Value) -> bool {
        true
    }
    /// A last word on what the installer window shows (a recipe's own wording).
    fn adjust_info(&self, _info: &mut Value) {}

    fn support(&self) -> Value {
        let b = self.base();
        let (level, note) = self.platform(&b.host.system);
        if level != "none" && !(b.host.arch == "amd64" || b.host.arch == "arm64") {
            return json!({"level": "none", "note": format!("{} isn't available for this processor ({}) yet.", self.name(), b.host.arch)});
        }
        json!({"level": level, "note": note})
    }

    fn forget(&self) {
        *self.base().installed.lock().unwrap() = None;
    }

    fn installed(&self) -> Value {
        if let Some((at, v)) = self.base().installed.lock().unwrap().clone() {
            if at.elapsed() < Duration::from_secs(4) {
                return v;
            }
        }
        let d = self.detect();
        *self.base().installed.lock().unwrap() = Some((Instant::now(), d.clone()));
        d
    }

    fn title(&self, op: &str, rel: Option<&Value>) -> String {
        if op == "uninstall" {
            return format!("Remove {}", self.name());
        }
        let inst = self.installed();
        let new = rel.map(|r| version_of(r["tag"].as_str().unwrap_or(""))).unwrap_or_default();
        if !inst["installed"].as_bool().unwrap_or(false) {
            return format!("Install {}", self.name());
        }
        let cur = inst["version"].as_str().unwrap_or("");
        if !new.is_empty() && !cur.is_empty() && net::is_newer(&new, cur) {
            return format!("Update {} to {new}", self.name());
        }
        format!("Reinstall {}", self.name())
    }
}

pub fn platforms(r: &dyn Recipe) -> Value {
    let mut out = Map::new();
    for s in ["linux", "darwin", "windows"] {
        let (level, note) = r.platform(s);
        out.insert(s.into(), json!({"level": level, "note": note}));
    }
    Value::Object(out)
}

pub fn describe(r: &dyn Recipe) -> Value {
    let rel = r.base().sources.cached_with(r.repo(), &|x| r.wants(x));
    json!({"id": r.id(), "name": r.name(), "blurb": r.blurb(), "glyph": r.glyph(), "color": r.color(), "repo": r.repo(),
           "platforms": platforms(r), "support": r.support(), "installed": r.installed(),
           "latest": rel.map(|v| version_of(v["tag"].as_str().unwrap_or(""))).unwrap_or_default()})
}

/// The options with what was chosen filled in, and the chosen values.
fn options_with(r: &dyn Recipe, op: &str, given: &Value) -> Res<(Vec<Value>, Map<String, Value>)> {
    let mut fields = r.options(op);
    let empty = Map::new();
    let given = match given {
        Value::Null => &empty,
        Value::Object(m) => m,
        _ => return Err(bad("options must be an object")),
    };
    let known: Vec<String> = fields.iter().map(|f| f["key"].as_str().unwrap_or("").to_string()).collect();
    let mut unknown: Vec<&String> = given.keys().filter(|k| !known.contains(k)).collect();
    if !unknown.is_empty() {
        unknown.sort();
        return Err(bad(format!("unknown option: {}", unknown.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", "))));
    }
    let mut out = Map::new();
    for f in fields.iter_mut() {
        let key = f["key"].as_str().unwrap_or("").to_string();
        let v = match given.get(&key) {
            Some(v) => coerce(f, v)?,
            None => f["value"].clone(),
        };
        f["value"] = v.clone();
        out.insert(key, v);
    }
    Ok((fields, out))
}

/// Everything the installer window shows before starting.
pub fn info(r: &Arc<dyn Recipe>, op: &str, given: &Value) -> Res<Value> {
    check_op(op)?;
    let (sup, inst) = (r.support(), r.installed());
    let none = sup["level"] == "none";
    let (fields, opts) = options_with(r.as_ref(), op, given)?;
    let (mut rel, mut rel_err, mut hint) = (None, String::new(), String::new());
    if op == "install" && !none {
        match r.base().sources.latest_with(r.repo(), false, &|x| r.wants(x)) {
            Ok(v) => rel = Some(v),
            Err(crate::util::Error::Failed(m, h)) => {
                rel_err = m;
                hint = h;
            }
            Err(e) => rel_err = e.to_string(),
        }
    }
    let reqs = if none { vec![] } else { r.requirements(op) };
    let installed = inst["installed"].as_bool().unwrap_or(false);
    let blocked = if none {
        sup["note"].as_str().unwrap_or("").to_string()
    } else if op == "uninstall" && !installed {
        format!("{} isn't installed on this computer.", r.name())
    } else if op == "uninstall" && !r.can_uninstall().0 {
        r.can_uninstall().1
    } else if op == "install" && !rel_err.is_empty() {
        rel_err.clone()
    } else if reqs.iter().any(|x| x["blocking"].as_bool().unwrap_or(false)) {
        "This computer needs a few things first (see above).".to_string()
    } else {
        String::new()
    };
    let size: u64 = rel.as_ref().map(|rv: &Value| {
        r.assets(rv).iter().map(|n| rv["assets"].get(n).and_then(|a| a["size"].as_u64()).unwrap_or(0)).sum()
    }).unwrap_or(0);
    let steps = if none || (op == "uninstall" && !installed) { vec![] } else { r.clone().steps(op, &opts) };
    let release = rel.as_ref().map(|rv| json!({
        "version": version_of(rv["tag"].as_str().unwrap_or("")), "tag": rv["tag"], "published": rv["published"],
        "prerelease": rv["prerelease"], "size": size, "sizeText": if size > 0 { fmt_size(size) } else { String::new() }, "url": rv["url"]}));
    let mut out = json!({
        "app": r.id(), "name": r.name(), "op": op, "title": r.title(op, rel.as_ref()), "host": r.base().host.describe(),
        "support": sup, "installed": inst, "platforms": platforms(r.as_ref()), "release": release,
        "requirements": reqs, "options": fields,
        "summary": if none { vec![] } else { r.summary(op, &opts, rel.as_ref()) },
        "steps": steps.iter().map(|s| json!({"id": s.id, "label": s.label})).collect::<Vec<_>>(),
        "needsAdmin": r.needs_admin(op), "blocked": blocked,
        "blockedHint": if !blocked.is_empty() && blocked == rel_err { hint } else { String::new() },
    });
    r.adjust_info(&mut out);
    Ok(out)
}

pub struct Plan {
    pub title: String,
    pub steps: Vec<StepDef>,
    pub finish: Box<dyn Fn(&Session) -> Value + Send + Sync>,
    pub vars: Map<String, Value>,
    pub recipe: Arc<dyn Recipe>,
}

pub fn plan(r: &Arc<dyn Recipe>, op: &str, given: &Value) -> Res<Plan> {
    let i = info(r, op, given)?;
    let blocked = i["blocked"].as_str().unwrap_or("");
    if !blocked.is_empty() {
        return Err(failed(blocked, ""));
    }
    let (_fields, opts) = options_with(r.as_ref(), op, given)?;
    let steps = r.clone().steps(op, &opts);
    let mut vars = Map::new();
    vars.insert("opts".into(), Value::Object(opts.clone()));
    vars.insert("before".into(), i["installed"].clone());
    let (rr, op2) = (r.clone(), op.to_string());
    Ok(Plan {
        title: i["title"].as_str().unwrap_or("").to_string(),
        steps,
        finish: Box::new(move |s| rr.finish(s, &op2, &opts)),
        vars,
        recipe: r.clone(),
    })
}
