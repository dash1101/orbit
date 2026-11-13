//! Orbit's local server: the web app plus a small JSON API over the app adapters. The same API as the Python
//! Orbit, so the page and Orbit's own checks work unchanged.
//!
//! Security: it listens on 127.0.0.1 only and has no login. To stop web pages from using it: the Host header must
//! be a loopback name (no DNS rebinding), any Origin must be Orbit's own, and POSTs must be JSON (a plain HTML form
//! can't send that).
use crate::apps::{self, custom::validate as validate_custom, App, Ctx};
use crate::config::Config;
use crate::desktop_entry;
use crate::engine::{Installer, Sources};
use crate::github_login::Login;
use crate::host::Host;
use crate::net::{self, Releases};
use crate::recipe;
use crate::runner::{terminal_argv, Runner};
use crate::term::{StartArgs, Terminals};
use crate::util::{bad, Error, Res};
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::io::Read;
use std::sync::atomic::{AtomicU16, Ordering};
use std::sync::{Arc, Mutex, RwLock, Weak};
use std::time::{Duration, Instant};

include!(concat!(env!("OUT_DIR"), "/app_icons.rs"));
const INDEX: &[u8] = include_bytes!("../../orbit/static/index.html");
const ICON: &[u8] = include_bytes!("../../orbit/static/icon.svg");

const THEMES: [(&str, &str); 9] = [("tui", "dusk"), ("phosphor", "phosphor"), ("amber", "amber"), ("paper", "paper"), ("frost", "frost"),
                                   ("ember", "ember"), ("ocean", "ocean"), ("lilac", "lilac"), ("mono", "mono")];
/// What a custom theme may set (the same tokens the built-in ones use).
const THEME_COLORS: [&str; 14] = ["bg", "raise", "line", "line-hi", "text", "muted", "legend", "accent", "accent-ink", "ok", "warn", "down", "off", "sel"];
pub const ORBIT_REPO: &str = "dash1101/orbit";
pub const ORBIT_RELEASES: &str = "https://github.com/dash1101/orbit/releases";
const CACHE: Duration = Duration::from_millis(1200);

fn link(which: &str) -> Option<String> {
    match which {
        "project" => Some(format!("https://github.com/{ORBIT_REPO}")),
        "releases" => Some(ORBIT_RELEASES.into()),
        "issues" => Some(format!("https://github.com/{ORBIT_REPO}/issues")),
        _ => None,
    }
}

fn valid_theme(t: &Value) -> Res<Value> {
    let Some(m) = t.as_object().filter(|m| m.keys().all(|k| ["id", "label", "tokens"].contains(&k.as_str()))) else {
        return Err(bad("a theme is {id, label, tokens}"));
    };
    let id = m.get("id").and_then(|v| v.as_str()).unwrap_or("");
    if !regex::Regex::new(r"^custom-[a-z0-9]{1,24}$").unwrap().is_match(id) {
        return Err(bad("a custom theme's id is custom-<letters and digits>"));
    }
    let label = m.get("label").and_then(|v| v.as_str()).map(|s| s.trim().to_string()).unwrap_or_default();
    if label.is_empty() || m["label"].as_str().unwrap().chars().count() > 40 {
        return Err(bad("give the theme a name (up to 40 characters)"));
    }
    let Some(tok) = m.get("tokens").and_then(|v| v.as_object()) else { return Err(bad("unknown theme token")) };
    if tok.keys().any(|k| !THEME_COLORS.contains(&k.as_str()) && k != "radius" && k != "glow") {
        return Err(bad("unknown theme token"));
    }
    let hex = regex::Regex::new(r"^#[0-9a-fA-F]{6}$").unwrap();
    let mut out = Map::new();
    for k in THEME_COLORS {
        match tok.get(k).and_then(|v| v.as_str()) {
            Some(c) if hex.is_match(c) => {
                out.insert(k.into(), json!(c.to_lowercase()));
            }
            _ => return Err(bad(format!("{k}: a colour like #1c1c1e"))),
        }
    }
    let r = match tok.get("radius") {
        None => 0,
        Some(Value::Number(n)) if n.as_i64().map(|x| (0..=14).contains(&x)).unwrap_or(false) => n.as_i64().unwrap(),
        _ => return Err(bad("corner radius is 0–14")),
    };
    let glow = match tok.get("glow") {
        None => false,
        Some(Value::Bool(b)) => *b,
        _ => return Err(bad("glow is on or off")),
    };
    out.insert("radius".into(), json!(r));
    out.insert("glow".into(), json!(glow));
    Ok(json!({"id": id, "label": label, "tokens": out}))
}

pub struct Hub {
    pub ctx: Arc<Ctx>,
    apps: RwLock<Vec<Arc<dyn App>>>,
    cache: Mutex<HashMap<String, (Instant, Value)>>,
    pub github: Login,
    pub terminals: Terminals,
    last_seen: Mutex<Instant>,
    asked: Mutex<HashMap<String, Instant>>, // repo -> when its latest release was last looked up from here
    pub host: Host,
    pub installer: Installer,
    pub port: AtomicU16,
}

impl Hub {
    pub fn new(dry_run: bool) -> Arc<Hub> {
        Hub::with(Arc::new(Config::new(None)), Arc::new(Runner::new(dry_run)), Host::real(), Arc::new(Sources::from_env()))
    }

    pub fn with(config: Arc<Config>, runner: Arc<Runner>, host: Host, sources: Arc<Sources>) -> Arc<Hub> {
        Arc::new_cyclic(|me: &Weak<Hub>| {
            let ctx = Arc::new(Ctx { config: config.clone(), runner: runner.clone(), releases: Arc::new(Releases::new()) });
            let github = Login::new(config.clone());
            let c2 = config.clone();
            net::set_login_provider(Arc::new(move || c2.get("github").get("token").and_then(|t| t.as_str()).unwrap_or("").to_string()));
            let r2 = runner.clone();
            let spawn: crate::engine::Spawn = Arc::new(move |argv: &[String]| r2.spawn(argv));
            let weak = me.clone();
            let on_change: crate::engine::OnChange = Arc::new(move |app_id: &str| {
                if let Some(h) = weak.upgrade() {
                    h.installed_changed(app_id);
                }
            });
            let installer = Installer::new(host.clone(), sources.clone(), crate::recipes::load(&host, &sources), runner.dry_run, spawn, Some(on_change));
            let apps = apps::load(&ctx);
            Hub { ctx, apps: RwLock::new(apps), cache: Mutex::new(HashMap::new()), github, terminals: Terminals::default(),
                  last_seen: Mutex::new(Instant::now()), asked: Mutex::new(HashMap::new()), host, installer, port: AtomicU16::new(0) }
        })
    }

