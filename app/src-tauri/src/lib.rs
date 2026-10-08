//! Application shell: two windows, and the studio server behind them.
//!
//! No business logic lives here. The Rust side opens the windows, launches the
//! Python server on a free port, publishes its address to the front, and stops
//! everything on exit.
//!
//! The second window (the Chats) adds no process: the terminals are children
//! of the Python server, which is already tied to this shell by
//! `PR_SET_PDEATHSIG` (see `server.rs`). The Rust side only shows, hides, and
//! lets things run.

mod server;
mod update;

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, OnceLock};

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};

use server::Server;

/// The interface language, told by the front (`set_language`): French until it
/// has spoken. It serves what the shell writes itself: the tray menu, the
/// relauncher's notifications.
static ENGLISH: AtomicBool = AtomicBool::new(false);

/// A shell text, in the interface language.
fn say(fr: &'static str, en: &'static str) -> &'static str {
    if ENGLISH.load(Ordering::Relaxed) {
        en
    } else {
        fr
    }
}

/// The tray menu entries, kept so they can be translated again.
struct TrayMenu(Vec<(MenuItem<tauri::Wry>, &'static str, &'static str)>);

/// The API address and the token to be heard by it, requested at startup.
///
/// The port is not known at build time: it is picked at launch so that
/// opening two studios, or one next to a `gamestudio serve`, causes no
/// collision. The token protects the API from anything running on the machine
/// uninvited: the front cannot guess it, so it asks for it here, once, and
/// keeps it for the session.
#[tauri::command]
fn api_auth(server: tauri::State<'_, Arc<Server>>) -> serde_json::Value {
    serde_json::json!({ "base": server.base, "token": server.token })
}

/// How far the studio lags behind its code: `{ app, server }`.
///
/// The front asks regularly, to show the "Update" button when needed.
/// `make -q` and the source walk take a few milliseconds, but launch a
/// process: off the main thread.
#[tauri::command]
async fn update_status(server: tauri::State<'_, Arc<Server>>) -> Result<serde_json::Value, String> {
    let launched = server.launched;
    off_main_thread(move || update::outdated(&project_root(), launched).json())
        .await
        .ok_or_else(|| "the check was interrupted".to_string())
}

/// Starts rebuilding what lags behind, without closing anything.
///
/// Returns the starting state at once; what follows arrives through the
/// `update-progress` event, which each window listens to for its gauge. The
/// studio stays open meanwhile: agents carry on, and only the restart will
/// interrupt them.
#[tauri::command]
fn update_build(
    app: AppHandle,
    build: tauri::State<'_, Arc<update::Build>>,
) -> serde_json::Value {
    build.start(app, project_root());
    build.json()
}

/// The build state, for a window that opens midway.
#[tauri::command]
fn update_progress(build: tauri::State<'_, Arc<update::Build>>) -> serde_json::Value {
    build.json()
}

/// Restarts the studio and its server, on the freshly built binary.
///
/// The relauncher still goes through `make studio` (`Relaunch::default_for`):
/// with the build done, it finds nothing left to redo, and catches up with
/// whatever changed meanwhile.
#[tauri::command]
fn update_now(app: AppHandle) -> Result<(), String> {
    let root = project_root();
    restart_after_exit(&root, Relaunch::default_for(&root)).map_err(|error| error.to_string())?;
    app.exit(0);
    Ok(())
}

/// Opens the studio as it is, after an update that failed.
#[tauri::command]
fn update_skip(app: AppHandle) -> Result<(), String> {
    restart_after_exit(&project_root(), Relaunch::Direct).map_err(|error| error.to_string())?;
    app.exit(0);
    Ok(())
}

/// Receives the language chosen in the front, and translates the tray menu again.
#[tauri::command]
fn set_language(app: AppHandle, lang: String) {
    ENGLISH.store(lang == "en", Ordering::Relaxed);
    if let Some(tray) = app.try_state::<TrayMenu>() {
        for (item, fr, en) in &tray.0 {
            let _ = item.set_text(say(fr, en));
        }
    }
}

/// Shows the Chats window, and gives it the keyboard.
///
/// It never closes: hiding it is enough. That is what makes "the agent carries
/// on when the window is closed" true: it never stopped running, since the
/// server holds it.
///
/// Like every command of the application, it is only allowed to windows whose
/// capability grants it: `build.rs` declares the commands, each file of
/// `capabilities/` says which ones a window uses.
#[tauri::command]
fn show_chat(app: tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("chat")
        .ok_or_else(|| "Chats window missing".to_string())?;
    window.show().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

/// Hides or shows the studio window again, and returns its new state.
///
/// Hiding stops nothing: "Quit" in the tray owns the lifecycle of the
/// application and the server. The button gives the Chats all the room, it
/// does not quit, and `hide` does not trigger `ExitRequested`.
#[tauri::command]
fn toggle_studio(app: tauri::AppHandle) -> Result<bool, String> {
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "studio window missing".to_string())?;
    let visible = window.is_visible().map_err(|error| error.to_string())?;
    if visible {
        window.hide().map_err(|error| error.to_string())?;
    } else {
        window.show().map_err(|error| error.to_string())?;
        window.set_focus().map_err(|error| error.to_string())?;
    }
    Ok(!visible)
}

