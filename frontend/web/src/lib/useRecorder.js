import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api.js';

// Hold-to-talk state machine (brief 03 §7):
//   pointerdown → record (webm/opus, iOS mp4 fallback) + AnalyserNode level
//   pointerup   → stop; <1s discard with toast, else upload immediately
//   pointerleave/cancel → discard ("keep your thumb on the button")
//   8s hard cap → auto-STOP-AND-SEND (enthusiasm is never punished)
//   tab hidden mid-recording → silent discard
// Upload responses: {status:'ok'} → success beat (poll advances the phase);
// {status:'re_record', reason} → shake + server's reason verbatim.

const MIN_MS = 1000;
const MAX_MS = 8000;

function pickMime() {
  if (typeof MediaRecorder === 'undefined') return null;
  if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) return 'audio/webm;codecs=opus';
  if (MediaRecorder.isTypeSupported('audio/webm')) return 'audio/webm';
  if (MediaRecorder.isTypeSupported('audio/mp4')) return 'audio/mp4'; // iOS Safari
  return '';
}

export function useRecorder() {
  const [status, setStatus] = useState('idle'); // idle | recording | uploading
  const [toast, setToast] = useState(null); // { text, shake? }
  const [level, setLevel] = useState(0); // 0..1 mic input, drives button breathing
  const [lastClipUrl, setLastClipUrl] = useState(null);

  const recRef = useRef(null); // { recorder, stream, audioCtx, raf, chunks, startedAt, hardStop, intent }
  const toastTimer = useRef(null);

  const showToast = useCallback((text, shake = false) => {
    clearTimeout(toastTimer.current);
    setToast({ text, shake, key: Date.now() });
    toastTimer.current = setTimeout(() => setToast(null), 2800);
  }, []);

  const teardown = useCallback(() => {
    const r = recRef.current;
    if (!r) return;
    cancelAnimationFrame(r.raf);
    clearTimeout(r.hardStop);
    r.stream.getTracks().forEach((t) => t.stop());
    r.audioCtx.close().catch(() => {});
    recRef.current = null;
    setLevel(0);
  }, []);

  const upload = useCallback(
    async (blob, mime) => {
      setStatus('uploading');
      const url = URL.createObjectURL(blob);
      try {
        const ext = mime.includes('mp4') ? 'm4a' : 'webm';
        const res = await api.uploadAudio(blob, `recording.${ext}`);
        if (res.status === 'ok') {
          navigator.vibrate?.(20);
          setLastClipUrl((old) => {
            if (old) URL.revokeObjectURL(old);
            return url; // kept for the confirm screen's self-replay
          });
          setStatus('idle');
        } else {
          URL.revokeObjectURL(url);
          setStatus('idle');
          showToast(res.reason || 'One more take!', true);
        }
      } catch {
        URL.revokeObjectURL(url);
        setStatus('idle');
        showToast('The hall Wi-Fi hiccupped — hold to try again.', true);
      }
    },
    [showToast],
  );

  const stop = useCallback(
    (intent) => {
      const r = recRef.current;
      if (!r || r.stopping) return;
      r.stopping = true;
      r.intent = intent; // 'send' | 'discard' | 'auto'
      r.recorder.stop();
    },
    [],
  );

  const start = useCallback(async () => {
    if (recRef.current || status === 'uploading') return;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch {
      showToast('We lost your mic — check Chrome’s site settings.', true);
      return;
    }

    const mime = pickMime();
    const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioCtx.createMediaStreamSource(stream);
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    const buf = new Uint8Array(analyser.fftSize);

    const r = {
      recorder, stream, audioCtx, chunks: [],
      startedAt: performance.now(), raf: 0, hardStop: 0, intent: 'send', stopping: false,
    };
    recRef.current = r;

    const loop = () => {
      analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i += 1) {
        const v = (buf[i] - 128) / 128;
        sum += v * v;
      }
      setLevel(Math.min(1, Math.sqrt(sum / buf.length) * 3));
      r.raf = requestAnimationFrame(loop);
    };
    r.raf = requestAnimationFrame(loop);

    recorder.ondataavailable = (e) => e.data.size > 0 && r.chunks.push(e.data);
    recorder.onstop = () => {
      const elapsed = performance.now() - r.startedAt;
      const { intent, chunks } = r;
      const type = recorder.mimeType || mime || 'audio/webm';
      teardown();
      if (intent === 'discard') {
        setStatus('idle');
        return;
      }
      if (elapsed < MIN_MS) {
        setStatus('idle');
        showToast('Hold longer — give it a full breath!');
        return;
      }
      if (intent === 'auto') showToast('Time’s up — sending!');
      upload(new Blob(chunks, { type }), type);
    };

    r.hardStop = setTimeout(() => stop('auto'), MAX_MS);
    recorder.start();
    navigator.vibrate?.(30);
    setStatus('recording');
  }, [status, showToast, stop, teardown, upload]);

  // tab backgrounded mid-recording → discard silently (brief 03 §6)
  useEffect(() => {
    const onHide = () => {
      if (document.hidden && recRef.current) {
        stop('discard');
        setStatus('idle');
      }
    };
    document.addEventListener('visibilitychange', onHide);
    return () => document.removeEventListener('visibilitychange', onHide);
  }, [stop]);

  useEffect(() => () => { teardown(); clearTimeout(toastTimer.current); }, [teardown]);

  const discardWithNudge = () => {
    if (status === 'recording') {
      stop('discard');
      showToast('Keep your thumb on the button!');
    }
  };

  // No pointer capture on purpose: sliding off the button discards
  // (pointerleave safety per the work order §2.5).
  const handlers = {
    onPointerDown: (e) => { e.preventDefault(); start(); },
    onPointerUp: () => { if (status === 'recording') stop('send'); },
    onPointerCancel: discardWithNudge,
    onPointerLeave: discardWithNudge,
    onContextMenu: (e) => e.preventDefault(), // long-press must not open a menu
  };

  return { status, toast, level, lastClipUrl, handlers };
}
