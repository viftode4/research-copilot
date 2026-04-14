use std::collections::BTreeMap;

use serde::Deserialize;
use serde_json::Value;
use thiserror::Error;

const SUPPORTED_ENVELOPE_SCHEMA_MAJOR: &str = "1";
const SUPPORTED_SNAPSHOT_SCHEMA_MAJOR: &str = "1";

#[derive(Debug, Error)]
pub enum ContractError {
    #[error("backend returned empty stdout")]
    EmptyResponse,
    #[error("backend JSON was invalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("backend reported failure: {message}")]
    BackendReportedFailure { message: String },
    #[error("backend contract is missing required field `{path}`")]
    MissingField { path: String },
    #[error("backend contract field `{path}` must be {expected}, got {actual}")]
    InvalidType {
        path: String,
        expected: &'static str,
        actual: &'static str,
    },
    #[error(
        "backend contract field `{path}` uses unsupported schema `{actual}` (expected major version {supported_major}.x)"
    )]
    UnsupportedSchema {
        path: String,
        supported_major: &'static str,
        actual: String,
    },
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct StatusEnvelope {
    pub ok: bool,
    pub schema_version: String,
    pub timestamp: String,
    pub workspace: String,
    pub data: Option<StatusData>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct StatusData {
    pub config: ConfigSummary,
    pub integrations: IntegrationSummary,
    pub workspace: WorkspaceSummary,
    pub snapshot: DashboardSnapshot,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct ConfigSummary {
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub budget_usd: f64,
    #[serde(default)]
    pub api_key_set: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub struct IntegrationSummary {
    #[serde(default)]
    pub arxiv: bool,
    #[serde(default)]
    pub wandb: bool,
    #[serde(default)]
    pub slurm: bool,
    #[serde(default)]
    pub semantic_scholar_api_key: bool,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct WorkspaceSummary {
    #[serde(default)]
    pub directory: String,
    #[serde(default)]
    pub invocation_directory: String,
    #[serde(default)]
    pub mode: String,
    #[serde(default)]
    pub research_root: String,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct DashboardSnapshot {
    pub schema_version: String,
    pub snapshot_owner: String,
    pub snapshot_state: String,
    #[serde(default)]
    pub runtime: Option<RuntimeRecord>,
    pub jobs: Vec<JobRecord>,
    pub experiments: Vec<ExperimentRecord>,
    pub insights: Vec<InsightRecord>,
    pub papers: Vec<PaperRecord>,
    pub context_entries: Vec<ContextRecord>,
    pub experiment_status_counts: BTreeMap<String, usize>,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Deserialize)]
pub struct RuntimeRecord {
    #[serde(default)]
    pub brain_driver: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub current_phase: String,
    #[serde(default)]
    pub goal: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub last_action: String,
    #[serde(default)]
    pub freshness_label: String,
    #[serde(default)]
    pub freshness_state: String,
    #[serde(default)]
    pub operator_mode: String,
    #[serde(default)]
    pub pending_nudge_count: usize,
    #[serde(default)]
    pub transport: String,
    #[serde(default)]
    pub pane_id: String,
    #[serde(default)]
    pub session_id: String,
    #[serde(default)]
    pub run_id: String,
    #[serde(default)]
    pub workspace: String,
    #[serde(default)]
    pub window_name: String,
    #[serde(default)]
    pub last_heartbeat_at: String,
    #[serde(default)]
    pub last_experiment_id: String,
    #[serde(default)]
    pub is_active: bool,
    #[serde(default)]
    pub is_stale: bool,
    #[serde(default)]
    pub iteration: usize,
}

#[derive(Clone, Debug, Deserialize)]
pub struct JobRecord {
    #[serde(default)]
    pub job_id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub partition: String,
    #[serde(default)]
    pub submitted_at: String,
    #[serde(default)]
    pub log_tail: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct ExperimentRecord {
    #[serde(default)]
    pub experiment_id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub dataset: String,
    #[serde(default)]
    pub model_type: String,
    #[serde(default)]
    pub updated_at: String,
    #[serde(default)]
    pub results_summary: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct InsightRecord {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub confidence: String,
    #[serde(default)]
    pub content: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct PaperRecord {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub year: String,
    #[serde(default)]
    pub relevance_notes: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct ContextRecord {
    #[serde(default)]
    pub key: String,
    #[serde(default)]
    pub context_type: String,
    #[serde(default)]
    pub value: String,
}

impl StatusEnvelope {
    pub fn parse_and_validate(raw: &str) -> Result<Self, ContractError> {
        if raw.trim().is_empty() {
            return Err(ContractError::EmptyResponse);
        }

        let value: Value = serde_json::from_str(raw)?;
        validate_contract(&value)?;
        Ok(serde_json::from_value(value)?)
    }
}

fn validate_contract(root: &Value) -> Result<(), ContractError> {
    let root = expect_object(root, "$")?;
    let ok = expect_bool(root, "ok", "ok")?;
    let envelope_schema = expect_string(root, "schema_version", "schema_version")?;
    ensure_supported_schema(
        "schema_version",
        envelope_schema,
        SUPPORTED_ENVELOPE_SCHEMA_MAJOR,
    )?;
    expect_string(root, "timestamp", "timestamp")?;
    expect_string(root, "workspace", "workspace")?;

    if !ok {
        let message = root
            .get("error")
            .and_then(Value::as_object)
            .and_then(|error| error.get("message"))
            .and_then(Value::as_str)
            .unwrap_or("backend returned ok=false without an error.message")
            .to_string();
        return Err(ContractError::BackendReportedFailure { message });
    }

    let data = expect_object_field(root, "data", "data")?;
    expect_object_field(data, "config", "data.config")?;
    expect_object_field(data, "integrations", "data.integrations")?;
    expect_object_field(data, "workspace", "data.workspace")?;
    let snapshot = expect_object_field(data, "snapshot", "data.snapshot")?;

    let snapshot_schema =
        expect_string(snapshot, "schema_version", "data.snapshot.schema_version")?;
    ensure_supported_schema(
        "data.snapshot.schema_version",
        snapshot_schema,
        SUPPORTED_SNAPSHOT_SCHEMA_MAJOR,
    )?;
    expect_string(snapshot, "snapshot_owner", "data.snapshot.snapshot_owner")?;
    expect_string(snapshot, "snapshot_state", "data.snapshot.snapshot_state")?;
    expect_array_field(snapshot, "jobs", "data.snapshot.jobs")?;
    expect_array_field(snapshot, "experiments", "data.snapshot.experiments")?;
    expect_array_field(snapshot, "insights", "data.snapshot.insights")?;
    expect_array_field(snapshot, "papers", "data.snapshot.papers")?;
    expect_array_field(snapshot, "context_entries", "data.snapshot.context_entries")?;
    expect_object_field(
        snapshot,
        "experiment_status_counts",
        "data.snapshot.experiment_status_counts",
    )?;

    let runtime = expect_field(snapshot, "runtime", "data.snapshot.runtime")?;
    if !runtime.is_null() && !runtime.is_object() {
        return Err(ContractError::InvalidType {
            path: "data.snapshot.runtime".to_string(),
            expected: "object or null",
            actual: json_type_name(runtime),
        });
    }

    Ok(())
}

fn expect_field<'a>(
    parent: &'a serde_json::Map<String, Value>,
    key: &str,
    path: &str,
) -> Result<&'a Value, ContractError> {
    parent.get(key).ok_or_else(|| ContractError::MissingField {
        path: path.to_string(),
    })
}

fn expect_object_field<'a>(
    parent: &'a serde_json::Map<String, Value>,
    key: &str,
    path: &str,
) -> Result<&'a serde_json::Map<String, Value>, ContractError> {
    expect_object(expect_field(parent, key, path)?, path)
}

fn expect_array_field<'a>(
    parent: &'a serde_json::Map<String, Value>,
    key: &str,
    path: &str,
) -> Result<&'a Vec<Value>, ContractError> {
    let value = expect_field(parent, key, path)?;
    value.as_array().ok_or_else(|| ContractError::InvalidType {
        path: path.to_string(),
        expected: "array",
        actual: json_type_name(value),
    })
}

fn expect_object<'a>(
    value: &'a Value,
    path: &str,
) -> Result<&'a serde_json::Map<String, Value>, ContractError> {
    value.as_object().ok_or_else(|| ContractError::InvalidType {
        path: path.to_string(),
        expected: "object",
        actual: json_type_name(value),
    })
}