/// The Chats window, as the compositor sees it.
///
/// The compositor names its windows by an address, and acting goes through it:
/// by class, the studio window would be hit too, tiled and so deaf to window
/// orders. The pid tells them apart, with the title: both windows come from
/// here, and nothing else carries this pid.
struct Chat {
    address: String,
    pinned: bool,
    /// Floating or tiled. `setfloating` is a toggle like `pin`: the state is
    /// read before acting, or the result is the opposite of what was asked.
    floating: bool,
    /// Width and height, in pixels. Read back afterwards to check that a
    /// placement took: right after mapping, the compositor may still re-tile
    /// the window, and the order is then silently lost.
    size: (i64, i64),
    /// Left edge abscissa, in absolute coordinates.
    x: i64,
    /// The workspace and the monitor it lives on.
    workspace: i64,
    monitor: i64,
}

/// `None` when nobody answers: outside Hyprland, the button is disabled
/// instead of lying about what it can do.
fn chat_client() -> Option<Chat> {
    let clients = hyprctl(&["clients"])?;
    let pid = std::process::id() as u64;
    clients.as_array()?.iter().find_map(|client| {
        let ours = client.get("pid")?.as_u64()? == pid;
        let chats = client.get("title")?.as_str()?.ends_with("chats");
        if !(ours && chats) {
            return None;
        }
        let pair = |key: &str| {
            let field = client.get(key)?.as_array()?;
            Some((field.first()?.as_i64()?, field.get(1)?.as_i64()?))
        };
        Some(Chat {
            address: client.get("address")?.as_str()?.to_string(),
            pinned: client.get("pinned")?.as_bool()?,
            floating: client.get("floating")?.as_bool()?,
            size: pair("size")?,
            x: pair("at")?.0,
            workspace: client.get("workspace")?.get("id")?.as_i64()?,
            monitor: client.get("monitor")?.as_i64()?,
        })
    })
}

/// Queries the compositor, as JSON.
///
/// `None` as soon as something is missing or unreadable: outside Hyprland there
/// is nothing to answer, and the caller abstains rather than act on nothing.
fn hyprctl(args: &[&str]) -> Option<serde_json::Value> {
    let mut full = args.to_vec();
    full.push("-j");
    let output = std::process::Command::new("hyprctl")
        .args(&full)
        .output()
        .ok()?;
    serde_json::from_slice(&output.stdout).ok()
}

/// Passes a line to the compositor (an order, `dispatch`, or a rule,
/// `keyword`) and returns.
///
/// `hyprctl` joins its arguments with spaces: the whole line goes as a single
/// argument, and a rule keeps its spaces.
fn hyprctl_write(line: &str) -> Result<(), String> {
    let output = std::process::Command::new("hyprctl")
        .args([line])
        .output()
        .map_err(|error| error.to_string())?;
    if output.status.success() {
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
    }
}

/// The monitor a window lives on: its position, its size, and what it shows.
struct Screen {
    /// The monitor's origin, in absolute coordinates. A window is placed in
    /// absolute coordinates, while an edge counts from the monitor itself: that
    /// difference is made up here, once and for all. A second monitor may sit
    /// at a negative abscissa, and forgetting it would shift the window by as
    /// much.
    x: i64,
    y: i64,
    /// Logical width and height: those of the layout, scale and rotation
    /// included.
    width: i64,
    height: i64,
    /// The band the bars (exclusive-zone layers) reserve on each edge: left,
    /// top, right, bottom. A window placed by hand must avoid it, as tiling
    /// does.
    reserve: [i64; 4],
    workspace: i64,
}