    pub fn dry_run(&self) -> bool {
        self.ctx.dry_run()
    }

    pub fn seen(&self) {
        *self.last_seen.lock().unwrap() = Instant::now();
    }

    /// When a page last asked for anything.
    pub fn last_seen(&self) -> Instant {
        *self.last_seen.lock().unwrap()
    }

    fn installed_changed(&self, app_id: &str) {
        net::forget_ports();
        self.cache.lock().unwrap().remove(app_id);
        if let Ok(a) = self.app(app_id) {
            a.forget_found();
        }
    }

    pub fn reload(&self) {
        *self.apps.write().unwrap() = apps::load(&self.ctx);
        self.cache.lock().unwrap().clear();
    }

    fn apps(&self) -> Vec<Arc<dyn App>> {
        self.apps.read().unwrap().clone()
    }

    pub fn app(&self, id: &str) -> Res<Arc<dyn App>> {
        self.apps().into_iter().find(|a| a.id() == id).ok_or_else(|| Error::NotFound("no such app".into()))
    }

    fn enabled(&self, id: &str) -> bool {
        self.ctx.config.app(id).get("enabled").and_then(|v| v.as_bool()).unwrap_or(true)
    }

    fn status_of(&self, a: &Arc<dyn App>) -> Value {
        let id = a.id();
        if let Some((at, v)) = self.cache.lock().unwrap().get(&id) {
            if at.elapsed() < CACHE {
                return v.clone();
            }
        }
        let na = self.unavailable(&id);
        if !na.is_empty() {
            // it can't run on this system: nothing to look at (and nothing to run to look)
            let s = json!({"state": "off", "headline": na, "facts": [], "units": [], "checked": net::now()});
            self.cache.lock().unwrap().insert(id, (Instant::now(), s.clone()));
            return s;
        }
        let a2 = a.clone();
        let mut s = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| a2.status()))
            .unwrap_or_else(|_| json!({"state": "unknown", "headline": "Orbit couldn't read this app", "facts": [], "units": []}));
        if s.get("state").is_none() {
            s["state"] = json!("unknown");
        }
        if s.get("facts").is_none() {
            s["facts"] = json!([]);
        }
        let st = s["state"].as_str().unwrap_or("").to_string();
        if (st == "down" || st == "unknown") && s["version"].as_str().unwrap_or("").is_empty() && self.not_installed(&id) {
            // an app that was never installed isn't broken: grey, with the install button, not red
            s["state"] = json!("off");
            s["headline"] = json!("Not installed");
            s["notInstalled"] = json!(true);
        }
        s["checked"] = json!(net::now());
        self.cache.lock().unwrap().insert(id, (Instant::now(), s.clone()));
        s
    }

    fn not_installed(&self, id: &str) -> bool {
        self.installer.get(id).map(|r| !r.installed()["installed"].as_bool().unwrap_or(false)).unwrap_or(false)
    }

    /// "Not available on Windows yet" for an app that doesn't run on this system (and isn't installed anyway).
    fn unavailable(&self, id: &str) -> String {
        match self.installer.get(id) {
            Some(r) if r.support()["level"] == "none" && !r.installed()["installed"].as_bool().unwrap_or(false) => {
                format!("Not available on {} yet", crate::host::system_name(&self.host.system))
            }
            _ => String::new(),
        }
    }

    pub fn overview(self: &Arc<Hub>) -> Value {
        self.learn_latest(); // so a card can offer "update to …" without the install page being opened first
        let apps = self.apps();
        let mut waits = HashMap::new();
        for a in apps.iter().filter(|a| self.enabled(&a.id())) {
            let (tx, rx) = std::sync::mpsc::channel();
            let (h, a2) = (self.clone(), a.clone());
            std::thread::spawn(move || {
                let _ = tx.send(h.status_of(&a2));
            });
            waits.insert(a.id(), rx);
        }
        let mut out = vec![];
        for a in &apps {
            let id = a.id();
            let mut d = a.describe();
            d["enabled"] = json!(self.enabled(&id));
            d["install"] = self.install_brief(&id);
            if let Some(rx) = waits.get(&id) {
                let s = rx.recv_timeout(Duration::from_secs(8)).unwrap_or_else(|_| json!({"state": "unknown", "headline": "took too long to answer"}));
                d["state"] = s["state"].clone();
                d["headline"] = json!(s["headline"].as_str().unwrap_or(""));
                d["version"] = json!(s["version"].as_str().unwrap_or(""));
                d["update"] = json!(s["update"]["available"].as_bool().unwrap_or(false));
                d["line"] = json!(s["line"].as_str().unwrap_or(""));
                let na = self.unavailable(&id);
                if !na.is_empty() {
                    d["state"] = json!("off");
                    d["headline"] = json!(na);
                    d["unavailable"] = json!(true);
                } else if s["state"] == "ok" || s["state"] == "warn" || s["running"].as_bool().unwrap_or(false) {
                    if !a.open_label().is_empty() {
                        d["open"] = json!(a.open_label());
                    }
                    d["cardActions"] = json!(a.card_actions());
                }
            } else {
                d["state"] = json!("off");
                d["headline"] = json!("hidden (orbit settings → apps)");
                d["version"] = json!("");
                d["update"] = json!(false);
            }
            out.push(d);
        }
        json!(out)
    }

    pub fn detail(&self, id: &str) -> Res<Value> {
        let a = self.app(id)?;
        self.cache.lock().unwrap().remove(id);
        let mut d = a.describe();
        d["status"] = self.status_of(&a);
        let na = self.unavailable(id);
        if !na.is_empty() {
            let note = self.installer.get(id).map(|r| r.support()["note"].clone()).unwrap_or(Value::Null);
            d["status"]["state"] = json!("off");
            d["status"]["headline"] = json!(na);
            d["status"]["unavailable"] = json!(true);
            d["status"]["detail"] = note;
        }
        d["actions"] = json!(a.actions());
        d["settings"] = json!(a.settings());
        d["enabled"] = json!(self.enabled(id));
        d["install"] = self.install_brief(id);
        Ok(d)
    }

    // ---- installer -----------------------------------------------------------------------------
    fn install_brief(&self, id: &str) -> Value {
        let Some(r) = self.installer.get(id) else { return Value::Null };
        let (inst, sup) = (r.installed(), r.support());
        let latest = self.installer.sources.cached_with(r.repo(), &|x| r.wants(x)).map(|rel| crate::util::version_of(rel["tag"].as_str().unwrap_or(""))).unwrap_or_default();
        let (installed, version, pending) = (inst["installed"].as_bool().unwrap_or(false), inst["version"].as_str().unwrap_or(""),
                                             inst["pending"].as_str().unwrap_or(""));
        // "migrate": an app that replaces an older one (YTM Starcast: Pocketfader) offers to move it over even when the
        // version alone wouldn't say so. The recipe only says so while the old app is still the install here.
        let migrate = inst["migrate"].as_bool().unwrap_or(false);
        json!({"installed": installed, "version": version, "supported": sup["level"], "latest": latest,
               "busy": self.installer.busy(id), "pending": pending, "migrate": migrate,
               "update": installed && !latest.is_empty() && pending.is_empty()
                         && (migrate || (!version.is_empty() && net::is_newer(&latest, version)))})
    }

    /// First look: learn the latest versions in the background, once (then at most every 10 minutes per app).
    fn learn_latest(self: &Arc<Hub>) {
        if !self.ctx.config.get("checkUpdates").as_bool().unwrap_or(true) {
            return;
        }
        for r in self.installer.recipes.clone() {
            let repo = r.repo().to_string();
            let due = {
                let mut asked = self.asked.lock().unwrap();
                let due = self.installer.sources.cached(&repo).is_none()
                    && asked.get(&repo).map(|t| t.elapsed() > Duration::from_secs(600)).unwrap_or(true)
                    && r.support()["level"] != "none";
                if due {
                    asked.insert(repo.clone(), Instant::now()); // not again for 10 minutes, even when GitHub can't be reached
                }
                due
            };
            if due {
                let h = self.clone();
                std::thread::spawn(move || {
                    let _ = h.installer.sources.latest(&repo, false);
                });
            }
        }
    }

    pub fn install_list(self: &Arc<Hub>) -> Value {
        self.learn_latest();
        let mut out = vec![];
        for r in &self.installer.recipes {
            let mut d = recipe::describe(r.as_ref());
            if let Ok(a) = self.app(r.id()) {
                d["icon"] = a.describe()["icon"].clone();
            } else if r.self_app() {
                d["icon"] = json!("/icon.svg");
                d["self"] = json!(true);
            }
            if let (Some(m), Value::Object(b)) = (d.as_object_mut(), self.install_brief(r.id())) {
                m.extend(b);
            }
            d["installed"] = r.installed();
            out.push(d);
        }
        json!({"host": self.host.describe(), "dryRun": self.dry_run(), "apps": out})
    }

    pub fn install_refresh(self: &Arc<Hub>) -> Value {
        let handles: Vec<_> = self.installer.recipes.iter().map(|r| {
            let (h, repo) = (self.clone(), r.repo().to_string());
            std::thread::spawn(move || {
                let _ = h.installer.sources.latest(&repo, true);
            })
        }).collect();
        for h in handles {
            let _ = h.join();
        }
        self.install_list()
    }

    pub fn install_next(self: &Arc<Hub>, jid: &str, nid: &str) -> Res<Value> {
        let job = self.installer.job(jid).ok_or_else(|| Error::NotFound(jid.into()))?;
        let item = job.result()["next"].as_array().cloned().unwrap_or_default().into_iter().find(|n| n["id"] == nid);
        let Some(item) = item.filter(|i| ["url", "terminal", "shortcut", "program", "restart"].contains(&i["kind"].as_str().unwrap_or(""))) else {
            return Err(bad("that isn't available"));
        };
        let runner = &self.ctx.runner;
        let argv: Vec<String> = item["argv"].as_array().cloned().unwrap_or_default().iter().filter_map(|x| x.as_str().map(String::from)).collect();
        match item["kind"].as_str().unwrap_or("") {
            "restart" => self.restart(),
            "program" => {
                // a window the app itself installed (a recipe's own fixed path, never one from the request)
                if argv.is_empty() || !runner.launch(&argv) {
                    return Err(bad(format!("couldn't open it; run: {}", argv.join(" "))));
                }
                Ok(json!({"ok": true, "message": format!("opened {}", item["label"].as_str().unwrap_or(""))}))
            }
            "shortcut" => {
                // a Start-menu entry the install just made (a recipe's own path, never one from the request)
                let path = item["path"].as_str().unwrap_or("");
                if !path.to_lowercase().ends_with(".lnk") || !runner.open_file(path) {
                    return Err(bad("couldn't open it; look for it in the Start menu"));
                }
                let name = path.rsplit(['\\', '/']).next().unwrap_or(path);
                Ok(json!({"ok": true, "message": format!("opened {}", &name[..name.len() - 4])}))
            }
            "url" => {
                let url = item["url"].as_str().unwrap_or("");
                if !["http://127.0.0.1:", "https://127.0.0.1:", "http://localhost:"].iter().any(|p| url.starts_with(p)) {
                    return Err(bad("not a local address"));
                }
                runner.open_url(url);
                Ok(json!({"ok": true, "message": format!("opened {url}")}))
            }
            _ => {
                let term = terminal_argv(&argv, &job.app_name).ok_or_else(|| bad(format!("no terminal program found; run: {}", argv.join(" "))))?;
                runner.launch(&term);
                Ok(json!({"ok": true, "message": format!("opened a terminal: {}", argv.join(" "))}))
            }
        }
    }

    pub fn run_action(&self, a: &Arc<dyn App>, action: &str, args: &Map<String, Value>, cols: u16, rows: u16) -> Res<Value> {
        let spec = a.actions().into_iter().find(|x| x["id"] == action).ok_or_else(|| bad("that action isn't available right now"))?;
        if !spec["term"].as_bool().unwrap_or(false) {
            return a.run_action(action, args);
        }
        let cmd = a.term_command(action, args)?.ok_or_else(|| bad("unknown action"))?;
        let strs = |k: &str| -> Vec<String> { cmd[k].as_array().cloned().unwrap_or_default().iter().filter_map(|x| x.as_str().map(String::from)).collect() };
        let (argv, mask, cleanup) = (strs("argv"), strs("mask"), strs("cleanup"));
        let mut shown = cmd["shown"].as_str().map(String::from).unwrap_or_else(|| argv.join(" "));
        for s in &mask {
            shown = shown.replace(s.as_str(), "••••••");
        }
        if self.dry_run() && spec["changes"].as_bool().unwrap_or(false) {
            for p in &cleanup {
                let _ = std::fs::remove_file(p);
            }
            return Ok(json!({"ok": true, "message": format!("dry run: would run {shown}")}));
        }
        let title = cmd["title"].as_str().map(String::from).unwrap_or_else(|| spec["label"].as_str().unwrap_or("").to_string());
        let s = self.terminals.start(&argv, &title, StartArgs { cols, rows, cleanup, mask, shown: Some(shown.clone()) })?;
        Ok(json!({"ok": true, "term": s.id, "title": s.title, "message": format!("running {shown}")}))
    }

    pub fn hub_info(self: &Arc<Hub>) -> Value {
        let c = self.ctx.config.all();
        let mut themes: Vec<Value> = THEMES.iter().map(|(id, label)| json!({"id": id, "label": label})).collect();
        for t in c["themes"].as_array().cloned().unwrap_or_default() {
            let mut t = t;
            t["custom"] = json!(true);
            themes.push(t);
        }
        json!({"name": "Orbit", "version": crate::VERSION, "dryRun": self.dry_run(), "github": self.github.state(), "system": self.host.system,
               "orbitUpdate": self.orbit_update(), "menu": desktop_entry::state(), "themes": themes, "themeTokens": THEME_COLORS,
               "settings": {"theme": c["theme"], "refreshSec": c["refreshSec"], "checkUpdates": c["checkUpdates"], "autoUpdate": c["autoUpdate"],
                            "notifyApprovals": c["notifyApprovals"]},
               "apps": self.apps().iter().map(|a| { let mut d = a.describe(); d["enabled"] = json!(self.enabled(&a.id())); d }).collect::<Vec<_>>()})
    }

    pub fn set_hub(self: &Arc<Hub>, body: &Value) -> Res<Value> {
        let b = body.as_object().ok_or_else(|| bad("send an object"))?;
        let mut patch = Map::new();
        if let Some(themes) = b.get("themes") {
            let list = themes.as_array().filter(|l| l.len() <= 30).ok_or_else(|| bad("themes is a list (up to 30)"))?;
            let themes: Vec<Value> = list.iter().map(valid_theme).collect::<Res<_>>()?;
            let mut ids: Vec<&str> = themes.iter().map(|t| t["id"].as_str().unwrap()).collect();
            ids.sort();
            ids.dedup();
            if ids.len() != themes.len() {
                return Err(bad("two themes have the same id"));
            }
            patch.insert("themes".into(), json!(themes));
        }
        let builtin = |id: &str| THEMES.iter().any(|(t, _)| *t == id);
        let custom_ids = |themes: &Value| -> Vec<String> { themes.as_array().cloned().unwrap_or_default().iter().filter_map(|t| t["id"].as_str().map(String::from)).collect() };
        if let Some(theme) = b.get("theme") {
            let custom = custom_ids(patch.get("themes").unwrap_or(&self.ctx.config.get("themes")));
            let t = theme.as_str().unwrap_or("");
            if !(builtin(t) || custom.iter().any(|c| c == t)) {
                return Err(bad("unknown theme"));
            }
            patch.insert("theme".into(), json!(t));
        } else if let Some(themes) = patch.get("themes") {
            let cur = self.ctx.config.get("theme").as_str().unwrap_or("").to_string();
            if !(builtin(&cur) || custom_ids(themes).contains(&cur)) {
                patch.insert("theme".into(), json!("tui")); // the theme in use was deleted
            }
        }
        if let Some(v) = b.get("refreshSec") {
            match v.as_i64().filter(|n| v.is_i64() && (1..=60).contains(n)) {
                Some(n) => {
                    patch.insert("refreshSec".into(), json!(n));
                }
                None => return Err(bad("refresh must be 1–60 seconds")),
            }
        }
        if let Some(v) = b.get("autoUpdate") {
            patch.insert("autoUpdate".into(), json!(v.as_bool().ok_or_else(|| bad("autoUpdate is true or false"))?));
        }
        if let Some(v) = b.get("notifyApprovals") {
            patch.insert("notifyApprovals".into(), json!(v.as_bool().ok_or_else(|| bad("notifyApprovals is true or false"))?));
        }
        if let Some(v) = b.get("checkUpdates") {
            patch.insert("checkUpdates".into(), json!(v.as_bool().ok_or_else(|| bad("checkUpdates is true or false"))?));
        }
        if let Some(order) = b.get("order") {
            let mut given: Vec<String> = order.as_array().map(|o| o.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default();
            let mut ids: Vec<String> = self.apps().iter().map(|a| a.id()).collect();
            given.sort();
            ids.sort();
            if !order.is_array() || order.as_array().unwrap().len() != given.len() || given != ids {
                return Err(bad("order must list every app once"));
            }
            patch.insert("order".into(), order.clone());
        }
        if let Some(en) = b.get("enabled") {
            let ids: Vec<String> = self.apps().iter().map(|a| a.id()).collect();
            let ok = en.as_object().map(|m| m.iter().all(|(k, v)| ids.contains(k) && v.is_boolean())).unwrap_or(false);
            if !ok {
                return Err(bad("enabled is {app id: true/false}"));
            }
            for (k, v) in en.as_object().unwrap() {
                let mut p = Map::new();
                p.insert("enabled".into(), v.clone());
                self.ctx.config.update_app(k, p);
            }
        }
        if b.keys().any(|k| !["theme", "themes", "refreshSec", "checkUpdates", "autoUpdate", "notifyApprovals", "order", "enabled"].contains(&k.as_str())) {
            return Err(bad("unknown hub setting"));
        }
        let reorder = patch.contains_key("order");
        if !patch.is_empty() {
            self.ctx.config.update(patch);
        }
        if reorder {
            self.reload();
        }
        Ok(self.hub_info())
    }

    pub fn add_custom(&self, spec: &Value) -> Res<Value> {
        let spec = validate_custom(spec)?;
        let id = spec["id"].as_str().unwrap_or("").to_string();
        if self.app(&id).is_ok() {
            return Err(bad("an app with that id already exists"));
        }
        let mut custom = self.ctx.config.get("custom").as_array().cloned().unwrap_or_default();
        custom.push(spec);
        let mut m = Map::new();
        m.insert("custom".into(), json!(custom));
        self.ctx.config.update(m);
        self.reload();
        Ok(json!({"ok": true, "id": id}))
    }

    /// Start Orbit again (after it updated itself): the waiting new version takes this one's place, starts, and
    /// this one closes its window and ends.
    pub fn restart(&self) -> Res<Value> {
        use crate::recipes::orbit::{running_program, switch, waiting};
        if self.dry_run() {
            return Ok(json!({"ok": true, "message": "dry run: Orbit would restart now"}));
        }
        if self.installer.busy("orbit").is_some() {
            // its download would land after the switch, and wait as an update to the version just put in place
            return Err(bad("Orbit's update is still being downloaded: restart Orbit when it's ready"));
        }
        let busy: Vec<String> = self.installer.recipes.iter().filter(|r| r.id() != "orbit" && self.installer.busy(r.id()).is_some())
            .map(|r| r.name().to_string()).collect();
        if !busy.is_empty() {
            return Err(bad(format!("{} {} still being installed or removed: restart Orbit when that's done",
                                   busy.join(" and "), if busy.len() == 1 { "is" } else { "are" })));
        }
        let mut exe = running_program().ok_or_else(|| bad("Orbit can't find its own program; close it and open it yourself"))?;
        let before = exe.clone();
        if !waiting(Some(&exe)).is_empty() {
            match switch(Some(&exe)) {
                Ok(Some(p)) => exe = p, // the same file, or its new name (a download named with a version)
                Ok(None) => {}
                Err(e) => return Err(bad(format!("couldn't switch to the new Orbit ({e}); this one keeps running"))),
            }
        }
        // a program that moved (a name with its version in it → the plain name): the new Orbit has Orbit's own menu
        // entry follow it once it runs (on Windows that starts PowerShell, which must not hold up the switch)
        let moved_from = if exe != before { before.to_string_lossy().into_owned() } else { String::new() };
        start_again(&exe, &moved_from)?;
        Ok(json!({"ok": true, "message": "restarting Orbit…"}))
    }

    /// Install Orbit for this user (Linux, desktop_entry::install), not while its own update is being downloaded
    /// (that would land next to the old file). Copied to another drive, it starts again from the new place.
    pub fn install_for_user(&self) -> Res<Value> {
        if !self.dry_run() && self.installer.busy("orbit").is_some() {
            return Err(bad("Orbit's update is still being downloaded: install Orbit when it's ready"));
        }
        let mut r = desktop_entry::install(&self.ctx.runner);
        if r["copied"] == json!(true) {
            let program = std::path::PathBuf::from(r["program"].as_str().unwrap_or(""));
            match start_again(&program, "") {
                Ok(()) => r["message"] = json!(format!("{}; Orbit starts again from there…", r["message"].as_str().unwrap_or(""))),
                Err(_) => r["message"] = json!(format!("{}; close Orbit and open it from your apps menu", r["message"].as_str().unwrap_or(""))),
            }
        }
        Ok(r)
    }

    /// A newer Orbit on GitHub? Looked up in the background, never blocking. A newer Orbit already downloaded and
    /// waiting for a restart comes first.
    pub fn orbit_update(self: &Arc<Hub>) -> Value {
        let w = crate::recipes::orbit::waiting(None);
        if !w.is_empty() {
            return json!({"version": w, "url": ORBIT_RELEASES, "here": true, "pending": true});
        }
        if !self.ctx.config.get("checkUpdates").as_bool().unwrap_or(true) {
            return Value::Null;
        }
        // the newest release with a file for this computer (looked up in the background with the other apps'
        // releases); ARM and Mac programs are only built at milestones, so a newer release may not be for this one
        self.learn_latest();
        let Some(r) = self.installer.get("orbit") else { return Value::Null };
        let Some(rel) = self.installer.sources.cached_with(ORBIT_REPO, &|x| r.wants(x)) else { return Value::Null };
        let tag = rel["tag"].as_str().unwrap_or("");
        if !net::is_newer(tag, crate::VERSION) {
            return Value::Null;
        }
        let here = self.installer.get("orbit").map(|r| !r.requirements("install").iter().any(|x| x["blocking"].as_bool().unwrap_or(false))).unwrap_or(false);
        json!({"version": crate::util::version_of(tag), "url": rel["url"].as_str().filter(|u| !u.is_empty()).unwrap_or(ORBIT_RELEASES), "here": here})
    }

    /// Orbit updates itself (the "update Orbit by itself" setting, on unless turned off): shortly after it starts, a
    /// newer release is downloaded, checked and tried in the background, exactly as the update button does. It takes
    /// over the next time Orbit starts (the top bar offers "restart for orbit …" to switch now): nothing restarts by
    /// itself while it's in use.
    pub fn update_itself_later(self: &Arc<Hub>) {
        let h = self.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_secs(5));
            let on = |k: &str| h.ctx.config.get(k).as_bool().unwrap_or(true);
            if h.dry_run() || !on("autoUpdate") || !on("checkUpdates") {
                return;
            }
            // the installer's own source of releases (the one the update downloads from)
            let Some(r) = h.installer.get("orbit") else { return };
            let Ok(rel) = h.installer.sources.latest_with(ORBIT_REPO, true, &|x| r.wants(x)) else { return };
            let tag = rel["tag"].as_str().unwrap_or("").to_string();
            if !net::is_newer(&tag, crate::VERSION) {
                return;
            }
            if crate::recipes::orbit::running_program().map(|e| crate::recipes::orbit::deb_install(&e)).unwrap_or(false) {
                return; // the Debian package: installing needs the password, so the top bar offers it instead
            }
            if r.requirements("install").iter().any(|x| x["blocking"].as_bool().unwrap_or(false)) {
                return; // can't replace itself here (a folder it can't write to): the top bar offers the download page
            }
            // Looked at last, after asking GitHub: an update started by hand meanwhile is either still running or
            // already waiting (in that order, so one finishing in between is seen as waiting).
            if h.installer.busy("orbit").is_some() || !crate::recipes::orbit::waiting(None).is_empty() {
                return;
            }
            match h.installer.start("orbit", "install", &Value::Null) {
                Ok(_) => println!("Orbit {tag} is out: downloading it in the background; it takes over at the next start"),
                Err(e) => eprintln!("orbit: couldn't update by itself: {e}"),
            }
        });
    }

    /// While Orbit runs (Linux): a desktop notification when Nova has more waiting for approval than it had (a browser
    /// to add, or something a browser asked for), so it's seen with Orbit in the background. Silent, and off with
    /// the notifyApprovals setting. Nova 0.9.20 and newer report what's waiting (counts only).
    pub fn watch_approvals(self: &Arc<Hub>) {
        if !cfg!(target_os = "linux") || crate::runner::which("notify-send").is_none() {
            return;
        }
        let h = self.clone();
        std::thread::spawn(move || {
            let mut seen = (0u64, 0u64);
            loop {
                let on = h.ctx.config.get("notifyApprovals").as_bool().unwrap_or(true) && h.enabled("nova");
                let now = if on { h.app("nova").ok().map(|a| a.approvals_waiting()).unwrap_or((0, 0)) } else { (0, 0) };
                let text = match (now.0 > seen.0, now.1 > seen.1) {
                    (true, _) if now.0 == 1 => Some("A browser is asking to be added to Nova: approve it in Orbit with the code it shows."),
                    (true, _) => Some("Browsers are asking to be added to Nova: approve them in Orbit with the codes they show."),
                    (false, true) => Some("A browser is waiting for you to approve what it asked: approve it in Orbit with the code it shows."),
                    _ => None,
                };
                if let Some(text) = text {
                    let argv: Vec<String> = ["notify-send", "--app-name=Orbit", &format!("--icon={}", desktop_entry::APP_ID),
                        "--hint=boolean:suppress-sound:true", "--hint=string:desktop-entry:app.novalabs.orbit",
                        "Nova: approval waiting", text].iter().map(|s| s.to_string()).collect();
                    let _ = crate::runner::run_capture(&argv, Duration::from_secs(5), true);
                }
                seen = now;
                std::thread::sleep(Duration::from_secs(15)); // Nova updates what's waiting about every 15 s
            }
        });
    }

    pub fn github_access(&self) -> Value {
        let mut repos: Vec<String> = self.installer.recipes.iter().map(|r| r.repo().to_string()).collect();
        repos.push(ORBIT_REPO.into());
        repos.dedup();
        json!({"access": self.github.access(&repos), "state": self.github.state()})
    }

    pub fn check_updates(self: &Arc<Hub>) -> Value {
        for a in self.apps() {
            let repo = a.repo();
            if !repo.is_empty() {
                let h = self.clone();
                std::thread::spawn(move || {
                    h.ctx.releases.latest(&repo, true);
                });
            }
        }
        self.cache.lock().unwrap().clear();
        json!({"ok": true, "message": "checking for updates…"})
    }
}

