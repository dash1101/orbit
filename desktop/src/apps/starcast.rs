//! YTM Starcast: a YouTube Music companion. A phone remote (volume, outputs, EQ and the YouTube Music tab), the music
//! as a Discord status, and Refine inside YouTube Music. A user service with a local JSON API on port 8777.
//!
//! YTM Starcast is what Pocketfader and YTM Beacon became. Until a computer has moved over, this page also works with
//! the Pocketfader that still runs there: same address, same API, and its service is used for start/stop and the log.
use super::{discord, field, open_url, release_info, run_unit_action, unit_actions, unit_states, with, App, Ctx};
use crate::net;
use crate::systemd;
use crate::util::{bad, Res};
use serde_json::{json, Map, Value};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub const DEFAULT_URL: &str = "http://127.0.0.1:8777";
const UNIT: &str = "ytm-starcast.service";
const LEGACY_UNIT: &str = "pocketfader.service"; // Pocketfader, before the computer moved over to YTM Starcast
/// YTM Starcast's parts, each with its own switch (`GET/POST /api/parts`): key -> (label, help)
const PARTS: [(&str, &str, &str); 4] = [
    ("remote", "YouTube Music remote", "Phones and the desktop window may control the YouTube Music tab."),
    ("discord", "Discord status", "Show what you play on YouTube Music as your Discord status."),
    ("refine", "Refine", "Improvements inside YouTube Music itself (its own options are in the page's ✦ panel)."),
    ("audio", "This computer's audio", "Volume, outputs and the equalizer in YTM Starcast's apps."),
];
const HIDE_FOR: [i64; 3] = [30, 60, 180];

pub struct Starcast {
    ctx: Arc<Ctx>,
    found: Mutex<Option<(Instant, String)>>, // which service runs it here (forgotten after an install)
    beacon: Mutex<Option<Value>>,            // the last Discord status payload, so the buttons match what it is doing
}

fn truthy(v: &Value) -> bool {
    !(v.is_null() || v == &json!(false) || v == &json!("") || v == &json!(0) || v == &json!([]) || v == &json!({}))
}

fn s_of(v: &Value) -> String {
    v.as_str().map(String::from).unwrap_or_else(|| if v.is_null() { String::new() } else { v.to_string() })
}

impl Starcast {
    pub fn new(ctx: Arc<Ctx>) -> Starcast {
        Starcast { ctx, found: Mutex::new(None), beacon: Mutex::new(None) }
    }

    fn base(&self) -> String {
        let url = self.hub().get("url").and_then(|u| u.as_str()).filter(|u| !u.is_empty()).unwrap_or(DEFAULT_URL).trim_end_matches('/').to_string();
        match url::Url::parse(&url) {
            Ok(u) if u.scheme() == "http" || u.scheme() == "https" => url,
            _ => DEFAULT_URL.into(),
        }
    }

    fn headers(&self) -> Vec<(&'static str, String)> {
        let tok = self.hub().get("token").and_then(|t| t.as_str()).unwrap_or("").to_string();
        if tok.is_empty() { vec![] } else { vec![("Authorization", format!("Bearer {tok}"))] }
    }

    fn get(&self, path: &str, timeout: f64) -> Result<Value, net::HttpError> {
        net::get_json(&format!("{}{path}", self.base()), &net::Req { timeout, headers: self.headers(), insecure: false })
    }

    fn post(&self, path: &str, body: &Value, timeout: f64) -> Result<Value, net::HttpError> {
        net::post_json(&format!("{}{path}", self.base()), body, &net::Req { timeout, headers: self.headers(), insecure: false })
    }

    /// YTM Starcast's Discord status payload, or None (not running, or too old to have one).
    fn discord_payload(&self) -> Option<Value> {
        self.get("/api/beacon", 1.5).ok().filter(|b| b.is_object())
    }

    /// Whether a newer YTM Starcast exists: Orbit's own release check, else what the app itself says when Orbit's
    /// check is off.
    fn update_info(&self, s: &mut Map<String, Value>) {
        let v = s.get("version").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if let Some(rel) = release_info(self, &v, false) {
            s.insert("update".into(), rel);
            return;
        }
        if let Ok(up) = self.get("/api/update", 1.5) {
            s.insert("update".into(), json!({"latest": up["latest"], "url": format!("https://github.com/{}/releases", self.repo()),
                                             "available": truthy(&up["available"])}));
        }
    }