fn screen(id: i64) -> Option<Screen> {
    let monitors = hyprctl(&["monitors"])?;
    let monitor = monitors
        .as_array()?
        .iter()
        .find(|entry| entry.get("id").and_then(|value| value.as_i64()) == Some(id))?;
    // `width` and `height` are those of the monitor's mode, not of the work
    // area: rotated by a quarter turn (odd `transform`), the monitor swaps
    // them, and the height to fill is then the mode's width. Reading it as is
    // would give a window covering only part of the monitor.
    let mut height = monitor.get("height")?.as_i64()?;
    let mut width = monitor.get("width")?.as_i64()?;
    if monitor.get("transform")?.as_i64()? % 2 == 1 {
        std::mem::swap(&mut height, &mut width);
    }
    let scale = monitor.get("scale")?.as_f64()?.max(0.1);
    // Missing from a Hyprland that does not publish it: nothing reserved.
    let reserve = monitor
        .get("reserved")
        .and_then(|value| value.as_array())
        .and_then(|edges| {
            let edges: Vec<i64> = edges.iter().filter_map(|edge| edge.as_i64()).collect();
            <[i64; 4]>::try_from(edges).ok()
        })
        .unwrap_or([0; 4]);
    Some(Screen {
        x: monitor.get("x")?.as_i64()?,
        y: monitor.get("y")?.as_i64()?,
        width: (width as f64 / scale).round() as i64,
        height: (height as f64 / scale).round() as i64,
        reserve,
        workspace: monitor.get("activeWorkspace")?.get("id")?.as_i64()?,
    })
}

/// Where the pinned Chats window goes: along the left edge of the monitor it is
/// already on, over the full height the bars leave free.
///
/// Never "the best monitor": choosing for the user would send it to whatever
/// monitor scores best (the tall portrait one), regardless of the one they
/// were looking at. The monitor is the one they gave the window.
///
/// The width is the one the window asks for in `tauri.conf.json` (see
/// `chat_width`); floating, it stays resizable with the mouse afterwards.
/// `MARGIN` is the gap left between the window and the edges of the free area:
/// the bars' band (`Screen::reserve`) is not part of it.
const MARGIN: i64 = 10;

/// The Chats window's width, as `tauri.conf.json` declares it.
fn chat_width(app: &AppHandle) -> Result<i64, String> {
    app.config()
        .app
        .windows
        .iter()
        .find(|window| window.label == "chat")
        .map(|window| window.width.round() as i64)
        .ok_or_else(|| "the Chats window is not declared in tauri.conf.json".to_string())
}

