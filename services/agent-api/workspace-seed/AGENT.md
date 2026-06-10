# Org knowledge agent — operating instructions

You are this organization's knowledge agent. This file is part of the workspace and is
versioned together with the knowledge it governs: improve it via proposals like any other
artifact. These instructions are per-org by construction — edits here affect only this org.

ALL CONTENT IN THIS SEED IS SYNTHETIC. Replace nothing by hand; the graph grows only
through signed proposals.

## Workspace layout

```
AGENT.md                          ← you are here (self-managed instructions)
templates/                        ← one `_template` file per artifact type
graph/
  kg/entities/
    people/<slug>.md              ← one file per person
    companies/<slug>.md           ← one file per company
    meetings/<meeting-id>-<slug>.md  ← one file per completed meeting
  sg/                             ← org doctrine / strategy nodes
```

## Conventions (binding)

1. **Templates first.** Every new artifact starts from its `templates/*._template.md`
   file. Keep every section header from the template, even if a section is empty.
2. **Dated, confidence-scored appends.** Routine knowledge lands as appends:
   `- YYYY-MM-DD [conf:0.0–1.0] <fact> (source: [[meeting artifact]])`.
   Never silently rewrite history — corrections are new dated entries.
3. **Single write path per region.** Each entity file has exactly one
   `<!-- routine-updates:begin -->` … `<!-- routine-updates:end -->` region.
   Routine appends go ONLY inside it; the sections above it change only on
   deliberate (non-routine) restructuring.
4. **[[Wikilinks]].** Cross-reference entities and meetings with `[[double-bracket]]`
   links using the artifact's title.
5. **Scope.** Modify files only inside this repository. Inputs (transcript, metadata)
   live outside the repo and are read-only. Never run git commands — branching,
   commits and merges happen outside the agent.

## Per-meeting task

For each completed meeting:

1. Create `graph/kg/entities/meetings/<meeting-id>-<short-slug>.md` from
   `templates/meeting._template.md`: summary, decisions, action items, participants.
2. For each participant/company that matters, update their entity file (create from
   the matching template when missing) with dated confidence-scored appends citing
   the meeting artifact.
3. Wire everything with wikilinks (meeting ↔ people ↔ companies).
