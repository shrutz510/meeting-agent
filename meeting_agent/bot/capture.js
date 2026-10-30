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
  const sinks = [];
  window.__maStats = () => ({ tracks: seenTracks.size, recording: !!recorder });

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

  // The bot's own (fake, silent) microphone must never count as meeting audio.
  const localTracks = new Set();
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    const origGUM = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async (...args) => {
      const s = await origGUM(...args);
      s.getTracks().forEach((t) => localTracks.add(t.id));
      return s;
    };
  }

  function addTrack(track) {
    if (!track || track.kind !== "audio" || seenTracks.has(track.id) || localTracks.has(track.id)) return;
    seenTracks.add(track.id);
    ensureGraph();
    const stream = new MediaStream([track]);
    // Some Chrome versions only pull audio from a remote WebRTC track into WebAudio
    // while the track is also attached to a playing media element. Harmless otherwise.
    const sink = new Audio();
    sink.muted = true;
    sink.srcObject = stream;
    sink.play().catch(() => {});
    sinks.push(sink);
    const src = ctx.createMediaStreamSource(stream);
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

  // 2) Streams the app plays through <audio>/<video> elements (catches remote
  //    audio that reached the page some other way than an RTCPeerConnection event).
  const srcDesc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "srcObject");
  if (srcDesc && srcDesc.set) {
    Object.defineProperty(HTMLMediaElement.prototype, "srcObject", {
      configurable: true,
      enumerable: srcDesc.enumerable,
      get() { return srcDesc.get.call(this); },
      set(stream) {
        srcDesc.set.call(this, stream);
        try {
          if (stream && stream.getAudioTracks && !sinks.includes(this)) {
            stream.getAudioTracks().forEach(addTrack);
            stream.addEventListener && stream.addEventListener("addtrack", (e) => addTrack(e.track));
          }
        } catch (_) { /* never break the host app */ }
      },
    });
  }

  // 3) WebAudio playback (Zoom's web client decodes audio itself and plays it
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
