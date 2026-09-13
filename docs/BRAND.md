# Quantify brand and interface

The word Quantify is the interface mark, set with wide letter spacing. Use the existing `.wordmark` and `.wordmark.lg` classes. The app does not place an illustrated mascot or an extra glyph beside the name.

## Color has a job

| Token | Value | Use |
|---|---|---|
| `--bg` | `#f7f7f5` | Page background |
| `--surface` | `#ffffff` | Cards and rows |
| `--rail` | `#f4f4f1` | Navigation background |
| `--ink` | `#15171a` | Main text and actual sales series |
| `--ink-2` | `#4b5058` | Necessary secondary text |
| `--line` | `#e7e6e2` | Decorative card boundaries |
| `--accent` | `#14634f` | Primary actions, selection, current/peak chart value |
| `--series-1` | `#2f6f8f` | Expected sales series |
| `--down` | `#a4402c` | Lower than comparison, with a signed number |
| `--up` | `#17694f` | Higher than comparison, with a signed number |
| `--warn` | `#8a5a12` | A condition that needs attention |

Expected and sold lines also differ by dash pattern. Every series has a written key. Hourly bars use solid blue for expected demand and green for the peak; actual sales use dark ink. Pale border tokens are for decoration, never the only visible data mark. More sales is not automatically good: describe the change.

## Readable at the counter

Use the system sans font. Financial and operating figures use tabular numbers. Keep necessary captions at least 12px, normal app text at 13px or above, and touch input text at 16px. Small decorative labels are not a substitute for readable explanations. Avoid growing the headline merely because the screen is wide: the operating table should remain easy to reach.

Radii follow the existing 7/10/13/18px scale. Borders and restrained shadows separate related work. Use the established CSS regions when changing a component.

## Writing

Use the business's name, location, actual records, and local time. Say when an input is a sample, an estimate, stale, or unknown. Cite a dated source for local wage references; employer-specific costs require that employer's figures.

State the practical action first, then the evidence. Give quantities their units and comparisons their reference period. Explain uncertainty without promising a sale, a customer's return, delivery, or a completed payment. The model-assisted writer follows `quantify_app/skills/plain-language.md`.