// ---- HTTP ------------------------------------------------------------------------------------------------
struct Reply {
    code: u16,
    body: Vec<u8>,
    ctype: &'static str,
    csp: bool,
}

fn json_reply(code: u16, v: &Value) -> Reply {
    Reply { code, body: v.to_string().into_bytes(), ctype: "application/json; charset=utf-8", csp: false }
}

fn err_reply(e: &Error) -> Reply {
    match e {
        Error::NotFound(m) => json_reply(404, &json!({"error": if m.is_empty() { "not found" } else { m }})),
        Error::Bad(m) => json_reply(400, &json!({"error": m})),
        Error::Failed(m, h) if h.is_empty() => json_reply(400, &json!({"error": m})),
        Error::Failed(m, h) => json_reply(400, &json!({"error": m, "hint": h})),
        Error::Cancelled => json_reply(400, &json!({"error": "cancelled"})),
    }
}

fn query(url: &str) -> HashMap<String, String> {
    let q = url.split_once('?').map(|x| x.1).unwrap_or("");
    url::form_urlencoded::parse(q.as_bytes()).into_owned().collect()
}

fn caps(re: &str, path: &str) -> Option<Vec<String>> {
    let c = regex::Regex::new(re).unwrap().captures(path)?;
    Some(c.iter().map(|m| m.map(|m| m.as_str().to_string()).unwrap_or_default()).collect())
}