    /// Discord status lines. Discord's problems never change the app's own state (the line is coloured instead).
    fn discord_facts(facts: &mut Vec<Value>, b: Option<&Value>, parts: &Value) {
        let off = parts["discord"] == json!(false) || b.is_none() || b.map(|b| b["reachable"] == json!(false)).unwrap_or(true);
        if off {
            facts.push(json!(["discord status", "off", "muted"]));
            if let Some(b) = b {
                if truthy(&b["beaconApp"]["running"]) {
                    facts.push(json!(["old ytm beacon", "still running: move over to YTM Starcast", "warn"]));
                }
            }
            return;
        }
        let b = b.unwrap();
        let (code, tone, text) = discord::describe(b);
        let t = match tone.as_str() { "warn" => json!("warn"), "off" => json!("muted"), _ => Value::Null };
        facts.push(json!(["discord status", text, t]));
        let state = &b["discord"]["state"];
        if truthy(state) && code != "beacon-app" {
            facts.push(json!(["discord", s_of(state), if state == "connected" { Value::Null } else { json!("warn") }]));
        }
    }

    /// The Discord card's settings: only those the payload has (so Orbit follows what YTM Starcast accepts).
    fn discord_settings(b: &Value, master: bool) -> Vec<Value> {
        let c = &b["settings"];
        let mut card = vec![];
        if master {
            card.push(with(field("discord.visible", "Show on Discord", "bool", json!(truthy(&b["visible"])), "app"),
                           json!({"help": "Master switch. Turning it on also ends a timed hide."})));
        }
        let rows: [(&str, &str, &str, Value); 6] = [
            ("privateMode", "Private mode", "bool", json!({"help": "Only “Listening to music”: no song, artist, art or button."})),
            ("statusDisplay", "Name in member list", "choice", json!({"options": [["name", "app name"], ["state", "artist"], ["details", "song title"]]})),
            ("showAlbumArt", "Album art", "bool", json!({})),
            ("showProgress", "Progress bar", "bool", json!({})),
            ("showStateIcon", "Play/pause badge", "bool", json!({})),
            ("showButtons", "“Listen” button", "bool", json!({})),
        ];
        for (key, label, type_, extra) in rows {
            if c.get(key).is_some() {
                let value = if type_ == "bool" { json!(truthy(&c[key])) } else { c[key].clone() };
                card.push(with(field(&format!("discord.{key}"), label, type_, value, "app"), extra));
            }
        }
        let mut paused = vec![];
        if c.get("showPaused").is_some() {
            paused.push(field("discord.showPaused", "Keep showing while paused", "bool", json!(truthy(&c["showPaused"])), "app"));
        }
        if c.get("pauseTimeoutMinutes").is_some() {
            paused.push(with(field("discord.pauseTimeoutMinutes", "Hide after (minutes, 0 = never)", "int",
                                   json!(c["pauseTimeoutMinutes"].as_i64().unwrap_or(0)), "app"), json!({"min": 0, "max": 1440})));
        }
        let mut groups: Vec<Value> = [json!({"title": "discord card", "fields": card}), json!({"title": "when paused", "fields": paused})]
            .into_iter().filter(|g| g["fields"].as_array().map(|f| !f.is_empty()).unwrap_or(false)).collect();
        if c.get("hideMatching").is_some() {
            let words = c["hideMatching"].as_array().cloned().unwrap_or_default();
            groups.push(json!({"title": "never show", "fields": [with(field("discord.hideMatching", "Songs containing", "words", json!(words), "app"),
                json!({"help": "Comma-separated. A song whose title, artist or album contains one of these is never shown."}))]}));
        }
        groups
    }

