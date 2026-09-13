# Working on Quantify with another agent

Two agents work on this repository: Claude Code (session `quantify-full-platform-dd`, orchestrating
a rebuild with subagents) and Codex (design and user-experience polish). This file is how they
stay out of each other's way. Read it fully before editing anything.

## The log

All agent-to-agent communication goes through `AGENT_COORDINATION.md` in the repo root (the file Codex
created; it is the only log). Append a block:

```
### 2026-09-13 14:40 - Codex to Claude - <subject>
Your message. Keep it short and concrete: what you are about to touch, what you need, what you found.
```

Check the file before every editing session and answer any message addressed to you. Never edit
or delete another agent's messages. Claude checks the log between build stages and after every
notification; Codex should check it before starting a task and after finishing one.

## Where things are

- Branch `quantify-ux-overhaul` in this checkout is the integration branch. `main` is untouched.
- The build plan (binding for both agents): `C:\Users\osavi\AppData\Local\Temp\claude\c--Users-osavi-Downloads-QUANTIFY-Full-Platform\89dd5a67-64d9-4180-b957-1a08ad967119\scratchpad\plan.md`.
  It fixes the vocabulary, the information architecture, the per-screen layouts, the API contract
  changes and the CSS system. Design work must stay inside it (or propose a change in the log).
- Audit findings (about 740, from 19 independent audits): the same folder, `results\regions\*.json`.
  Competitor synthesis: `competitors.md`; Shopify/Polaris design rules: `shopify-data.md` and
  `shopify-structure.md` in the same folder.
- Writing rules: `quantify_app/skills/plain-language.md` and `docs/BRAND.md`. No em dashes (a test
  fails the build), no marketing words, sentence case, every number with a comparison, green means
  "more than normal" and never "good".

## Who edits what, and when

Claude's subagents are editing `web/app.js`, `web/styles.css`, `server.py`, `quantify_app/*.py`,
`tests/*.py` in separate git worktrees right now (stage 1: shell and CSS system, backend
infrastructure; stage 2: Today, sheets, History, Order, Settings, landing and tour, backend core,
backend supply). Those branches get merged into `quantify-ux-overhaul` by Claude.

Until Claude posts `READY FOR DESIGN PASS` in the log, Codex must not edit `web/app.js`,
`web/styles.css`, `server.py` or `quantify_app/` in this checkout: the merge would clobber it.
What Codex can do now, and is asked to do:

1. Read the plan, the findings, and the Shopify digests, and post a design critique and proposal in
   the log: the type ladder, spacing, colour use, card anatomy, chart minimalism, what makes
   the current screens look generated, and the concrete rules Codex intends to apply.
2. Prepare its design pass as a list of CSS-level and markup-level changes per screen, keyed to the
   plan's section 2 layouts, so it can be applied quickly after the handoff.
3. Work on a branch named `codex-design` created from `quantify-ux-overhaul` at handoff time, commit
   there, and post the branch name in the log. Claude merges it.

After the handoff, the division is: Codex owns the look (CSS, spacing, type, colour, chart styling,
markup structure inside a screen when it is purely visual). Claude owns behaviour, data, copy,
backend and tests. If a visual change needs a behaviour or copy change, ask in the log.

## Running and testing

- `python server.py --port 8787` (SQLite at `data/quantify.db`; use a copy for experiments:
  `QUANTIFY_DB=<path>`; `QUANTIFY_AUTH_BYPASS=1` signs you in automatically;
  `QUANTIFY_DISABLE_SCHEDULER=1` keeps background jobs off).
- `node --check web/app.js` and `python -m pytest tests/ -q` must pass before any commit.
  `tests/test_quantify.py::test_no_class_ships_without_a_style` requires every class used in
  `web/app.js` to have a rule in `web/styles.css`.
- Playwright (Python, Chromium) is installed for screenshots. Primary sizes: 1024x768 (counter iPad,
  landscape), 820x1180, 390x844, 1440x900.
- Never commit `data/`, never push, never touch `main`.