const APP_RE: &str = r"^/api/apps/([a-z0-9][a-z0-9-]{0,39})(/[a-z]+)?$";
const ICON_RE: &str = r"^/apps/([a-z0-9][a-z0-9-]{0,39}\.(?:svg|png))$";
const TERM_RE: &str = r"^/api/term/([0-9a-f]{12})(/input|/interrupt|/resize|/close)?$";
const INST_RE: &str = r"^/api/install/([a-z0-9][a-z0-9-]{0,39})(/plan|/start)?$";
const JOB_RE: &str = r"^/api/install/jobs/([0-9a-f]{12})(/cancel|/next)?$";

fn get(hub: &Arc<Hub>, url: &str) -> Res<Reply> {
    hub.seen();
    let path = url.split('?').next().unwrap_or("");
    let q = query(url);
    let from = || q.get("from").and_then(|f| f.parse::<usize>().ok()).unwrap_or(0);
    match path {
        "/" | "/index.html" => return Ok(Reply { code: 200, body: INDEX.to_vec(), ctype: "text/html; charset=utf-8", csp: true }),
        "/icon.svg" => return Ok(Reply { code: 200, body: ICON.to_vec(), ctype: "image/svg+xml", csp: false }),
        "/api/hub" => return Ok(json_reply(200, &hub.hub_info())),
        "/api/github" => return Ok(json_reply(200, &hub.github.state())),
        "/api/github/access" => return Ok(json_reply(200, &hub.github_access())),
        "/api/apps" => return Ok(json_reply(200, &json!({"apps": hub.overview(), "dryRun": hub.dry_run()}))),
        "/api/install" => return Ok(json_reply(200, &hub.install_list())),
        "/api/term" => return Ok(json_reply(200, &json!({"terminals": hub.terminals.list()}))),
        _ => {}
    }
    if let Some(c) = caps(ICON_RE, path) {
        if let Some((n, data)) = APP_ICONS.iter().find(|(n, _)| *n == c[1]) {
            return Ok(Reply { code: 200, body: data.to_vec(), ctype: if n.ends_with(".svg") { "image/svg+xml" } else { "image/png" }, csp: false });
        }
    }
    if let Some(c) = caps(TERM_RE, path).filter(|c| c[2].is_empty()) {
        return Ok(json_reply(200, &hub.terminals.get(&c[1])?.snapshot(from())));
    }
    if let Some(c) = caps(JOB_RE, path).filter(|c| c[2].is_empty()) {
        let job = hub.installer.job(&c[1]).ok_or_else(|| Error::NotFound("no such install".into()))?;
        return Ok(json_reply(200, &job.snapshot(from())));
    }
    if let Some(c) = caps(INST_RE, path).filter(|c| c[2].is_empty()) {
        let op = q.get("op").cloned().unwrap_or_else(|| "install".into());
        return Ok(json_reply(200, &recipe::info(&hub.installer.recipe(&c[1])?, &op, &Value::Null)?));
    }
    if path.starts_with("/api/jobs/") {
        return Err(Error::NotFound("no such job".into()));
    }
    if let Some(c) = caps(APP_RE, path) {
        if c[2].is_empty() {
            return Ok(json_reply(200, &hub.detail(&c[1])?));
        }
        if c[2] == "/logs" {
            let logs = hub.app(&c[1])?.logs();
            return Ok(Reply { code: 200, body: if logs.is_empty() { "(nothing logged)".into() } else { logs.into_bytes() },
                              ctype: "text/plain; charset=utf-8", csp: false });
        }
    }
    Err(Error::NotFound("not found".into()))
}

