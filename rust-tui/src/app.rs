use std::path::PathBuf;

use tokio::sync::mpsc::UnboundedSender;

use crate::bridge;
use crate::model::{
    ContextRecord, DashboardSnapshot, ExperimentRecord, InsightRecord, JobRecord, PaperRecord,
    RuntimeRecord, StatusEnvelope,
};
use crate::Cli;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Screen {
    Overview,
    Runs,
    Experiments,
    Research,
}

impl Screen {
    pub const ALL: [Screen; 4] = [
        Screen::Overview,
        Screen::Runs,
        Screen::Experiments,
        Screen::Research,
    ];

    pub fn title(self) -> &'static str {
        match self {
            Screen::Overview => "Overview",
            Screen::Runs => "Runs",
            Screen::Experiments => "Experiments",
            Screen::Research => "Research",
        }
    }
}

#[derive(Debug)]
pub enum RefreshMessage {
    Loaded(Result<StatusEnvelope, bridge::BridgeError>),
}

#[derive(Clone, Copy, Debug)]
pub enum ResearchItem<'a> {
    Insight(&'a InsightRecord),
    Paper(&'a PaperRecord),
    Context(&'a ContextRecord),
}

impl<'a> ResearchItem<'a> {
    pub fn kind(self) -> &'static str {
        match self {
            ResearchItem::Insight(_) => "Insight",
            ResearchItem::Paper(_) => "Paper",
            ResearchItem::Context(_) => "Context",
        }
    }

    pub fn title(self) -> &'a str {
        match self {
            ResearchItem::Insight(record) => &record.title,
            ResearchItem::Paper(record) => &record.title,
            ResearchItem::Context(record) => &record.key,
        }
    }

    pub fn accent(self) -> &'a str {
        match self {
            ResearchItem::Insight(record) => &record.confidence,
            ResearchItem::Paper(record) => &record.year,
            ResearchItem::Context(record) => &record.context_type,
        }
    }

    pub fn body(self) -> &'a str {
        match self {
            ResearchItem::Insight(record) => &record.content,
            ResearchItem::Paper(record) => &record.relevance_notes,
            ResearchItem::Context(record) => &record.value,
        }
    }

    pub fn meta(self) -> String {
        match self {
            ResearchItem::Insight(record) => format!("confidence {}", record.confidence),
            ResearchItem::Paper(record) => format!("year {}", record.year),
            ResearchItem::Context(record) => format!("type {}", record.context_type),
        }
    }
}

pub struct App {
    pub cli: Cli,
    pub workspace: PathBuf,
    pub screen: Screen,
    pub selected_job: usize,
    pub selected_experiment: usize,
    pub selected_research: usize,
    pub show_help: bool,
    pub should_quit: bool,
    pub last_error: Option<String>,
    pub status_line: String,
    pub refresh_in_flight: bool,
    pub envelope: Option<StatusEnvelope>,
}

impl App {
    pub fn new(cli: Cli, workspace: PathBuf) -> Self {
        Self {
            cli,
            workspace,
            screen: Screen::Overview,
            selected_job: 0,
            selected_experiment: 0,
            selected_research: 0,
            show_help: false,
            should_quit: false,
            last_error: None,
            status_line: "starting Rust TUI scaffold".to_string(),
            refresh_in_flight: false,
            envelope: None,
        }
    }

    pub fn cycle_screen(&mut self) {
        let current = Screen::ALL
            .iter()
            .position(|screen| *screen == self.screen)
            .unwrap_or_default();
        self.screen = Screen::ALL[(current + 1) % Screen::ALL.len()];
    }

    pub fn move_selection(&mut self, delta: isize) {
        let len = match self.screen {
            Screen::Overview | Screen::Runs => self.jobs().len(),
            Screen::Experiments => self.experiments().len(),
            Screen::Research => self.research_record_count(),
        };

        if len == 0 {
            return;
        }

        let update = |index: &mut usize| {
            let next = (*index as isize + delta).rem_euclid(len as isize) as usize;
            *index = next;
        };

        match self.screen {
            Screen::Overview | Screen::Runs => update(&mut self.selected_job),
            Screen::Experiments => update(&mut self.selected_experiment),
            Screen::Research => update(&mut self.selected_research),
        }
    }

    pub fn request_refresh(&mut self, tx: &UnboundedSender<RefreshMessage>) {
        if self.refresh_in_flight {
            return;
        }
        self.refresh_in_flight = true;
        self.status_line = "refreshing backend snapshot…".to_string();
        let cli = self.cli.clone();
        let workspace = self.workspace.clone();
        let tx = tx.clone();
        tokio::spawn(async move {
            let result = bridge::load_status(&cli, &workspace).await;
            let _ = tx.send(RefreshMessage::Loaded(result));
        });
    }

