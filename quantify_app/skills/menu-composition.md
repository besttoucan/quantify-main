# Skill: menu composition

You read a point-of-sale item label and work out what the item is physically
made of, so the kitchen can see what a change in demand means for prep.

## What you are given

The raw label exactly as the register prints it, the category it sits in, the
price, any modifier names, and the other items on the same menu. Register labels
are often abbreviated, inconsistent, or written for a keypad rather than a human.

## What to produce

A short list of components. A component is something a kitchen orders, holds, or
preps. For a chocolate shake that is ice cream base, chocolate syrup, milk, and a
cup with a lid. For a double cheeseburger it is two beef patties, a bun, cheese
slices, and the standard garnish.

For each component give:

- **name**: what the kitchen calls it, in the singular.
- **role**: `base`, `protein`, `dairy`, `produce`, `bread`, `sauce`, `sweetener`,
  `beverage`, `packaging`, or `other`.
- **share**: roughly how much of the item's cost or bulk this component is, as a
  percentage. The shares should total close to 100.
- **quantity**: the amount used in one sold unit, when the label supports it.
  Write it the way a kitchen would: "2 patties", "about 6 oz", "1 cup". Leave it
  empty rather than guessing a number the label does not support.
- **confidence**: `high`, `medium`, or `low`.

## Confidence rules

- **High**: the label names the components outright, or the item is a standard
  preparation with one common recipe. "Double cheeseburger" is high.
- **Medium**: the category and price make the composition very likely, but a
  house variation could change it. "House sandwich" is medium.
- **Low**: the label is ambiguous, a proper noun, or unique to this restaurant.
  "The Ridgeway" is low. Say plainly that a recipe would settle it.

## Rules

- Never state an exact gram weight, cost, or yield. You are reading a label, not
  a recipe card. Ranges and household units only.
- Do not invent house specialities. If the label does not tell you, mark the
  confidence low.
- Include packaging when the item is served to go, because it is a real thing
  the operator buys and runs out of.
- Keep the list to the components that matter. Salt and pepper are not worth a
  line. Six to eight components is a full answer; three is often enough.
- Write one short note saying what would raise the confidence, usually "confirm
  against the recipe" or "confirm the patty weight".
