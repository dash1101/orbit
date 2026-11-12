//! Orbit's window: its own window on the system's web engine (Tauri), showing the page the local server serves.
//!
//! On Windows and macOS the window is part of this program. On Linux it is a second build carried inside this one
//! and unpacked once into the cache folder: that build needs WebKitGTK, and this way Orbit itself still starts on a
//! computer without it, offers to install it, or falls back to the browser. Without a window (no engine, and the
//! person said no), Orbit opens in the default browser and ends a few minutes after the page was last seen.
use crate::server::Hub;
use serde_json::json;
use std::sync::{Arc, Mutex};
use std::time::Duration;

static CHILD: Mutex<Option<std::process::Child>> = Mutex::new(None);

/// Close Orbit's window before Orbit ends (a restart). In-process windows close with the program.
pub fn close() {
    if let Some(mut c) = CHILD.lock().unwrap().take() {
        let _ = c.kill();
    }
}

fn saved_size(hub: &Hub) -> (f64, f64) {
    let w = hub.ctx.config.get("window");
    match (w.get(0).and_then(|v| v.as_f64()), w.get(1).and_then(|v| v.as_f64())) {
        (Some(a), Some(b)) if a >= 600.0 && b >= 400.0 => (a, b),
        _ => (1180.0, 760.0),
    }
}

/// Show Orbit. Returns when the person closes the window.
pub fn show(url: &str, hub: &Arc<Hub>) -> i32 {
    #[allow(unused_variables)]
    let size = saved_size(hub);
    #[cfg(all(feature = "window", windows))]
    {
        if crate::webengine::ensure_windows() {
            let h = hub.clone();
            return run(url, size, Box::new(move |w, hh| save_size(&h, w, hh)));
        }
    }
    #[cfg(all(feature = "window", target_os = "macos"))]
    {
        let h = hub.clone();
        return run(url, size, Box::new(move |w, hh| save_size(&h, w, hh)));
    }
    #[cfg(all(feature = "window", target_os = "linux"))]
    {
        if crate::webengine::ensure_linux() {
            let h = hub.clone();
            return run(url, size, Box::new(move |w, hh| save_size(&h, w, hh)));
        }
    }
    #[cfg(all(target_os = "linux", not(feature = "window"), orbit_window_inside))]
    {
        if crate::webengine::ensure_linux() {
            if let Some(code) = linux_window(url, size) {
                return code;
            }
        }
    }
    browser(url, hub)
}

fn save_size(hub: &Hub, w: f64, h: f64) {
    let mut m = serde_json::Map::new();
    m.insert("window".into(), json!([w.round() as i64, h.round() as i64]));
    hub.ctx.config.update(m);
}

/// The default browser instead of a window: Orbit stops once the page hasn't been seen for five minutes.
pub fn browser(url: &str, hub: &Arc<Hub>) -> i32 {
    if !crate::runner::open_path(url) {
        eprintln!("Open {url} in a browser to use Orbit.");
    }
    wait_for_page(hub, Duration::from_secs(300))
}

pub fn wait_for_page(hub: &Arc<Hub>, idle: Duration) -> i32 {
    println!("Orbit is running. Close the tab and it stops by itself, or press Ctrl+C.");
    loop {
        std::thread::sleep(Duration::from_secs(5));
        if hub.last_seen().elapsed() > idle {
            return 0;
        }
    }
}

// ---- the window itself (Tauri) -------------------------------------------------------------------------
pub type SizeReport = Box<dyn Fn(f64, f64) + Send + Sync>;

