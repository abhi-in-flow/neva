import { useEffect, useRef, useState } from 'react';
import HudBar from '../components/HudBar.jsx';
import { api } from '../lib/api.js';
import { useRecorder } from '../lib/useRecorder.js';
import { langByCode } from '../lib/languages.js';
import '../styles/speaker.css';

// Speaker flow, brief 03. One component owns BOTH speaking phases so the
// card image element persists across the shrink-up seam. Label blackout is
// architectural: SpeakerStage (the recording UI) never receives a label —
// the label prop exists only on ConfirmPanel, and the server only sends it
// in the confirm phase anyway. Never cached, never forwarded. Confirm copy
// presents the system card label as the target concept, not ASR/translation.
//
// Re-record is intentionally absent: the backend has no turn-reset endpoint
// (Wave 1 cut). The client does not fake a return to speaking_view_image.

const MicGlyph = ({ size = 44 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <rect x="9" y="2.5" width="6" height="11.5" rx="3" fill="currentColor" />
    <path d="M4.5 11a7.5 7.5 0 0 0 15 0" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    <path d="M12 18.5V22" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
  </svg>
);

export default function Speaker({ state }) {
  const confirmPhase = state.phase === 'speaking_confirm_label';
  const rec = useRecorder();
  const native = langByCode(localStorage.getItem('ddf_native_lang'))?.en;

  return (
    <main className="speaker-shell">
      <HudBar player={state.player} />

      <figure className={`card-frame ${confirmPhase ? 'card-frame-thumb' : ''}`}>
        <img
          className={`card-img ${rec.status === 'recording' ? 'card-dim' : ''}`}
          src={state.turn?.card_image_url}
          alt="Your secret card — describe it aloud"
          draggable="false"
        />
      </figure>

      {confirmPhase ? (
        <ConfirmPanel label={state.turn?.label?.text} clipUrl={rec.lastClipUrl} />
      ) : (
        <SpeakerStage rec={rec} native={native} />
      )}

      {rec.toast && (
        <div key={rec.toast.key} className={`toast ${rec.toast.shake ? 'toast-shake' : ''}`} role="status">
          {rec.toast.text}
        </div>
      )}
    </main>
  );
}

/* ---------- recording stage: NO label prop exists here ---------- */

function SpeakerStage({ rec, native }) {
  const [coach] = useState(() => !localStorage.getItem('ddf_coached'));
  useEffect(() => {
    if (coach) localStorage.setItem('ddf_coached', '1');
  }, [coach]);

  const recording = rec.status === 'recording';
  const uploading = rec.status === 'uploading';

  return (
    <section className="stage">
      <p className="stage-instruction">
        {recording
          ? 'We’re listening…'
          : `Hold the button — describe it in ${native || 'your language'}`}
      </p>

      <div className="talk-wrap">
        {/* 8s ring timer draws around the button while held */}
        <svg className={`ring ${recording ? 'ring-run' : ''}`} viewBox="0 0 120 120" aria-hidden="true">
          <circle className="ring-track" cx="60" cy="60" r="54" />
          <circle className="ring-fill" cx="60" cy="60" r="54" />
        </svg>

        <button
          type="button"
          className={`talk ${recording ? 'talk-live' : ''} ${coach && !recording ? 'talk-coach' : ''}`}
          style={recording ? { '--mic-level': rec.level } : undefined}
          disabled={uploading}
          aria-label={recording ? 'Recording — release to send' : 'Hold to talk'}
          {...rec.handlers}
        >
          {uploading ? <span className="spinner spinner-warm" aria-label="Sending" /> : <MicGlyph />}
        </button>
      </div>

      <p className="stage-hint">
        {uploading ? 'Sending your voice…' : recording ? 'Release to send · slides off = cancel' : '1–8 seconds'}
      </p>
    </section>
  );
}

/* ---------- confirm panel: the only place the label exists ---------- */

function ConfirmPanel({ label, clipUrl }) {
  const [sending, setSending] = useState(false);
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef(null);

  const replay = () => {
    if (!clipUrl) return;
    if (!audioRef.current) audioRef.current = new Audio(clipUrl);
    audioRef.current.currentTime = 0;
    audioRef.current.play();
    setPlaying(true);
    audioRef.current.onended = () => setPlaying(false);
  };
  useEffect(() => () => audioRef.current?.pause(), []);

  const send = async () => {
    setSending(true);
    try {
      await api.confirmLabel();
      // the poll advances the phase; keep the button settled meanwhile
    } catch {
      setSending(false);
    }
  };

  return (
    <section className="confirm">
      <p className="confirm-ask">Your target concept was:</p>
      <p className="confirm-label" lang="und">{label}</p>
      <p className="confirm-check">Did your recording describe this?</p>

      {clipUrl && (
        <button type="button" className="replay" onClick={replay}>
          <span className={`replay-dot ${playing ? 'replay-live' : ''}`} aria-hidden="true" />
          {playing ? 'Playing your take…' : 'Hear your take'}
        </button>
      )}

      <div className="confirm-actions">
        <button type="button" className="cta" disabled={sending} onClick={send}>
          {sending ? <span className="spinner spinner-warm" aria-label="Sending" /> : 'Yes, send it →'}
        </button>
      </div>
    </section>
  );
}