    /// YTM Starcast's desktop window, wherever it was installed: YTM Starcast's places first, then Pocketfader's.
    pub fn gui_paths() -> Vec<PathBuf> {
        let home = crate::config::home();
        let local = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(|| home.join("AppData").join("Local"));
        let mut out = vec![];
        for (cmd, folder, win) in [("ytm-starcast-gui", "ytm-starcast", "YTM Starcast"), ("pocketfader-gui", "pocketfader", "Pocketfader")] {
            if let Some(p) = crate::runner::which(cmd) {
                out.push(p);
            }
            out.push(home.join(".local/share").join(folder).join("venv/bin").join(cmd));
            out.push(local.join("Programs").join(win).join(format!("{cmd}.exe")));
            out.push(home.join("Library/Application Support").join(win).join("venv/bin").join(cmd));
        }
        out
    }

    /// The old YTM Beacon program, when it is still on this computer.
    pub fn beacon_binary() -> Option<PathBuf> {
        if let Some(p) = crate::runner::which("ytm-beacon") {
            return Some(p);
        }
        let home = crate::config::home();
        let p = if cfg!(windows) {
            std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(|| home.join("AppData").join("Local")).join("ytm-beacon").join("ytm-beacon.exe")
        } else {
            home.join(".local/bin/ytm-beacon")
        };
        p.exists().then_some(p)
    }
}

impl App for Starcast {
    fn ctx(&self) -> &Ctx {
        &self.ctx
    }
    fn id(&self) -> String {
        "ytm-starcast".into()
    }
    fn name(&self) -> String {
        "YTM Starcast".into()
    }
    fn blurb(&self) -> String {
        "YouTube Music companion: a phone remote, your Discord status and a better YouTube Music".into()
    }
    fn glyph(&self) -> String {
        "S".into()
    }
    fn color(&self) -> String {
        "#38d9c4".into()
    }
    fn repo(&self) -> String {
        "dash1101/ytm-starcast".into()
    }
    fn homepage(&self) -> String {
        "https://github.com/dash1101/ytm-starcast".into()
    }
    fn icon(&self) -> String {
        "ytm-starcast.svg".into()
    }
    fn open_label(&self) -> String {
        "open web app".into()
    }
    fn forget_found(&self) {
        *self.found.lock().unwrap() = None;
    }

    /// ytm-starcast.service, or Pocketfader's service while that is the one this computer has (before the move).
    fn units(&self) -> Vec<(String, bool)> {
        if let Some((until, unit)) = self.found.lock().unwrap().clone() {
            if until > Instant::now() {
                return vec![(unit, true)];
            }
        }
        let r = &self.ctx.runner;
        let mut unit = UNIT.to_string();
        if systemd::here() && systemd::show(r, UNIT, true)["state"] == "missing"
            && systemd::show(r, LEGACY_UNIT, true)["state"].as_str().map(|s| s != "missing").unwrap_or(false) {
            unit = LEGACY_UNIT.into();
        }
        *self.found.lock().unwrap() = Some((Instant::now() + Duration::from_secs(10), unit.clone()));
        vec![(unit, true)]
    }

    fn hub_fields(&self) -> Vec<Value> {
        let h = self.hub();
        let g = |k: &str| h.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
        let url = if g("url").is_empty() { DEFAULT_URL.to_string() } else { g("url") };
        vec![
            with(field("url", "Address", "text", json!(url), "hub"), json!({"help": "Where YTM Starcast answers on this computer."})),
            with(field("token", "Access token", "secret", json!(g("token")), "hub"),
                 json!({"help": "Only needed when YTM Starcast runs with --lan, --bind or --token."})),
        ]
    }

