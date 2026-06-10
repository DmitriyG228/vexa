---
meeting_id: meeting-alpha
date: 2026-04-14
participants:
  - Mara Voss
  - Jonas Pike
  - Ines Calder
  - Ravi Mehta
---
# Atlas Rover — Q3 firmware planning

## Summary
The team planned the Q3 firmware work for the Atlas rover line. Firmware v2.4 will carry
the encoder drift compensation and secure boot verification. The motor controller refactor
merges only once a CI latency budget test passes. Supplier quality talks with Tessier and
the embedded-role interview loop start this week.

## Decisions
- Ship the encoder drift software compensation in firmware v2.4; open the supplier quality discussion with Tessier in parallel.
- The motor controller refactor merges only after a CI latency budget test exists and passes.
- Secure boot verification ships in v2.4 ahead of the fleet scale-up.

## Action Items
- [ ] Draft the Tessier quality addendum and send it — Ravi Mehta
- [ ] Build the CI latency budget test (end of June) — Jonas Pike
- [ ] Arrange the bench rig transfer from the Hamburg lab — Ravi Mehta
- [ ] Draft the key provisioning ceremony runbook (by July 10) — Ines Calder
- [ ] Coordinate the embedded-role interview loop with recruiting — Ravi Mehta
