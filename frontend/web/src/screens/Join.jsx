import { useEffect, useRef, useState } from 'react';
import { api, setToken } from '../lib/api.js';
import { generateName } from '../lib/names.js';
import { LANGUAGES } from '../lib/languages.js';
import '../styles/join.css';

// Brief 01: two steps, no scroll each. Step 1 = identity (tagline hero,
// generated nickname + shuffle, native language). Step 2 = other languages
// (dual-script chips, native excluded, ≥1 required) + the one warm CTA that
// asks for the mic and joins in a single gesture.

const DiceIcon = () => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <rect x="3" y="3" width="18" height="18" rx="4" stroke="currentColor" strokeWidth="2" />
    <circle cx="8.5" cy="8.5" r="1.7" fill="currentColor" />
    <circle cx="15.5" cy="15.5" r="1.7" fill="currentColor" />
    <circle cx="15.5" cy="8.5" r="1.7" fill="currentColor" />
    <circle cx="8.5" cy="15.5" r="1.7" fill="currentColor" />
  </svg>
);

const MicIcon = () => (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor" />
    <path d="M5 11a7 7 0 0 0 14 0" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    <path d="M12 18v3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </svg>
);

export default function Join({ onJoined }) {
  const [step, setStep] = useState(1);
  const [nickname, setNickname] = useState(() => generateName());
  const [nameSpin, setNameSpin] = useState(0);
  const [native, setNative] = useState(null);
  const [common, setCommon] = useState(() => new Set());
  const [otherOpen, setOtherOpen] = useState(false);
  const [otherLang, setOtherLang] = useState('');
  const [error, setError] = useState(null); // { where, text }
  const [cta, setCta] = useState('idle'); // idle | asking-mic | mic-denied | joining | join-failed
  const [micProblem, setMicProblem] = useState('denied'); // denied | no-mic
  const step2Ref = useRef(null);

  const shuffle = () => {
    setNickname(generateName(nickname));
    setNameSpin((n) => n + 1);
  };

  const pickNative = (code) => {
    setNative(code);
    setCommon((prev) => {
      const next = new Set(prev);
      next.delete(code); // native never doubles as a common language
      return next;
    });
    setError(null);
  };

  const toggleCommon = (code) => {
    setCommon((prev) => {
      const next = new Set(prev);
      next.has(code) ? next.delete(code) : next.add(code);
      return next;
    });
    setError(null);
  };

  const goToStep2 = () => {
    if (!nickname.trim()) {
      setError({ where: 'nickname', text: 'The TV needs a name to cheer for!' });
      return;
    }
    if (!native) {
      setError({ where: 'native', text: 'Pick your mother tongue — it’s your superpower here.' });
      return;
    }
    setError(null);
    setStep(2);
  };

  useEffect(() => {
    if (step === 2) step2Ref.current?.focus();
  }, [step]);

  const letsPlay = async () => {
    const langs = [...common];
    if (otherLang.trim()) langs.push(otherLang.trim().toLowerCase());
    if (langs.length === 0) {
      setError({ where: 'common', text: 'Pick at least one — your partner needs a language you share.' });
      return;
    }
    setError(null);

    // One gesture, three acts: mic → join → play (brief 01 §7)
    setCta('asking-mic');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      // Permission is what we needed; recording streams are opened per-turn.
      stream.getTracks().forEach((t) => t.stop());
    } catch (err) {
      setMicProblem(err?.name === 'NotFoundError' ? 'no-mic' : 'denied');
      setCta('mic-denied');
      return;
    }

    setCta('joining');
    try {
      const { session_token } = await api.join({
        nickname: nickname.trim(),
        native_lang: native,
        common_langs: langs,
      });
      setToken(session_token);
      // Queued-screen copy personalizes on the declared native language.
      localStorage.setItem('ddf_native_lang', native);
      onJoined();
    } catch {
      setCta('join-failed');
    }
  };

  if (cta === 'mic-denied') {
    return (
      <main className="join-shell">
        <section className="mic-denied" aria-live="assertive">
          <span className="mic-denied-glyph" aria-hidden="true">🎤</span>
          <h1 className="mic-denied-title">We can’t hear you yet</h1>
          <p>
            Your voice is the whole game — without the mic there’s nothing to play.
            No recordings happen until you hold the talk button.
          </p>
          {micProblem === 'no-mic' ? (
            <p>
              We couldn’t find a microphone on this device. If you have a headset,
              plug it in and try again — or borrow a friend’s phone!
            </p>
          ) : (
            <ol>
              <li>Tap the <strong>⋮ menu → Settings → Site settings</strong> in Chrome</li>
              <li>Open <strong>Microphone</strong> and allow this site</li>
              <li>Come back — we saved your details</li>
            </ol>
          )}
          <button type="button" className="cta" onClick={letsPlay}>
            <MicIcon /> Try the mic again
          </button>
        </section>
      </main>
    );
  }

  return (
    <main className="join-shell">
      <header className="join-top">
        <span className="step-dots" aria-label={`Step ${step} of 2`}>
          <i className={step === 1 ? 'on' : ''} />
          <i className={step === 2 ? 'on' : ''} />
        </span>
        {step === 2 && (
          <button type="button" className="back-link" onClick={() => setStep(1)}>
            ← back
          </button>
        )}
      </header>

      <div className="join-track" data-step={step}>
        {/* ---------------- Step 1: identity ---------------- */}
        <section className="join-step" aria-hidden={step !== 1} inert={step !== 1 ? '' : undefined}>
          <h1 className="hero">
            Speak your <span className="hero-accent">language.</span>
            <small>Win points. Teach an AI.</small>
          </h1>

          <div className="field">
            <label htmlFor="nickname">Your game name</label>
            <div className="name-row">
              <input
                id="nickname"
                value={nickname}
                maxLength={20}
                autoComplete="off"
                onChange={(e) => setNickname(e.target.value)}
              />
              <button
                type="button"
                className="shuffle"
                onClick={shuffle}
                aria-label="Shuffle name"
                data-spin={nameSpin}
              >
                <DiceIcon />
              </button>
            </div>
            {error?.where === 'nickname' && <p className="field-error">{error.text}</p>}
          </div>

          <div className="field">
            <label id="native-label">Your native language — your mother tongue</label>
            <div className="chip-grid" role="radiogroup" aria-labelledby="native-label">
              {LANGUAGES.map((l) => (
                <button
                  key={l.code}
                  type="button"
                  role="radio"
                  aria-checked={native === l.code}
                  className={`chip ${native === l.code ? 'chip-on' : ''}`}
                  onClick={() => pickNative(l.code)}
                >
                  <span className="chip-en">{l.en}</span>
                  {l.native !== l.en && <span className="chip-native">{l.native}</span>}
                </button>
              ))}
            </div>
            {error?.where === 'native' && <p className="field-error">{error.text}</p>}
          </div>

          <button type="button" className="cta" onClick={goToStep2}>
            Next →
          </button>
        </section>

        {/* ---------------- Step 2: languages ---------------- */}
        <section className="join-step" aria-hidden={step !== 2} inert={step !== 2 ? '' : undefined}>
          <h2 className="step-title" ref={step2Ref} tabIndex={-1}>
            What else do you speak?
          </h2>
          <p className="step-sub">We pair you with someone who shares one of these.</p>

          <div className="chip-grid" role="group" aria-label="Other languages you speak">
            {LANGUAGES.filter((l) => l.code !== native).map((l) => (
              <button
                key={l.code}
                type="button"
                aria-pressed={common.has(l.code)}
                className={`chip ${common.has(l.code) ? 'chip-on' : ''}`}
                onClick={() => toggleCommon(l.code)}
              >
                <span className="chip-en">{l.en}</span>
                {l.native !== l.en && <span className="chip-native">{l.native}</span>}
              </button>
            ))}
            <button
              type="button"
              aria-pressed={otherOpen}
              className={`chip ${otherOpen ? 'chip-on' : ''}`}
              onClick={() => setOtherOpen(!otherOpen)}
            >
              <span className="chip-en">Other…</span>
            </button>
          </div>

          {otherOpen && (
            <input
              className="other-input"
              placeholder="Type your language"
              value={otherLang}
              maxLength={30}
              onChange={(e) => setOtherLang(e.target.value)}
            />
          )}
          {error?.where === 'common' && <p className="field-error">{error.text}</p>}

          <div className="cta-block">
            <button
              type="button"
              className={`cta ${cta === 'asking-mic' ? 'cta-pulse' : ''}`}
              disabled={cta === 'asking-mic' || cta === 'joining'}
              onClick={letsPlay}
            >
              {cta === 'asking-mic' && <>Listening for permission…</>}
              {cta === 'joining' && <span className="spinner" aria-label="Joining" />}
              {(cta === 'idle' || cta === 'join-failed') && (
                <>
                  Let’s play <MicIcon />
                </>
              )}
            </button>
            <p className="mic-note">
              {cta === 'join-failed'
                ? 'The hall Wi-Fi hiccupped — tap to try again.'
                : 'We’ll ask for your mic — that’s the whole game! 🎤'}
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}
