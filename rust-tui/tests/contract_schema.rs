use std::fs;
use std::path::{Path, PathBuf};

use serde_json::Value;

fn fixture_path(relative: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join(relative)
}

fn load_json_fixture(relative: &str) -> Value {
    let path = fixture_path(relative);
    let raw = fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("failed to read fixture {}: {error}", path.display()));
    serde_json::from_str(&raw)
        .unwrap_or_else(|error| panic!("failed to parse fixture {}: {error}", path.display()))
}

fn require_object<'a>(value: &'a Value, path: &str) -> &'a serde_json::Map<String, Value> {
    value
        .as_object()
        .unwrap_or_else(|| panic!("expected object at {path}"))
}

fn require_key<'a>(object: &'a serde_json::Map<String, Value>, key: &str, path: &str) -> &'a Value {
    object
        .get(key)
        .unwrap_or_else(|| panic!("missing required key {path}.{key}"))
}

fn validate_status_contract(value: &Value) {
    let root = require_object(value, "$");
    for key in ["ok", "schema_version", "timestamp", "workspace", "data"] {
        require_key(root, key, "$");
    }

    let data = require_object(require_key(root, "data", "$"), "$.data");
    for key in ["config", "integrations", "workspace", "snapshot"] {
        require_key(data, key, "$.data");
    }

    let snapshot = require_object(require_key(data, "snapshot", "$.data"), "$.data.snapshot");
    for key in [
        "schema_version",
        "snapshot_owner",
        "snapshot_state",
        "jobs",
        "experiments",
        "insights",
        "papers",
        "context_entries",
        "experiment_status_counts",
        "runtime",
    ] {
        require_key(snapshot, key, "$.data.snapshot");
    }
}

fn detect_module_marker(workspace: &Path) -> bool {
    if workspace
        .join("src")
        .join("research_copilot")
        .join("main.py")
        .exists()
    {
        true
    } else {
        false
    }
}

#[test]
fn minimal_fixture_satisfies_required_contract() {
    let value = load_json_fixture("status_minimal.json");
    validate_status_contract(&value);

    let snapshot = &value["data"]["snapshot"];
    assert_eq!(snapshot["jobs"].as_array().map(Vec::len), Some(1));
    assert_eq!(snapshot["experiments"].as_array().map(Vec::len), Some(1));
    assert_eq!(snapshot["runtime"]["brain_driver"].as_str(), Some("codex"));
}

#[test]
fn additive_fixture_documents_forward_compatible_fields() {
    let value = load_json_fixture("status_additive.json");
    validate_status_contract(&value);

    assert_eq!(
        value["extra_top_level"]["source"].as_str(),
        Some("future-cli")
    );
    assert_eq!(
        value["data"]["snapshot"]["runtime"]["transport_metadata"]["session_name"].as_str(),
        Some("research-runtime")
    );
    assert_eq!(
        value["data"]["snapshot"]["jobs"][0]["labels"]["owner"].as_str(),
        Some("team-red")
    );
}

#[test]
fn polished_fixture_documents_richer_screen_data_without_changing_contract() {
    let value = load_json_fixture("status_polished.json");
    validate_status_contract(&value);

    let snapshot = &value["data"]["snapshot"];
    assert_eq!(snapshot["experiments"].as_array().map(Vec::len), Some(3));
    assert_eq!(snapshot["insights"].as_array().map(Vec::len), Some(2));
    assert_eq!(snapshot["papers"].as_array().map(Vec::len), Some(2));
    assert_eq!(
        snapshot["context_entries"].as_array().map(Vec::len),
        Some(2)
    );
    assert_eq!(
        snapshot["context_entries"][1]["value"].as_str(),
        Some("Keep the Rust dashboard read-only while the Python workflow layer remains authoritative.")
    );
}

#[test]
fn missing_snapshot_fixture_fails_contract_guard() {
    let value = load_json_fixture("status_missing_snapshot.json");
    let error = std::panic::catch_unwind(|| validate_status_contract(&value))
        .expect_err("fixture should violate the required contract");
    let message = if let Some(message) = error.downcast_ref::<String>() {
        message.clone()
    } else if let Some(message) = error.downcast_ref::<&str>() {
        message.to_string()
    } else {
        String::from("unknown panic")
    };

    assert!(
        message.contains("$.data.snapshot"),
        "unexpected panic: {message}"
    );
}

#[test]
fn bridge_fixture_layouts_match_documented_trust_rules() {
    let module_workspace = fixture_path("workspaces/module_workspace");
    let cli_workspace = fixture_path("workspaces/cli_workspace");

    assert!(detect_module_marker(&module_workspace));
    assert!(!detect_module_marker(&cli_workspace));
}