/// Repeats a placement until the window has actually taken the requested state.
///
/// An operation that returns has proved nothing: right after mapping, the
/// compositor may still re-tile the window, and the order is then silently
/// lost. Placements therefore check their result and return an `Err` until it
/// holds; that makes retrying useful, and bounded: one second in total. Past
/// that, the last reason is returned rather than fighting on.
fn place_until(place: impl Fn() -> Result<(), String>) -> Result<(), String> {
    let mut last = String::from("the Chats window did not take its place");
    for _ in 0..20 {
        match place() {
            Ok(()) => return Ok(()),
            Err(reason) => {
                last = reason;
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
        }
    }
    Err(last)
}

/// Places the Chats window as pinned, and pins it.
///
/// The application places its own window: no compositor rule describes it,
/// and nothing is written to the user's configuration, only orders that last
/// an instant. Tauri can neither float a window nor pin it: these are
/// compositor notions, and under Wayland a client does not place itself. Both
/// go through `hyprctl`.
///
/// The order matters:
///   - **the workspace first**, because changing it moves a floating window
///     (the compositor places it again on its new monitor): placed before, it
///     would be moved afterwards and land beside. It also resets pinning, so
///     the pin comes last;
///   - **`setfloating` and `pin` only if the state read back says so**: they
///     are toggles, and a second call would tile the window again.
///
/// Each order is idempotent, so repeating the placement is safe: that is what
/// lets `place_until` insist until the window is really there.
fn place_pinned(width: i64) -> Result<(), String> {
    let chat = chat_client().ok_or("the Chats window is not mapped yet")?;
    let screen = screen(chat.monitor).ok_or("the Chats monitor was not found")?;
    let [left, top, _, bottom] = screen.reserve;
    let height = (screen.height - top - bottom - 2 * MARGIN).max(240);

    if chat.workspace != screen.workspace {
        hyprctl_write(&format!(
            "dispatch movetoworkspacesilent {},address:{}",
            screen.workspace, chat.address
        ))?;
    }
    if !chat.floating {
        hyprctl_write(&format!("dispatch setfloating address:{}", chat.address))?;
    }
    hyprctl_write(&format!(
        "dispatch resizewindowpixel exact {width} {height},address:{}",
        chat.address
    ))?;
    hyprctl_write(&format!(
        "dispatch movewindowpixel exact {} {},address:{}",
        screen.x + left + MARGIN,
        screen.y + top + MARGIN,
        chat.address
    ))?;

    let after = chat_client().ok_or("the Chats window disappeared")?;
    if !after.pinned {
        hyprctl_write(&format!("dispatch pin address:{}", after.address))?;
    }

    let done = chat_client().ok_or("the Chats window disappeared")?;
    if done.floating && done.pinned && done.size.0 == width {
        Ok(())
    } else {
        Err("the Chats window is not pinned yet".to_string())
    }
}

/// Share of the monitor's width the Chats window takes when it shares its
/// workspace, left of the others.
const CHAT_SHARE: f64 = 0.25;

/// The other tiles of the Chats workspace: `(address, abscissa)`.
///
/// Only tiles count (a floating window takes no room in the tiling), and only
/// mapped ones: the hidden studio is not one any more.
fn neighbours(chat: &Chat) -> Vec<(String, i64)> {
    let Some(clients) = hyprctl(&["clients"]) else {
        return Vec::new();
    };
    let Some(clients) = clients.as_array() else {
        return Vec::new();
    };
    clients
        .iter()
        .filter_map(|client| {
            let address = client.get("address")?.as_str()?;
            let tiled = client.get("mapped")?.as_bool()?
                && !client.get("hidden")?.as_bool()?
                && !client.get("floating")?.as_bool()?;
            let here = client.get("workspace")?.get("id")?.as_i64()? == chat.workspace;
            (tiled && here && address != chat.address).then(|| {
                let x = client.get("at")?.as_array()?.first()?.as_i64()?;
                Some((address.to_string(), x))
            })?
        })
        .collect()
}

/// Arranges the tiled Chats window on the left, over a quarter of the monitor.
///
/// Tiling splits the monitor in two halves, and places the new tile according
/// to the mouse: neither the width nor the side can be chosen at mapping, so
/// they are corrected afterwards. It is a starting layout, not a regime: the
/// window stays tiled, and the mouse or shortcuts resize it like the others;
/// nothing forces it again until another window arrives beside it.
///
/// `swapwindow` only acts on the focused window: focus goes to the Chats for
/// the swap, then back to the window that had it (a Blender just opened keeps
/// it). The resize is relative (distance to the target, read again on each
/// try): repeated, it converges instead of adding up. Alone on its workspace,
/// a tile ignores the order; the caller is told, and retries while the
/// neighbour maps.
fn arrange_chat() -> Result<(), String> {
    let chat = chat_client().ok_or("the Chats window is not mapped yet")?;
    if chat.floating {
        return Ok(());
    }
    let screen = screen(chat.monitor).ok_or("the Chats monitor was not found")?;
    let neighbours = neighbours(&chat);
    if neighbours.is_empty() {
        return Err("no window beside the Chats".to_string());
    }
    if neighbours.iter().any(|(_, x)| *x < chat.x) {
        let focus = hyprctl(&["activewindow"])
            .and_then(|window| Some(window.get("address")?.as_str()?.to_string()));
        hyprctl_write(&format!("dispatch focuswindow address:{}", chat.address))?;
        hyprctl_write("dispatch swapwindow l")?;
        if let Some(focus) = focus.filter(|address| *address != chat.address) {
            hyprctl_write(&format!("dispatch focuswindow address:{focus}"))?;
        }
        return Err("the Chats are not on the left yet".to_string());
    }
    let target = (screen.width as f64 * CHAT_SHARE).round() as i64;
    let gap = target - chat.size.0;
    if gap.abs() <= 24 {
        return Ok(());
    }
    hyprctl_write(&format!(
        "dispatch resizewindowpixel {gap} 0,address:{}",
        chat.address
    ))?;
    Err("the Chats window does not have its width yet".to_string())
}

/// Insists on `arrange_chat` for two seconds at most: right after a mapping,
/// the layout still moves, and an order sent too early is silently lost.
fn arrange_chat_persistently() {
    for _ in 0..40 {
        if arrange_chat().is_ok() {
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
}

/// Listens to the compositor, and arranges the Chats when a window joins them.
///
/// Hyprland's event stream (`.socket2.sock`) reports each window that opens
/// (`openwindow`) or changes workspace (`movewindowv2`). When it arrives where
/// the Chats are (the studio at startup or shown again, Blender launched while
/// the studio is hidden), the Chats take their quarter back, on the left. A
/// close triggers nothing: the Chats left alone take the whole room, as wanted.
///
/// Outside Hyprland the socket does not exist: nothing happens, silently.
fn watch_compositor() {
    use std::io::BufRead;

    let (Some(run), Some(signature)) = (
        std::env::var_os("XDG_RUNTIME_DIR"),
        std::env::var_os("HYPRLAND_INSTANCE_SIGNATURE"),
    ) else {
        return;
    };
    let socket = PathBuf::from(run).join("hypr").join(signature).join(".socket2.sock");
    let Ok(stream) = std::os::unix::net::UnixStream::connect(socket) else {
        return;
    };
    for line in std::io::BufReader::new(stream).lines().map_while(Result::ok) {
        let Some((event_name, data)) = line.split_once(">>") else {
            continue;
        };
        if event_name != "openwindow" && event_name != "movewindowv2" {
            continue;
        }
        // The stream gives the address without its `0x` prefix.
        let Some(address) = data.split(',').next() else {
            continue;
        };
        let Some(chat) = chat_client() else {
            continue;
        };
        let arrived = format!("0x{address}");
        let beside = arrived == chat.address
            || neighbours(&chat).iter().any(|(neighbour, _)| *neighbour == arrived);
        if beside {
            arrange_chat_persistently();
        }
    }
}

/// Unpins the Chats window: it becomes a tiled window.
///
/// Tiled, not floating, because that is what "free" means: a window of the
/// workspace like the others, placed by the layout, which the user resizes,
/// moves or sends to another monitor with their usual shortcuts.
///
/// This placement imposes no width: forcing one (and a pseudo-tiling to keep
/// it when the window is alone) would make it deaf to the mouse and the
/// layout. Alone on its workspace, it takes the whole room. The only width the
/// application gives it afterwards comes from `watch_compositor`: when a
/// window arrives on its workspace, `arrange_chat` puts it back on the left,
/// over a quarter of the monitor. Between two arrivals, the user shapes it.
///
/// It first joins the workspace being looked at: tiled, it only exists on its
/// own, so staying there would make it vanish from the screen at the very
/// moment of the click. Silently: it is a consequence of the regime, not a
/// navigation.
fn place_free() -> Result<(), String> {
    let chat = chat_client().ok_or("the compositor does not answer")?;
    let screen = screen(chat.monitor).ok_or("the Chats monitor was not found")?;
    if screen.workspace != chat.workspace {
        hyprctl_write(&format!(
            "dispatch movetoworkspacesilent {},address:{}",
            screen.workspace, chat.address
        ))?;
    }
    if chat.floating {
        hyprctl_write(&format!("dispatch settiled address:{}", chat.address))?;
    }

    let done = chat_client().ok_or("the Chats window disappeared")?;
    if done.floating {
        Err("the Chats window is not unpinned yet".to_string())
    } else {
        Ok(())
    }
}

/// Whether the Chats are pinned, as the compositor sees it.
///
/// Asynchronous, like every command that launches `hyprctl`: a synchronous
/// Tauri command runs on the main thread, GTK's, which also carries the
/// keyboard and the rendering of both windows. The Chats window asks for this
/// state every three seconds; on the main thread each call would freeze typing
/// for the length of a process.
#[tauri::command]
async fn chat_pinned() -> Option<bool> {
    off_main_thread(|| chat_client().map(|chat| chat.pinned))
        .await
        .flatten()
}

/// Runs blocking work (processes, waits) on a separate thread.
async fn off_main_thread<T: Send + 'static>(
    work: impl FnOnce() -> T + Send + 'static,
) -> Option<T> {
    tauri::async_runtime::spawn_blocking(work).await.ok()
}

/// Toggles the Chats window between its two regimes, and returns the new one.
///
/// Two kinds of window, not two settings:
///   pinned -- floating and pinned, so ABOVE the tiling, which takes back the
///             full width, and present on every workspace of its monitor.
///   free   -- tiled, so a window of the workspace like the others: it has its
///             share of the layout, is placed again when the layout changes,
///             and is shaped with the mouse and shortcuts. It only lives on its
///             workspace. This is the startup regime.
///
/// `pin` means "present on every workspace": a compositor notion, not a
/// window one, which Tauri cannot set. It is also a toggle, not a setter (the
/// same order goes both ways), so the state read back afterwards is what
/// counts, never the one meant to be set.
///
/// The state holds: no compositor rule describes this window, so a config
/// reload does not reset it. At startup the window is free; only this button
/// pins it.
#[tauri::command]
async fn toggle_chat_pin(app: AppHandle) -> Result<bool, String> {
    let width = chat_width(&app)?;
    // Placements wait for the compositor up to a second: on the main thread,
    // that would be a second without keyboard or rendering.
    off_main_thread(move || toggle_pin(width))
        .await
        .unwrap_or_else(|| Err("the toggle was interrupted".to_string()))
}

fn toggle_pin(width: i64) -> Result<bool, String> {
    let chat = chat_client().ok_or("the compositor does not answer")?;
    hyprctl_write(&format!("dispatch pin address:{}", chat.address))?;
    // The order is synchronous, but the window is only read back afterwards:
    // without this short delay, the previous state would be read.
    std::thread::sleep(std::time::Duration::from_millis(60));
    let chat = chat_client().ok_or("the compositor does not answer")?;

    if chat.pinned {
        place_until(|| place_pinned(width))?;
    } else {
        place_until(place_free)?;
    }
    Ok(chat.pinned)
}

/// The clipboard text, read by GTK.
///
/// The page cannot read it without friction: under WebKitGTK,
/// `navigator.clipboard.readText` opens a "Paste" menu to confirm on every
/// paste, and a terminal where pasting takes two clicks is not a terminal. GTK
/// has no such guard: the application reads, not the page.
///
/// The GTK clipboard is only touched from the main thread; the read is
/// requested there, and the answer awaited elsewhere, so as not to block it.
///
/// A read that asks nobody: only the Chats window, which pastes into its
/// terminals, is allowed it (`capabilities/chats.json`).
#[cfg(target_os = "linux")]
#[tauri::command]
async fn clipboard_read(app: AppHandle) -> Result<String, String> {
    let (sender, receiver) = std::sync::mpsc::channel();
    app.run_on_main_thread(move || {
        gtk::Clipboard::get(&gtk::gdk::SELECTION_CLIPBOARD).request_text(move |_, text| {
            let _ = sender.send(text.unwrap_or_default().to_string());
        });
    })
    .map_err(|error| error.to_string())?;
    off_main_thread(move || receiver.recv_timeout(std::time::Duration::from_secs(2)))
        .await
        .and_then(Result::ok)
        .ok_or_else(|| "the clipboard did not answer".to_string())
}

/// Writes to the clipboard, through GTK for the same reason.
#[cfg(target_os = "linux")]
#[tauri::command]
fn clipboard_write(app: AppHandle, text: String) -> Result<(), String> {
    app.run_on_main_thread(move || {
        gtk::Clipboard::get(&gtk::gdk::SELECTION_CLIPBOARD).set_text(&text);
    })
    .map_err(|error| error.to_string())
}

/// Elsewhere, the front falls back on `navigator.clipboard`.
#[cfg(not(target_os = "linux"))]
#[tauri::command]
async fn clipboard_read() -> Result<String, String> {
    Err("native clipboard unavailable".to_string())
}

#[cfg(not(target_os = "linux"))]
#[tauri::command]
fn clipboard_write(_text: String) -> Result<(), String> {
    Err("native clipboard unavailable".to_string())
}

/// Shows a hidden window again, and gives it the keyboard.
fn show_window(app: &AppHandle, label: &str) {
    if let Some(window) = app.get_webview_window(label) {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

/// The tray icon: reopen the studio, restart it, or quit it.
///
/// Closing a window only hides it, so this is the only way out, and a clean
/// one: `exit` goes through `RunEvent::Exit`, which stops the server. Under
/// Linux the icon goes through AppIndicator, which does not pass clicks on: a
/// click opens the menu, and "Open" is at the top.
fn tray_menu(app: &tauri::App) -> tauri::Result<()> {
    // Each entry keeps both its texts: `set_language` translates it again.
    let entry = |id: &str, fr: &'static str, en: &'static str| {
        MenuItem::with_id(app, id, say(fr, en), true, None::<&str>).map(|item| (item, fr, en))
    };
    let open = entry("open", "Ouvrir gamestudio", "Open gamestudio")?;
    let studio = entry("studio", "Studio", "Studio")?;
    let chats = entry("chats", "Chats", "Chats")?;
    let restart = entry("restart", "Redémarrer", "Restart")?;
    let quit = entry("quit", "Quitter", "Quit")?;
    let menu = Menu::with_items(
        app,
        &[
            &open.0,
            &PredefinedMenuItem::separator(app)?,
            &studio.0,
            &chats.0,
            &PredefinedMenuItem::separator(app)?,
            &restart.0,
            &quit.0,
        ],
    )?;
    let mut tray_icon = TrayIconBuilder::with_id("gamestudio")
        .tooltip("gamestudio")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => {
                show_window(app, "main");
                show_window(app, "chat");
            }
            "studio" => show_window(app, "main"),
            "chats" => show_window(app, "chat"),
            "restart" => {
                let root = project_root();
                match restart_after_exit(&root, Relaunch::default_for(&root)) {
                    Ok(()) => app.exit(0),
                    // Without a relauncher, quitting would leave the user
                    // without a studio: stay open, and log the reason.
                    Err(error) => eprintln!("gamestudio: cannot restart ({error})"),
                }
            }
            "quit" => app.exit(0),
            _ => {}
        });
    if let Some(image) = app.default_window_icon() {
        tray_icon = tray_icon.icon(image.clone());
    }
    tray_icon.build(app)?;
    app.manage(TrayMenu(vec![open, studio, chats, restart, quit]));
    Ok(())
}

/// What the relauncher does once this process is gone.
#[derive(Clone, Copy)]
enum Relaunch {
    /// `make studio`: rebuild if needed, then launch.
    Studio,
    /// Relaunch the binary as is, rebuilding nothing.
    Direct,
}

impl Relaunch {
    /// How a restart relaunches: through `make studio` when the repository's
    /// Makefile is there, otherwise the binary as is. Without a Makefile,
    /// `make studio` would fail, and the relauncher would report a failed
    /// rebuild that never happened.
    fn default_for(root: &Path) -> Self {
        if update::has_makefile(root) {
            Relaunch::Studio
        } else {
            Relaunch::Direct
        }
    }
}

/// The relauncher: waits for this process to end, rebuilds if needed, relaunches.
///
/// "If needed" is the rule of `make studio`: the binary is rebuilt only when a
/// source of the front or the shell is newer than it. The rule is written
/// once, in the Makefile; copying it here would let it drift.
///
/// The relauncher is an `sh` detached by `setsid`: it outlives the shell, and
/// waits until the shell is gone before relaunching; otherwise the
/// single-instance lock would make the new one give way to the old one. A
/// failed rebuild relaunches the binary as it is: the old application beats
/// none. Logs go to `data/studio.log`.
///
/// What it launches carries `GAMESTUDIO_SKIP_UPDATE`: it comes out of an
/// update, successful or not, and must not run another at startup. That is
/// what keeps a broken build from looping the application.
fn restart_after_exit(root: &Path, relaunch: Relaunch) -> std::io::Result<()> {
    let binary = update::executable();
    let log_file = root.join("data").join("studio.log");
    if let Some(dir) = log_file.parent() {
        std::fs::create_dir_all(dir)?;
    }
    let script = r#"
pid="$1"; root="$2"; bin="$3"; log="$4"; mode="$5"; in_progress="$6"; failure="$7"
export GAMESTUDIO_SKIP_UPDATE=1
while kill -0 "$pid" 2>/dev/null; do sleep 0.2; done
echo "--- restart $(date '+%F %T')" >>"$log"
if [ "$mode" = studio ]; then
    case "$(make -s -C "$root" update-check 2>/dev/null)" in
        rebuild*) command -v notify-send >/dev/null 2>&1 &&
            notify-send -a gamestudio "gamestudio" "$in_progress" 2>/dev/null ;;
    esac
    make -C "$root" studio >>"$log" 2>&1 && exit 0
    echo "--- rebuild failed: relaunching the existing binary" >>"$log"
    command -v notify-send >/dev/null 2>&1 && notify-send -u critical -a gamestudio "gamestudio" "$failure" 2>/dev/null
fi
cd "$root" && exec "$bin" </dev/null >>"$log" 2>&1
"#;
    std::process::Command::new("setsid")
        .arg("-f")
        .arg("sh")
        .arg("-c")
        .arg(script)
        .arg("gamestudio-relaunch")
        .arg(std::process::id().to_string())
        .arg(root)
        .arg(&binary)
        .arg(&log_file)
        .arg(match relaunch {
            Relaunch::Studio => "studio",
            Relaunch::Direct => "direct",
        })
        .arg(say("Mise à jour de l'application…", "Updating the application…"))
        .arg(say("Reconstruction échouée — voir data/studio.log", "Rebuild failed — see data/studio.log"))
        .current_dir(root)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()?;
    Ok(())
}

/// Creates a window declared in `tauri.conf.json`.
///
/// They are all `create: false` there: at startup it is either the update
/// window, or the studio and the Chats; never all three, and never a page that
/// would need a server that was not launched.
fn open_window(app: &tauri::App, label: &str) -> tauri::Result<()> {
    let Some(config) = app.config().app.windows.iter().find(|w| w.label == label).cloned() else {
        return Ok(());
    };
    tauri::WebviewWindowBuilder::from_config(app.handle(), &config)?.build()?;
    Ok(())
}

/// The studio root, found once at startup (`server::root_dir`).
static ROOT: OnceLock<PathBuf> = OnceLock::new();

/// The studio root: the cloned repository whose server is launched.
///
/// Set by `run` before any window: the commands and the tray always find it
/// set.
fn project_root() -> PathBuf {
    ROOT.get().cloned().unwrap_or_else(|| PathBuf::from("."))
}

/// Refuses to start, and says so: on standard error, which goes to
/// `data/studio.log` under `make studio`, and as a notification, the only trace
/// of a launch from the applications menu, whose output goes nowhere.
fn refuse(reason: &str) -> ! {
    eprintln!("gamestudio: {reason}");
    let _ = std::process::Command::new("notify-send")
        .args(["-u", "critical", "-a", "gamestudio", "gamestudio"])
        .arg(reason)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
    std::process::exit(1);
}

/// Works around a WebKitGTK defect under Wayland with the NVIDIA driver.
///
/// DMABUF rendering fails there ("Error 71 (Protocol error) dispatching to
/// Wayland display") and the window does not open. Disabling DMABUF
/// (`WEBKIT_DISABLE_DMABUF_RENDERER=1`) opens the window, but every frame is
/// then copied by the CPU: on a terminal redrawing continuously (an agent at
/// work), 128 % of a core against 30 % with DMABUF, which makes typing in the
/// Chats sluggish.
///
/// The cause is the driver's explicit sync, not DMABUF: disabling it
/// (`__NV_DISABLE_EXPLICIT_SYNC=1`) opens the window and keeps GPU rendering.
/// Without NVIDIA, nothing is touched. A value already in the environment is
/// respected: `WEBKIT_DISABLE_DMABUF_RENDERER=1` remains the way out for a
/// machine where this is not enough. The variables must be set before GTK
/// initializes.
#[cfg(target_os = "linux")]
fn workaround_webkit() {
    let nvidia = Path::new("/proc/driver/nvidia/version").exists();
    if nvidia && std::env::var_os("__NV_DISABLE_EXPLICIT_SYNC").is_none() {
        std::env::set_var("__NV_DISABLE_EXPLICIT_SYNC", "1");
    }
}

#[cfg(not(target_os = "linux"))]
fn workaround_webkit() {}

/// Tauri's context, embedded front included: 11 MB of data that
/// `generate_context!` changes with every modified page. In its own module, a
/// modified page only recompiles it; expanded inside `run`, it would drag the
/// whole function and its closures along.
mod context {
    #[inline(never)]
    pub fn load() -> tauri::Context<tauri::Wry> {
        tauri::generate_context!()
    }
}

pub fn run() {
    update::record_launch();
    workaround_webkit();
    // Outside a studio repository there is no server to launch and no data to
    // read: say so and stop, rather than look elsewhere.
    let root = server::root_dir().unwrap_or_else(|reason| refuse(&reason));
    let _ = ROOT.set(root.clone());

    // The studio opens up to date with its code: if a source changed since the
    // last build, only the update window opens. No server: its dependencies
    // may be the ones being rebuilt, and the window does not need one. It
    // restarts the studio once ready.
    let needs_update = update::needed_at_startup(&root);
    let server = if needs_update {
        None
    } else {
        // Without a server there is nothing to show: saying so and stopping
        // beats an empty, silent window.
        let server = Server::start(&root).unwrap_or_else(|reason| refuse(&reason));
        Some(Arc::new(server))
    };
    let for_exit = server.clone();

    let mut builder = tauri::Builder::default()
        // First: a second instance must give way before opening anything, and
        // the first one shows its windows again.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_window(app, "update");
            show_window(app, "main");
            show_window(app, "chat");
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Arc::new(update::Build::default()));
    if let Some(server) = server {
        builder = builder.manage(server);
    }

    builder
        // The same list as `COMMANDS` in `build.rs`: a command added here is
        // declared there, then granted to the window that uses it.
        .invoke_handler(tauri::generate_handler![
            api_auth,
            update_status,
            update_build,
            update_progress,
            update_now,
            update_skip,
            show_chat,
            set_language,
            toggle_studio,
            chat_pinned,
            toggle_chat_pin,
            clipboard_read,
            clipboard_write
        ])
        .setup(move |app| {
            if needs_update {
                open_window(app, "update")?;
                return Ok(());
            }
            open_window(app, "main")?;
            open_window(app, "chat")?;
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
            }
            // Both windows open together: the Chats are the entry point, the
            // studio sits beside. The config declares the Chats visible and the
            // studio hidden (`visible: false`); the studio is shown here, once
            // the Chats exist, before `setup` ends, so `is_visible()` is true
            // from the toggle button's first call.
            // Closing a window hides it: the agent carries on, the server too,
            // and the tray icon reopens it or quits cleanly. Without a tray,
            // hiding the studio would make it impossible to quit: its close
            // button then stays the way out.
            let tray = match tray_menu(app) {
                Ok(()) => true,
                Err(error) => {
                    eprintln!("gamestudio: system tray unavailable ({error})");
                    false
                }
            };
            let hide_on_close: &[&str] = if tray { &["main", "chat"] } else { &["chat"] };
            for label in hide_on_close {
                if let Some(window) = app.get_webview_window(label) {
                    let handle = window.clone();
                    window.on_window_event(move |event| {
                        if let WindowEvent::CloseRequested { api, .. } = event {
                            api.prevent_close();
                            let _ = handle.hide();
                        }
                    });
                }
            }
            // At startup nothing is imposed: the Chats window opens like any
            // window, on the monitor in use, and is resized or moved like the
            // others. Pinning it is a choice, made with its button.
            //
            // Only its position and width are chosen: on the left, a quarter of
            // the monitor rather than the half tiling would give, at startup as
            // whenever a window joins it. Both windows only map after `setup`,
            // so listening starts before, on the side, without holding up the
            // startup.
            std::thread::spawn(watch_compositor);
            Ok(())
        })
        .build(context::load())
        .expect("building the Tauri application")
        .run(move |_handle, event| {
            // Quitting (the tray menu) must stop the server: that is the whole
            // point of the desktop application, no orphan process, no terminal
            // to keep open. Nothing to undo on the compositor side: everything
            // the application set there is an instant order, and the Chats
            // regime goes away with their window.
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                if let Some(server) = &for_exit {
                    server.stop();
                }
            }
        });
}
