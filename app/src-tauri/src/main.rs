//! The binary's entry point: it runs the shell defined in `lib.rs`.

// On Windows, a graphical application must not open a console: leaving the
// terminal behind is the point of the desktop shell.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    gamestudio_lib::run()
}
