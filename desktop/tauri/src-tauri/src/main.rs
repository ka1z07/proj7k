//! proj7k's desktop window (ADR-0037): one frameless window over the dashboard that is already running on
//! 127.0.0.1:7770. `python -m proj7k.desktop` finds or starts the dashboard first, then runs this with `--url`.
//! The dashboard's shell draws its own title bar and calls back here (window.__TAURI__) to drag, minimise,
//! maximise and close; on macOS the native traffic lights stay, over the shell's title bar.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{Manager, Url, WebviewUrl, WebviewWindowBuilder};

const DEFAULT_URL: &str = "http://127.0.0.1:7770/#home";

/// `--url <url>` or `--url=<url>`; the dashboard's home page otherwise.
fn target_url() -> Url {
    let mut args = std::env::args().skip(1);
    let mut url = DEFAULT_URL.to_string();
    while let Some(arg) = args.next() {
        if arg == "--url" {
            if let Some(value) = args.next() {
                url = value;
            }
        } else if let Some(value) = arg.strip_prefix("--url=") {
            url = value.to_string();
        }
    }
    url.parse().unwrap_or_else(|_| DEFAULT_URL.parse().expect("default url"))
}

fn main() {
    tauri::Builder::default()
        // A second launch (the shortcut clicked again) brings the open window forward instead of a second one.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(target_url()))
                .title("proj7k")
                .inner_size(1440.0, 920.0)
                .min_inner_size(960.0, 600.0);
            #[cfg(target_os = "macos")]
            let builder = builder.title_bar_style(tauri::TitleBarStyle::Overlay).hidden_title(true);
            #[cfg(not(target_os = "macos"))]
            let builder = builder.decorations(false);
            builder.build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running proj7k");
}
