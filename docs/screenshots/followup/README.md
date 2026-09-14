# Follow-up History screenshots

These four unchanged viewport captures illustrate the [September 13 follow-up verification](../../FOLLOWUP_2026-09-13.md). They come from `scratchpad/codex-design/followup-history-final-v2`, the run whose working source was committed as `cebca54f4919898a67a3696cba479bca490a33df`.

The temporary SQLite fixture is accessed through the real local API. **Alpha Cafe, 17 units, and $170 on August 1 are deliberately seeded test data, not customer business data.** The fixture includes incomplete historical expectations and no measured ticket/hourly coverage. The screenshots show those unavailable values without replacing recorded sales or inventing a complete score.

| Image | State |
| --- | --- |
| [390-incomplete-days.png](390-incomplete-days.png) | Phone Days list: sales retained, ticket count unavailable, concise scoring reason |
| [1024-incomplete-days.png](1024-incomplete-days.png) | Tablet Days list with the same evidence labels |
| [390-incomplete-day.png](390-incomplete-day.png) | Phone day detail: full reason and an item without an expectation |
| [1024-incomplete-track-record.png](1024-incomplete-track-record.png) | Tablet Track record: no complete days to score |

Each image was visually inspected and copied byte-for-byte, without cropping or other edits. [manifest.json](manifest.json) records the source run, dimensions, byte sizes, and SHA-256 hashes. The source run contains six captures at two widths with no recorded JavaScript errors or horizontal overflow; only these four are retained here. They are illustrations of these states, not proof that every application flow was tested. Existing screenshot collections are preserved.