    fn status(&self) -> Value {
        let units = unit_states(self);
        let unit = units.first().cloned().unwrap_or(json!({}));
        let mut facts: Vec<Value> = vec![];
        let mut s = Map::new();
        s.insert("units".into(), json!(units));
        let info = match self.get("/api/info", 1.5) {
            Ok(i) if truthy(&i) => i,
            other => {
                *self.beacon.lock().unwrap() = None;
                let err = other.err().map(|e| e.message).unwrap_or_default();
                let st = unit["state"].as_str().unwrap_or("");
                if st == "stopped" || st == "failed" {
                    s.insert("state".into(), json!(if st == "failed" { "down" } else { "off" }));
                    s.insert("headline".into(), json!(if st == "stopped" { "Not running" } else { "The service failed: see the log" }));
                } else {
                    s.insert("state".into(), json!("down"));
                    s.insert("headline".into(), json!(format!("Can't reach YTM Starcast at {}", self.base())));
                    s.insert("detail".into(), json!(err));
                }
                s.insert("facts".into(), json!(facts));
                return Value::Object(s);
            }
        };
        s.insert("version".into(), json!(s_of(&info["version"])));
        self.update_info(&mut s);
        let done = |mut s: Map<String, Value>, facts: Vec<Value>, state: &str, headline: String| {
            s.insert("state".into(), json!(state));
            s.insert("headline".into(), json!(headline));
            s.insert("facts".into(), json!(facts));
            Value::Object(s)
        };
        if truthy(&info["auth"]) && self.hub().get("token").and_then(|t| t.as_str()).unwrap_or("").is_empty() {
            return done(s, facts, "warn", "Running, but it needs an access token (settings → connection)".into());
        }
        let st = match self.get("/api/state", 1.5) {
            Ok(v) => v,
            Err(e) => return done(s, facts, "warn", format!("Running, but /api/state failed: {}", e.message)),
        };
        let (vol, media, caps, parts) = (&st["volume"], &st["media"], &st["capabilities"], &st["parts"]);
        let audio_off = parts["audio"] == json!(false);
        if audio_off {
            facts.push(json!(["computer audio", "switched off in YTM Starcast", "muted"]));
        } else if truthy(&vol["available"]) {
            let dev = s_of(&vol["device"]);
            facts.push(json!(["output", if dev.is_empty() { "—".to_string() } else { dev }]));
            let pct = if vol["percent"].is_null() { "?".to_string() } else { s_of(&vol["percent"]) };
            facts.push(json!(["volume", format!("{}{pct}%", if truthy(&vol["muted"]) { "muted · " } else { "" })]));
        } else {
            facts.push(json!(["output", "no output device", "warn"]));
        }
        let ytm = self.get("/api/ytm/state", 1.5).unwrap_or(json!({}));
        let now = if truthy(&ytm["connected"]) { ytm["now"].clone() } else { json!({}) };
        let pick = |a: &Value, b: &Value| if truthy(a) { s_of(a) } else { s_of(b) };
        let title = pick(&now["title"], &media["title"]);
        if !title.is_empty() {
            let artist = pick(&now["artist"], &media["artist"]);
            let playing = if truthy(&now) { truthy(&now["playing"]) } else { truthy(&media["playing"]) };
            let by = if artist.is_empty() { String::new() } else { format!(" — {artist}") };
            facts.push(json!([if playing { "playing" } else { "paused" }, format!("{title}{by}")]));
            s.insert("line".into(), json!(format!("{}{title}{by}", if playing { "▶ " } else { "❚❚ " })));
        }
        let b = self.discord_payload();
        *self.beacon.lock().unwrap() = b.clone();
        if truthy(&caps["ytm_bridge"]) {
            if truthy(&ytm["connected"]) {
                facts.push(json!(["browser extension", "connected"]));
            } else {
                let seen = b.as_ref().and_then(|b| b["extension"]["lastSeenAgoSec"].as_f64());
                facts.push(json!(["browser extension", match seen { None => "not connected".to_string(), Some(x) => format!("last seen {}", discord::ago(Some(x))) }, "muted"]));
            }
        }
        Self::discord_facts(&mut facts, b.as_ref(), parts);
        if !audio_off {
            let eq = if truthy(&caps["eq"]) { if truthy(&caps["engine_label"]) { s_of(&caps["engine_label"]) } else { "on".into() } } else { "not available".into() };
            facts.push(json!(["eq", eq]));
        }
        let sleep = st["sleep"]["remainingSec"].as_f64().unwrap_or(0.0);
        if sleep > 0.0 {
            facts.push(json!(["sleep timer", format!("{} min left", sleep as i64 / 60)]));
        }
        if let Some(b) = b.as_ref().filter(|b| b["reachable"] != json!(false)) {
            let more = discord::details(b);
            if !more.is_empty() {
                s.insert("more".into(), json!(more));
            }
        }
        let device = if !audio_off && truthy(&vol["available"]) { s_of(&vol["device"]) } else { String::new() };
        if unit["state"] == "missing" {
            facts.push(json!(["service", "not installed as a user service", "muted"]));
        } else if unit["unit"] == LEGACY_UNIT {
            facts.push(json!(["service", "still Pocketfader's: update to move it over to YTM Starcast", "muted"]));
        }
        done(s, facts, "ok", format!("Running{}", if device.is_empty() { String::new() } else { format!(" · {device}") }))
    }

