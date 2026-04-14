use std::fs;
use std::path::{Path, PathBuf};

use ratatui::backend::TestBackend;
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

fn make_cli() -> Cli {
    Cli {
        workspace: PathBuf::from(env!("CARGO_MANIFEST_DIR")),
        refresh_interval_ms: 2000,
        backend_mode: BackendMode::Auto,
        cli_bin: "research-copilot".to_string(),
        python_bin: "python".to_string(),
        python_module: "research_copilot.main".to_string(),
        no_mouse: true,
    }
}

fn load_fixture(relative: &str) -> StatusEnvelope {
    let path = fixture_path(relative);
    let raw = fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("failed to read fixture {}: {error}", path.display()));
    StatusEnvelope::parse_and_validate(&raw)
        .unwrap_or_else(|error| panic!("failed to parse fixture {}: {error}", path.display()))
}

fn render_app(app: &App, width: u16, height: u16) -> String {
    let backend = TestBackend::new(width, height);
    let mut terminal = Terminal::new(backend).expect("terminal");
    terminal.draw(|frame| ui::render(frame, app)).expect("draw");
    let buffer = terminal.backend().buffer();

    (0..height)
        .map(|y| {
            (0..width)
                .map(|x| buffer[(x, y)].symbol())
                .collect::<String>()
                .trim_end()
                .to_string()
        })
        .collect::<Vec<_>>()
        .join("\n")
}

#[test]
fn polished_fixture_exercises_multiple_experiments_and_research_records() {
    let envelope = load_fixture("status_polished.json");
    let snapshot = &envelope.data.expect("data").snapshot;

    assert_eq!(snapshot.experiments.len(), 3);
    assert_eq!(snapshot.insights.len(), 2);
    assert_eq!(snapshot.papers.len(), 2);
    assert_eq!(snapshot.context_entries.len(), 2);
    assert_eq!(
        snapshot.runtime.as_ref().expect("runtime").brain_driver,
        "codex"
    );
}

#[test]
fn experiments_screen_render_shows_polished_queue_and_selected_summary() {
    let mut app = App::new(make_cli(), PathBuf::from(env!("CARGO_MANIFEST_DIR")));
    app.envelope = Some(load_fixture("status_polished.json"));
    app.screen = Screen::Experiments;
    app.selected_experiment = 1;
    app.status_line = "3 experiments • queued focus".to_string();

    let rendered = render_app(&app, 120, 40);

    for needle in [
        "Portfolio",
        "Status lanes",
        "Experiments queue (3)",
        "Experiment command deck",
        "Results + next read",
        "Ablation sweep",
        "OpenML-CTR23",
        "XGBoost + PFN",
        "Waiting for cluster capacity before running the full ablation matrix.",
    ] {
        assert!(
            rendered.contains(needle),
            "missing `{needle}` in:\n{rendered}"
        );
    }
}

#[test]
fn research_screen_render_shows_combined_feed_and_context_guidance() {
    let mut app = App::new(make_cli(), PathBuf::from(env!("CARGO_MANIFEST_DIR")));
    app.envelope = Some(load_fixture("status_polished.json"));
    app.screen = Screen::Research;
    app.selected_research = 5;
    app.status_line = "6 research items • context focus".to_string();

    let rendered = render_app(&app, 120, 40);

    for needle in [
        "Research stream",
        "Knowledge feed (6)",
        "Selected record",
        "Why it matters",
        "CONTEXT",
        "operator_note",
        "Keep the Rust dashboard read-only while the Python workflow layer remains authoritative.",
        "Use this context",
        "read-only",
    ] {
        assert!(
            rendered.contains(needle),
            "missing `{needle}` in:\n{rendered}"
        );
    }
}
