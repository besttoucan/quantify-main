"""Open the real app in a real browser and photograph it.

Run the server first, then point this at it:

    python server.py --port 8970          (with QUANTIFY_AUTH_BYPASS=1)
    python tools/shots.py --port 8970

Every shot lands in docs/screenshots/. Console errors and failed requests are
printed, because a screenshot that looks fine while the console is full of
exceptions is not a passing check.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8970)
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--only", default="", help="run one shot by name")
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    OUT.mkdir(parents=True, exist_ok=True)

    problems: list[str] = []

    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": args.width, "height": args.height})
        page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type in {"error", "warning"} else None)
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("requestfailed", lambda r: problems.append(f"requestfailed: {r.url}"))

        def shot(name: str, *, full: bool = False) -> None:
            if args.only and args.only != name:
                return
            page.wait_for_timeout(700)
            path = OUT / f"{name}.png"
            page.screenshot(path=str(path), full_page=full)
            print(f"  {name:<22} {path.relative_to(ROOT)}")

        def go(path: str) -> None:
            page.goto(base + path, wait_until="networkidle")
            page.wait_for_timeout(500)

        # The first run tutorial covers the app, which is correct behaviour and
        # wrong for a screenshot pass. It has its own run below.
        page.goto(base + "/", wait_until="domcontentloaded")
        page.evaluate("localStorage.setItem('quantify.tour', 'done')")

        print("landing")
        go("/")
        shot("landing")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.45)")
        shot("landing-mid")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        shot("landing-bottom")

        print("app")
        go("/app")
        shot("today")
        page.evaluate("window.scrollTo(0, 900)")
        shot("today-scrolled")

        print("history")
        page.click("[data-view='history']")
        page.wait_for_timeout(1200)
        shot("history")

        # The day sheet is the screen the owner reported as broken.
        rows = page.query_selector_all(".dayrow[data-day-detail]")
        if rows:
            rows[0].click()
            page.wait_for_timeout(1800)
            shot("day-sheet")
            body = page.query_selector(".sheet-body")
            if body:
                box = body.bounding_box()
                print(f"  sheet-body box: {box}")
                for sel in [".hours", ".dt", ".mixrow"]:
                    node = page.query_selector(f".sheet-body {sel}")
                    print(f"    {sel:<10} -> {node.bounding_box() if node else 'MISSING'}")
                page.evaluate("document.querySelector('.sheet-body').scrollTop = 400")
                shot("day-sheet-scrolled")
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

        print("item sheet")
        page.click("[data-view='today']")
        page.wait_for_timeout(1500)
        row = page.query_selector("[data-item-sheet]")
        if row:
            row.click()
            page.wait_for_timeout(2500)
            shot("item-sheet")
            page.evaluate("document.querySelector('.sheet-body').scrollTop = 700")
            shot("item-sheet-scrolled")
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)

        print("menu and settings")
        page.click("[data-view='menu']")
        page.wait_for_timeout(1800)
        shot("menu")
        page.click("[data-view='settings']")
        page.wait_for_timeout(1200)
        shot("settings")
        costs = page.query_selector("[data-stab='costs']")
        if costs:
            costs.click()
            page.wait_for_timeout(1500)
            shot("settings-costs")

        browser.close()

    if problems:
        print("\nPROBLEMS:")
        seen = set()
        for line in problems:
            if line in seen:
                continue
            seen.add(line)
            print("  " + line)
        return 1
    print("\nno console errors, no failed requests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