fn post(hub: &Arc<Hub>, url: &str, body: &Value) -> Res<Reply> {
    let path = url.split('?').next().unwrap_or("");
    let ok = |v: Value| Ok(json_reply(200, &v));
    let s = |k: &str| body.get(k).and_then(|v| v.as_str()).map(String::from);
    match path {
        "/api/hub" => return ok(hub.set_hub(body)?),
        "/api/hub/custom" => return ok(hub.add_custom(body)?),
        "/api/hub/check-updates" => {
            let h = hub.clone();
            std::thread::spawn(move || {
                h.ctx.releases.latest(ORBIT_REPO, true);
            });
            return ok(hub.check_updates());
        }
        "/api/hub/menu" => {
            // {} adds a launcher for this copy; on Linux, {"install": true} installs Orbit for this user, {"remove": true} removes that
            let flag = |k: &str| body.get(k).and_then(|v| v.as_bool()).unwrap_or(false);
            let r = &hub.ctx.runner;
            return ok(if flag("install") { hub.install_for_user()? } else if flag("remove") { desktop_entry::remove(r) } else { desktop_entry::add(r) });
        }
        "/api/hub/window" => {
            // the window's size when it closed (the Linux window build reports it here)
            let size = body.get("size").and_then(|v| v.as_array()).filter(|a| a.len() == 2)
                .and_then(|a| Some((a[0].as_i64()?, a[1].as_i64()?)))
                .filter(|(w, h)| (600..=10000).contains(w) && (400..=10000).contains(h))
                .ok_or_else(|| bad("size is [width, height]"))?;
            let mut m = Map::new();
            m.insert("window".into(), json!([size.0, size.1]));
            hub.ctx.config.update(m);
            return ok(json!({"ok": true}));
        }
        "/api/hub/restart" => return ok(hub.restart()?),
        "/api/hub/open-link" => {
            let url = link(&s("which").unwrap_or_default()).ok_or_else(|| bad("which link?"))?;
            if !hub.ctx.runner.open_url(&url) {
                return Err(bad(format!("couldn't open a browser; the address is {url}")));
            }
            return ok(json!({"ok": true, "message": format!("opened {url}")}));
        }
        "/api/hub/open-release" => {
            let u = hub.orbit_update()["url"].as_str().map(String::from).unwrap_or_else(|| ORBIT_RELEASES.into());
            hub.ctx.runner.open_url(&u);
            return ok(json!({"ok": true, "message": "opened Orbit's releases"}));
        }
        "/api/github/login" => return ok(hub.github.start().map_err(|e| bad(e.0))?),
        "/api/github/open" => {
            hub.ctx.runner.open_url("https://github.com/login/device");
            return ok(json!({"ok": true, "message": "opened github.com/login/device"}));
        }
        "/api/github/cancel" => {
            hub.github.cancel();
            return ok(hub.github.state());
        }
        "/api/github/logout" => return ok(hub.github.sign_out()),
        "/api/install/refresh" => return ok(hub.install_refresh()),
        _ => {}
    }
    if let Some(c) = caps(TERM_RE, path).filter(|c| !c[2].is_empty()) {
        let t = hub.terminals.get(&c[1])?;
        match c[2].as_str() {
            "/input" => {
                let text = s("text").filter(|t| t.chars().count() <= 4096).ok_or_else(|| bad("send text"))?;
                return ok(json!({"ok": t.write(&text)}));
            }
            "/interrupt" => t.interrupt(),
            "/resize" => {
                let n = |k: &str| body.get(k).and_then(|v| v.as_i64()).filter(|x| (10..=500).contains(x));
                let (Some(cols), Some(rows)) = (n("cols"), n("rows")) else { return Err(bad("cols and rows are 10–500")) };
                t.resize(cols as u16, rows as u16);
            }
            _ => hub.terminals.close(&t.id)?,
        }
        return ok(json!({"ok": true}));
    }
    if let Some(c) = caps(JOB_RE, path) {
        if c[2] == "/cancel" {
            return ok(hub.installer.cancel(&c[1])?.snapshot(0));
        }
        if c[2] == "/next" {
            let nid = s("id").ok_or_else(|| bad("which next step?"))?;
            return ok(hub.install_next(&c[1], &nid)?);
        }
    }
    if let Some(c) = caps(INST_RE, path).filter(|c| c[2] == "/plan" || c[2] == "/start") {
        let op = match body.get("op") {
            None => "install".to_string(),
            Some(Value::String(o)) => o.clone(),
            _ => return Err(bad("op is install or uninstall")),
        };
        let options = body.get("options").cloned().unwrap_or(Value::Null);
        let r = hub.installer.recipe(&c[1])?;
        if c[2] == "/plan" {
            return ok(recipe::info(&r, &op, &options)?);
        }
        return ok(hub.installer.start(&c[1], &op, &options)?.snapshot(0));
    }
    if let Some(c) = caps(APP_RE, path).filter(|c| c[2] == "/settings" || c[2] == "/action") {
        let a = hub.app(&c[1])?;
        let res = if c[2] == "/settings" {
            apps::apply_settings(a.as_ref(), body)?
        } else {
            let action = s("action").filter(|a| a.chars().count() <= 40).ok_or_else(|| bad("which action?"))?;
            let args = match body.get("args") {
                None | Some(Value::Null) => Map::new(),
                Some(Value::Object(m)) => m.clone(),
                _ => return Err(bad("args must be an object")),
            };
            let n = |k: &str, d: i64| body.get(k).and_then(|v| v.as_i64()).unwrap_or(d);
            let (mut cols, mut rows) = (n("cols", 100), n("rows", 30));
            if !(10..=500).contains(&cols) || !(10..=500).contains(&rows) {
                (cols, rows) = (100, 30);
            }
            net::forget_ports(); // it may have started or stopped something: look again
            let res = hub.run_action(&a, &action, &args, cols as u16, rows as u16)?;
            if res["removed"].as_bool().unwrap_or(false) {
                hub.reload();
            }
            res
        };
        hub.cache.lock().unwrap().remove(&a.id());
        return ok(res);
    }
    Err(Error::NotFound("not found".into()))
}

