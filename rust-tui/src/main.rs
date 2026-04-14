use std::io::{self, Stdout};
use std::time::Duration;

use anyhow::Result;
use clap::Parser;
use crossterm::event::{Event as CrosstermEvent, EventStream, KeyCode, KeyEventKind};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use futures::StreamExt;
use ratatui::backend::CrosstermBackend;
use ratatui::Terminal;
use research_copilot_rust_tui::app::{App, RefreshMessage, Screen};
use research_copilot_rust_tui::ui;
use research_copilot_rust_tui::Cli;
use tracing::error;

struct TerminalGuard {
    mouse_enabled: bool,
}

impl Drop for TerminalGuard {
    fn drop(&mut self) {
        let _ = restore_terminal(self.mouse_enabled);
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    let workspace = cli
        .workspace
        .canonicalize()
        .unwrap_or_else(|_| cli.workspace.clone());
    let mouse_enabled = !cli.no_mouse;

    init_tracing();
    install_panic_hook(mouse_enabled);

    let mut terminal = setup_terminal(mouse_enabled)?;
    let _guard = TerminalGuard { mouse_enabled };

    let mut app = App::new(cli.clone(), workspace);
    let mut reader = EventStream::new();
    let mut tick = tokio::time::interval(Duration::from_millis(cli.refresh_interval_ms));
    let (refresh_tx, mut refresh_rx) = tokio::sync::mpsc::unbounded_channel::<RefreshMessage>();

    app.request_refresh(&refresh_tx);

    while !app.should_quit {
        terminal.draw(|frame| ui::render(frame, &app))?;

        tokio::select! {
            _ = tick.tick() => {
                app.request_refresh(&refresh_tx);
            }
            Some(message) = refresh_rx.recv() => {
                app.handle_refresh(message);
            }
            maybe_event = reader.next() => {
                match maybe_event {
                    Some(Ok(event)) => handle_event(&mut app, event, &refresh_tx),
                    Some(Err(error)) => app.set_error(format!("event stream error: {error}")),
                    None => break,
                }
            }
        }
    }

    terminal.show_cursor()?;
    Ok(())
}

fn handle_event(
    app: &mut App,
    event: CrosstermEvent,
    refresh_tx: &tokio::sync::mpsc::UnboundedSender<RefreshMessage>,
) {
    match event {
        CrosstermEvent::Key(key) if key.kind == KeyEventKind::Press => match key.code {
            KeyCode::Char('q') => app.should_quit = true,
            KeyCode::Char('r') => app.request_refresh(refresh_tx),
            KeyCode::Char('?') => app.show_help = !app.show_help,
            KeyCode::Char('1') => app.screen = Screen::Overview,
            KeyCode::Char('2') => app.screen = Screen::Runs,
            KeyCode::Char('3') => app.screen = Screen::Experiments,
            KeyCode::Char('4') => app.screen = Screen::Research,
            KeyCode::Tab => app.cycle_screen(),
            KeyCode::Char('j') | KeyCode::Down => app.move_selection(1),
            KeyCode::Char('k') | KeyCode::Up => app.move_selection(-1),
            _ => {}
        },
        CrosstermEvent::Resize(width, height) => {
            app.set_status(format!("resized to {width}x{height}"))
        }
        CrosstermEvent::Mouse(_) => {}
        _ => {}
    }
}

fn setup_terminal(mouse_enabled: bool) -> Result<Terminal<CrosstermBackend<Stdout>>> {
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    if mouse_enabled {
        execute!(stdout, crossterm::event::EnableMouseCapture)?;
    }
    let backend = CrosstermBackend::new(stdout);
    Ok(Terminal::new(backend)?)
}

fn restore_terminal(mouse_enabled: bool) -> Result<()> {
    disable_raw_mode()?;
    let mut stdout = io::stdout();
    if mouse_enabled {
        execute!(stdout, crossterm::event::DisableMouseCapture)?;
    }
    execute!(stdout, LeaveAlternateScreen)?;
    Ok(())
}

fn init_tracing() {
    use tracing_subscriber::EnvFilter;

    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    let _ = tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_target(false)
        .try_init();
}

fn install_panic_hook(mouse_enabled: bool) {
    let hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |panic_info| {
        if let Err(error) = restore_terminal(mouse_enabled) {
            error!("failed to restore terminal after panic: {error}");
        }
        hook(panic_info);
    }));
}
