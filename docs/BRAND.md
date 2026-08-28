# Brand

## The name is the mark

There is no illustrated logo. The word **QUANTIFY** set in uppercase with wide
letter-spacing is the mark, next to a small dark square holding three stacked
rules that get brighter toward the bottom: a reading, taken.

A picture of a thing is weaker than the thing. An operator scanning a tablet at
six in the morning needs to know where they are in one glance, and a word does
that better than a symbol they have to learn.

```html
<span class="wordmark"><span class="glyph"></span>Quantify</span>
```

`.wordmark` and `.wordmark.lg` are the only two sizes. The glyph is drawn in CSS,
so it never loads late and never pixelates. `web/assets/favicon.svg` is the same
idea at 64 pixels.

## Palette

One neutral family, one accent. Colour carries meaning or it is not used.

| Token | Value | Where |
|---|---|---|
| `--bg` | `#f7f7f5` | The page |
| `--surface` | `#ffffff` | Cards and rows |
| `--rail` | `#f4f4f1` | Sidebar, a tint of the page rather than a slab of something else |
| `--ink` | `#15171a` | Primary text |
| `--ink-3` | `#797f88` | Secondary text |
| `--line` | `#e7e6e2` | Hairline borders, the main structural device |
| `--accent` | `#14634f` | Active state, positive change, the trust meter |
| `--down` | `#a4402c` | Negative change only |
| `--warn` | `#8a5a12` | Needs a look, never used for negative change |

Green never means "good". It means "more than normal". More is not always good,
and the copy beside it always says which.

## Type

The system sans, tuned rather than replaced. No web fonts: the interface runs
under a strict content security policy with no external origins, and a font that
arrives late is worse than one that was always there.

- Display sizes carry tight tracking (`-.02em` to `-.03em`).
- Micro labels are 10.5px uppercase at `.09em`, in `--ink-4`.
- **Every number is tabular.** Columns must not shift when a figure updates
  underneath the reader, and this interface updates itself.

## Shape

Radii climb with the size of the thing: 7px on chips, 10px on inputs and buttons,
13px on cards, 18px on modals. Elevation is a hairline border plus a shadow of
almost nothing. Nothing floats without a reason.

## Writing

The rules live in `quantify_app/skills/plain-language.md` and are applied to both
writers, so the product reads the same whether or not a model is connected.

- Never an em dash.
- Never a number without something to compare it against.
- No marketing language. No dramatised shifts. It is a busy hour, not a battle.
- Say the size of the change and the size of the doubt in the same breath.

A test in `tests/test_quantify.py` fails the build if an em dash or a marketing
word reaches anything an operator reads.
