# Runtime configuration

All executable entry points use `src.utils.config.RuntimeSettings`. Configuration
is resolved in this order:

1. the versioned profile selected by `CMF_PROFILE`;
2. explicit `CMF_*` environment-variable overrides;
3. fail-closed type, range, privacy and local-network validation.

The project deliberately does not load `.env` files at runtime. `.env.example`
is a safe reference for shell, CI or deployment configuration; copy values into
the execution environment using the mechanism appropriate to that platform.

## Profiles

- `development` preserves the existing local Qwen setup and synthetic defaults.
- `benchmark` isolates generated outputs, requires integration checks and uses
  the Llama candidate selected for development calibration.
- `hospital` defaults to PHI, a conservative threshold of `1.0`, and fails at
  import/startup unless PHI activation, named approval and positive retention
  are provided. It also requires the complete integration and OHDSI DQD gates.
  It is a safety template, not deployment authorization.

Relative paths are anchored at the repository root, independent of the current
working directory. Absolute paths are accepted on the host platform. No profile
contains credentials, identities or institutional approvals.

Validate and display the effective non-secret configuration before a run:

```bash
python -m src.utils.config
python -m src.quality.preflight
```

Example PowerShell override:

```powershell
$env:CMF_PROFILE = "benchmark"
$env:CMF_FHIR_DIR = "D:\synthetic-fhir"
python -m src.utils.config
```

Example POSIX shell override:

```bash
CMF_PROFILE=benchmark CMF_FHIR_DIR=/data/synthetic-fhir \
  python -m src.utils.config
```

`SIMULATE_LIS_NOISE` remains accepted temporarily for compatibility. New
automation must use `CMF_SIMULATE_LIS_NOISE`.
