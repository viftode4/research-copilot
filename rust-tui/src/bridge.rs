use std::path::Path;
use std::process::Stdio;

use clap::ValueEnum;
use thiserror::Error;
use tokio::process::Command;

use crate::model::{ContractError, StatusEnvelope};
use crate::Cli;

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
pub enum BackendMode {
    Auto,
    Cli,
    Module,
}

#[derive(Debug, Error)]
pub enum BridgeError {
    #[error("failed to spawn backend command in {mode:?} mode: {source}")]
    Spawn {
        mode: BackendMode,
        #[source]
        source: std::io::Error,
    },
    #[error("backend command failed in {mode:?} mode: {message}")]
    CommandFailed { mode: BackendMode, message: String },
    #[error("{0}")]
    Contract(#[from] ContractError),
    #[error("auto backend detection failed after trying: {attempts}")]
    AutoBackendFailed { attempts: String },
    #[error(
        "module backend mode executes workspace-local Python and is disabled unless RC_RUST_TUI_TRUST_LOCAL_CODE=1 is set"
    )]
    UntrustedModuleMode,
}

pub async fn load_status(cli: &Cli, workspace: &Path) -> Result<StatusEnvelope, BridgeError> {
    let mut recoverable_failures = Vec::new();

    for mode in backend_attempt_order(cli, workspace) {
        match load_status_with_mode(cli, workspace, mode).await {
            Ok(envelope) => return Ok(envelope),
            Err(error) if cli.backend_mode == BackendMode::Auto && error.is_recoverable() => {
                recoverable_failures.push(format!("{mode:?}: {error}"));
            }
            Err(error) => return Err(error),
        }
    }

    Err(BridgeError::AutoBackendFailed {
        attempts: recoverable_failures.join(" | "),
    })
}

fn backend_attempt_order(cli: &Cli, _workspace: &Path) -> Vec<BackendMode> {
    match cli.backend_mode {
        BackendMode::Auto => vec![BackendMode::Cli],
        explicit => vec![explicit],
    }
}

async fn load_status_with_mode(
    cli: &Cli,
    workspace: &Path,
    mode: BackendMode,
) -> Result<StatusEnvelope, BridgeError> {
    let stdout = match mode {
        BackendMode::Cli => run_cli(cli, workspace).await?,
        BackendMode::Module => run_module(cli, workspace).await?,
        BackendMode::Auto => unreachable!("auto mode should be expanded before dispatch"),
    };

    Ok(StatusEnvelope::parse_and_validate(&stdout)?)
}

async fn run_cli(cli: &Cli, workspace: &Path) -> Result<String, BridgeError> {
    let mut command = Command::new(&cli.cli_bin);
    command
        .current_dir(workspace)
        .arg("--workspace")
        .arg(workspace)
        .arg("status")
        .arg("--json");
    run_command(command, BackendMode::Cli).await
}

async fn run_module(cli: &Cli, workspace: &Path) -> Result<String, BridgeError> {
    enforce_module_trust_gate()?;
    let mut command = Command::new(&cli.python_bin);
    command
        .current_dir(workspace)
        .arg("-m")
        .arg(&cli.python_module)
        .arg("--workspace")
        .arg(workspace)
        .arg("status")
        .arg("--json");
    if let Some(pythonpath) = repo_pythonpath(workspace) {
        command.env("PYTHONPATH", pythonpath);
    }
    run_command(command, BackendMode::Module).await
}

fn enforce_module_trust_gate() -> Result<(), BridgeError> {
    let trusted = std::env::var("RC_RUST_TUI_TRUST_LOCAL_CODE")
        .map(|value| value == "1" || value.eq_ignore_ascii_case("true"))
        .unwrap_or(false);
    if trusted {
        Ok(())
    } else {
        Err(BridgeError::UntrustedModuleMode)
    }
}

fn repo_pythonpath(workspace: &Path) -> Option<String> {
    let src = workspace.join("src");
    if !src.join("research_copilot").exists() {
        return None;
    }

    let mut paths = vec![src.display().to_string()];
    if let Ok(existing) = std::env::var("PYTHONPATH") {
        if !existing.trim().is_empty() {
            paths.push(existing);
        }
    }
    Some(join_paths(paths))
}

#[cfg(windows)]
fn join_paths(paths: Vec<String>) -> String {
    paths.join(";")
}

#[cfg(not(windows))]
fn join_paths(paths: Vec<String>) -> String {
    paths.join(":")
}

async fn run_command(mut command: Command, mode: BackendMode) -> Result<String, BridgeError> {
    command.stderr(Stdio::piped()).stdout(Stdio::piped());
    let output = command
        .output()
        .await
        .map_err(|source| BridgeError::Spawn { mode, source })?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        let message = if !stderr.is_empty() {
            stderr
        } else if !stdout.is_empty() {
            format!("stdout: {stdout}")
        } else {
            format!("exit status {}", output.status)
        };
        return Err(BridgeError::CommandFailed { mode, message });
    }

    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

