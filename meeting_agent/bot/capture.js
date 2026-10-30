// Injected into every frame before page scripts run. It taps the audio the
// meeting app *receives* (other participants) and records it with MediaRecorder.
// Chunks are passed back to Python via window.__maChunk(frameId, base64).
// Nothing leaves the machine: the binding writes straight to a local file.
(() => {
  if (window.__maInstalled) return;
  window.__maInstalled = true;

  const FRAME_ID = Math.random().toString(36).slice(2, 10);
  const HOOK_WEBAUDIO = !!window.__maHookWebAudio; // set by Python for apps that play audio via WebAudio (Zoom)
  let ctx = null, dest = null, analyser = null, recorder = null;
  const seenTracks = new Set();

  function ensureGraph() {
    if (ctx) return;
    ctx = new AudioContext({ sampleRate: 48000 });
    dest = ctx.createMediaStreamDestination();
    analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    dest.__maOwn = true;
  }

  function toBase64(buf) {
    const bytes = new Uint8Array(buf);
    let bin = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(bin);
  }

  function startRecorder() {
    if (recorder) return;
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "audio/webm";
    recorder = new MediaRecorder(dest.stream, { mimeType: mime, audioBitsPerSecond: 64000 });
    recorder.ondataavailable = async (e) => {
      if (!e.data || !e.data.size || !window.__maChunk) return;
      window.__maChunk(FRAME_ID, toBase64(await e.data.arrayBuffer()));
    };
    recorder.start(2000);
  }

  function addTrack(track) {
    if (!track || track.kind !== "audio" || seenTracks.has(track.id)) return;
    seenTracks.add(track.id);
    ensureGraph();
    const src = ctx.createMediaStreamSource(new MediaStream([track]));
    src.connect(dest);
    src.connect(analyser);
    if (ctx.state !== "running") ctx.resume();
    startRecorder();
  }

  // 1) WebRTC: every remote audio track (Google Meet, Jitsi, Teams, most others).
  const OrigPC = window.RTCPeerConnection;
  if (OrigPC) {
    class HookedPC extends OrigPC {
      constructor(...args) {
        super(...args);
        this.addEventListener("track", (e) => addTrack(e.track));
      }
    }
    window.RTCPeerConnection = HookedPC;
    if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = HookedPC;
  }

  // 2) WebAudio playback (Zoom's web client decodes audio itself and plays it
  //    through an AudioContext rather than an RTCPeerConnection).
  if (HOOK_WEBAUDIO && window.AudioNode) {
    const origConnect = AudioNode.prototype.connect;
    const taps = new WeakMap();
    AudioNode.prototype.connect = function (target, ...rest) {
      const result = origConnect.call(this, target, ...rest);
      try {
        if (target instanceof AudioDestinationNode && this.context !== ctx) {
          let tap = taps.get(this.context);
          if (!tap) {
            tap = this.context.createMediaStreamDestination();
            taps.set(this.context, tap);
            tap.stream.getAudioTracks().forEach(addTrack);
          }
          origConnect.call(this, tap);
        }
      } catch (_) { /* never break the host app */ }
      return result;
    };
  }

  window.__maLevel = () => {
    if (!analyser) return 0;
    const data = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(data);
    let sum = 0;
    for (const v of data) sum += v * v;
    return Math.sqrt(sum / data.length);
  };

  window.__maStop = () => new Promise((resolve) => {
    if (!recorder || recorder.state === "inactive") return resolve(false);
    recorder.addEventListener("stop", () => setTimeout(() => resolve(true), 500));
    recorder.stop();
  });
})();