    fn settings(&self) -> Vec<Value> {
        let mut groups = vec![];
        let Ok(prefs) = self.get("/api/prefs", 1.5) else {
            groups.push(json!({"title": "ytm starcast", "fields": [field("_note", "", "note", json!("Start YTM Starcast to change its settings."), "app")]}));
            groups.push(json!({"title": "connection", "fields": self.hub_fields()}));
            return groups;
        };
        let mut fields = vec![with(field("pauseOthers", "Pause other players", "bool", json!(truthy(&prefs["pauseOthers"])), "app"),
                                   json!({"help": "When YouTube Music starts playing, pause every other player on the computer."}))];
        let parts = self.get("/api/parts", 1.5).ok().map(|p| p["parts"].clone()).filter(|p| p.is_object()).unwrap_or(json!({})); // older: no switches
        for (key, label, help) in PARTS {
            if let Some(b) = parts[key].as_bool() {
                fields.push(with(field(&format!("parts.{key}"), label, "bool", json!(b), "app"), json!({"help": help})));
            }
        }
        fields.push(field("_note", "", "note", json!("Everything else (outputs, EQ, YouTube Music) is in YTM Starcast itself: use “open web app”."), "app"));
        groups.push(json!({"title": "ytm starcast", "fields": fields}));
        if let Some(b) = self.discord_payload() {
            if b["reachable"] != json!(false) && b["settings"].is_object() {
                groups.extend(Self::discord_settings(&b, parts.get("discord").is_none()));
            }
        }
        groups.push(json!({"title": "connection", "fields": self.hub_fields()}));
        groups
    }

    fn apply_app_settings(&self, patch: Map<String, Value>) -> Res<()> {
        if self.ctx.dry_run() {
            return Ok(());
        }
        let (mut prefs, mut parts, mut disc, mut visible) = (Map::new(), Map::new(), Map::new(), None);
        for (k, v) in patch {
            if k == "discord.visible" {
                visible = Some(v);
            } else if let Some(x) = k.strip_prefix("discord.") {
                disc.insert(x.into(), v);
            } else if let Some(x) = k.strip_prefix("parts.") {
                parts.insert(x.into(), v);
            } else {
                prefs.insert(k, v);
            }
        }
        let refused = |e: net::HttpError| bad(format!("YTM Starcast refused: {}", e.message));
        if !parts.is_empty() {
            self.post("/api/parts", &Value::Object(parts), 3.0).map_err(refused)?;
        }
        if let Some(v) = visible {
            self.post("/api/beacon/visibility", &json!({"action": if truthy(&v) { "show" } else { "hide" }}), 3.0).map_err(refused)?;
        }
        if !disc.is_empty() {
            self.post("/api/beacon/settings", &Value::Object(disc), 3.0).map_err(refused)?;
        }
        if !prefs.is_empty() {
            self.post("/api/prefs", &Value::Object(prefs), 3.0).map_err(refused)?;
        }
        Ok(())
    }