impl BridgeError {
    fn is_recoverable(&self) -> bool {
        matches!(
            self,
            BridgeError::Spawn { .. } | BridgeError::CommandFailed { .. }
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_dir(label: &str) -> PathBuf {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("duration")
            .as_nanos();
        let path = std::env::temp_dir().join(format!("research-copilot-rust-tui-{label}-{suffix}"));
        fs::create_dir_all(&path).expect("workspace dir");
        path
    }

    fn status_fixture() -> String {
        fs::read_to_string(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("tests")
                .join("fixtures")
                .join("status_minimal.json"),
        )
        .expect("fixture")
    }

    fn make_cli(workspace: PathBuf, backend_mode: BackendMode) -> Cli {
        Cli {
            workspace,
            refresh_interval_ms: 2000,
            backend_mode,
            cli_bin: "research-copilot".to_string(),
            python_bin: "python".to_string(),
            python_module: "research_copilot.main".to_string(),
            no_mouse: true,
        }
    }

    #[test]
    fn auto_mode_prefers_cli_only_even_when_repo_markers_exist() {
        let workspace = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("manifest parent")
            .to_path_buf();
        let cli = make_cli(workspace.clone(), BackendMode::Auto);

        assert_eq!(
            backend_attempt_order(&cli, &workspace),
            vec![BackendMode::Cli]
        );
    }

    #[test]
    fn auto_mode_uses_cli_only_when_repo_markers_are_missing() {
        let workspace = temp_dir("auto-cli");
        let cli = make_cli(workspace.clone(), BackendMode::Auto);

        assert_eq!(
            backend_attempt_order(&cli, &workspace),
            vec![BackendMode::Cli]
        );

        fs::remove_dir_all(workspace).expect("cleanup temp dir");
    }

    #[test]
    fn repo_pythonpath_prepends_src_and_preserves_existing_pythonpath() {
        let workspace = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("manifest parent")
            .to_path_buf();
        let original = std::env::var("PYTHONPATH").ok();
        std::env::set_var("PYTHONPATH", "custom-path");

        let pythonpath = repo_pythonpath(&workspace).expect("repo pythonpath");
        let expected_prefix = workspace.join("src").display().to_string();

        assert!(pythonpath.starts_with(&expected_prefix));
        assert!(pythonpath.contains("custom-path"));

        if let Some(original) = original {
            std::env::set_var("PYTHONPATH", original);
        } else {
            std::env::remove_var("PYTHONPATH");
        }
    }

    #[test]
    fn module_mode_requires_explicit_trust_gate() {
        std::env::remove_var("RC_RUST_TUI_TRUST_LOCAL_CODE");
        let error = enforce_module_trust_gate().expect_err("module mode should require trust");
        assert!(matches!(error, BridgeError::UntrustedModuleMode));
    }

    #[tokio::test]
    async fn cli_mode_load_status_smoke_works_with_stub_cli() {
        let workspace = temp_dir("cli-smoke");
        let fixture_path = workspace.join("status.json");
        fs::write(&fixture_path, status_fixture()).expect("fixture write");
        let cli_script = workspace.join("research-copilot.cmd");
        fs::write(
            &cli_script,
            format!("@echo off\r\ntype \"{}\"\r\n", fixture_path.display()),
        )
        .expect("cli script");

        let mut cli = make_cli(workspace.clone(), BackendMode::Cli);
        cli.cli_bin = cli_script.display().to_string();

        let envelope = load_status(&cli, &workspace)
            .await
            .expect("cli smoke should pass");
        assert!(envelope.ok);
        assert_eq!(
            envelope.data.expect("data").snapshot.schema_version,
            "1.0.0"
        );

        fs::remove_dir_all(workspace).expect("cleanup");
    }

    #[tokio::test]
    async fn auto_mode_fails_closed_without_falling_back_to_module() {
        let workspace = temp_dir("auto-fail-closed");
        let pkg_dir = workspace.join("src").join("research_copilot");
        fs::create_dir_all(&pkg_dir).expect("pkg dir");
        fs::write(pkg_dir.join("__init__.py"), "").expect("init");
        fs::write(pkg_dir.join("main.py"), "print('module should not run')").expect("main");

        let mut cli = make_cli(workspace.clone(), BackendMode::Auto);
        cli.cli_bin = "definitely-not-a-real-cli-bin".to_string();

        let error = load_status(&cli, &workspace)
            .await
            .expect_err("auto should fail closed on missing cli");
        let rendered = error.to_string();
        assert!(rendered.contains("Cli"));
        assert!(!rendered.contains("UntrustedModuleMode"));

        fs::remove_dir_all(workspace).expect("cleanup");
    }

    #[tokio::test]
    async fn module_mode_load_status_smoke_requires_trust_and_runs_when_enabled() {
        let workspace = temp_dir("module-smoke");
        let pkg_dir = workspace.join("src").join("research_copilot");
        fs::create_dir_all(&pkg_dir).expect("pkg dir");
        fs::write(pkg_dir.join("__init__.py"), "").expect("init");
        let payload = status_fixture();
        fs::write(
            pkg_dir.join("main.py"),
            format!(
                "import sys\nPAYLOAD = r'''{}'''\nif __name__ == '__main__':\n    sys.stdout.write(PAYLOAD)\n",
                payload
            ),
        )
        .expect("main");

        let cli = make_cli(workspace.clone(), BackendMode::Module);
        std::env::remove_var("RC_RUST_TUI_TRUST_LOCAL_CODE");
        let error = load_status(&cli, &workspace)
            .await
            .expect_err("module should require trust");
        assert!(matches!(error, BridgeError::UntrustedModuleMode));

        std::env::set_var("RC_RUST_TUI_TRUST_LOCAL_CODE", "1");
        let envelope = load_status(&cli, &workspace)
            .await
            .expect("trusted module mode should pass");
        assert!(envelope.ok);
        std::env::remove_var("RC_RUST_TUI_TRUST_LOCAL_CODE");

        fs::remove_dir_all(workspace).expect("cleanup");
    }
}
