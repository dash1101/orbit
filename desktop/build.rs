//! Orbit's build: the window (feature `window`) is built with Tauri's own build step; the Linux program carries the
//! window build inside it when ORBIT_WINDOW_BIN names one (see src/window.rs).
fn main() {
    #[cfg(feature = "window")]
    tauri_build::build();
    println!("cargo:rerun-if-env-changed=ORBIT_WINDOW_BIN");
    if let Ok(p) = std::env::var("ORBIT_WINDOW_BIN") {
        if !p.is_empty() {
            println!("cargo:rerun-if-changed={p}");
            println!("cargo:rustc-cfg=orbit_window_inside");
        }
    }
    println!("cargo::rustc-check-cfg=cfg(orbit_window_inside)");
    for f in ["index.html", "icon.svg"] {
        println!("cargo:rerun-if-changed=../orbit/static/{f}");
    }
    println!("cargo:rerun-if-changed=../orbit/static/apps");
    // The apps' icons, built into the program: one entry per file in orbit/static/apps.
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../orbit/static/apps");
    let mut rows = String::from("pub static APP_ICONS: &[(&str, &[u8])] = &[\n");
    let mut names: Vec<_> = std::fs::read_dir(&dir).map(|d| d.flatten().map(|e| e.path()).collect()).unwrap_or_default();
    names.sort();
    for p in names {
        let n = p.file_name().unwrap().to_string_lossy().to_string();
        if n.ends_with(".svg") || n.ends_with(".png") {
            rows.push_str(&format!("    ({n:?}, include_bytes!({:?})),\n", p.canonicalize().unwrap()));
        }
    }
    rows.push_str("];\n");
    std::fs::write(std::path::Path::new(&std::env::var("OUT_DIR").unwrap()).join("app_icons.rs"), rows).unwrap();
}
