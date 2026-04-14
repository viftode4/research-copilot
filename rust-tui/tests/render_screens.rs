use std::fs;
use std::path::{Path, PathBuf};

use ratatui::backend::TestBackend;
use ratatui::buffer::Buffer;
use ratatui::Terminal;
use research_copilot_rust_tui::app::{App, Screen};
use research_copilot_rust_tui::bridge::BackendMode;
use research_copilot_rust_tui::model::StatusEnvelope;
use research_copilot_rust_tui::{ui, Cli};

fn fixture_path(relative: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join(relative)
}

fn load_fixture(relative: &str) -> String {
    let path = fixture_path(relative);
    fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("failed to read fixture {}: {error}", path.display()))
}

fn make_cli(workspace: &Path) -> Cli {
    Cli {
        workspace: workspace.to_path_buf(),
        refresh_interval_ms: 2_000,
        backend_mode: BackendMode::Auto,
        cli_bin: "research-copilot".to_string(),
        python_bin: "python".to_string(),
        python_module: "research_copilot.main".to_string(),
        no_mouse: true,
    }
}

fn app_from_fixture(relative: &str) -> App {
    let raw = load_fixture(relative);
    let envelope = StatusEnvelope::parse_and_validate(&raw).expect("fixture should decode");
    let workspace = PathBuf::from(&envelope.workspace);
    let mut app = App::new(make_cli(&workspace), workspace);
    app.envelope = Some(envelope);
    app.status_line = "fixture render".to_string();
    app
}

fn render_to_text(app: &App) -> String {
    let backend = TestBackend::new(140, 42);
    let mut terminal = Terminal::new(backend).expect("terminal");
    terminal
        .draw(|frame| ui::render(frame, app))
        .expect("render should succeed");

    buffer_to_text(terminal.backend().buffer())
}

fn buffer_to_text(buffer: &Buffer) -> String {
    let area = buffer.area;
    let mut rows = Vec::with_capacity(area.height as usize);

    for y in 0..area.height {
        let mut row = String::with_capacity(area.width as usize);
        for x in 0..area.width {
            row.push_str(buffer[(x, y)].symbol());
        }
        rows.push(row.trim_end().to_string());
    }

    rows.join("\n")
}

#[test]
fn render_experiments_screen_shows_polished_summary_and_focus() {
    let mut app = app_from_fixture("status_experiments_research_rich.json");
    app.screen = Screen::Experiments;
    app.selected_experiment = 1;

    let rendered = render_to_text(&app);

    for needle in [
        "Portfolio",
        "Status lanes",
        "Experiment command deck",
        "Experiments queue (3)",
        "Selected experiment",
        "Result digest",
        "Ablation: warmup",
        "PFN + warmup",
        "Waiting for GPU capacity; hypothesis is smoother early-epoch stability.",
    ] {
        assert!(
            rendered.contains(needle),
            "missing `{needle}` in rendered experiment view:\n{rendered}"
        );
    }
}

#[test]
fn render_research_screen_shows_combined_feed_and_selected_record() {
    let mut app = app_from_fixture("status_experiments_research_rich.json");
    app.screen = Screen::Research;
    app.selected_research = 2;

    let rendered = render_to_text(&app);

    for needle in [
        "Research stream",
        "Knowledge feed (5)",
        "Selected record",
        "Why it matters",
        "Operator dashboards for research loops",
        "PAPER",
        "Use this context",
    ] {
        assert!(
            rendered.contains(needle),
            "missing `{needle}` in rendered research view:\n{rendered}"
        );
    }
}

#[test]
fn render_research_screen_empty_state_stays_read_only_and_legible() {
    let mut app = app_from_fixture("status_research_empty.json");
    app.screen = Screen::Research;

    let rendered = render_to_text(&app);

    for needle in [
        "Knowledge feed (0)",
        "No research records published yet.",
        "No research item selected.",
        "Collection digest",
        "Why it matters",
    ] {
        assert!(
            rendered.contains(needle),
            "missing `{needle}` in empty research view:\n{rendered}"
        );
    }
}

#[test]
fn render_research_screen_handles_runtime_null_without_losing_research_records() {
    let mut app = app_from_fixture("status_runtime_null.json");
    app.screen = Screen::Research;

    let rendered = render_to_text(&app);

    for needle in [
        "NO RUNTIME",
        "Selected record",
        "Runtime-free fallback contract",
        "Bridge can still render research records without live runtime metadata.",
    ] {
        assert!(
            rendered.contains(needle),
            "missing `{needle}` in runtime-null research view:\n{rendered}"
        );
    }
}
