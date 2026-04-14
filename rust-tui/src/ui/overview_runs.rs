use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, List, ListItem, ListState, Paragraph, Wrap};
use ratatui::Frame;

use crate::app::App;
use crate::model::{JobRecord, RuntimeRecord};

use super::shared::{kv_line, metric_line, pill, status_color, truncate_block, truncate_inline};

pub(super) fn render_overview(frame: &mut Frame, app: &App, area: Rect) {
    let chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(40), Constraint::Percentage(60)])
        .split(area);
    frame.render_widget(runtime_panel(app), chunks[0]);
    frame.render_widget(overview_panel(app), chunks[1]);
}

pub(super) fn render_runs(frame: &mut Frame, app: &App, area: Rect) {
    let chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(42), Constraint::Percentage(58)])
        .split(area);
    render_runs_list(frame, app, chunks[0]);
    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(12), Constraint::Min(7)])
        .split(chunks[1]);
    frame.render_widget(run_detail_panel(app), right[0]);
    frame.render_widget(run_log_panel(app), right[1]);
}

fn runtime_panel(app: &App) -> Paragraph<'static> {
    let lines = if let Some(runtime) = app.runtime() {
        vec![
            Line::from(vec![
                pill(
                    runtime.status.to_uppercase(),
                    Color::Black,
                    status_color(&runtime.status),
                ),
                Span::raw(" "),
                Span::raw(format!("phase {}", runtime.current_phase)),
            ]),
            kv_line("goal", truncate_block(&runtime.goal, 88)),
            kv_line("summary", truncate_block(&runtime.summary, 88)),
            kv_line(
                "transport",
                format!("{} {}", runtime.transport, runtime.pane_id),
            ),
            kv_line(
                "operator",
                format!(
                    "{} • {} nudges",
                    runtime.operator_mode, runtime.pending_nudge_count
                ),
            ),
        ]
    } else {
        vec![
            Line::from("No active runtime published yet."),
            Line::from("Refresh to load a snapshot."),
        ]
    };
    Paragraph::new(Text::from(lines))
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Runtime pulse "),
        )
        .wrap(Wrap { trim: true })
}

fn overview_panel(app: &App) -> Paragraph<'static> {
    let snapshot = app.snapshot();
    Paragraph::new(Text::from(vec![
        metric_line("runs", app.jobs().len()),
        metric_line("experiments", app.experiments().len()),
        metric_line("research", app.research_record_count()),
        kv_line(
            "focused run",
            app.selected_job_record()
                .map(|job| truncate_inline(&job.name, 36))
                .unwrap_or_else(|| "None".to_string()),
        ),
        kv_line(
            "top insight",
            snapshot
                .and_then(|s| s.insights.first())
                .map(|i| truncate_inline(&i.title, 44))
                .unwrap_or_else(|| "None".to_string()),
        ),
    ]))
    .block(Block::default().borders(Borders::ALL).title(" Overview "))
    .wrap(Wrap { trim: true })
}

fn render_runs_list(frame: &mut Frame, app: &App, area: Rect) {
    let jobs = app.jobs();
    let items: Vec<ListItem> = if jobs.is_empty() {
        vec![ListItem::new(Line::from("No runs published yet."))]
    } else {
        jobs.iter()
            .map(|job| {
                ListItem::new(vec![
                    Line::from(vec![
                        pill(
                            job.status.to_uppercase(),
                            Color::Black,
                            status_color(&job.status),
                        ),
                        Span::raw(" "),
                        Span::styled(
                            job.name.clone(),
                            Style::default().add_modifier(Modifier::BOLD),
                        ),
                    ]),
                    Line::from(vec![
                        Span::styled(job.partition.clone(), Style::default().fg(Color::Gray)),
                        Span::raw(" • "),
                        Span::styled(
                            truncate_inline(&job.submitted_at, 28),
                            Style::default().fg(Color::DarkGray),
                        ),
                    ]),
                ])
            })
            .collect()
    };
    let mut state = ListState::default();
    if !jobs.is_empty() {
        state.select(Some(app.selected_job.min(jobs.len().saturating_sub(1))));
    }
    frame.render_stateful_widget(
        List::new(items)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(format!(" Runs ({}) ", jobs.len())),
            )
            .highlight_style(
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD),
            )
            .highlight_symbol("▌ "),
        area,
        &mut state,
    );
}

fn run_detail_panel(app: &App) -> Paragraph<'static> {
    let text = app
        .selected_job_record()
        .map(|job| {
            Text::from(vec![
                Line::from(vec![
                    pill(
                        job.status.to_uppercase(),
                        Color::Black,
                        status_color(&job.status),
                    ),
                    Span::raw(" "),
                    Span::styled(
                        job.name.clone(),
                        Style::default()
                            .fg(Color::White)
                            .add_modifier(Modifier::BOLD),
                    ),
                ]),
                kv_line("job id", job.job_id.clone()),
                kv_line("partition", job.partition.clone()),
                kv_line("submitted", job.submitted_at.clone()),
                Line::from(""),
                Line::from(run_operator_summary(job, app.runtime())),
            ])
        })
        .unwrap_or_else(|| Text::from("Select a run to inspect details"));
    Paragraph::new(text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Selected run "),
        )
        .wrap(Wrap { trim: true })
}

fn run_log_panel(app: &App) -> Paragraph<'static> {
    let text = app
        .selected_job_record()
        .map(|job| {
            if job.log_tail.trim().is_empty() {
                Text::from("No log tail published yet.")
            } else {
                Text::from(
                    job.log_tail
                        .lines()
                        .map(|line| Line::from(line.to_string()))
                        .collect::<Vec<_>>(),
                )
            }
        })
        .unwrap_or_else(|| Text::from("Log output will appear here once a run is selected"));
    Paragraph::new(text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Latest log tail "),
        )
        .wrap(Wrap { trim: false })
}

fn run_operator_summary(job: &JobRecord, runtime: Option<&RuntimeRecord>) -> String {
    match runtime {
        Some(runtime) if runtime.status.eq_ignore_ascii_case("running") => format!(
            "{} is the currently focused run while the {} brain reports phase {}.",
            job.name, runtime.brain_driver, runtime.current_phase
        ),
        Some(runtime) => format!(
            "{} is focused while the runtime is {} and waiting for the next observation step.",
            job.name, runtime.status
        ),
        None => format!(
            "{} is visible from the last published snapshot; no live runtime metadata is attached.",
            job.name
        ),
    }
}
