//! Orbit: one place for your apps. `orbit` opens the window; `orbit --no-window` only runs the local server.
#![allow(dead_code)] // parts kept for the apps that come next (Hesper) while the port settles
#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

mod apps;
mod config;
mod desktop_entry;
mod engine;
mod github_login;
mod host;
mod net;
mod recipe;
mod recipes;
mod runner;
mod server;
mod systemd;
mod term;
mod util;
mod webengine;
mod window;

use std::time::{Duration, Instant};

/// The version; ORBIT_VERSION at build time overrides it (Orbit's own update check builds a "99.0.0").
pub const VERSION: &str = match option_env!("ORBIT_VERSION") {
    Some(v) => v,
    None => env!("CARGO_PKG_VERSION"),
};

struct Args {
    port: u16,
    no_window: bool,
    dry_run: bool,
    write_version: Option<String>,
    window_of: Option<String>,
    size: (f64, f64),
    uninstall: bool,
}

const USAGE: &str = "usage: orbit [--port N] [--no-window] [--dry-run] [--version]\n\n\
One place for all your apps.\n\n\
  --port N      local port (default 8790; 0 = any free port)\n\
  --no-window   only run the server; open the printed address yourself\n\
  --dry-run     read everything, change nothing: actions and the apps' settings are recorded instead of applied\n\
  --uninstall   remove Orbit installed for you (\"install orbit\"); its settings stay\n\
  --version     show the version";

fn parse() -> Result<Args, String> {
    let mut a = Args { port: 8790, no_window: false, dry_run: false, write_version: None, window_of: None, size: (1180.0, 760.0), uninstall: false };
    let mut it = std::env::args().skip(1);
    while let Some(x) = it.next() {
        let (k, inline) = match x.split_once('=') {
            Some((k, v)) if k.starts_with("--") => (k.to_string(), Some(v.to_string())),
            _ => (x.clone(), None),
        };
        let mut val = |name: &str| inline.clone().or_else(|| it.next()).ok_or(format!("{name} needs a value"));
        match k.as_str() {
            "--port" => a.port = val("--port")?.parse().map_err(|_| "--port is a number".to_string())?,
            "--no-window" => a.no_window = true,
            "--dry-run" => a.dry_run = true,
            "--version" => {
                println!("orbit {VERSION}");
                std::process::exit(0);
            }
            "-h" | "--help" => {
                println!("{USAGE}");
                std::process::exit(0);
            }
            "--write-version" => a.write_version = Some(val("--write-version")?), // for Orbit's own updater
            "--uninstall" => a.uninstall = true,                                  // remove Orbit installed for this user
            "--window-of" => a.window_of = Some(val("--window-of")?),           // the Linux window build
            "--size" => {
                let v = val("--size")?;
                if let Some((w, h)) = v.split_once('x') {
                    a.size = (w.parse().unwrap_or(1180.0), h.parse().unwrap_or(760.0));
                }
            }
            other => return Err(format!("unknown option {other}\n\n{USAGE}")),
        }
    }
    Ok(a)
}

fn main() {
    std::process::exit(run());
}

