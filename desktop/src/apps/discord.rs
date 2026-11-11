//! Discord status: what YTM Starcast (or, before the move, Pocketfader relaying YTM Beacon) reports about the music
//! shown as the Discord status, turned into the words Orbit shows. Pure helpers, no network.
//!
//! The payload is YTM Starcast's `GET /api/beacon`. Older payloads (YTM Beacon's own `/v1/status`) work too: every
//! field is optional.
use serde_json::{json, Value};

pub const DEFAULT_CLIENT_ID: &str = "1567329407901634670"; // the Discord application the status shows as by default (public)

/// headline code -> (tone, text). The tone is only used for the Discord line: it never changes the app's own state.
fn headline_text(code: &str) -> Option<(&'static str, &'static str)> {
    Some(match code {
        "off" => ("off", "Off: not shown on Discord"),
        "snoozed" => ("off", "Hidden for a while: comes back by itself"),
        "discord-down" => ("warn", "Can't reach Discord: is it open?"),
        "no-client-id" => ("warn", "No Discord application ID set"),
        "extension-missing" => ("warn", "The browser extension isn't set up yet"),
        "extension-quiet" => ("warn", "The browser extension went quiet: keep a YouTube Music tab open"),
        "idle" => ("ok", "Ready · nothing playing"),
        "filtered" => ("ok", "Song hidden by your word filter"),
        "paused-hidden" => ("ok", "Paused · hidden while paused"),
        "paused-timeout" => ("ok", "Paused · hidden after the pause timeout"),
        "showing" => ("ok", "Showing on Discord"),
        "waiting" => ("ok", "Sending to Discord…"),
        "ad" => ("ok", "An advert is playing: hidden until the music is back"),
        "unreachable" => ("off", "Off"),
        "unsupported" => ("warn", "YTM Beacon is too old: update it"),
        "beacon-app" => ("warn", "The old YTM Beacon app is still running: move over to YTM Starcast"),
        _ => return None,
    })
}

fn obj<'a>(v: &'a Value, k: &str) -> &'a Value {
    match v.get(k) {
        Some(x) if !x.is_null() => x,
        _ => &Value::Null,
    }
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|x| x != 0.0).unwrap_or(false),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// The payload's own headline when it has one, else worked out in the same order as YTM Beacon's dashboard.
pub fn headline(s: &Value) -> String {
    if !truthy(s) {
        return "unreachable".into();
    }
    if truthy(obj(s, "headline")) {
        return s["headline"].as_str().map(String::from).unwrap_or_else(|| s["headline"].to_string());
    }
    let cfg = if truthy(obj(s, "config")) { obj(s, "config") } else { obj(s, "settings") };
    let visible = match obj(s, "visible") {
        Value::Null => cfg.get("enabled").map(truthy).unwrap_or(true),
        v => truthy(v),
    };
    if !visible {
        return if s["snoozeSec"].as_f64().unwrap_or(0.0) > 0.0 { "snoozed" } else { "off" }.into();
    }
    if obj(s, "discord")["state"] != "connected" {
        return "discord-down".into();
    }
    let ext = obj(s, "extension");
    let Some(seen) = ext["lastSeenAgoSec"].as_f64() else {
        return if truthy(&ext["everConnected"]) { "extension-quiet" } else { "extension-missing" }.into();
    };
    let quiet = ext["quietAfterSec"].as_f64().filter(|x| *x != 0.0).unwrap_or(90.0);
    if seen > quiet {
        return "extension-quiet".into();
    }
    if !truthy(obj(s, "nowPlaying")) {
        return "idle".into();
    }
    match s["hidden"].as_str() {
        Some(h @ ("filtered" | "paused-timeout")) => h.into(),
        Some("paused") => "paused-hidden".into(),
        _ => if truthy(obj(s, "presenceActive")) { "showing" } else { "waiting" }.into(),
    }
}

/// (code, tone, text) for the Discord line.
pub fn describe(s: &Value) -> (String, String, String) {
    let mut code = headline(s);
    if code == "beacon-running" || truthy(&obj(s, "beaconApp")["running"]) {
        code = "beacon-app".into(); // YTM Starcast's own Discord status waits until the old app is gone, whatever else it says
    }
    let (tone, mut text) = match headline_text(&code) {
        Some((t, x)) => (t.to_string(), x.to_string()),
        None => ("ok".into(), code.replace('-', " ")),
    };
    if code == "snoozed" {
        text = format!("Hidden for {} more min", ((s["snoozeSec"].as_f64().unwrap_or(0.0) as i64) / 60).max(1));
    }
    (code, tone, text)
}

pub fn ago(sec: Option<f64>) -> String {
    let Some(sec) = sec else { return "never".into() };
    let sec = sec as i64;
    if sec < 90 { format!("{sec} s ago") } else if sec < 5400 { format!("{} min ago", sec / 60) } else { format!("{} h ago", sec / 3600) }
}

fn s_of(v: &Value) -> String {
    v.as_str().map(String::from).unwrap_or_else(|| v.to_string())
}

