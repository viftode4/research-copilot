use std::path::PathBuf;

use clap::Parser;

pub mod app;
pub mod bridge;
pub mod model;
pub mod ui;

#[derive(Clone, Debug, Parser)]
#[command(
    name = "research-copilot-rust-tui",
    about = "Experimental Ratatui dashboard for Research Copilot"
)]
pub struct Cli {
    #[arg(long, default_value = ".")]
    pub workspace: PathBuf,
    #[arg(long, default_value_t = 2000)]
    pub refresh_interval_ms: u64,
    #[arg(long, value_enum, default_value_t = bridge::BackendMode::Auto)]
    pub backend_mode: bridge::BackendMode,
    #[arg(long, default_value = "research-copilot")]
    pub cli_bin: String,
    #[arg(long, default_value = "python")]
    pub python_bin: String,
    #[arg(long, default_value = "research_copilot.main")]
    pub python_module: String,
    #[arg(long, default_value_t = false)]
    pub no_mouse: bool,
}
