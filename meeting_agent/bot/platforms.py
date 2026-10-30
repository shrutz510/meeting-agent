"""Per-platform join flows.

Meeting UIs change often, so every step is best-effort and text/role based
rather than relying on generated CSS class names. If a step fails the bot keeps
going: worst case you click "Join" yourself in the (headed) browser window.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Callable

from playwright.sync_api import Page, TimeoutError as PWTimeout


def detect_platform(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "meet.google.com" in host:
        return "meet"
    if "zoom.us" in host:
        return "zoom"
    if "teams.microsoft.com" in host or "teams.live.com" in host:
        return "teams"
    if "jit.si" in host or "jitsi" in host:
        return "jitsi"
    return "generic"


def _click_first(page: Page, patterns: list[str], timeout: int = 3000) -> bool:
    for pat in patterns:
        loc = page.get_by_role("button", name=re.compile(pat, re.I))
        try:
            loc.first.click(timeout=timeout)
            return True
        except (PWTimeout, Exception):
            continue
    return False


def _fill_name(page: Page, name: str, selectors: list[str]) -> None:
    for sel in selectors:
        try:
            page.locator(sel).first.fill(name, timeout=2000)
            return
        except (PWTimeout, Exception):
            continue


class Platform:
    hook_webaudio = False
    # Text that indicates the call is over for the bot.
    ended_patterns: list[str] = []

    def __init__(self, page: Page, name: str, log: Callable[[str], None]):
        self.page, self.name, self.log = page, name, log

    def prepare_url(self, url: str) -> str:
        return url

    def join(self) -> None:
        pass

    def wait_until_in_call(self, timeout_s: int) -> bool:
        return True

    def post_chat(self, message: str) -> bool:
        return False

    def has_ended(self) -> bool:
        if not self.ended_patterns:
            return False
        try:
            body = self.page.inner_text("body", timeout=1000)
        except Exception:
            return False
        return any(re.search(p, body, re.I) for p in self.ended_patterns)

    def leave(self) -> None:
        _click_first(self.page, [r"^leave", r"leave call", r"hang ?up"], timeout=1500)


class GoogleMeet(Platform):
    ended_patterns = [r"you left the meeting", r"you've been removed", r"the call has ended",
                      r"you can't join this video call", r"no one responded to your request"]

    def join(self) -> None:
        p = self.page
        _click_first(p, [r"^got it$", r"^dismiss$", r"continue without microphone"], timeout=2000)
        _fill_name(p, self.name, ["input[aria-label='Your name']", "input[placeholder='Your name']", "input[type='text']"])
        _click_first(p, [r"turn off microphone"], timeout=2000)
        _click_first(p, [r"turn off camera"], timeout=2000)
        if not _click_first(p, [r"^join now$", r"^ask to join$", r"^join here too$", r"^join$"], timeout=8000):
            self.log("Could not find Meet's join button; click it in the browser window.")

    def wait_until_in_call(self, timeout_s: int) -> bool:
        try:
            self.page.get_by_role("button", name=re.compile(r"leave call", re.I)).first.wait_for(timeout=timeout_s * 1000)
            return True
        except PWTimeout:
            return False

    def post_chat(self, message: str) -> bool:
        p = self.page
        if not _click_first(p, [r"chat with everyone", r"^chat$"], timeout=3000):
            return False
        try:
            box = p.locator("textarea").first
            box.fill(message, timeout=3000)
            box.press("Enter")
            return True
        except Exception:
            return False


class Jitsi(Platform):
    ended_patterns = [r"you have been kicked", r"the meeting has been terminated", r"thank you for using"]

    def prepare_url(self, url: str) -> str:
        frag = urllib.parse.quote(f'"{self.name}"')
        extra = (f"userInfo.displayName={frag}&config.startWithAudioMuted=true"
                 "&config.startWithVideoMuted=true&config.prejoinConfig.enabled=false")
        return url + ("&" if "#" in url else "#") + extra

    def join(self) -> None:
        _fill_name(self.page, self.name, ["input[placeholder*='name' i]"])
        _click_first(self.page, [r"join meeting", r"^join$"], timeout=4000)

    def wait_until_in_call(self, timeout_s: int) -> bool:
        try:
            self.page.locator("#largeVideoContainer, .filmstrip").first.wait_for(timeout=timeout_s * 1000)
            return True
        except PWTimeout:
            return False

    def post_chat(self, message: str) -> bool:
        p = self.page
        try:
            if not _click_first(p, [r"open chat", r"^chat$"], timeout=3000):
                p.keyboard.press("c")
            box = p.locator("textarea").first
            box.fill(message, timeout=3000)
            box.press("Enter")
            return True
        except Exception:
            return False


class Zoom(Platform):
    hook_webaudio = True
    ended_patterns = [r"this meeting has been ended by host", r"you have been removed", r"meeting has ended"]

    def prepare_url(self, url: str) -> str:
        # https://zoom.us/j/123?pwd=x  ->  https://app.zoom.us/wc/join/123?pwd=x  (browser client)
        parsed = urllib.parse.urlparse(url)
        m = re.search(r"/j/(\d+)", parsed.path)
        if not m:
            return url
        q = f"?{parsed.query}" if parsed.query else ""
        return f"https://app.zoom.us/wc/join/{m.group(1)}{q}"

    def _frame(self):
        for f in self.page.frames:
            if "webclient" in f.url or "/wc/" in f.url:
                return f
        return self.page.main_frame

    def join(self) -> None:
        p = self.page
        _click_first(p, [r"join from your browser"], timeout=3000)
        f = self._frame()
        try:
            f.locator("#input-for-name, input[placeholder*='name' i]").first.fill(self.name, timeout=8000)
        except Exception:
            pass
        try:
            f.get_by_role("button", name=re.compile(r"^join$", re.I)).first.click(timeout=5000)
        except Exception:
            self.log("Could not find Zoom's join button; click it in the browser window.")

    def wait_until_in_call(self, timeout_s: int) -> bool:
        f = self._frame()
        try:
            f.get_by_role("button", name=re.compile(r"leave|join audio", re.I)).first.wait_for(timeout=timeout_s * 1000)
        except PWTimeout:
            return False
        try:  # The web client only receives audio once computer audio is joined.
            f.get_by_role("button", name=re.compile(r"join audio by computer", re.I)).first.click(timeout=5000)
        except Exception:
            pass
        return True

    def post_chat(self, message: str) -> bool:
        f = self._frame()
        try:
            f.get_by_role("button", name=re.compile(r"chat", re.I)).first.click(timeout=3000)
            box = f.locator("[contenteditable='true'], textarea").first
            box.fill(message, timeout=3000)
            box.press("Enter")
            return True
        except Exception:
            return False


class Teams(Platform):
    ended_patterns = [r"you've been removed", r"the meeting has ended", r"you left the meeting"]

    def join(self) -> None:
        p = self.page
        _click_first(p, [r"continue on this browser", r"join on the web instead"], timeout=5000)
        _fill_name(p, self.name, ["input[placeholder*='name' i]", "input[data-tid='prejoin-display-name-input']"])
        _click_first(p, [r"^join now$"], timeout=8000)

    def wait_until_in_call(self, timeout_s: int) -> bool:
        try:
            self.page.get_by_role("button", name=re.compile(r"^leave", re.I)).first.wait_for(timeout=timeout_s * 1000)
            return True
        except PWTimeout:
            return False


PLATFORMS = {"meet": GoogleMeet, "jitsi": Jitsi, "zoom": Zoom, "teams": Teams, "generic": Platform}
