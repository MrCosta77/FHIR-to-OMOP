# PHI control policy for a future controlled pilot

Status: technical safeguards implemented; institutional approval and identity
provider integration pending.

## Default posture

The application defaults to `SYNTHETIC`. `DEIDENTIFIED` must only be selected
after the institution confirms the de-identification method. Setting
`CMF_DATA_CLASSIFICATION=PHI` fails closed unless all of the following are set:

- `CMF_PHI_ENABLED=true`;
- `CMF_PHI_POLICY_APPROVED_BY` with the named institutional approver;
- `CMF_PHI_RETENTION_DAYS` with a positive approved retention period;
- `CMF_PHI_SALT` supplied by the institution, with at least 32 characters;
- `CMF_PHI_KEY_VERSION` supplied and governed by the institution;
- a loopback-only Ollama endpoint.

The repository does not choose a hospital retention period. That is an
institutional legal, privacy and records-management decision.
The public synthetic-development key is rejected by the hospital profile. The
secret itself is never persisted; only its declared version and a truncated,
one-way fingerprint may appear in non-secret run provenance.

## Redaction and model boundary

Direct email, formatted telephone, US SSN, IP address, explicit MRN/NHS/NIF/SNS
and FHIR Patient references are replaced before source text enters an LLM
prompt. LLM reasons and clinical signals are redacted again before persistence.
This pattern control does not reliably detect personal names or every national
identifier; PHI ingestion therefore also requires upstream minimization and an
approved de-identification/data-loss-prevention control.

Only `localhost` or an IP loopback endpoint is accepted. Raw prompts and raw
model responses are never written to the security audit log.

## Access

For PHI, a claimed portal identity must match `CMF_AUTHENTICATED_USER` and be in
the comma-separated `CMF_REVIEWER_ALLOWLIST`,
`CMF_ADJUDICATOR_ALLOWLIST` or `CMF_SOURCE_ADMIN_ALLOWLIST`, according to the
operation. Source administrators may register or deactivate hospital source
identities but cannot clinically review or adjudicate mappings. These
environment claims must be injected by an
institution-managed authenticated deployment, not supplied by the end user.
The current standalone Streamlit text field is not authentication and remains
limited to synthetic or approved de-identified data.

## Logging and retention

`security_audit_log` accepts only metadata such as target table, model,
decision, redaction categories and outcome. It rejects raw source, prompt,
response, patient and identifier fields, and rejects values matching direct
identifier patterns. Audit retention must follow the approved period; automatic
purging is intentionally not enabled before the institution specifies backup,
legal-hold and deletion requirements.
