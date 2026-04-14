use std::collections::BTreeSet;

use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, List, ListItem, ListState, Paragraph, Wrap};
use ratatui::Frame;

use crate::app::{App, ResearchItem};
use crate::model::{DashboardSnapshot, ExperimentRecord};

use super::shared::{
    fallback_text, kv_line, metric_line, non_empty, pill, research_kind_color, status_color,
    truncate_inline,
};

pub(super) fn render_experiments(frame: &mut Frame, app: &App, area: Rect) {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(7), Constraint::Min(12)])
        .split(area);
    let summary = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(30),
            Constraint::Percentage(34),
            Constraint::Percentage(36),
        ])
        .split(rows[0]);
    let body = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(12), Constraint::Min(8)])
        .split(rows[1]);
    let top = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(38), Constraint::Percentage(62)])
        .split(body[0]);
    frame.render_widget(experiment_inventory_card(app), summary[0]);
    frame.render_widget(experiment_status_card(app), summary[1]);
    frame.render_widget(experiment_focus_card(app), summary[2]);
    render_experiments_list(frame, app, top[0]);
    frame.render_widget(experiment_detail_panel(app), top[1]);
    frame.render_widget(experiment_results_panel(app), body[1]);
}

fn experiment_inventory_card(app: &App) -> Paragraph<'static> {
    let experiments = app.experiments();
    let datasets = experiments
        .iter()
        .filter_map(|e| (!e.dataset.trim().is_empty()).then_some(e.dataset.as_str()))
        .collect::<BTreeSet<_>>()
        .len();
    let models = experiments
        .iter()
        .filter_map(|e| (!e.model_type.trim().is_empty()).then_some(e.model_type.as_str()))
        .collect::<BTreeSet<_>>()
        .len();
    let selected = if experiments.is_empty() {
        "none".to_string()
    } else {
        format!("{} / {}", app.selected_experiment + 1, experiments.len())
    };
    Paragraph::new(Text::from(vec![
        metric_line("total experiments", experiments.len()),
        kv_line("selected", selected),
        kv_line("datasets", datasets.to_string()),
        kv_line("model families", models.to_string()),
    ]))
    .block(Block::default().borders(Borders::ALL).title(" Portfolio "))
    .wrap(Wrap { trim: true })
}

fn experiment_status_card(app: &App) -> Paragraph<'static> {
    let mut lines = vec![Line::from(Span::styled(
        "Status lanes",
        Style::default()
            .fg(Color::Cyan)
            .add_modifier(Modifier::BOLD),
    ))];
    if let Some(snapshot) = app.snapshot() {
        for (status, count) in snapshot.experiment_status_counts.iter().take(4) {
            lines.push(Line::from(vec![
                pill(status.to_uppercase(), Color::Black, status_color(status)),
                Span::raw(" "),
                Span::raw(count.to_string()),
            ]));
        }
    }
    Paragraph::new(Text::from(lines))
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Health board "),
        )
        .wrap(Wrap { trim: true })
}

fn experiment_focus_card(app: &App) -> Paragraph<'static> {
    let text = app
        .selected_experiment_record()
        .map(|e| {
            Text::from(vec![
                Line::from(vec![
                    pill(
                        e.status.to_uppercase(),
                        Color::Black,
                        status_color(&e.status),
                    ),
                    Span::raw(" "),
                    Span::styled(
                        truncate_inline(non_empty(&e.name, &e.experiment_id), 26),
                        Style::default().fg(Color::White),
                    ),
                ]),
                kv_line(
                    "dataset/model",
                    format!(
                        "{} • {}",
                        fallback_text(&e.dataset),
                        fallback_text(&e.model_type)
                    ),
                ),
                kv_line("updated", truncate_inline(&e.updated_at, 28)),
                kv_line("navigation", "j/k focus • read-only bridge"),
            ])
        })
        .unwrap_or_else(|| Text::from("No experiment selected."));
    Paragraph::new(text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Experiment command deck "),
        )
        .wrap(Wrap { trim: true })
}

