"""`kifs status` must be pipeable into jq, so stdout carries JSON and nothing else."""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_status_stdout_is_pure_json(settings, tmp_path):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "KIFS_DATA_DIR": str(settings.data_dir),
        "PYTHONPATH": str(REPO / "src"),
        # Keep the test offline: no Infisical lookup, no ~/.env.global.
        "KIFS_GLOBAL_ENV": str(tmp_path / "absent.env"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "kifs", "--no-infisical", "--no-log-file", "status"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["games_indexed"] == 0
    assert "records_by_status" in report
    # The log line about loading the database must not be on stdout.
    assert "INFO" not in result.stdout