fn header(req: &tiny_http::Request, name: &str) -> Option<String> {
    req.headers().iter().find(|h| h.field.as_str().as_str().eq_ignore_ascii_case(name)).map(|h| h.value.as_str().to_string())
}

fn handle(hub: &Arc<Hub>, mut req: tiny_http::Request) {
    let port = hub.port.load(Ordering::Relaxed);
    let ok_hosts = [format!("127.0.0.1:{port}"), format!("localhost:{port}")];
    let reply = (|| -> Reply {
        let host = header(&req, "Host").unwrap_or_default();
        if !ok_hosts.contains(&host) {
            return json_reply(403, &json!({"error": "forbidden host"}));
        }
        if let Some(origin) = header(&req, "Origin") {
            if !ok_hosts.iter().any(|h| origin == format!("http://{h}")) {
                return json_reply(403, &json!({"error": "forbidden origin"}));
            }
        }
        let url = req.url().to_string();
        let r = match req.method() {
            tiny_http::Method::Get | tiny_http::Method::Head => get(hub, &url),
            tiny_http::Method::Post => (|| {
                if !header(&req, "Content-Type").unwrap_or_default().starts_with("application/json") {
                    return Err(bad("send JSON"));
                }
                let n: usize = header(&req, "Content-Length").and_then(|n| n.parse().ok()).unwrap_or(0);
                if n > 64 << 10 {
                    return Err(bad("too large"));
                }
                let mut buf = Vec::new();
                req.as_reader().take(n as u64).read_to_end(&mut buf).map_err(|e| bad(e.to_string()))?;
                let body: Value = if buf.is_empty() { json!({}) } else { serde_json::from_slice(&buf).map_err(|_| bad("invalid JSON"))? };
                post(hub, &url, &body)
            })(),
            _ => Err(Error::NotFound("not found".into())),
        };
        r.unwrap_or_else(|e| err_reply(&e))
    })();
    let mut resp = tiny_http::Response::from_data(reply.body).with_status_code(reply.code);
    let mut add = |k: &str, v: &str| {
        if let Ok(h) = tiny_http::Header::from_bytes(k.as_bytes(), v.as_bytes()) {
            resp.add_header(h);
        }
    };
    add("Content-Type", reply.ctype);
    add("Cache-Control", "no-store");
    add("X-Content-Type-Options", "nosniff");
    add("Referrer-Policy", "no-referrer");
    add("Server", "orbit");
    if reply.csp {
        add("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; \
                                         connect-src 'self'; frame-ancestors 'none'");
    }
    let _ = req.respond(resp);
}

