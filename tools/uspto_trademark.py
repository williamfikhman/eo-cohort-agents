"""Search the USPTO trademark database for a client's mark and screenshot it.

Standing rule from William: every agreement gets a screenshot of the client's
USPTO trademark record for Schedule C. Run this before `agreement prepare`:

    python tools/uspto_trademark.py <client-slug> [--mark "Exact Mark"]

It opens the USPTO Trademark Search, searches the mark (the client's brand name
by default), waits for results, and writes build/<slug>/trademark-uspto.png,
which render.py then places under Schedule C item 4. Needs network access to
uspto.gov; this is a real browser session, so it takes a few seconds.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

SEARCH_URL = "https://tmsearch.uspto.gov/search/search-information"


def _launch_options() -> dict:
    """Use a Chromium already on the machine when Playwright's own download is
    absent: PLAYWRIGHT_CHROMIUM_PATH if set, else any /opt/pw-browsers install."""
    import glob, os

    explicit = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")
    if explicit:
        return {"executable_path": explicit}
    for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                    "/opt/pw-browsers/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"):
        found = sorted(glob.glob(pattern))
        if found:
            return {"executable_path": found[-1]}
    return {}


def screenshot_mark(mark: str, out_path: Path, timeout_ms: int = 60_000) -> Path:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, **_launch_options())
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        try:
            page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            # The search box is the first text input on the page; the USPTO UI
            # changes often, so fall back to any visible text input.
            box = page.locator("input[type='text'], input[type='search']").first
            box.wait_for(state="visible", timeout=timeout_ms)
            box.fill(mark)
            box.press("Enter")
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except PlaywrightTimeout:
                pass  # results usually render before the network goes idle
            page.wait_for_timeout(2_000)
            page.screenshot(path=str(out_path), full_page=True)
        finally:
            browser.close()
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("slug", help="client slug, e.g. mitigate-stress")
    parser.add_argument("--mark", help="mark to search; defaults to the client's brand name")
    args = parser.parse_args()

    from config import ConfigError, load_client

    try:
        client = load_client(args.slug)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    mark = args.mark or client.values.get("brand_name") or client.company_legal_name
    out = REPO_ROOT / "build" / args.slug / "trademark-uspto.png"
    print(f"searching USPTO for {mark!r} ...")
    try:
        screenshot_mark(mark, out)
    except Exception as exc:  # network refused, UI changed, browser missing
        print(f"could not capture the USPTO record: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        print("Render will fall back to 'TBD' for the Schedule C exhibit.", file=sys.stderr)
        return 2
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
