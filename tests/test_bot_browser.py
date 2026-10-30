"""Browser tests for the join flows and the audio tap, using local mock pages.

These exercise our logic against pages shaped like Meet/Zoom's join screens. They
can't prove the live sites still look this way; that needs a real meeting.
"""

import pytest

pw_api = pytest.importorskip("playwright.sync_api")

from meeting_agent.bot.platforms import GoogleMeet, JoinFailed, Zoom  # noqa: E402
from meeting_agent.bot.runner import CAPTURE_JS  # noqa: E402

MEET_PAGE = """
<input aria-label="Your name">
<button aria-label="Turn off microphone" onclick="window.micOff=true">mic</button>
<button aria-label="Turn off camera">cam</button>
<button id="ask">Ask to join</button>
<div id="status"></div>
<script>
document.getElementById('ask').onclick = () => {
  window.joinedAs = document.querySelector('input').value;
  document.getElementById('status').textContent = 'Asking to be let in...';
  setTimeout(() => {
    document.getElementById('status').innerHTML = OUTCOME;
  }, 800);
};
</script>
"""

ZOOM_PAGE = """
<a href="#" id="browser">Join from your browser</a>
<div id="preview" hidden>
  <input id="input-for-name"><button id="join">Join</button>
</div>
<script>
document.getElementById('browser').onclick = (e) => { e.preventDefault();
  document.getElementById('preview').hidden = false; };
document.getElementById('join').onclick = () => {
  window.joinedAs = document.getElementById('input-for-name').value;
  document.body.innerHTML = '<p>Please wait, the meeting host will let you in soon.</p>';
  setTimeout(() => {
    document.body.innerHTML = '<button>Participants</button><button>Leave</button>' +
      '<button onclick="window.audioJoined=true">Join Audio by Computer</button>';
  }, 800);
};
</script>
"""


@pytest.fixture(scope="module")
def browser():
    with pw_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch(headless=True, args=["--use-fake-ui-for-media-stream",
                                                        "--use-fake-device-for-media-stream",
                                                        "--autoplay-policy=no-user-gesture-required"])
        except Exception as e:  # browsers not installed
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        b.close()


def _page(browser, html):
    page = browser.new_page()
    page.set_content(html)
    return page


def test_meet_guest_join_admitted(browser):
    page = _page(browser, MEET_PAGE.replace("OUTCOME", "'<button aria-label=\"Leave call\">x</button>'"))
    logs = []
    meet = GoogleMeet(page, "Notetaker", logs.append)
    meet.join()
    assert page.evaluate("window.joinedAs") == "Notetaker"
    assert page.evaluate("window.micOff") is True
    assert meet.wait_until_in_call(timeout_s=10)
    assert any("admit" in m for m in logs)


def test_meet_denied_fails_fast(browser):
    page = _page(browser, MEET_PAGE.replace("OUTCOME", "'Someone in the call denied your request to join'"))
    meet = GoogleMeet(page, "Notetaker", lambda *_: None)
    meet.join()
    with pytest.raises(JoinFailed, match="denied"):
        meet.wait_until_in_call(timeout_s=10)


def test_meet_guest_blocked_explains_login(browser):
    page = _page(browser, "<h1>You can't join this video call</h1>")
    with pytest.raises(JoinFailed, match="meeting-agent login"):
        GoogleMeet(page, "Notetaker", lambda *_: None).wait_until_in_call(timeout_s=5)


def test_zoom_browser_join_waiting_room_and_audio(browser):
    page = _page(browser, ZOOM_PAGE)
    logs = []
    zoom = Zoom(page, "Notetaker", logs.append)
    zoom.join()
    assert page.evaluate("window.joinedAs") == "Notetaker"
    assert zoom.wait_until_in_call(timeout_s=10)
    assert any("waiting room" in m for m in logs)
    assert page.evaluate("window.audioJoined") is True


def test_zoom_wrong_passcode_fails_fast(browser):
    page = _page(browser, "<p>The meeting passcode is wrong. Please try again.</p>")
    with pytest.raises(JoinFailed, match="passcode"):
        Zoom(page, "Notetaker", lambda *_: None).wait_until_in_call(timeout_s=5)


def test_capture_ignores_own_mic_and_hears_remote(browser):
    ctx = browser.new_context(permissions=["microphone"])
    ctx.add_init_script(CAPTURE_JS)
    # getUserMedia only exists on secure pages, so serve a blank page over (mocked) https.
    ctx.route("https://meeting.test/", lambda route: route.fulfill(body="<html></html>", content_type="text/html"))
    page = ctx.new_page()
    page.goto("https://meeting.test/")
    stats = page.evaluate("""async () => {
      // The bot's own (fake) mic shown in a self-view: must be ignored.
      const local = await navigator.mediaDevices.getUserMedia({audio: true});
      const selfView = document.createElement('video'); selfView.srcObject = local;
      // A remote participant: tone sent over a loopback WebRTC connection.
      const ac = new AudioContext(); const osc = ac.createOscillator();
      const d = ac.createMediaStreamDestination(); osc.connect(d); osc.start();
      const a = new RTCPeerConnection(), b = new RTCPeerConnection();
      a.onicecandidate = e => e.candidate && b.addIceCandidate(e.candidate);
      b.onicecandidate = e => e.candidate && a.addIceCandidate(e.candidate);
      d.stream.getTracks().forEach(t => a.addTrack(t, d.stream));
      await a.setLocalDescription(await a.createOffer()); await b.setRemoteDescription(a.localDescription);
      await b.setLocalDescription(await b.createAnswer()); await a.setRemoteDescription(b.localDescription);
      await new Promise(r => setTimeout(r, 2500));
      return {...window.__maStats(), level: window.__maLevel()};
    }""")
    ctx.close()
    assert stats["tracks"] == 1
    assert stats["recording"] is True
    assert stats["level"] > 0.01
