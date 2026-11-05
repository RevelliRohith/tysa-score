# Final panel and scoring corrections

Implemented 5 November 2026.

## Behavior

- Email login resolves existing email addresses without case sensitivity. Ambiguous shorthand addresses are rejected. Scoped Stage Manager accounts route to their stage workspace, and D5 assignments participate in role and championship discovery.
- Panels remain independent by championship, event, age category, gender division and round. Mandatory rosters contain 9 / 11 / 12 / 11 officials. Chief Judge is separate from every D seat. Incomplete panels can be saved but cannot score or accept a stage push.
- CD assignment saves retain prior seat-assignment records and write an audit record. Closed panels release their active batch. The all-panels directory shows panel status.
- Each scoring desk resolves the authenticated official's actual seat. Every athlete has its own database draft. Asana navigation and refresh preserve editable values; a complete review precedes one final sheet submission. Only that successful final action locks the athlete's sheet. Backend validation rejects incomplete sheets, invalid scores, other officials' sheets and athletes outside the active pushed batch.
- Chief Judge HTML and JSON responses contain only the assigned panel's current active pushed batch. The dashboard polls every two seconds and shows draft values, submitted sheets, missing fields, evaluator decisions, timing and penalties. Chief Judge cannot write judge scores. Provisional release and incident review remain separate, audited actions.
- CD reopening records the full previous sheet in the audit log before unlocking. Judges can open their correction from the portal even after the original batch has completed. Resubmission increments the revision and locks the sheet again. Revoking a correction restores the previous submitted sheet and preserves the abandoned draft in the audit log.
- Existing calculation formulas remain in the shared scoring engine. Required D counts are now fixed at five for Traditional and four for other events. Calculations select current officials' latest revisions.

## Database preservation

Applied migrations:

- `competition.0007_judgepanel_d5_judge_alter_judgepanel_is_active`
- `competition.0008_reconcile_panel_readiness`
- `scoring.0005_unlock_unsubmitted_items`

Counts before and after migration were identical:

| Records | Before | After |
| --- | ---: | ---: |
| Championships | 1 | 1 |
| Athletes | 125 | 125 |
| Championship registrations | 125 | 125 |
| Event entries | 155 | 155 |
| Judge panels | 6 | 6 |
| Stage batches | 1 | 1 |
| Performances | 21 | 21 |
| Judge submissions | 42 | 42 |
| Asana score items | 194 | 194 |

No seed, flush, reset or deletion command was run against the championship database.

The six existing panels require officials before activation. Panels 15 and 16 need D5; panel 17 needs Artistry Judge 2 and Time Judge 2; panel 18 needs both Artistry Judges and Time Judge 2. Panels 19 and 20 have no required officials assigned. Complete these through CD Panel Setup; existing assignments and scores are retained.

## Verification

Tests use `config.test_settings`, which overrides database storage with isolated SQLite and uses in-memory Channels. Browser verification uses an isolated Django live server and headless Chromium.

Focused backend verification:

```powershell
venv/Scripts/python.exe -m pytest tests/test_final_panel_workflow.py tests/test_corrections_rbac.py tests/test_panel_exclusive_assignment.py tests/test_views.py tests/test_channels_consumers.py test_scoring_engine_suite.py --ds=config.test_settings -q
```

Browser verification:

```powershell
venv/Scripts/python.exe -m pytest tests/test_final_panel_browser.py --ds=config.test_settings -q
```

The browser regression exercises actual autosave controls, prior-asana editing, refresh persistence, independent athletes, Chief live draft visibility, complete review and final locking.

The broader SQLite suite excludes the two PostgreSQL concurrency modules because SQLite cannot validate PostgreSQL row-lock behavior. Existing unrelated failures concern the certificate verification route's argument name and two older E2E workflows whose staff users have no scoped operational roles. Permissions were not relaxed to satisfy those fixtures. See `final-panel-test-results.txt` for the final run.

A pre-existing migration drift remains in `registrations.ChampionshipRegistration.status`; no unrelated registration migration was generated. Django system checks and migration checks for the changed competition/scoring apps pass.

Final results: 41 focused backend tests passed; the browser regression passed. The broader non-PostgreSQL run had 63 passes and the three existing failures described above. JavaScript syntax checks, Django system checks, and migration checks for the changed apps passed.