fn run() -> i32 {
    let a = match parse() {
        Ok(a) => a,
        Err(e) => {
            eprintln!("orbit: {e}");
            return 2;
        }
    };
    if a.uninstall {
        return desktop_entry::uninstall_cli(); // Settings → Apps → Orbit → Uninstall (Windows)
    }
    if let Some(path) = a.write_version {
        // The updater starts a downloaded Orbit this way to make sure it runs and is the version it should be. A file,
        // not the terminal: Orbit for Windows has no console to print to.
        return match std::fs::write(&path, format!("orbit {VERSION}\n")) { Ok(_) => 0, Err(_) => 1 };
    }
    if let Some(url) = a.window_of {
        #[cfg(feature = "window")]
        return window::window_only(&url, a.size, a.port);
        #[cfg(not(feature = "window"))]
        {
            eprintln!("orbit: this build has no window ({url})");
            return 2;
        }
    }
    let restarted = std::env::var("ORBIT_RESTARTED").map(|v| v == "1").unwrap_or(false);
    std::env::remove_var("ORBIT_RESTARTED");
    let moved_from = std::env::var("ORBIT_MOVED_FROM").unwrap_or_default();
    std::env::remove_var("ORBIT_MOVED_FROM");
    let delete_old = std::env::var("ORBIT_DELETE_OLD").unwrap_or_default();
    std::env::remove_var("ORBIT_DELETE_OLD");
    if restarted && !delete_old.is_empty() {
        // "install orbit" copied this program from where it was downloaded: that copy goes once the Orbit before ends
        std::thread::spawn(move || {
            for _ in 0..120 {
                if std::fs::remove_file(&delete_old).is_ok() || !std::path::Path::new(&delete_old).exists() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(500));
            }
        });
    }
    desktop_entry::installed_started();
    if restarted && !moved_from.is_empty() {
        // the Orbit before was named with its version: Orbit's own menu entry follows this one
        std::thread::spawn(move || {
            if let Some(me) = recipes::orbit::running_program() {
                desktop_entry::follow(std::path::Path::new(&moved_from), &me);
            }
        });
    }
    if !restarted && switch_to_waiting_update(a.port) {
        return 0;
    }
    std::thread::spawn(|| {
        // The program an update replaced is deleted once it has ended (on Windows that can take a moment).
        for _ in 0..120 {
            if recipes::orbit::cleanup_old(None) {
                return;
            }
            std::thread::sleep(Duration::from_secs(5));
        }
    });
    let hub = server::Hub::new(a.dry_run);
    let wait = if restarted { Duration::from_secs(60) } else { Duration::ZERO };
    let port = match listen(&hub, a.port, wait) {
        Ok(p) => p,
        Err(e) => {
            if a.port != 0 && !a.no_window {
                // Already open? Then just show another window onto it.
                let url = format!("http://127.0.0.1:{}/", a.port);
                if let Ok(v) = net::get_json(&format!("{url}api/hub"), &net::Req { timeout: 1.0, ..Default::default() }) {
                    if v["name"] == "Orbit" {
                        return window::show(&url, &hub);
                    }
                }
            }
            eprintln!("orbit: can't listen on port {}: {e}", a.port);
            return 1;
        }
    };
    hub.update_itself_later();
    hub.watch_approvals();
    let url = format!("http://127.0.0.1:{port}/");
    println!("Orbit {VERSION} on {url}{}", if a.dry_run { "  (dry run: nothing will be changed)" } else { "" });
    if a.no_window {
        loop {
            std::thread::sleep(Duration::from_secs(3600));
        }
    }
    if restarted && page_still_open(&hub, Duration::from_secs(6)) {
        // After an update: a browser page from before still shows Orbit and reloads onto this one by itself.
        return window::wait_for_page(&hub, Duration::from_secs(300));
    }
    window::show(&url, &hub)
}

/// Listen on the port; after a restart, wait (up to `wait`) for the Orbit before to let go of it.
fn listen(hub: &std::sync::Arc<server::Hub>, port: u16, wait: Duration) -> std::io::Result<u16> {
    let end = Instant::now() + wait;
    loop {
        match server::serve(hub, port) {
            Ok(p) => return Ok(p),
            Err(e) if Instant::now() >= end => return Err(e),
            Err(_) => std::thread::sleep(Duration::from_millis(300)),
        }
    }
}

/// Does an Orbit page from before a restart still ask this Orbit for things? (It polls every second or so.)
fn page_still_open(hub: &server::Hub, wait: Duration) -> bool {
    let since = Instant::now();
    while since.elapsed() < wait {
        if hub.last_seen() > since {
            return true;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

/// An update got ready but Orbit was closed instead of restarted: switch to it now, before anything else, and start
/// it. Not while another Orbit runs from this program.
fn switch_to_waiting_update(port: u16) -> bool {
    let Some(exe) = recipes::orbit::running_program() else { return false };
    if recipes::orbit::waiting(Some(&exe)).is_empty() {
        return false;
    }
    if port != 0 && net::port_open("127.0.0.1", port, Duration::from_millis(300)) {
        return false;
    }
    let Ok(Some(new)) = recipes::orbit::switch(Some(&exe)) else { return false };
    if new != exe {
        desktop_entry::follow(&exe, &new);
    }
    let mut argv = vec![new.to_string_lossy().into_owned()];
    argv.extend(std::env::args().skip(1));
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        let err = std::process::Command::new(&argv[0]).args(&argv[1..]).exec();
        eprintln!("orbit: couldn't start the new Orbit: {err}");
        false
    }
    #[cfg(not(unix))]
    {
        runner::detached(&argv, &[]).is_ok()
    }
}
