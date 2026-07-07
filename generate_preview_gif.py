"""Capture an animated scrolling GIF of the dashboard for the README.

Tours the allocation rings, watchlist, and a company's deep-analysis panel.
Output: docs/dashboard-preview.gif
"""

import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import FloatRect, ViewportSize, sync_playwright, Page

from generate_preview import SEED_COLORS_JS, write_preview_html

DOCS_OUT = Path(__file__).parent / "docs" / "dashboard-preview.gif"
VIEWPORT: ViewportSize = {"width": 1200, "height": 502}  # 2.39:1
INITIAL_WAIT_MS = 600   # Three.js CDN load + first render
FRAME_INTERVAL_MS = 60  # ~16.7 fps
PALETTE_COLORS = 180
DEMO_TICKER = "JPM"


def _bounding_box_center_offset(bbox: FloatRect, x_pct: float, y_pct: float) -> tuple[float, float]:
    return bbox["x"] + bbox["width"] * x_pct, bbox["y"] + bbox["height"] * y_pct


def _ease(t: float) -> float:
    """easeInOutQuad — smooth start/stop for the scroll reveal."""
    return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2


class Recorder:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.frames: list[Image.Image] = []

    def shoot(self, n: int = 1) -> None:
        for _ in range(n):
            raw = self.page.screenshot()
            self.frames.append(Image.open(io.BytesIO(raw)).convert("RGB"))
            self.page.wait_for_timeout(FRAME_INTERVAL_MS)

    def scroll_to(self, y: float) -> None:
        self.page.evaluate(f"window.scrollTo(0, {y})")

    def offset_top(self, selector: str) -> float:
        return self.page.evaluate(
            "s => document.querySelector(s)?.getBoundingClientRect().top + window.scrollY",
            selector,
        )

    def ease_scroll(self, target_y: float, steps: int) -> None:
        start_y = self.page.evaluate("window.scrollY")
        for i in range(1, steps + 1):
            self.scroll_to(start_y + (target_y - start_y) * _ease(i / steps))
            self.shoot()


def capture_frames(page: Page) -> list[Image.Image]:
    """Choreograph the tour: rings → watchlist → click a company → deep-analysis brief.

    Panels are pinned by subtracting the sticky-header height from each scroll target.
    """
    rec = Recorder(page)
    header_h = page.evaluate("document.querySelector('header')?.offsetHeight || 0")

    rec.scroll_to(0)
    portfolio_bbox = page.locator("#chart-portfolio canvas").bounding_box()
    rec.shoot(8)
    if portfolio_bbox:
        page.mouse.move(*_bounding_box_center_offset(portfolio_bbox, 0.70, 0.50))
    rec.shoot(10)

    rec.ease_scroll(rec.offset_top("#watchlist-panel") - header_h - 12, steps=16)
    rec.shoot(12)

    page.locator(f'.wl-row[data-ticker="{DEMO_TICKER}"] .wl-name').click()
    rec.shoot(6)
    rec.ease_scroll(rec.offset_top("#analysis-panel") - header_h - 12, steps=4)
    rec.shoot(10)

    page.locator('#analysis-panel [data-an-tf="5Y"]').click()
    rec.shoot(18)

    return rec.frames


def assemble_gif(frames: list[Image.Image]) -> None:
    if not frames:
        raise ValueError("No frames captured — GIF assembly aborted")
    # Quantize every frame against one palette so colors don't flicker across the scroll.
    ref = frames[len(frames) // 2]
    palette_ref = ref.quantize(colors=PALETTE_COLORS, method=Image.Quantize.MEDIANCUT)
    quantized = [f.quantize(palette=palette_ref) for f in frames]

    DOCS_OUT.parent.mkdir(parents=True, exist_ok=True)
    quantized[0].save(
        DOCS_OUT,
        save_all=True,
        append_images=quantized[1:],
        duration=FRAME_INTERVAL_MS,
        loop=0,
        optimize=True,
    )


def main() -> None:
    html_path = write_preview_html()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.add_init_script(SEED_COLORS_JS)
        page.goto(html_path.as_uri())
        page.wait_for_load_state("networkidle")  # ensure Three.js CDN script loads
        page.wait_for_timeout(INITIAL_WAIT_MS)
        frames = capture_frames(page)
        browser.close()

    assemble_gif(frames)
    size_kb = DOCS_OUT.stat().st_size // 1024
    print(f"GIF saved: {DOCS_OUT}  ({size_kb} KB, {len(frames)} frames @ {FRAME_INTERVAL_MS}ms)")


if __name__ == "__main__":
    main()
