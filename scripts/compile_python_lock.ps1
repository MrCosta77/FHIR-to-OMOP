$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)
uv pip compile `
    --universal `
    --generate-hashes `
    --python-version 3.12 `
    --output-file requirements.lock `
    requirements.in
