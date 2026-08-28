# Interface Design Decisions

## One product, not a page per feature

The user asked for an AI-powered operating tool, not an application that exposes every brainstorm as a new dashboard. Quantify therefore has three work views and one setup area:

- Brief
- Outlook
- Results
- Setup

Weather, events, menu interpretation, service timing, item movement, and email are features inside those workflows.

## Editorial hierarchy

The Brief reads like an operating memo:

1. Headline
2. Four decisive measures
3. Ranked priorities
4. Service curve
5. Dense demand ledger
6. Supporting context
7. Menu-mix pressure
8. Week ahead

This order puts decisions before charts.

## No “AI app” visual clichés

The interface intentionally avoids:

- Gradient hero panels
- Dozens of rounded statistic cards
- Sparkle icons
- Chatbot-first interaction
- Floating glass panels
- Decorative 3D illustrations
- Repeated generic badges
- Huge empty margins around little information
- A separate dashboard for every data source

Cards are used only for coherent operating units. Ledgers and compact rows are used for comparable information.

## Explainability without clutter

The main screen shows only the most important reason. Deeper model information is available by opening an item. This is progressive disclosure: operators see the decision immediately, while skeptical owners can inspect analog days, component predictions, confidence, and overrides.

## Honest uncertainty

Quantify uses ranges and confidence. It does not replace uncertainty with false decimal precision. Low-confidence high-value items become explicit watch items.

## Responsive behavior

Desktop uses a fixed left navigation and dense operating canvas. iPad/mobile uses a bottom navigation while preserving the same hierarchy. No separate mobile product is required.

## Accessibility

- Native buttons, forms, tables/ledgers, and headings
- Visible focus states
- Meaningful text labels
- Sufficient contrast
- No information communicated by color alone
- Screen-reader live regions for loading/errors/toasts
