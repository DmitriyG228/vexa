# Meeting note schema (frozen benchmark fixture schema)

> This is the **benchmark fixture schema** frozen for pack `ei-agent`. The production
> workspace schema is owned by the `ei-workspace` pack; this one exists only so the
> benchmark can score schema compliance deterministically.

A per-meeting note is a markdown file at `notes/<meeting_id>.md` with:

1. **YAML front matter** delimited by `---` lines, containing exactly these keys:
   - `meeting_id`: string — must equal the transcript's `meeting_id`
   - `date`: string — ISO date `YYYY-MM-DD`, from the transcript
   - `participants`: YAML list of the participant names from the transcript
2. An **H1 title** line (`# <title>`).
3. A `## Summary` section: 2–6 sentences of prose.
4. A `## Decisions` section: a markdown bullet list (`- `), one bullet per decision
   made in the meeting. No bullets outside the list.
5. A `## Action Items` section: a markdown bullet list where every bullet has the form
   `- [ ] <task> — <owner>` (checkbox, task text, em- or hyphen-dash, owner name).

Sections must appear in that order. No other top-level (`#`/`##`) headings are allowed.
