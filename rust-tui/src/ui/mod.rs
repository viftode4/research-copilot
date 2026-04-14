use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, Clear, Paragraph, Tabs};
use ratatui::Frame;

use crate::app::{App, Screen};

mod experiments_research;
mod overview_runs;
mod shared;

use experiments_research::{render_experiments, render_research};
use overview_runs::{render_overview, render_runs};
use shared::{centered_rect, footer, help_popup, pill};

pub fn render(frame: &mut Frame, app: &App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(5),
            Constraint::Length(3),
            Constraint::Min(12),
            Constraint::Length(2),
        ])
        .split(frame.area());

    frame.render_widget(header(app), chunks[0]);
    frame.render_widget(tabs(app), chunks[1]);
    render_body(frame, app, chunks[2]);
    frame.render_widget(footer(app), chunks[3]);

    if app.show_help {
        let area = centered_rect(72, 68, frame.area());
        frame.render_widget(Clear, area);
        frame.render_widget(help_popup(), area);
    }
}

fn header(app: &App) -> Paragraph<'static> {
    let runtime = app.runtime();
    let snapshot = app.snapshot();
    let mut lines = vec![Line::from(vec![
        Span::styled(
            "Research Copilot",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw("  "),
        pill("Rust TUI", Color::Black, Color::Cyan),
        Span::raw(" "),
        pill("read-only", Color::Black, Color::Yellow),
        Span::raw(" "),
        runtime
            .map(|r| pill(r.brain_driver.to_uppercase(), Color::Black, Color::Magenta))
            .unwrap_or_else(|| pill("NO RUNTIME", Color::Black, Color::DarkGray)),
    ])];
    lines.push(Line::from(vec![
        Span::styled("workspace ", Style::default().fg(Color::DarkGray)),
        Span::raw(app.workspace.display().to_string()),
        Span::raw("   "),
        Span::styled("view ", Style::default().fg(Color::DarkGray)),
        Span::raw(app.screen.title().to_string()),
        Span::raw("   "),
        Span::styled("snapshot ", Style::default().fg(Color::DarkGray)),
        Span::raw(
            snapshot
                .map(|s| s.snapshot_state.as_str())
                .unwrap_or("waiting")
                .to_string(),
        ),
    ]));
    lines.push(Line::from(vec![
        Span::styled("status ", Style::default().fg(Color::DarkGray)),
        Span::raw(app.status_line.clone()),
    ]));
    Paragraph::new(Text::from(lines)).block(
        Block::default()
            .borders(Borders::ALL)
            .title(" Mission control "),
    )
}

fn tabs(app: &App) -> Tabs<'static> {
    let titles: Vec<Line<'static>> = Screen::ALL
        .iter()
        .map(|screen| Line::from(format!(" {} ", screen.title())))
        .collect();
    let selected = Screen::ALL
        .iter()
        .position(|screen| *screen == app.screen)
        .unwrap_or_default();
    Tabs::new(titles)
        .select(selected)
        .block(Block::default().borders(Borders::ALL).title(" Views "))
        .style(Style::default().fg(Color::Gray))
        .highlight_style(
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        )
        .divider("│")
}

fn render_body(frame: &mut Frame, app: &App, area: Rect) {
    match app.screen {
        Screen::Overview => render_overview(frame, app, area),
        Screen::Runs => render_runs(frame, app, area),
        Screen::Experiments => render_experiments(frame, app, area),
        Screen::Research => render_research(frame, app, area),
    }
}
