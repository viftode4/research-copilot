use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, Paragraph, Wrap};

use crate::app::{App, Screen};

pub(super) fn footer(app: &App) -> Paragraph<'static> {
    if app.screen == Screen::Research {
        if let Some(item) = app.selected_research_item() {
            let body = fallback_text(item.body());
            if body.len() < 100 {
                return Paragraph::new(Line::from(body));
            }
        }
    }
    let mut hints = vec![
        Span::styled("1-4", Style::default().add_modifier(Modifier::BOLD)),
        Span::raw(" views • "),
        Span::styled("Tab", Style::default().add_modifier(Modifier::BOLD)),
        Span::raw(" cycle • "),
        Span::styled("j/k", Style::default().add_modifier(Modifier::BOLD)),
        Span::raw(" move focus • "),
        Span::styled("r", Style::default().add_modifier(Modifier::BOLD)),
        Span::raw(" refresh • "),
        Span::styled("?", Style::default().add_modifier(Modifier::BOLD)),
        Span::raw(" help • "),
        Span::styled("q", Style::default().add_modifier(Modifier::BOLD)),
        Span::raw(" quit"),
    ];
    if app.refresh_in_flight {
        hints.push(Span::raw(" • "));
        hints.push(Span::styled(
            "refresh in progress",
            Style::default().fg(Color::Yellow),
        ));
    }
    Paragraph::new(Line::from(hints))
}

pub(super) fn help_popup() -> Paragraph<'static> {
    Paragraph::new(Text::from(vec![
        Line::from(Span::styled(
            "Phase 2 Rust TUI",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from(""),
        Line::from("Intent:"),
        Line::from("- keep the bridge read-only"),
        Line::from("- make runtime, runs, experiments, and research legible at a glance"),
        Line::from("- borrow the density and hierarchy of tools like lazygit/lazydocker"),
        Line::from(""),
        Line::from("Keyboard:"),
        Line::from("1-4  switch views directly"),
        Line::from("Tab  cycle views"),
        Line::from("j/k  move the focused item inside the active screen"),
        Line::from("r    refresh the Python snapshot bridge"),
        Line::from("?    toggle this help"),
        Line::from("q    quit"),
    ]))
    .block(Block::default().title(" Help ").borders(Borders::ALL))
    .wrap(Wrap { trim: true })
}

pub(super) fn status_color(status: &str) -> Color {
    let s = status.to_ascii_lowercase();
    if [
        "running",
        "active",
        "success",
        "healthy",
        "complete",
        "completed",
    ]
    .iter()
    .any(|item| s.contains(item))
    {
        Color::Green
    } else if ["queued", "pending", "stale", "warning"]
        .iter()
        .any(|item| s.contains(item))
    {
        Color::Yellow
    } else if ["failed", "error", "dead", "cancelled", "canceled"]
        .iter()
        .any(|item| s.contains(item))
    {
        Color::Red
    } else {
        Color::Blue
    }
}

pub(super) fn research_kind_color(kind: &str) -> Color {
    match kind {
        "Insight" => Color::Magenta,
        "Paper" => Color::Cyan,
        "Context" => Color::Yellow,
        _ => Color::Blue,
    }
}

pub(super) fn pill(label: impl Into<String>, fg: Color, bg: Color) -> Span<'static> {
    Span::styled(
        format!(" {} ", label.into()),
        Style::default().fg(fg).bg(bg).add_modifier(Modifier::BOLD),
    )
}

pub(super) fn kv_line(label: &str, value: impl Into<String>) -> Line<'static> {
    Line::from(vec![
        Span::styled(format!("{label} "), Style::default().fg(Color::DarkGray)),
        Span::raw(value.into()),
    ])
}

pub(super) fn metric_line(label: &str, value: usize) -> Line<'static> {
    Line::from(vec![
        Span::styled(
            format!("{value:>2}"),
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw("  "),
        Span::styled(label.to_string(), Style::default().fg(Color::Gray)),
    ])
}

pub(super) fn fallback_text(input: &str) -> String {
    if input.trim().is_empty() {
        "—".to_string()
    } else {
        input.to_string()
    }
}

pub(super) fn non_empty<'a>(preferred: &'a str, fallback: &'a str) -> &'a str {
    if preferred.trim().is_empty() {
        fallback
    } else {
        preferred
    }
}

pub(super) fn truncate_inline(input: &str, max_chars: usize) -> String {
    let mut chars = input.chars();
    let visible: String = chars.by_ref().take(max_chars).collect();
    if chars.next().is_some() {
        format!("{visible}…")
    } else {
        visible
    }
}

pub(super) fn truncate_block(input: &str, max_chars: usize) -> String {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        "—".to_string()
    } else {
        truncate_inline(trimmed, max_chars)
    }
}

pub(super) fn centered_rect(percent_x: u16, percent_y: u16, area: Rect) -> Rect {
    let popup = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y) / 2),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y) / 2),
        ])
        .split(area);
    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x) / 2),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x) / 2),
        ])
        .split(popup[1])[1]
}