    pub fn handle_refresh(&mut self, message: RefreshMessage) {
        self.refresh_in_flight = false;
        match message {
            RefreshMessage::Loaded(Ok(envelope)) => {
                self.last_error = None;
                self.envelope = Some(envelope);
                self.sync_selection();
                self.status_line = self
                    .snapshot()
                    .map(|snapshot| {
                        let runtime = snapshot
                            .runtime
                            .as_ref()
                            .map(|runtime| runtime.status.as_str())
                            .unwrap_or("idle");
                        format!(
                            "{} runs • {} experiments • {} research items • runtime {runtime}",
                            snapshot.jobs.len(),
                            snapshot.experiments.len(),
                            snapshot.insights.len()
                                + snapshot.papers.len()
                                + snapshot.context_entries.len()
                        )
                    })
                    .unwrap_or_else(|| "backend responded with no snapshot payload".to_string());
            }
            RefreshMessage::Loaded(Err(error)) => self.set_error(error.to_string()),
        }
    }

    pub fn set_error(&mut self, message: String) {
        self.last_error = Some(message);
        self.status_line = "backend refresh failed".to_string();
        self.refresh_in_flight = false;
    }

    pub fn set_status(&mut self, message: String) {
        self.status_line = message;
    }

    pub fn snapshot(&self) -> Option<&DashboardSnapshot> {
        self.envelope
            .as_ref()
            .and_then(|envelope| envelope.data.as_ref())
            .map(|data| &data.snapshot)
    }

    pub fn jobs(&self) -> &[JobRecord] {
        self.snapshot()
            .map(|snapshot| snapshot.jobs.as_slice())
            .unwrap_or(&[])
    }

    pub fn experiments(&self) -> &[ExperimentRecord] {
        self.snapshot()
            .map(|snapshot| snapshot.experiments.as_slice())
            .unwrap_or(&[])
    }

    pub fn insights(&self) -> &[InsightRecord] {
        self.snapshot()
            .map(|snapshot| snapshot.insights.as_slice())
            .unwrap_or(&[])
    }

    pub fn papers(&self) -> &[PaperRecord] {
        self.snapshot()
            .map(|snapshot| snapshot.papers.as_slice())
            .unwrap_or(&[])
    }

    pub fn context_entries(&self) -> &[ContextRecord] {
        self.snapshot()
            .map(|snapshot| snapshot.context_entries.as_slice())
            .unwrap_or(&[])
    }

    pub fn runtime(&self) -> Option<&RuntimeRecord> {
        self.snapshot()
            .and_then(|snapshot| snapshot.runtime.as_ref())
    }

    pub fn selected_job_record(&self) -> Option<&JobRecord> {
        let jobs = self.jobs();
        jobs.get(self.selected_job.min(jobs.len().saturating_sub(1)))
    }

    pub fn selected_experiment_record(&self) -> Option<&ExperimentRecord> {
        let experiments = self.experiments();
        experiments.get(
            self.selected_experiment
                .min(experiments.len().saturating_sub(1)),
        )
    }

    pub fn selected_research_item(&self) -> Option<ResearchItem<'_>> {
        let mut index = self.selected_research;
        let insights = self.insights();
        if index < insights.len() {
            return insights.get(index).map(ResearchItem::Insight);
        }

        index = index.saturating_sub(insights.len());
        let papers = self.papers();
        if index < papers.len() {
            return papers.get(index).map(ResearchItem::Paper);
        }

        index = index.saturating_sub(papers.len());
        self.context_entries().get(index).map(ResearchItem::Context)
    }

    pub fn research_record_count(&self) -> usize {
        self.snapshot()
            .map(|snapshot| {
                snapshot.insights.len() + snapshot.papers.len() + snapshot.context_entries.len()
            })
            .unwrap_or(0)
    }

    fn sync_selection(&mut self) {
        let job_len = self.jobs().len();
        let experiment_len = self.experiments().len();
        let research_len = self.research_record_count();
        clamp_index(&mut self.selected_job, job_len);
        clamp_index(&mut self.selected_experiment, experiment_len);
        clamp_index(&mut self.selected_research, research_len);
    }
}

fn clamp_index(index: &mut usize, len: usize) {
    if len == 0 {
        *index = 0;
    } else if *index >= len {
        *index = len - 1;
    }
}
