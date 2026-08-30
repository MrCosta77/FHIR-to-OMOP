import sys

from scripts import run_e2e_evaluation


def test_e2e_launcher_uses_isolated_acceptance_test(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0

    def fake_run(command, *, cwd, check):
        captured.update(command=command, cwd=cwd, check=check)
        return Completed()

    monkeypatch.setattr(run_e2e_evaluation.subprocess, "run", fake_run)

    assert run_e2e_evaluation.main() == 0
    assert captured == {
        "command": [
            sys.executable,
            "-m",
            "pytest",
            str(run_e2e_evaluation.ACCEPTANCE_TEST),
            "-m",
            "integration",
            "-q",
        ],
        "cwd": run_e2e_evaluation.PROJECT_ROOT,
        "check": False,
    }