#[cfg(feature = "window")]
pub fn run(url: &str, size: (f64, f64), report: SizeReport) -> i32 {
    let target: tauri::Url = match url.parse() {
        Ok(u) => u,
        Err(_) => return 2,
    };
    let report = Arc::new(report);
    let r2 = report.clone();
    tauri::Builder::default()
        .setup(move |app| {
            let home = target.clone();
            let win = tauri::WebviewWindowBuilder::new(app, "main", tauri::WebviewUrl::External(target.clone()))
                .title("Orbit")
                .inner_size(size.0, size.1)
                .min_inner_size(720.0, 480.0)
                .on_navigation(move |u| {
                    // Links that leave Orbit (release pages and so on) open in the browser, not in the window.
                    let same = u.origin() == home.origin();
                    if !same {
                        crate::runner::open_path(u.as_str());
                    }
                    same
                })
                .build()?;
            // A remembered (or the default) size larger than this screen: fit it to the screen instead.
            if let Ok(Some(m)) = win.current_monitor() {
                let screen = m.size().to_logical::<f64>(m.scale_factor());
                let (w, h) = (size.0.min(screen.width * 0.92), size.1.min(screen.height * 0.86));
                if (w, h) != size {
                    let _ = win.set_size(tauri::LogicalSize::new(w, h));
                    let _ = win.center();
                }
            }
            Ok(())
        })
        .on_window_event(move |win, ev| {
            if let tauri::WindowEvent::CloseRequested { .. } = ev {
                if let (Ok(sz), Ok(scale)) = (win.inner_size(), win.scale_factor()) {
                    let l = sz.to_logical::<f64>(scale);
                    r2(l.width, l.height);
                }
            }
        })
        .run(tauri::generate_context!())
        .map(|_| 0)
        .unwrap_or(1)
}

/// `orbit --window-of URL`: only the window (the Linux window build, started by the program).
#[cfg(feature = "window")]
pub fn window_only(url: &str, size: (f64, f64), port: u16) -> i32 {
    let report: SizeReport = Box::new(move |w, h| {
        let _ = crate::net::agent(Duration::from_secs(2), false)
            .post(&format!("http://127.0.0.1:{port}/api/hub/window"))
            .set("Content-Type", "application/json")
            .send_string(&json!({"size": [w.round() as i64, h.round() as i64]}).to_string());
    });
    run(url, size, report)
}

#[cfg(all(target_os = "linux", not(feature = "window"), orbit_window_inside))]
static WINDOW_BIN: &[u8] = include_bytes!(env!("ORBIT_WINDOW_BIN"));

/// Unpack the window build (once per version) and run it. None when it couldn't be started.
#[cfg(all(target_os = "linux", not(feature = "window"), orbit_window_inside))]
fn linux_window(url: &str, size: (f64, f64)) -> Option<i32> {
    use sha2::{Digest, Sha256};
    use std::os::unix::fs::PermissionsExt;
    let tag = hex::encode(&Sha256::digest(WINDOW_BIN)[..8]);
    let cache = std::env::var_os("XDG_CACHE_HOME").filter(|v| !v.is_empty()).map(std::path::PathBuf::from)
        .unwrap_or_else(|| crate::config::home().join(".cache")).join("orbit");
    let bin = cache.join(format!("orbit-window-{tag}"));
    if std::fs::metadata(&bin).map(|m| m.len() != WINDOW_BIN.len() as u64).unwrap_or(true) {
        std::fs::create_dir_all(&cache).ok()?;
        let tmp = cache.join(format!(".orbit-window-{}", crate::util::new_id()));
        std::fs::write(&tmp, WINDOW_BIN).ok()?;
        std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o755)).ok()?;
        std::fs::rename(&tmp, &bin).ok()?;
        // older window builds go
        for e in std::fs::read_dir(&cache).ok()?.flatten() {
            let n = e.file_name().to_string_lossy().into_owned();
            if n.starts_with("orbit-window-") && e.path() != bin {
                let _ = std::fs::remove_file(e.path());
            }
        }
    }
    let port = url::Url::parse(url).ok()?.port()?;
    let child = std::process::Command::new(&bin)
        .args(["--window-of", url, "--size", &format!("{}x{}", size.0 as i64, size.1 as i64), "--port", &port.to_string()])
        .spawn().ok()?;
    *CHILD.lock().unwrap() = Some(child);
    loop {
        std::thread::sleep(Duration::from_millis(250));
        let mut g = CHILD.lock().unwrap();
        match g.as_mut() {
            Some(c) => match c.try_wait() {
                Ok(Some(st)) => return Some(st.code().unwrap_or(0)),
                Ok(None) => {}
                Err(_) => return Some(0),
            },
            None => return Some(0),
        }
    }
}