fn expect_bool<'a>(
    parent: &'a serde_json::Map<String, Value>,
    key: &str,
    path: &str,
) -> Result<bool, ContractError> {
    let value = expect_field(parent, key, path)?;
    value.as_bool().ok_or_else(|| ContractError::InvalidType {
        path: path.to_string(),
        expected: "boolean",
        actual: json_type_name(value),
    })
}

fn expect_string<'a>(
    parent: &'a serde_json::Map<String, Value>,
    key: &str,
    path: &str,
) -> Result<&'a str, ContractError> {
    let value = expect_field(parent, key, path)?;
    value.as_str().ok_or_else(|| ContractError::InvalidType {
        path: path.to_string(),
        expected: "string",
        actual: json_type_name(value),
    })
}

fn ensure_supported_schema(
    path: &str,
    actual: &str,
    supported_major: &'static str,
) -> Result<(), ContractError> {
    let actual_major = actual.split('.').next().unwrap_or_default();
    if actual_major == supported_major {
        Ok(())
    } else {
        Err(ContractError::UnsupportedSchema {
            path: path.to_string(),
            supported_major,
            actual: actual.to_string(),
        })
    }
}

fn json_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decodes_status_fixture_contract() {
        let fixture = include_str!("../tests/fixtures/status_minimal.json");
        let envelope = StatusEnvelope::parse_and_validate(fixture)
            .expect("fixture should decode and validate");

        assert!(envelope.ok);
        assert_eq!(envelope.schema_version, "1.0");
        let data = envelope.data.expect("data");
        assert_eq!(data.snapshot.schema_version, "1.0.0");
        assert_eq!(data.snapshot.jobs.len(), 1);
        assert_eq!(data.snapshot.experiments.len(), 1);
        assert!(data.snapshot.runtime.is_some());
        assert_eq!(
            data.snapshot.runtime.as_ref().expect("runtime").session_id,
            "codex-runtime-smoke-812c5304"
        );
    }

    #[test]
    fn missing_required_snapshot_field_is_rejected() {
        let raw = r#"{
          "ok": true,
          "schema_version": "1.0",
          "timestamp": "2026-04-14T00:00:00+00:00",
          "workspace": "G:\\Projects\\research-copilot",
          "data": {
            "config": {},
            "integrations": {},
            "workspace": {},
            "snapshot": {
              "schema_version": "1.0.0",
              "snapshot_owner": "tests",
              "snapshot_state": "complete",
              "jobs": [],
              "experiments": [],
              "insights": [],
              "papers": [],
              "context_entries": [],
              "experiment_status_counts": {}
            }
          }
        }"#;

        let error = StatusEnvelope::parse_and_validate(raw).expect_err("missing runtime");
        assert!(matches!(
            error,
            ContractError::MissingField { path } if path == "data.snapshot.runtime"
        ));
    }

    #[test]
    fn backend_failure_envelope_surfaces_error_message() {
        let raw = r#"{
          "ok": false,
          "schema_version": "1.0",
          "timestamp": "2026-04-14T00:00:00+00:00",
          "workspace": "G:\\Projects\\research-copilot",
          "data": null,
          "error": {
            "code": "BROKEN",
            "message": "backend failed before snapshot generation"
          }
        }"#;

        let error = StatusEnvelope::parse_and_validate(raw).expect_err("ok=false");
        assert!(matches!(
            error,
            ContractError::BackendReportedFailure { message }
                if message == "backend failed before snapshot generation"
        ));
    }
}
