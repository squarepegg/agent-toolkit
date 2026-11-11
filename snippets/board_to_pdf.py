"""
Open Miro in a real browser, let you log in once, then PDF a board.

Setup (one time):
    pip install playwright
    playwright install chromium

First run — log in step:
    python board_to_pdf.py login
        Browser opens. Sign into Miro (SSO and all). Close the window when done.
        Session is saved to ./miro-profile/ for next time.

Capture a board:
    python board_to_pdf.py pdf <BOARD_URL>
        Opens the board, waits for it to settle, saves board.pdf.
"""

import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE = Path(__file__).parent / "miro-profile"


def login() -> None:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,
            viewport={"width": 1600, "height": 1000},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://miro.com/login/")
        print("Sign in to Miro in the browser window.")
        print("When you're done and see your dashboard, come back here and press Enter.")
        input()
        print("Closing browser and saving session...")
        ctx.close()


def to_pdf(board_url: str) -> None:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,                       # canvas renders better headed
            viewport={"width": 1920, "height": 1200},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(board_url, wait_until="networkidle")
        # Give the canvas time to fully render (Miro streams content in).
        time.sleep(8)
        # Try keyboard shortcut to fit board to screen.
        page.keyboard.press("Shift+1")
        time.sleep(2)
        page.pdf(path="board.pdf", format="A2", landscape=True, print_background=True)
        print("Saved board.pdf")
        ctx.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "login":
        login()
    elif cmd == "pdf" and len(sys.argv) > 2:
        to_pdf(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(1)