fn render_experiments_list(frame: &mut Frame, app: &App, area: Rect) {
    let experiments = app.experiments();
    let items: Vec<ListItem> = if experiments.is_empty() {
        vec![ListItem::new(Line::from(
            "No experiments published by the snapshot yet.",
        ))]
    } else {
        experiments.iter().map(experiment_list_item).collect()
    };
    let mut state = ListState::default();
    if !experiments.is_empty() {
        state.select(Some(
            app.selected_experiment
                .min(experiments.len().saturating_sub(1)),
        ));
    }
    frame.render_stateful_widget(
        List::new(items)
            .block(
                Block::default()
                    .title(format!(" Experiments queue ({}) ", experiments.len()))
                    .borders(Borders::ALL),
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

fn experiment_list_item(e: &ExperimentRecord) -> ListItem<'static> {
    ListItem::new(vec![
        Line::from(vec![
            pill(
                e.status.to_uppercase(),
                Color::Black,
                status_color(&e.status),
            ),
            Span::raw(" "),
            Span::styled(
                non_empty(&e.name, &e.experiment_id).to_string(),
                Style::default().add_modifier(Modifier::BOLD),
            ),
        ]),
        Line::from(vec![
            Span::styled(
                format!(
                    "{} • {}",
                    fallback_text(&e.dataset),
                    fallback_text(&e.model_type)
                ),
                Style::default().fg(Color::Gray),
            ),
            Span::raw(" • "),
            Span::styled(
                truncate_inline(&e.updated_at, 24),
                Style::default().fg(Color::DarkGray),
            ),
        ]),
        Line::from(Span::styled(
            truncate_inline(&e.results_summary, 72),
            Style::default().fg(Color::DarkGray),
        )),
    ])
}

fn experiment_detail_panel(app: &App) -> Paragraph<'static> {
    let text = app
        .selected_experiment_record()
        .map(|e| {
            Text::from(vec![
                Line::from(vec![
                    pill(
                        e.status.to_uppercase(),
                        Color::Black,
                        status_color(&e.status),
                    ),
                    Span::raw(" "),
                    Span::styled(
                        non_empty(&e.name, &e.experiment_id).to_string(),
                        Style::default()
                            .fg(Color::White)
                            .add_modifier(Modifier::BOLD),
                    ),
                ]),
                kv_line("experiment id", fallback_text(&e.experiment_id)),
                kv_line("dataset", fallback_text(&e.dataset)),
                kv_line("model", fallback_text(&e.model_type)),
                kv_line("updated", fallback_text(&e.updated_at)),
                Line::from(""),
                Line::from(experiment_operator_summary(e)),
            ])
        })
        .unwrap_or_else(|| Text::from("Select an experiment to inspect detail"));
    Paragraph::new(text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Selected experiment "),
        )
        .wrap(Wrap { trim: true })
}

fn experiment_results_panel(app: &App) -> Paragraph<'static> {
    let text = app.selected_experiment_record().map(|e| Text::from(vec![
        Line::from(Span::styled("Result digest", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD))),
        Line::from(fallback_text(&e.results_summary)),
        Line::from(""),
        Line::from(Span::styled("Why it matters", Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD))),
        Line::from("The queue on the left lets you skim status and summaries quickly, while the right side keeps the selected experiment readable without losing the board."),
        Line::from(""),
        kv_line("bridge", "Python remains the source of truth for mutations"),
        kv_line("navigation", "j/k focuses experiments • Tab or 1-4 switches views"),
    ])).unwrap_or_else(|| Text::from("Result summaries will appear here once an experiment is selected."));
    Paragraph::new(text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Results + next read "),
        )
        .wrap(Wrap { trim: true })
}

pub(super) fn render_research(frame: &mut Frame, app: &App, area: Rect) {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(7), Constraint::Min(12)])
        .split(area);
    let summary = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(30),
            Constraint::Percentage(32),
            Constraint::Percentage(38),
        ])
        .split(rows[0]);
    let body = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(12), Constraint::Min(8)])
        .split(rows[1]);
    let top = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(38), Constraint::Percentage(62)])
        .split(body[0]);
    frame.render_widget(research_inventory_card(app), summary[0]);
    frame.render_widget(research_feed_card(app), summary[1]);
    frame.render_widget(research_focus_card(app), summary[2]);
    render_research_list(frame, app, top[0]);
    frame.render_widget(research_detail_panel(app), top[1]);
    frame.render_widget(research_library_panel(app), body[1]);
}