/// Listen on 127.0.0.1 (port 0 = any free port) and answer in the background. Returns the port.
/// Start `exe` as the new Orbit (it waits for this one to let go of the port), then close the window and end.
fn start_again(exe: &std::path::Path, moved_from: &str) -> Res<()> {
    let mut argv = vec![exe.to_string_lossy().into_owned()];
    argv.extend(std::env::args().skip(1));
    crate::runner::detached(&argv, &[("ORBIT_RESTARTED", "1"), ("ORBIT_MOVED_FROM", moved_from)])
        .map_err(|e| bad(format!("couldn't start Orbit again: {e}; close it and open it yourself")))?;
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(800));
        crate::window::close();
        std::process::exit(0);
    });
    Ok(())
}

pub fn serve(hub: &Arc<Hub>, port: u16) -> std::io::Result<u16> {
    let server = tiny_http::Server::http(("127.0.0.1", port)).map_err(|e| std::io::Error::new(std::io::ErrorKind::AddrInUse, e.to_string()))?;
    let actual = server.server_addr().to_ip().map(|a| a.port()).unwrap_or(port);
    hub.port.store(actual, Ordering::Relaxed);
    let server = Arc::new(server);
    for i in 0..8 {
        let (s, h) = (server.clone(), hub.clone());
        std::thread::Builder::new().name(format!("orbit-http-{i}")).spawn(move || {
            for req in s.incoming_requests() {
                let h2 = h.clone();
                // a bug in one request must not take the server down
                if std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| handle(&h2, req))).is_err() {
                    eprintln!("orbit: a request hit a problem");
                }
            }
        })?;
    }
    Ok(actual)
}
