"""Per-platform join flows.

Meeting UIs change often, so every step is best-effort and text/role based
rather than relying on generated CSS class names. If a step fails the bot keeps
going: worst case you click "Join" yourself in the (headed) browser window.

The bot never tries to get past a CAPTCHA or a sign-in wall on its own: it tells
you, and you deal with it in the bot's window.
"""

from __future__ import annotations

import re
import time
import urllib.parse
from typing import Callable

from playwright.sync_api import Frame, Page, TimeoutError as PWTimeout


class JoinFailed(Exception):
    """The meeting refused the bot (denied, wrong passcode, blocked, ...)."""


def detect_platform(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if host == "meet.google.com":
        return "meet"
    if host == "zoom.us" or host.endswith(".zoom.us") or host.endswith("zoomgov.com"):
        return "zoom"
    if "teams.microsoft.com" in host or "teams.live.com" in host:
        return "teams"
    if "jit.si" in host or "jitsi" in host:
        return "jitsi"
    return "generic"


def _click_first(scope: Page | Frame, patterns: list[str], timeout: int = 3000,
                 roles: tuple[str, ...] = ("button",)) -> bool:
    for pat in patterns:
        for role in roles:
            try:
                scope.get_by_role(role, name=re.compile(pat, re.I)).first.click(timeout=timeout)
                return True
            except Exception:
                continue
    return False


def _visible(scope: Page | Frame, role: str, pattern: str) -> bool:
    try:
        return scope.get_by_role(role, name=re.compile(pattern, re.I)).first.is_visible()
    except Exception:
        return False


def _fill_name(scope: Page | Frame, name: str, selectors: list[str]) -> bool:
    for sel in selectors:
        try:
            loc = scope.locator(sel).first
            if loc.is_visible():
                loc.fill(name, timeout=2000)
                return True
        except Exception:
            continue
    return False


def _body_text(scope: Page | Frame) -> str:
    try:
        return scope.locator("body").inner_text(timeout=1000)
    except Exception:
        return ""


def _match(text: str, patterns) -> str | None:
    for p in patterns:
        pat = p[0] if isinstance(p, tuple) else p
        if re.search(pat, text, re.I):
            return p[1] if isinstance(p, tuple) else pat
    return None


class Platform:
    hook_webaudio = False
    # Text that means the call is over for the bot.
    ended_patterns: list[str] = []
    # (pattern, explanation) pairs that mean the bot will not get in.
    blocked_patterns: list[tuple[str, str]] = []
    # (pattern, message) pairs shown once while waiting to get in.
    waiting_patterns: list[tuple[str, str]] = []

    def __init__(self, page: Page, name: str, log: Callable[[str], None]):
        self.page, self.name, self.log = page, name, log
        self._said: set[str] = set()

    def prepare_url(self, url: str) -> str:
        return url

    def scope(self) -> Page | Frame:
        return self.page

    def join(self) -> None:
        pass

    def in_call(self) -> bool:
        return True

    def after_join(self) -> None:
        pass

    def _say_once(self, msg: str) -> None:
        if msg not in self._said:
            self._said.add(msg)
            self.log(msg)

    def wait_until_in_call(self, timeout_s: int) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.in_call():
                self.after_join()
                return True
            text = _body_text(self.scope())
            reason = _match(text, self.blocked_patterns)
            if reason:
                raise JoinFailed(reason)
            waiting = _match(text, self.waiting_patterns)
            if waiting:
                self._say_once(waiting)
            if _captcha_present(self.page):
                self._say_once("A CAPTCHA appeared. Please solve it in the bot's browser window; "
                               "the bot will continue once you're through.")
            self.page.wait_for_timeout(2000)
        return False

    def post_chat(self, message: str) -> bool:
        return False

    def has_ended(self) -> bool:
        return bool(self.ended_patterns) and _match(_body_text(self.scope()), self.ended_patterns) is not None

    def leave(self) -> None:
        _click_first(self.scope(), [r"^leave", r"leave call", r"hang ?up"], timeout=1500)


def _captcha_present(page: Page) -> bool:
    try:
        return any(("recaptcha" in f.url or "hcaptcha" in f.url) for f in page.frames) and \
            page.locator("iframe[src*='captcha'], iframe[title*='captcha' i]").first.is_visible()
    except Exception:
        return False


class GoogleMeet(Platform):
    ended_patterns = [r"you left the meeting", r"you've been removed", r"you have been removed",
                      r"the call has ended", r"your host ended the meeting", r"meeting has ended for everyone"]
    blocked_patterns = [
        (r"you can't join this video call",
         "Google Meet won't let this browser join as a guest. Run `meeting-agent login` and sign the "
         "bot's browser into a Google account (ideally a dedicated one), or ask the host to allow guests."),
        (r"denied your request|you can't join this call", "The host denied the bot's request to join."),
        (r"no one responded to your request", "Nobody admitted the bot in time."),
        (r"check your meeting code|meeting code.*(invalid|isn't valid)", "That Meet link isn't valid."),
    ]
    waiting_patterns = [
        (r"asking to be let in|asking to join|waiting for someone to let you in|you'll join .* when someone lets you in",
         "Waiting for someone in the meeting to admit the bot (look for the 'Admit' prompt)."),
    ]

    def join(self) -> None:
        p = self.page
        _click_first(p, [r"^got it$", r"^dismiss$", r"^no thanks$",
                         r"continue without microphone"], timeout=1500)
        if _fill_name(p, self.name, ["input[aria-label*='name' i]", "input[placeholder*='name' i]"]):
            self.log(f"Joining as guest '{self.name}'.")
        else:
            self.log("Joining with the Google account signed into the bot's browser profile.")
        _click_first(p, [r"turn off microphone"], timeout=2000)
        _click_first(p, [r"turn off camera"], timeout=2000)
        # Never "Switch here": that would kick the same account off another device.
        if not _click_first(p, [r"^join now$", r"^ask to join$", r"^join here too$", r"^join$"], timeout=10000):
            self.log("Could not find Meet's join button; click it in the bot's window.")

    def in_call(self) -> bool:
        return _visible(self.page, "button", r"leave call")

    def post_chat(self, message: str) -> bool:
        p = self.page
        if not _click_first(p, [r"chat with everyone", r"^chat$", r"open chat"], timeout=3000):
            return False
        try:
            box = p.locator("textarea[aria-label*='message' i], textarea").first
            box.fill(message, timeout=4000)
            box.press("Enter")
            p.wait_for_timeout(500)
            _click_first(p, [r"chat with everyone", r"^close$"], timeout=1500)  # close the panel again
            return True
        except Exception:
            return False


class Zoom(Platform):
    hook_webaudio = True
    ended_patterns = [r"meeting has been ended by (the )?host", r"host has ended this meeting",
                      r"you have been removed", r"this meeting has ended"]
    blocked_patterns = [
        (r"passcode (is )?(wrong|incorrect)|incorrect passcode", "Zoom says the passcode is wrong. "
         "Use the full invite link (it contains ?pwd=...)."),
        (r"invalid meeting id|meeting id is not valid|this meeting link is invalid", "That Zoom meeting ID isn't valid."),
        (r"unable to join this meeting|you cannot join this meeting|authorized attendees only|"
         r"only authenticated users",
         "This Zoom meeting only allows signed-in users. Run `meeting-agent login --url https://zoom.us/signin` "
         "and sign the bot's browser into a Zoom account."),
        (r"join from (your )?browser is (disabled|not allowed)|web client.*disabled",
         "The host has disabled joining from the browser. Ask them to enable 'Show a Join from your "
         "browser link' in Zoom settings, or use `meeting-agent record` with a loopback device."),
    ]
    waiting_patterns = [
        (r"host will let you in soon|waiting room", "In Zoom's waiting room; waiting for the host to admit the bot."),
        (r"waiting for the host to start|meeting has not started", "The meeting hasn't started yet; waiting for the host."),
    ]

    def prepare_url(self, url: str) -> str:
        # https://us02web.zoom.us/j/123?pwd=x  ->  https://app.zoom.us/wc/join/123?pwd=x  (browser client)
        parsed = urllib.parse.urlparse(url)
        if "/wc/" in parsed.path:
            return url
        m = re.search(r"/j/(\d+)", parsed.path)
        if not m:
            return url  # e.g. a personal /my/<name> link: follow Zoom's own redirect
        q = f"?{parsed.query}" if parsed.query else ""
        return f"https://app.zoom.us/wc/join/{m.group(1)}{q}"

    def scope(self) -> Page | Frame:
        for f in self.page.frames:
            if f is not self.page.main_frame and ("webclient" in f.url or "/wc/" in f.url):
                return f
        return self.page

    def join(self) -> None:
        p = self.page
        # Landing page for /j/ and /my/ links.
        _click_first(p, [r"join from (your )?browser"], timeout=3000, roles=("link", "button"))
        p.wait_for_timeout(2000)
        # Privacy-preserving choice on cookie banners only; never accepts terms on your behalf.
        _click_first(p, [r"^reject all", r"^decline"], timeout=1500)
        f = self.scope()
        _click_first(f, [r"^mute$"], timeout=1500)          # preview: mic off
        _click_first(f, [r"^stop video$"], timeout=1500)    # preview: camera off
        if not _fill_name(f, self.name, ["#input-for-name", "input[placeholder*='name' i]", "input[aria-label*='name' i]"]):
            self.log("Zoom didn't ask for a name (signed-in profile, or the page is still loading).")
        if not _click_first(f, [r"^join$"], timeout=8000):
            self.log("Could not find Zoom's Join button; click it in the bot's window.")

    def in_call(self) -> bool:
        f = self.scope()
        return _visible(f, "button", r"^leave") or _visible(f, "button", r"participants")

    def after_join(self) -> None:
        # The browser client only receives meeting audio once "computer audio" is joined.
        f = self.scope()
        if _click_first(f, [r"join audio by computer"], timeout=4000):
            return
        if _click_first(f, [r"^join audio"], timeout=3000):
            _click_first(f, [r"join audio by computer", r"computer audio"], timeout=3000)

    def post_chat(self, message: str) -> bool:
        f = self.scope()
        try:
            if not _click_first(f, [r"^chat", r"open the chat"], timeout=3000):
                return False
            box = f.locator("[contenteditable='true'], textarea").last
            box.fill(message, timeout=4000)
            box.press("Enter")
            return True
        except Exception:
            return False

    def leave(self) -> None:
        f = self.scope()
        if _click_first(f, [r"^leave"], timeout=1500):
            _click_first(f, [r"leave meeting"], timeout=1500)


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
        # meet.jit.si only starts a room once a signed-in host is present. If the bot
        # gets there first it is bounced to a sign-in page: go back and wait instead,
        # so nobody signs personal accounts into the bot's browser.
        room_url = self.page.url.split("#")[0]
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if self.page.locator("#largeVideoContainer, .filmstrip").first.is_visible():
                    return True
            except Exception:
                pass
            on_signin = "auth-static" in self.page.url or "/signin" in self.page.url
            if on_signin or re.search(r"waiting for (the )?(host|moderator)|has not yet started",
                                      _body_text(self.page), re.I):
                self._say_once("This Jitsi server needs the host to join (signed in) before guests can. "
                               "Join the meeting yourself in your own browser; the bot will get in "
                               "automatically. Don't sign in inside the bot's window.")
                if on_signin:
                    self.page.wait_for_timeout(15000)
                    self.page.goto(room_url, wait_until="domcontentloaded")
                    self.join()
            self.page.wait_for_timeout(2000)
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


class Teams(Platform):
    ended_patterns = [r"you've been removed", r"the meeting has ended", r"you left the meeting"]
    waiting_patterns = [(r"someone in the meeting should let you in soon", "In the Teams lobby; waiting to be admitted.")]

    def join(self) -> None:
        p = self.page
        _click_first(p, [r"continue on this browser", r"join on the web instead"], timeout=5000)
        _fill_name(p, self.name, ["input[placeholder*='name' i]", "input[data-tid='prejoin-display-name-input']"])
        _click_first(p, [r"^join now$"], timeout=8000)

    def in_call(self) -> bool:
        return _visible(self.page, "button", r"^leave")


PLATFORMS = {"meet": GoogleMeet, "jitsi": Jitsi, "zoom": Zoom, "teams": Teams, "generic": Platform}
