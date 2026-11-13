//! Windows: an app that runs from a scheduled task at logon (YTM Starcast's "YTM Starcast"). Whether the task is there
//! and starts at logon, and start, stop, restart and the at-logon switch, through schtasks (which Orbit runs without a
//! window). Whether the app runs is told by the app answering, not by the task.
use crate::runner::Runner;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

static SEEN: Mutex<Option<HashMap<String, (Instant, Value)>>> = Mutex::new(None);
const FRESH: Duration = Duration::from_secs(5); // the overview asks every few seconds: schtasks once in a while is enough

fn valid(task: &str) -> bool {
    !task.is_empty() && task.len() < 100 && task.chars().all(|c| c.is_alphanumeric() || " -_.".contains(c))
}

/// Like systemd::show: {"unit", "state": "stopped" | "missing", "enabled": "enabled" | "disabled"}. A task that is
/// there says "stopped"; the app's own answer says when it runs.
pub fn show(runner: &Runner, task: &str) -> Value {
    if !cfg!(windows) || !valid(task) {
        return json!({"unit": task, "state": "missing"});
    }
    if let Some((at, v)) = SEEN.lock().unwrap().as_ref().and_then(|m| m.get(task).cloned()) {
        if at.elapsed() < FRESH {
            return v;
        }
    }
    let (rc, out) = runner.read(&["schtasks".into(), "/query".into(), "/tn".into(), task.into(), "/xml".into()], Duration::from_secs(10));
    let v = if rc != 0 {
        json!({"unit": task, "state": "missing"})
    } else {
        // the task's XML (UTF-16 through a pipe: the NULs go) and its own <Settings><Enabled> (left out = on)
        let xml = out.replace('\0', "");
        let settings = xml.split("<Settings>").nth(1).and_then(|s| s.split("</Settings>").next()).unwrap_or("");
        let on = !settings.contains("<Enabled>false</Enabled>");
        json!({"unit": task, "state": "stopped", "enabled": if on { "enabled" } else { "disabled" }})
    };
    SEEN.lock().unwrap().get_or_insert_with(HashMap::new).insert(task.into(), (Instant::now(), v.clone()));
    v
}

/// The task's program, arguments and folder, from its XML (<Exec>).
fn command(xml: &str) -> Option<(String, String, String)> {
    let exec = xml.split("<Exec>").nth(1)?.split("</Exec>").next()?;
    let tag = |t: &str| -> String {
        let v = exec.split(&format!("<{t}>")).nth(1).and_then(|x| x.split(&format!("</{t}>")).next()).unwrap_or("");
        v.replace("&quot;", "\"").replace("&apos;", "'").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    };
    let program = tag("Command").trim().trim_matches('"').to_string();
    (!program.is_empty()).then(|| (program, tag("Arguments"), tag("WorkingDirectory")))
}

/// Start the task's own command with no window, as the task would but without the console window a console program
/// gets when the task runs it in the user's session (closing that window would end it).
fn start_hidden(runner: &Runner, task: &str) -> Option<(i32, String)> {
    let (rc, out) = runner.read(&["schtasks".into(), "/query".into(), "/tn".into(), task.into(), "/xml".into()], Duration::from_secs(10));
    let (program, args, dir) = command(&out.replace('\0', "")).filter(|_| rc == 0)?;
    if runner.dry_run {
        runner.record("spawn", vec![program, args]);
        return Some((0, String::new()));
    }
    let mut c = std::process::Command::new(&program);
    c.stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
    if !dir.trim().is_empty() {
        c.current_dir(dir.trim());
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.raw_arg(&args); // the task's arguments, as written there
        c.creation_flags(0x0800_0000 | 0x0000_0200); // CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP: no window, outlives Orbit
    }
    #[cfg(not(windows))]
    c.args(args.split_whitespace());
    Some(match c.spawn() {
        Ok(_) => (0, String::new()),
        Err(e) => (1, format!("couldn't start {program}: {e}")),
    })
}

fn forget(task: &str) {
    if let Some(m) = SEEN.lock().unwrap().as_mut() {
        m.remove(task);
    }
}

/// start | stop | restart | enable | disable. `program` is the app's program name (ytm-starcast.exe): stop also ends
/// a copy that runs outside the task. (ok, message)
pub fn act(runner: &Runner, task: &str, program: &str, verb: &str) -> (bool, String) {
    // (start runs the task's command itself, with no window; only when that can't be read does the task start it)
    if !valid(task) {
        return (false, "not a task name".into());
    }
    let st = show(runner, task);
    if st["state"] == "missing" {
        return (false, format!("the scheduled task {task} isn't there: reinstall the app"));
    }
    let off = st["enabled"] == "disabled";
    let sch = |args: &[&str]| -> (i32, String) {
        let mut argv = vec!["schtasks".to_string()];
        argv.extend(args.iter().map(|a| a.to_string()));
        runner.write(&argv, Duration::from_secs(20))
    };
    let start = || -> (i32, String) {
        if let Some(r) = start_hidden(runner, task) {
            return r;
        }
        if off {
            // switched off at logon: on for a moment to start it now, then off again
            sch(&["/change", "/tn", task, "/enable"]);
            let r = sch(&["/run", "/tn", task]);
            sch(&["/change", "/tn", task, "/disable"]);
            r
        } else {
            sch(&["/run", "/tn", task])
        }
    };
    let stop = || -> (i32, String) {
        let r = sch(&["/end", "/tn", task]);
        if !program.is_empty() {
            let _ = runner.write(&["taskkill".into(), "/F".into(), "/IM".into(), program.into()], Duration::from_secs(20));
        }
        r
    };
    let (rc, out) = match verb {
        "start" => start(),
        "stop" => stop(),
        "restart" => {
            stop();
            std::thread::sleep(Duration::from_millis(800));
            start()
        }
        "enable" => sch(&["/change", "/tn", task, "/enable"]),
        "disable" => sch(&["/change", "/tn", task, "/disable"]),
        _ => return (false, "unknown action".into()),
    };
    forget(task);
    let what = match verb { "enable" => "starts at logon", "disable" => "won't start at logon", "stop" => "stopped", "restart" => "restarted", _ => "started" };
    if rc == 0 { (true, format!("{task} {what}")) } else { (false, format!("{task}: {}", out.trim())) }
}

#[cfg(test)]
mod tests {
    #[test]
    fn the_command_comes_from_the_tasks_xml() {
        let xml = "<Task><Actions Context=\"Author\"><Exec><Command>\"C:\\Users\\a b\\YTM Starcast\\ytm-starcast.exe\"</Command>\
                   <Arguments>--audio windows --port 8778</Arguments><WorkingDirectory>C:\\Users\\a b\\YTM Starcast</WorkingDirectory>\
                   </Exec></Actions><Settings><Enabled>false</Enabled></Settings></Task>";
        let (p, a, d) = super::command(xml).unwrap();
        assert_eq!(p, r"C:\Users\a b\YTM Starcast\ytm-starcast.exe");
        assert_eq!(a, "--audio windows --port 8778");
        assert_eq!(d, r"C:\Users\a b\YTM Starcast");
        assert!(super::command("<Task></Task>").is_none());
    }
}
