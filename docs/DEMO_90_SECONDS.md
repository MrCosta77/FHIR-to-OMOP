# Demonstration script — 60–90 seconds

## Purpose

Show the safety and traceability of the mapping workflow, not merely that the
application has a user interface. Use only synthetic data and a proposal whose
expected interpretation has been checked before recording.

## Evidence rule

The recording may say that the project **implements** a governed workflow and
that a synthetic run **demonstrates** it. Do not describe the system as
clinically validated, production-ready, certified or authorized for PHI.

## Preparation

1. Use the `development` profile and synthetic database.
2. Confirm that the chosen proposal is present in the independent-review queue.
3. Prepare three governed synthetic identities: Reviewer A, Reviewer B and a
   distinct Adjudicator.
4. Record both reviews before the final screen capture, or capture the two review
   actions as a short accelerated sequence.
5. Keep terminals, paths, usernames, environment variables and local Athena
   files outside the visible crop.
6. Open the portal with:

   ```powershell
   python -m streamlit run src/app/review_portal.py
   ```

## Storyboard and narration

| Time | Screen | Suggested narration |
|---:|---|---|
| 0–8 s | One synthetic dirty/local clinical term and its target domain. | “Hospital data often contains local, legacy or incomplete terminology that cannot be safely converted by string matching alone.” |
| 8–20 s | Architecture diagram or retrieval candidate view. | “The pipeline resolves standard codes first. Only unresolved terms enter local retrieval, which supplies valid Standard OMOP candidates to a local model.” |
| 20–32 s | Proposal: source term, candidate, concept ID, confidence and rationale. | “The model cannot invent a concept ID. It can select one retrieved candidate or abstain, and every decision carries model, prompt, vocabulary and run provenance.” |
| 32–48 s | Reviewer A and Reviewer B actions, identities/votes mutually hidden. | “A model selection is still only a proposal. Two independent reviewers assess it blindly and must provide clinical rationales.” |
| 48–62 s | Blinded adjudication tab. | “Agreement does not publish automatically. A third, distinct adjudicator makes the final governed decision.” |
| 62–75 s | Approved mapping/STCM and affected OMOP event or status. | “Only adjudication can activate the source-to-concept mapping and apply it to the verified event.” |
| 75–90 s | Run report/DQD/benchmark summary. | “The resulting database is published atomically with run evidence and quality gates. Current results are synthetic and technically curated; clinical validation is the next milestone.” |

## What must be visible

- synthetic classification or an unmistakably synthetic dataset;
- source term and OMOP domain;
- candidate name and concept ID;
- `SELECT` or `ABSTAIN` status;
- mandatory rationale field;
- separation between independent review and adjudication;
- final governed status;
- at least one provenance or quality-evidence element.

## What must not be visible

- real patient data or identifiers;
- local secrets, pseudonymization keys or environment variables;
- Athena download contents or licensed vocabulary extracts;
- reviewer passwords or authentication tokens;
- a claim that confidence alone publishes a mapping;
- mutable benchmark percentages presented without protocol/version context.

## Capture variants

### Portfolio/GitHub version

- 16:9, 1080p;
- English captions;
- 60–90 seconds;
- no music required;
- link to `docs/PORTFOLIO_CASE_STUDY.md` in the description.

### Interview version

Use the same flow but pause after the proposal and explain the three design
choices: candidate binding, normal abstention and separation of duties.

## Recording acceptance checklist

- [ ] Complete story fits within 90 seconds.
- [ ] No PHI, secrets or local filesystem details are visible.
- [ ] Two reviewers and the adjudicator are distinct governed actors.
- [ ] Narration distinguishes proposal, review and publication.
- [ ] The final claim says synthetic/technical evidence, not clinical validation.
- [ ] Captions remain readable on a laptop screen.
- [ ] Repository URL and Apache-2.0 attribution appear in the final frame.