fn research_inventory_card(app: &App) -> Paragraph<'static> {
    let total = app.research_record_count();
    let selected = if total == 0 {
        "none".to_string()
    } else {
        format!("{} / {}", app.selected_research + 1, total)
    };
    Paragraph::new(Text::from(vec![
        metric_line("total items", total),
        kv_line("insights", app.insights().len().to_string()),
        kv_line("papers", app.papers().len().to_string()),
        kv_line("context", app.context_entries().len().to_string()),
        kv_line("selected", selected),
    ]))
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title(" Research inventory "),
    )
    .wrap(Wrap { trim: true })
}

fn research_feed_card(app: &App) -> Paragraph<'static> {
    let text = if let Some(snapshot) = app.snapshot() {
        Text::from(vec![
            Line::from(vec![
                pill("INSIGHT", Color::Black, research_kind_color("Insight")),
                Span::raw(" "),
                Span::raw(
                    snapshot
                        .insights
                        .first()
                        .map(|item| truncate_inline(&item.title, 24))
                        .unwrap_or_else(|| "None yet".to_string()),
                ),
            ]),
            Line::from(vec![
                pill("PAPER", Color::Black, research_kind_color("Paper")),
                Span::raw(" "),
                Span::raw(
                    snapshot
                        .papers
                        .first()
                        .map(|item| truncate_inline(&item.title, 24))
                        .unwrap_or_else(|| "None yet".to_string()),
                ),
            ]),
            Line::from(vec![
                pill("CONTEXT", Color::Black, research_kind_color("Context")),
                Span::raw(" "),
                Span::raw(
                    snapshot
                        .context_entries
                        .first()
                        .map(|item| truncate_inline(&item.key, 24))
                        .unwrap_or_else(|| "None yet".to_string()),
                ),
            ]),
        ])
    } else {
        Text::from("Waiting for snapshot…")
    };
    Paragraph::new(text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Research stream "),
        )
        .wrap(Wrap { trim: true })
}

fn research_focus_card(app: &App) -> Paragraph<'static> {
    let text = app
        .selected_research_item()
        .map(|item| {
            Text::from(vec![
                Line::from(vec![
                    pill(
                        item.kind().to_uppercase(),
                        Color::Black,
                        research_kind_color(item.kind()),
                    ),
                    Span::raw(" "),
                    Span::styled(
                        truncate_inline(item.title(), 28),
                        Style::default().fg(Color::White),
                    ),
                ]),
                kv_line("meta", fallback_text(&item.meta())),
                kv_line("accent", fallback_text(item.accent())),
                kv_line("navigation", "j/k focus • unified research queue"),
                kv_line("mission", "brain-agnostic, read-only operator view"),
            ])
        })
        .unwrap_or_else(|| Text::from("No research item selected."));
    Paragraph::new(text)
        .block(Block::default().borders(Borders::ALL).title(" Focus rail "))
        .wrap(Wrap { trim: true })
}