    fn actions(&self) -> Vec<Value> {
        let mut acts = vec![
            json!({"id": "open", "label": "open web app", "group": "open", "primary": true}),
            json!({"id": "gui", "label": "desktop window", "group": "open"}),
            json!({"id": "releases", "label": "releases", "group": "open"}),
        ];
        let b = self.beacon.lock().unwrap().clone();
        match b.filter(|b| b["reachable"] != json!(false)) {
            Some(b) => {
                if truthy(&b["beaconApp"]["canMigrate"]) {
                    acts.push(json!({"id": "migrate-beacon", "label": "move over from YTM Beacon", "group": "discord", "primary": true,
                        "confirm": "Move your Discord status over from the old YTM Beacon app? YTM Starcast removes YTM Beacon (its settings are \
                                    kept for YTM Starcast) and shows your status itself."}));
                } else if b["visible"] == json!(false) {
                    acts.push(json!({"id": "show", "label": "show now", "group": "discord", "primary": true}));
                } else {
                    for m in HIDE_FOR {
                        acts.push(json!({"id": format!("hide:{m}"), "label": if m < 60 { format!("hide {m} min") } else { format!("hide {} h", m / 60) }, "group": "discord"}));
                    }
                }
            }
            None => {
                if Self::beacon_binary().is_some() {
                    acts.push(json!({"id": "remove-beacon", "label": "remove YTM Beacon", "group": "maintenance", "term": true, "changes": true,
                        "confirm": "Remove the old YTM Beacon app? YTM Starcast shows your Discord status instead. Its settings are kept, so YTM \
                                    Starcast can take them over."}));
                }
            }
        }
        acts.extend(unit_actions(self));
        acts
    }

    fn run_action(&self, action: &str, _args: &Map<String, Value>) -> Res<Value> {
        let dry = if self.ctx.dry_run() { " (dry run)" } else { "" };
        match action {
            "open" => return open_url(self, &format!("{}/", self.base())),
            "gui" => {
                for exe in Self::gui_paths() {
                    if exe.exists() {
                        if !self.ctx.runner.launch(&[exe.to_string_lossy().into_owned()]) {
                            return Err(bad("couldn't open YTM Starcast's window"));
                        }
                        return Ok(json!({"ok": true, "message": "opened YTM Starcast's window"}));
                    }
                }
                return Err(bad("YTM Starcast's window isn't installed"));
            }
            "releases" => return open_url(self, &format!("{}/releases", self.homepage())),
            "migrate-beacon" => {
                if self.ctx.dry_run() {
                    return Ok(json!({"ok": true, "message": "dry run: would move over from YTM Beacon"}));
                }
                let r = self.post("/api/beacon/migrate", &json!({}), 60.0).map_err(|e| {
                    // YTM Starcast explains each refusal in its reply: show its words
                    if !e.text.is_empty() {
                        return bad(e.text);
                    }
                    bad(match e.message.as_str() {
                        "nothing_to_move" => "There's nothing to move over: YTM Beacon isn't installed here.".to_string(),
                        "uninstall_failed" => "YTM Beacon's uninstaller stopped with an error. Remove it with `ytm-beacon uninstall`, then try again.".to_string(),
                        other => format!("YTM Starcast: {other}"),
                    })
                })?;
                *self.beacon.lock().unwrap() = None;
                net::forget_ports();
                let msg = r["message"].as_str().filter(|m| !m.is_empty()).unwrap_or("moved over from YTM Beacon").to_string();
                return Ok(json!({"ok": true, "message": msg}));
            }
            _ => {}
        }
        let hide = action.strip_prefix("hide:").and_then(|m| m.parse::<i64>().ok()).filter(|m| HIDE_FOR.contains(m));
        if action == "show" || hide.is_some() {
            let body = match hide { Some(m) => json!({"action": "hide", "minutes": m}), None => json!({"action": "show"}) };
            if !self.ctx.dry_run() {
                self.post("/api/beacon/visibility", &body, 3.0).map_err(|e| bad(format!("YTM Starcast refused: {}", e.message)))?;
            }
            let what = match hide { Some(m) => format!("hidden for {m} min"), None => "shown on Discord".into() };
            return Ok(json!({"ok": true, "message": format!("{what}{dry}")}));
        }
        if action.starts_with("svc:") {
            return run_unit_action(self, action);
        }
        Err(bad("unknown action"))
    }

    fn term_command(&self, action: &str, _args: &Map<String, Value>) -> Res<Option<Value>> {
        if action != "remove-beacon" {
            return Ok(None);
        }
        let exe = Self::beacon_binary().ok_or_else(|| bad("YTM Beacon isn't installed"))?;
        Ok(Some(json!({"argv": [exe.to_string_lossy(), "uninstall"], "title": "YTM Beacon · remove"})))
    }
}
