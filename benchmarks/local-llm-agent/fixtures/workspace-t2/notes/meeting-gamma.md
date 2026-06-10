---
meeting_id: meeting-gamma
date: 2026-05-05
participants:
  - Jonas Pike
  - Ines Calder
  - Tomas Webb
---
# v2.4 release readiness review

## Summary
The team reviewed v2.4 release readiness. The CI latency budget test is merged and green;
worst-case actuation latency measured 0.82 ms against the 1.0 ms budget. Secure boot passed
the factory dry run, but the key provisioning ceremony exposed a gap: the backup key
custodian was unavailable, so the ceremony needs a second custodian. Release is gated on
the custodian fix and one open OTA rollback bug, tracked as FW-2291.

## Decisions
- v2.4 release is gated on appointing a second backup key custodian and on closing bug FW-2291.
- The OTA rollback path must be exercised on ten field-return units before general rollout.
- Latency budget results are published to the engineering wiki after every release candidate.

## Action Items
- [ ] Appoint and train a second backup key custodian — Ines Calder
- [ ] Fix the OTA rollback bug FW-2291 — Tomas Webb
- [ ] Run the rollback exercise on ten field-return units — Jonas Pike