fn render_research_list(frame: &mut Frame, app: &App, area: Rect) {
    let total = app.research_record_count();
    let items: Vec<ListItem> = if total == 0 {
        vec![ListItem::new(Line::from(
            "No research records published yet.",
        ))]
    } else {
        app.insights()
            .iter()
            .map(|r| research_list_item(ResearchItem::Insight(r)))
            .chain(
                app.papers()
                    .iter()
                    .map(|r| research_list_item(ResearchItem::Paper(r))),
            )
            .chain(
                app.context_entries()
                    .iter()
                    .map(|r| research_list_item(ResearchItem::Context(r))),
            )
            .collect()
    };
    let mut state = ListState::default();
    if total > 0 {
        state.select(Some(app.selected_research.min(total.saturating_sub(1))));
    }
    frame.render_stateful_widget(
        List::new(items)
            .block(
                Block::default()
                    .title(format!(" Knowledge feed ({}) ", total))
                    .borders(Borders::ALL),
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

fn research_list_item(item: ResearchItem<'_>) -> ListItem<'static> {
    ListItem::new(vec![
        Line::from(vec![
            pill(
                item.kind().to_uppercase(),
                Color::Black,
                research_kind_color(item.kind()),
            ),
            Span::raw(" "),
            Span::styled(
                truncate_inline(item.title(), 48),
                Style::default().add_modifier(Modifier::BOLD),
            ),
        ]),
        Line::from(vec![
            Span::styled(item.meta(), Style::default().fg(Color::Gray)),
            Span::raw(" • "),
            Span::styled(
                truncate_inline(item.body(), 56),
                Style::default().fg(Color::DarkGray),
            ),
        ]),
    ])
}

fn research_detail_panel(app: &App) -> Paragraph<'static> {
    let text = app
        .selected_research_item()
        .map(|item| {
            Text::from(vec![
                Line::from(vec![
                    pill(
                        item.kind().to_uppercase(),
                        Color::Black,
                        research_kind_color(item.kind()),
                    ),
                    Span::raw(" "),
                    Span::styled(
                        truncate_inline(item.title(), 52),
                        Style::default()
                            .fg(Color::White)
                            .add_modifier(Modifier::BOLD),
                    ),
                ]),
                kv_line("meta", fallback_text(&item.meta())),
                kv_line("accent", fallback_text(item.accent())),
                Line::from(""),
                Line::from(Span::styled(
                    "Use this context",
                    Style::default()
                        .fg(Color::Cyan)
                        .add_modifier(Modifier::BOLD),
                )),
                Line::from(fallback_text(item.body())),
            ])
        })
        .unwrap_or_else(|| Text::from("Select a research item to inspect detail"));
    Paragraph::new(text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Selected record "),
        )
        .wrap(Wrap { trim: true })
}

fn research_library_panel(app: &App) -> Paragraph<'static> {
    let text = app
        .snapshot()
        .map(|snapshot| research_library_text(snapshot, app.selected_research_item()))
        .unwrap_or_else(|| Text::from("Research notes will appear here after the first refresh."));
    Paragraph::new(text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Library notes "),
        )
        .wrap(Wrap { trim: true })
}

fn research_library_text(
    snapshot: &DashboardSnapshot,
    selected: Option<ResearchItem<'_>>,
) -> Text<'static> {
    let mut lines = vec![
        Line::from(Span::styled(
            "Collection digest",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
        kv_line(
            "top insight",
            snapshot
                .insights
                .first()
                .map(|item| truncate_inline(&item.title, 54))
                .unwrap_or_else(|| "None yet".to_string()),
        ),
        kv_line(
            "top paper",
            snapshot
                .papers
                .first()
                .map(|item| truncate_inline(&item.title, 54))
                .unwrap_or_else(|| "None yet".to_string()),
        ),
        kv_line(
            "top context",
            snapshot
                .context_entries
                .first()
                .map(|item| truncate_inline(&item.key, 54))
                .unwrap_or_else(|| "None yet".to_string()),
        ),
        Line::from(""),
        Line::from(Span::styled(
            "Why it matters",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
        Line::from("One unified queue makes it easier to scan what the brain is learning without bouncing between separate lists."),
        Line::from("The detail pane keeps the selected record readable while preserving the broader research feed."),
    ];
    if let Some(item) = selected {
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            "Use this context",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )));
        lines.push(Line::from(fallback_text(item.body())));
    }
    Text::from(lines)
}

fn experiment_operator_summary(e: &ExperimentRecord) -> String {
    format!("{} is in {} on dataset {} using {}. Keep the Python workflow commands as the mutation path while the Rust board focuses on clarity.", non_empty(&e.name, &e.experiment_id), fallback_text(&e.status), fallback_text(&e.dataset), fallback_text(&e.model_type))
}