/// One browser tab reporting what plays: "Brave · playing · 8 s ago".
fn source(x: &Value) -> String {
    if !x.is_object() {
        return s_of(x);
    }
    let who = [&x["agent"], &x["browser"], &x["name"]].into_iter().find(|v| truthy(v)).map(s_of).unwrap_or_else(|| "a browser".into());
    let mut bits = vec![who];
    if truthy(&x["state"]) {
        bits.push(s_of(&x["state"]));
    }
    if x["ageSec"].is_number() {
        bits.push(ago(x["ageSec"].as_f64()));
    }
    bits.join(" · ")
}

fn dur(v: &Value) -> String {
    let Some(sec) = v.as_f64().map(|x| x as i64).or_else(|| v.as_str().and_then(|s| s.parse().ok())) else { return "?".into() };
    let (d, r) = (sec / 86400, sec % 86400);
    let (h, r) = (r / 3600, r % 3600);
    let m = r / 60;
    format!("{}{}{m} min", if d > 0 { format!("{d} d ") } else { String::new() }, if h > 0 || d > 0 { format!("{h} h ") } else { String::new() })
}

/// Everything else the payload says, for the "details" box. Only what is there is shown.
pub fn details(raw: &Value) -> Vec<Value> {
    let (disc, ext, now) = (obj(raw, "discord"), obj(raw, "extension"), obj(raw, "nowPlaying"));
    let cfg = if truthy(obj(raw, "settings")) { obj(raw, "settings") } else { obj(raw, "config") };
    let mut out = vec![];
    if truthy(&disc["user"]) {
        out.push(json!(["discord user", s_of(&disc["user"])]));
    }
    let ep = disc["endpoint"].as_str().unwrap_or("").to_string();
    if !ep.is_empty() {
        let low = ep.to_lowercase();
        let client = [("vesktop", "Vesktop"), ("vencord", "Vencord"), ("webcord", "WebCord"), ("flatpak/com.discordapp", "Discord (Flatpak)"),
                      ("snap.discord", "Discord (Snap)")].iter().find(|(k, _)| low.contains(k)).map(|x| x.1).unwrap_or("Discord");
        out.push(json!(["discord app", client]));
        out.push(json!(["connection", ep]));
    }
    if cfg.get("clientId").is_some() {
        let cid = cfg["clientId"].as_str().unwrap_or("").trim().to_string();
        out.push(json!(["application id", if !cid.is_empty() && cid != DEFAULT_CLIENT_ID { format!("{cid}  (your own)") }
                        else { format!("{}  (the built-in one)", if cid.is_empty() { DEFAULT_CLIENT_ID } else { &cid }) }]));
    }
    if truthy(&now["title"]) && truthy(&now["album"]) {
        out.push(json!(["album", s_of(&now["album"])]));
    }
    if truthy(ext) {
        let build = if truthy(obj(raw, "extensionBuild")) { format!(" · build {}", s_of(&raw["extensionBuild"])) } else { String::new() };
        out.push(json!(["browser extension", format!("{}{build}", if truthy(&ext["everConnected"]) { "connected before" } else { "never connected" })]));
    }
    if let Some(src) = ext["sources"].as_array().filter(|a| !a.is_empty()) {
        out.push(json!(["reporting from", src.iter().map(source).collect::<Vec<_>>().join(", ")]));
    }
    if truthy(obj(raw, "snoozeSec")) {
        out.push(json!(["hidden for", format!("{} min more", (raw["snoozeSec"].as_f64().unwrap_or(0.0) as i64) / 60)]));
    }
    if truthy(obj(raw, "detail")) {
        out.push(json!(["last discord error", s_of(&raw["detail"])]));
    }
    let old = obj(raw, "beaconApp");
    if truthy(&old["running"]) || truthy(&old["installed"]) {
        let v = if truthy(&old["version"]) { format!(" · {}", s_of(&old["version"])) } else { String::new() };
        out.push(json!(["old ytm beacon app", format!("{}{v}", if truthy(&old["running"]) { "still running" } else { "installed, not running" })]));
    }
    if !obj(raw, "uptimeSec").is_null() {
        out.push(json!(["running for", dur(&raw["uptimeSec"])]));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_discord_line_reads_like_before() {
        assert_eq!(headline(&Value::Null), "unreachable");
        assert_eq!(headline(&json!({"headline": "showing"})), "showing");
        assert_eq!(headline(&json!({"visible": false, "snoozeSec": 600})), "snoozed");
        assert_eq!(headline(&json!({"visible": true, "discord": {"state": "connecting"}})), "discord-down");
        let base = json!({"visible": true, "discord": {"state": "connected"}, "extension": {"lastSeenAgoSec": 5}});
        assert_eq!(headline(&base), "idle");
        let mut playing = base.clone();
        playing["nowPlaying"] = json!({"title": "x"});
        playing["presenceActive"] = json!(true);
        assert_eq!(headline(&playing), "showing");
        playing["hidden"] = json!("paused");
        assert_eq!(headline(&playing), "paused-hidden");
        assert_eq!(describe(&json!({"visible": false, "snoozeSec": 30})).2, "Hidden for 1 more min");
        assert_eq!(describe(&json!({"headline": "showing", "beaconApp": {"running": true}})).0, "beacon-app");
        assert_eq!(dur(&json!(90061)), "1 d 1 h 1 min");
    }
}
