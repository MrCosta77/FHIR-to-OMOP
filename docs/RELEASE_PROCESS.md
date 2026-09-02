# Reproducibility and release process

FHIR-to-OMOP uses `VERSION` as its single product version. Python 3.12 and R
4.6.1 are the reproducible environments for version 0.2.2. A technical release
does not authorize clinical deployment and must retain
`deployment_authorized=false` in run evidence.

## Restore dependencies

Create a clean Python 3.12 virtual environment and install only the hashed lock:

```bash
python -m venv .venv
python -m pip install --require-hashes -r requirements.lock
```

`requirements.in` contains the seven direct constraints. `requirements.lock`
is a universal, transitive lock generated for Python 3.12. `requirements.txt`
is retained only as a compatibility entry point to that lock.

Install `renv` once in R 4.6.1 and restore the recorded CRAN and OHDSI GitHub
packages:

```r
install.packages("renv", repos = "https://cloud.r-project.org")
renv::restore(prompt = FALSE)
```

## Deliberately update a lock

Dependency changes require review. Edit `requirements.in`, install `uv`, then
regenerate and validate the universal lock:

```bash
uv pip compile --universal --generate-hashes --python-version 3.12 --output-file requirements.lock requirements.in
uv pip install --dry-run --system --require-hashes --python-platform x86_64-manylinux_2_28 --python-version 3.12 -r requirements.lock
```

After deliberately updating installed R packages, snapshot only the declared R
entry points and their transitive dependencies:

```r
renv::snapshot(
  packages = c("renv", "DataQualityDashboard", "DatabaseConnector", "readr", "rstudioapi", "shiny"),
  prompt = FALSE,
  force = TRUE
)
```

Review every version and source change in both lockfiles. Never regenerate a
lock merely to silence CI.

## Release checklist

1. Confirm the worktree is clean and synchronized with `origin/main`.
2. Confirm `VERSION` is stable SemVer and the matching changelog section is
   complete, dated and contains the clinical-safety status.
3. Run `python -m src.quality.release_metadata --release`.
4. Restore both locks in clean Python and R environments.
5. Run `python -m pytest -v` against a freshly built local database and retain
   the immutable run report. For hospital profiles, DQD must be run-linked and
   pass the approved policy.
6. Verify that no PHI, Athena vocabulary, DuckDB, model weights, raw prompts,
   source values, local paths or secrets are tracked or included as artifacts.
7. Obtain documented clinical, privacy and institutional approval separately
   if the intended use goes beyond a technical research release.
8. Commit the release metadata, push it, and create the signed or annotated tag
   `v<contents-of-VERSION>`. Pushing that tag triggers the release workflow.
9. Verify the GitHub release points to the reviewed commit and archive its run
   report and lockfile hashes with the release record.

The tag workflow refuses a tag that differs from `VERSION`, refuses missing or
pending licence metadata, installs the hash-verified Python environment and runs
all non-integration tests before publishing the GitHub release.
