"""Run the isolated synthetic 7D.4D hospital acceptance gate.

The acceptance test exercises mapping persistence, ingestion handoff, blinded
review, adjudication, STCM publication, and application across all six domains.
It deliberately uses controlled Chroma and Ollama doubles. The real local-LLM
and Athena/Chroma evaluation is the separately versioned Phase 5 benchmark.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_TEST = PROJECT_ROOT / "tests" / "test_e2e_hospital_acceptance.py"


def main() -> int:
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(ACCEPTANCE_TEST),
        "-m",
        "integration",
        "-q",
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
