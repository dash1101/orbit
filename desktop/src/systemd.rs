//! systemd units: read their state (always allowed) and start/stop/enable user units.
//! System units (like Nova's) are read-only here: changing them needs root, and Orbit never asks for it.
use crate::runner::Runner;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::time::Duration;

const PROPS: [&str; 11] = ["Id", "Description", "LoadState", "ActiveState", "SubState", "UnitFileState", "Result",
                           "ActiveEnterTimestamp", "InactiveEnterTimestamp", "MainPID", "NRestarts"];
const USER_VERBS: [&str; 5] = ["start", "stop", "restart", "enable", "disable"];

pub fn valid_unit(name: &str) -> bool {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"^[A-Za-z0-9@_.:\\-]{1,120}\.(service|socket|timer|path|target)$").unwrap())
        .is_match(name)
}

fn base(user: bool) -> Vec<String> {
    if user { vec!["systemctl".into(), "--user".into()] } else { vec!["systemctl".into()] }
}

/// systemd only exists on Linux: elsewhere Orbit doesn't run systemctl or journalctl at all.
pub fn here() -> bool {
    cfg!(target_os = "linux")
}

pub fn show(runner: &Runner, unit: &str, user: bool) -> Value {
    if !valid_unit(unit) {
        return json!({"unit": unit, "error": "not a unit name"});
    }
    if !here() {
        return json!({"unit": unit, "state": "missing"});
    }
    let mut argv = base(user);
    argv.extend(["show".into(), unit.into(), "--no-pager".into()]);
    argv.extend(PROPS.iter().map(|p| format!("-p{p}")));
    let (rc, out) = runner.read(&argv, Duration::from_secs(5));
    if rc != 0 {
        let e = if out.trim().is_empty() { format!("systemctl exit {rc}") } else { out.trim().to_string() };
        return json!({"unit": unit, "error": e});
    }
    let props: HashMap<String, String> = out.lines()
        .filter_map(|l| l.split_once('=').map(|(k, v)| (k.to_string(), v.to_string())))
        .collect();
    summarize(unit, &props, user)
}

pub fn summarize(unit: &str, p: &HashMap<String, String>, user: bool) -> Value {
    let g = |k: &str| p.get(k).cloned().unwrap_or_default();
    let (load, active, sub, enabled, result) = (g("LoadState"), g("ActiveState"), g("SubState"), g("UnitFileState"), g("Result"));
    let state = if load == "not-found" {
        "missing".to_string()
    } else if active == "failed" || (active == "inactive" && !(result.is_empty() || result == "success")) {
        "failed".into()
    } else if active == "active" || active == "reloading" {
        if ["running", "listening", "waiting", "exited", "mounted", "plugged"].contains(&sub.as_str()) {
            "running".into()
        } else if sub.is_empty() { "active".into() } else { sub.clone() }
    } else if active == "activating" {
        "starting".into()
    } else {
        "stopped".into()
    };
    let since = if active == "active" { g("ActiveEnterTimestamp") } else { g("InactiveEnterTimestamp") };
    let restarts: i64 = g("NRestarts").parse().unwrap_or(0);
    json!({"unit": unit, "user": user, "description": g("Description"), "state": state, "active": active, "sub": sub,
           "enabled": enabled, "result": result, "since": since, "restarts": restarts})
}

/// start/stop/restart/enable/disable a *user* unit. (ok, message)
pub fn act(runner: &Runner, unit: &str, verb: &str, user: bool) -> (bool, String) {
    if !here() {
        return (false, "There are no systemd services on this system.".into());
    }
    if !user {
        return (false, "System services need root; use the terminal command instead.".into());
    }
    if !USER_VERBS.contains(&verb) || !valid_unit(unit) {
        return (false, "unknown action".into());
    }
    let mut argv = base(true);
    argv.extend([verb.to_string(), unit.to_string()]);
    let (rc, out) = runner.write(&argv, Duration::from_secs(30));
    let msg = if out.trim().is_empty() { format!("{verb} {unit}: done") } else { out.trim().to_string() };
    (rc == 0, msg)
}

pub fn journal(runner: &Runner, unit: &str, user: bool, lines: u32) -> String {
    if !valid_unit(unit) {
        return String::new();
    }
    if !here() {
        return "(this app's log isn't available here: there's no systemd journal on this system)".into();
    }
    let mut argv: Vec<String> = ["journalctl", "--no-pager", "-q", "-o", "short-iso", "-n"].iter().map(|s| s.to_string()).collect();
    argv.push(lines.to_string());
    if user {
        argv.extend(["--user-unit".into(), unit.into()]);
    } else {
        argv.extend(["-u".into(), unit.into()]);
    }
    let (rc, out) = runner.read(&argv, Duration::from_secs(8));
    if rc != 0 && out.trim().is_empty() {
        return format!("(journalctl exit {rc})");
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unit_states_read_like_before() {
        let p = |pairs: &[(&str, &str)]| pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect::<HashMap<_, _>>();
        assert_eq!(summarize("a.service", &p(&[("LoadState", "not-found")]), true)["state"], "missing");
        assert_eq!(summarize("a.service", &p(&[("ActiveState", "active"), ("SubState", "running")]), true)["state"], "running");
        assert_eq!(summarize("a.service", &p(&[("ActiveState", "inactive"), ("Result", "exit-code")]), true)["state"], "failed");
        assert_eq!(summarize("a.service", &p(&[("ActiveState", "inactive"), ("Result", "success")]), true)["state"], "stopped");
        assert!(valid_unit("nova-api.service") && !valid_unit("rm -rf /") && !valid_unit("x.exe"));
    }
}
