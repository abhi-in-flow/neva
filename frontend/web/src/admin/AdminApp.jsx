/**
 * Demo-grade operator admin surface (pathname /admin).
 *
 * Separate from the player App: renders Decks, Metrics, Traces, and the focused
 * Tune panel. All game rules remain server-owned; this UI only calls admin and
 * public metrics endpoints.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AdminApiError,
  adminApi,
  clearAdminKey,
  getAdminKey,
  setAdminKey,
} from '../lib/adminApi.js';
import { ADMIN_DECK_POLL_MS } from '../lib/constants.js';
import {
  DEFAULT_CARD_COUNT,
  DEFAULT_REGION_TAG,
  EXAMPLE_PROMPTS,
  INDIAN_STATES,
} from './deckPresets.js';
import TunePanel from './TunePanel.jsx';
import '../styles/admin.css';

const TABS = [
  { id: 'decks', label: 'Decks' },
  { id: 'metrics', label: 'Metrics' },
  { id: 'traces', label: 'Traces' },
  { id: 'tune', label: 'Tune' },
];

function formatUsd(value) {
  if (typeof value !== 'number') return '—';
  return `$${value.toFixed(4)}`;
}

function formatPct(value) {
  if (typeof value !== 'number') return '—';
  return `${Math.round(value * 100)}%`;
}

function formatMicroUsd(value) {
  if (typeof value !== 'number') return '—';
  return `$${(value / 1_000_000).toFixed(5)}`;
}

/**
 * Derive a human-readable generation stage from persisted metrics + status.
 * Only claims stages that generation_metrics (or terminal status) support.
 */
function generationStageLabel(detail) {
  if (!detail) return '';
  if (detail.status === 'ready') return 'Ready for review';
  if (detail.status === 'live') return 'Live';
  if (detail.status === 'failed') return 'Generation failed';
  const metrics = detail.generation_metrics || {};
  const stage = metrics.progress_stage;
  const ready = typeof metrics.cards_ready === 'number' ? metrics.cards_ready : detail.card_count;
  const target = typeof metrics.cards_target === 'number' ? metrics.cards_target : null;
  if (stage === 'inventing_concepts') {
    return 'Inventing concepts with Gemini';
  }
  if (stage === 'generating_images') {
    if (target != null) {
      return `Generating images with Nano Banana 2 Lite — ${ready} / ${target}`;
    }
    return 'Generating images with Nano Banana 2 Lite';
  }
  if (stage === 'finalizing_decoys') {
    return 'Finalizing decoys';
  }
  if (detail.status === 'generating') {
    return 'Generating…';
  }
  return detail.status;
}

function progressFraction(detail) {
  const metrics = detail?.generation_metrics || {};
  const stage = metrics.progress_stage;
  const ready = typeof metrics.cards_ready === 'number' ? metrics.cards_ready : 0;
  const target = typeof metrics.cards_target === 'number' ? metrics.cards_target : 0;
  if (detail?.status === 'ready' || detail?.status === 'live') return 1;
  if (stage === 'inventing_concepts') return 0.08;
  if (stage === 'finalizing_decoys') return target > 0 ? 0.92 : 0.85;
  if (stage === 'generating_images' && target > 0) {
    return Math.min(0.9, 0.1 + (0.8 * ready) / target);
  }
  if (detail?.status === 'generating' && target > 0 && ready > 0) {
    return Math.min(0.9, ready / target);
  }
  return detail?.status === 'generating' ? 0.05 : 0;
}

function PrivacyNotice() {
  return (
    <p className="admin-privacy-notice">
      <strong>Privacy:</strong>
      {' '}No personal information is requested; only submitted audio is retained.
      Gemma 4 training and hosting run locally on this machine.
    </p>
  );
}

function AuthGate({ onReady }) {
  const [value, setValue] = useState(getAdminKey());
  const [error, setError] = useState('');

  const submit = (event) => {
    event.preventDefault();
    if (!value.trim()) {
      setError('Paste the deck admin key from the operator environment.');
      return;
    }
    setAdminKey(value.trim());
    setError('');
    onReady();
  };

  return (
    <div className="admin-root">
      <header className="admin-header">
        <h1 className="admin-brand">Operator Admin</h1>
      </header>
      <PrivacyNotice />
      <section className="admin-panel">
        <h2>Unlock</h2>
        <p className="admin-muted">
          Enter the shared <code>X-Deck-Admin-Key</code>. It stays in sessionStorage
          for this tab only and is never put in the URL.
        </p>
        {error ? <div className="admin-error">{error}</div> : null}
        <form onSubmit={submit}>
          <div className="admin-row">
            <input
              className="admin-input"
              type="password"
              autoComplete="off"
              placeholder="Deck admin key"
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </div>
          <button className="admin-btn" type="submit">Continue</button>
        </form>
      </section>
    </div>
  );
}

function DecksPanel() {
  const [decks, setDecks] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [prompt, setPrompt] = useState('');
  const [regionTag, setRegionTag] = useState(DEFAULT_REGION_TAG);
  const [cardCount, setCardCount] = useState(DEFAULT_CARD_COUNT);
  const [jsonText, setJsonText] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [expandedCard, setExpandedCard] = useState(null);

  const refresh = useCallback(async () => {
    const data = await adminApi.listDecks();
    setDecks(data.decks || []);
  }, []);

  const loadDetail = useCallback(async (deckId) => {
    setSelectedId(deckId);
    const data = await adminApi.getDeck(deckId);
    setDetail(data);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await refresh();
      } catch (err) {
        if (!cancelled) setError(err.message || 'Failed to load decks');
      }
    })();
    return () => { cancelled = true; };
  }, [refresh]);

  useEffect(() => {
    if (!detail || detail.status !== 'generating') return undefined;
    const timer = setInterval(async () => {
      try {
        await loadDetail(detail.deck_id);
        await refresh();
      } catch {
        /* keep polling quietly */
      }
    }, ADMIN_DECK_POLL_MS);
    return () => clearInterval(timer);
  }, [detail, loadDetail, refresh]);

  useEffect(() => {
    if (!expandedCard) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') setExpandedCard(null);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [expandedCard]);

  const onGenerateFromPrompt = async () => {
    setError('');
    setMessage('');
    if (!prompt.trim()) {
      setError('Enter a one-line theme prompt.');
      return;
    }
    setBusy(true);
    try {
      const result = await adminApi.generateDeckFromPrompt({
        region_tag: regionTag,
        prompt: prompt.trim(),
        card_count: Number(cardCount) || DEFAULT_CARD_COUNT,
      });
      setMessage(`Generation started: ${result.deck_id}`);
      await refresh();
      await loadDetail(result.deck_id);
    } catch (err) {
      if (err instanceof AdminApiError) {
        setError(typeof err.detail === 'string' ? err.detail : err.message);
      } else {
        setError(err.message || 'Generate failed');
      }
    } finally {
      setBusy(false);
    }
  };

  const onGenerateJson = async () => {
    setError('');
    setMessage('');
    setBusy(true);
    try {
      const payload = JSON.parse(jsonText);
      const result = await adminApi.generateDeck(payload);
      setMessage(`JSON generation started: ${result.deck_id}`);
      await refresh();
      await loadDetail(result.deck_id);
    } catch (err) {
      if (err instanceof SyntaxError) {
        setError('Concepts JSON is invalid.');
      } else if (err instanceof AdminApiError) {
        setError(typeof err.detail === 'string' ? err.detail : err.message);
      } else {
        setError(err.message || 'Generate failed');
      }
    } finally {
      setBusy(false);
    }
  };

  const onActivate = async (deckId) => {
    if (!window.confirm('Activate this deck as the sole live deck?')) return;
    setBusy(true);
    setError('');
    try {
      await adminApi.activateDeck(deckId);
      setMessage(`Activated ${deckId}`);
      await refresh();
      await loadDetail(deckId);
    } catch (err) {
      setError(err.detail || err.message || 'Activate failed');
    } finally {
      setBusy(false);
    }
  };

  const onFile = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setJsonText(await file.text());
  };

  const skeletonCount = useMemo(() => {
    if (!detail || detail.status !== 'generating') return 0;
    const metrics = detail.generation_metrics || {};
    const target = typeof metrics.cards_target === 'number'
      ? metrics.cards_target
      : DEFAULT_CARD_COUNT;
    const have = (detail.cards || []).length;
    return Math.max(0, target - have);
  }, [detail]);

  const fraction = progressFraction(detail);
  const costMetrics = detail?.generation_metrics || {};

  return (
    <>
      <section className="admin-panel">
        <h2>Generate deck</h2>
        <p className="admin-muted">
          Enter a one-line theme and pick an Indian state. Gemini invents the
          concepts; Nano Banana 2 Lite draws the cards. Activation stays explicit.
        </p>
        {error ? <div className="admin-error">{error}</div> : null}
        {message ? <p className="admin-muted">{message}</p> : null}
        <label className="admin-label" htmlFor="deck-prompt">Theme prompt</label>
        <input
          id="deck-prompt"
          className="admin-input"
          type="text"
          maxLength={240}
          placeholder="e.g. Monsoon market chaos with animals sharing umbrellas"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
        <div className="admin-form-grid">
          <div>
            <label className="admin-label" htmlFor="deck-state">Indian state</label>
            <select
              id="deck-state"
              className="admin-select"
              value={regionTag}
              onChange={(e) => setRegionTag(e.target.value)}
            >
              {INDIAN_STATES.map((state) => (
                <option key={state.region_tag} value={state.region_tag}>
                  {state.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="admin-label" htmlFor="deck-count">Cards (6–20)</label>
            <input
              id="deck-count"
              className="admin-input"
              type="number"
              min={6}
              max={20}
              value={cardCount}
              onChange={(e) => setCardCount(e.target.value)}
            />
          </div>
        </div>
        <div className="admin-examples" role="group" aria-label="Example prompts">
          {EXAMPLE_PROMPTS.map((example) => (
            <button
              key={example}
              type="button"
              className="admin-example"
              onClick={() => setPrompt(example)}
            >
              {example}
            </button>
          ))}
        </div>
        <div className="admin-row">
          <button
            className="admin-btn"
            type="button"
            disabled={busy}
            onClick={onGenerateFromPrompt}
          >
            Generate
          </button>
          <button className="admin-btn secondary" type="button" disabled={busy} onClick={refresh}>
            Refresh list
          </button>
        </div>

        <details
          className="admin-advanced"
          open={advancedOpen}
          onToggle={(e) => setAdvancedOpen(e.target.open)}
        >
          <summary>Advanced · paste concepts JSON</summary>
          <p className="admin-muted">
            Fallback for operators who already have
            {' '}
            <code>AdminDeckGenerateRequest</code>
            {' '}
            JSON.
          </p>
          <div className="admin-row">
            <input type="file" accept="application/json,.json" onChange={onFile} />
          </div>
          <textarea
            className="admin-textarea"
            value={jsonText}
            onChange={(e) => setJsonText(e.target.value)}
            placeholder='{"region_tag":"assam","concepts":[...]}'
          />
          <div className="admin-row">
            <button
              className="admin-btn secondary"
              type="button"
              disabled={busy}
              onClick={onGenerateJson}
            >
              Generate from JSON
            </button>
          </div>
        </details>
      </section>

      <section className="admin-panel">
        <h2>Decks</h2>
        <table className="admin-table">
          <thead>
            <tr>
              <th>Region</th>
              <th>Status</th>
              <th>Cards</th>
              <th>IPM</th>
              <th>Cost</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {decks.map((deck) => (
              <tr key={deck.deck_id}>
                <td>
                  <button
                    type="button"
                    className="admin-linkish"
                    onClick={() => loadDetail(deck.deck_id)}
                  >
                    {deck.region_tag}
                  </button>
                  <div className="admin-muted">{deck.deck_id.slice(0, 8)}…</div>
                </td>
                <td>
                  <span className={`admin-badge ${deck.status}`}>{deck.status}</span>
                </td>
                <td>{deck.card_count}</td>
                <td>{deck.generation_metrics?.images_per_minute ?? '—'}</td>
                <td>
                  <strong className="admin-cost-inline">
                    {formatUsd(deck.generation_metrics?.total_cost_usd)}
                  </strong>
                </td>
                <td>
                  {(deck.status === 'ready' || deck.status === 'live') && (
                    <button
                      className="admin-btn secondary"
                      type="button"
                      disabled={busy || deck.status === 'live'}
                      onClick={() => onActivate(deck.deck_id)}
                    >
                      {deck.status === 'live' ? 'Live' : 'Activate'}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {detail ? (
        <section className="admin-panel">
          <h2>Review · {detail.region_tag}</h2>
          <p className="admin-muted">
            <span className={`admin-badge ${detail.status}`}>{detail.status}</span>
            {' '}{detail.deck_id}
            {selectedId === detail.deck_id && detail.status === 'generating'
              ? ' · polling…'
              : ''}
          </p>
          {detail.status === 'generating' || detail.generation_metrics?.progress_stage ? (
            <div className="admin-progress">
              <div className="admin-progress-label">{generationStageLabel(detail)}</div>
              <div
                className="admin-progress-bar"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(fraction * 100)}
              >
                <div
                  className="admin-progress-fill"
                  style={{ width: `${Math.round(fraction * 100)}%` }}
                />
              </div>
            </div>
          ) : null}
          {detail.generation_metrics ? (
            <div className="admin-cost-spotlight" aria-live="polite">
              <div>
                <span>Estimated cost incurred</span>
                <strong>{formatUsd(costMetrics.total_cost_usd)}</strong>
              </div>
              <p>
                {costMetrics.images_attempted ?? 0}
                {' '}Nano Banana image calls
                {' · '}
                {costMetrics.flash_calls ?? 0}
                {' '}Gemini Flash calls
                {typeof costMetrics.nb2_unit_cost_usd === 'number'
                  ? ` · ${formatUsd(costMetrics.nb2_unit_cost_usd)} per NB2 image`
                  : ''}
              </p>
            </div>
          ) : null}
          {detail.failure_reason ? (
            <div className="admin-error">{detail.failure_reason}</div>
          ) : null}
          {detail.status !== 'generating' && detail.generation_metrics ? (
            <pre className="admin-pre">{JSON.stringify(detail.generation_metrics, null, 2)}</pre>
          ) : null}
          <h3 className="admin-muted">Cards</h3>
          <div className="admin-card-grid">
            {(detail.cards || []).map((card) => (
              <figure className="admin-card" key={card.card_id}>
                <button
                  className="admin-card-image-button"
                  type="button"
                  onClick={() => setExpandedCard(card)}
                  aria-label={`Open ${card.label_en} image`}
                >
                  <img src={card.image_url} alt={card.label_en} loading="lazy" />
                </button>
                <figcaption>
                  {card.label_en}
                  {card.verified ? ' · verified' : ' · unverified'}
                </figcaption>
              </figure>
            ))}
            {Array.from({ length: skeletonCount }, (_, index) => (
              <figure className="admin-card admin-card-skeleton" key={`skel-${index}`}>
                <div className="admin-skeleton-block" />
                <figcaption>Waiting…</figcaption>
              </figure>
            ))}
          </div>
          {(detail.status === 'ready' || detail.status === 'live') && (
            <div className="admin-row">
              <button
                className="admin-btn"
                type="button"
                disabled={busy || detail.status === 'live'}
                onClick={() => onActivate(detail.deck_id)}
              >
                {detail.status === 'live' ? 'Live' : 'Activate deck'}
              </button>
            </div>
          )}
        </section>
      ) : null}
      {expandedCard ? (
        <div
          className="admin-modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setExpandedCard(null);
          }}
        >
          <div
            className="admin-image-modal"
            role="dialog"
            aria-modal="true"
            aria-label={`${expandedCard.label_en} image preview`}
          >
            <button
              className="admin-modal-close"
              type="button"
              onClick={() => setExpandedCard(null)}
              aria-label="Close image preview"
            >
              ×
            </button>
            <img src={expandedCard.image_url} alt={expandedCard.label_en} />
            <div className="admin-image-modal-caption">
              <strong>{expandedCard.label_en}</strong>
              <span>{expandedCard.verified ? 'Verified' : 'Unverified'}</span>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

function MetricsPanel() {
  const [metrics, setMetrics] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [decks, setDecks] = useState([]);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    const [m, f, d] = await Promise.all([
      adminApi.metrics(),
      adminApi.funnel(),
      adminApi.listDecks(),
    ]);
    setMetrics(m);
    setFunnel(f);
    setDecks(d.decks || []);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        await load();
        if (!cancelled) setError('');
      } catch (err) {
        if (!cancelled) setError(err.message || 'Metrics load failed');
      }
    };
    tick();
    const timer = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [load]);

  return (
    <>
      <section className="admin-panel">
        <h2>Venue throughput</h2>
        {error ? <div className="admin-error">{error}</div> : null}
        <div className="admin-stat-grid">
          <div className="admin-stat">
            <strong>{metrics?.validated_pairs ?? '—'}</strong>
            validated pairs
          </div>
          <div className="admin-stat">
            <strong>{metrics?.training_eligible_pairs ?? '—'}</strong>
            training-eligible
          </div>
          <div className="admin-stat">
            <strong>{metrics?.language_count ?? '—'}</strong>
            languages
          </div>
          <div className="admin-stat">
            <strong>{formatPct(metrics?.gauntlet_pass_rate)}</strong>
            gauntlet pass
          </div>
          <div className="admin-stat">
            <strong>{formatUsd(metrics?.cost_per_validated_sample_usd)}</strong>
            $/validated sample
          </div>
          <div className="admin-stat">
            <strong>
              {typeof metrics?.deck_images_per_minute === 'number'
                ? metrics.deck_images_per_minute.toFixed(1)
                : '—'}
            </strong>
            deck images/min
          </div>
        </div>
        {Array.isArray(metrics?.languages) && metrics.languages.length > 0 ? (
          <p className="admin-muted">{metrics.languages.join(' · ')}</p>
        ) : null}
      </section>

      <section className="admin-panel">
        <h2>Eligibility funnel</h2>
        <div className="admin-stat-grid">
          <div className="admin-stat">
            <strong>{funnel?.validated_pairs ?? '—'}</strong>
            validated
          </div>
          <div className="admin-stat">
            <strong>{funnel?.packaged_records ?? '—'}</strong>
            packaged
          </div>
          <div className="admin-stat">
            <strong>{funnel?.training_eligible_pairs ?? '—'}</strong>
            eligible
          </div>
          <div className="admin-stat">
            <strong>{funnel?.jobs_pending ?? '—'}</strong>
            jobs pending
          </div>
          <div className="admin-stat">
            <strong>{funnel?.jobs_processing ?? '—'}</strong>
            processing
          </div>
          <div className="admin-stat">
            <strong>{funnel?.jobs_failed ?? '—'}</strong>
            failed
          </div>
        </div>
      </section>

      <section className="admin-panel">
        <h2>Deck generation metrics</h2>
        <p className="admin-muted">
          Per-image GenAI calls are not persisted as api_calls rows yet; deck
          totals below are the Track 3 evidence.
        </p>
        <table className="admin-table">
          <thead>
            <tr>
              <th>Region</th>
              <th>Status</th>
              <th>Attempted</th>
              <th>Accepted</th>
              <th>Reject %</th>
              <th>Total $</th>
            </tr>
          </thead>
          <tbody>
            {decks.map((deck) => (
              <tr key={deck.deck_id}>
                <td>{deck.region_tag}</td>
                <td><span className={`admin-badge ${deck.status}`}>{deck.status}</span></td>
                <td>{deck.generation_metrics?.images_attempted ?? '—'}</td>
                <td>{deck.generation_metrics?.images_accepted ?? '—'}</td>
                <td>
                  {typeof deck.generation_metrics?.reject_rate === 'number'
                    ? formatPct(deck.generation_metrics.reject_rate)
                    : '—'}
                </td>
                <td>{formatUsd(deck.generation_metrics?.total_cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

function TracesPanel() {
  const [calls, setCalls] = useState([]);
  const [workers, setWorkers] = useState([]);
  const [anyHealthy, setAnyHealthy] = useState(false);
  const [jobs, setJobs] = useState([]);
  const [counts, setCounts] = useState({});
  const [operation, setOperation] = useState('gauntlet_triage');
  const [expanded, setExpanded] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    const [callData, workerData, jobData] = await Promise.all([
      adminApi.apiCalls({
        limit: 25,
        operation: operation || undefined,
      }),
      adminApi.worker(),
      adminApi.jobs({ limit: 20 }),
    ]);
    setCalls(callData.calls || []);
    setWorkers(workerData.workers || []);
    setAnyHealthy(Boolean(workerData.any_healthy));
    setJobs(jobData.jobs || []);
    setCounts(jobData.counts_by_status || {});
  }, [operation]);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        await load();
        if (!cancelled) setError('');
      } catch (err) {
        if (!cancelled) setError(err.message || 'Traces load failed');
      }
    };
    tick();
    const timer = setInterval(tick, 4000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [load]);

  return (
    <>
      <section className="admin-panel">
        <h2>Worker</h2>
        {error ? <div className="admin-error">{error}</div> : null}
        <p>
          <span className={`admin-badge ${anyHealthy ? 'healthy' : 'unhealthy'}`}>
            {anyHealthy ? 'healthy' : 'no healthy worker'}
          </span>
        </p>
        <table className="admin-table">
          <thead>
            <tr>
              <th>Worker</th>
              <th>Status</th>
              <th>Heartbeat</th>
              <th>Healthy</th>
            </tr>
          </thead>
          <tbody>
            {workers.map((worker) => (
              <tr key={worker.worker_id}>
                <td>{worker.worker_id}</td>
                <td>{worker.status || '—'}</td>
                <td>{worker.heartbeat_at || '—'}</td>
                <td>{worker.healthy ? 'yes' : 'no'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="admin-panel">
        <h2>Model calls</h2>
        <div className="admin-row">
          <select
            className="admin-select"
            value={operation}
            onChange={(e) => setOperation(e.target.value)}
          >
            <option value="gauntlet_triage">gauntlet_triage</option>
            <option value="">all operations</option>
          </select>
        </div>
        <p className="admin-muted">
          Prompt text is redacted. Expand a row for token and error metadata only.
        </p>
        <table className="admin-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Model</th>
              <th>Op</th>
              <th>Status</th>
              <th>Latency</th>
              <th>Cost</th>
            </tr>
          </thead>
          <tbody>
            {calls.map((call) => (
              <React.Fragment key={call.id}>
                <tr>
                  <td>
                    <button
                      type="button"
                      className="admin-linkish"
                      onClick={() => setExpanded(expanded === call.id ? null : call.id)}
                    >
                      {call.created_at}
                    </button>
                  </td>
                  <td>{call.model}</td>
                  <td>{call.operation}</td>
                  <td>{call.status}</td>
                  <td>{call.latency_ms ?? '—'} ms</td>
                  <td>{formatMicroUsd(call.estimated_cost_microusd)}</td>
                </tr>
                {expanded === call.id ? (
                  <tr>
                    <td colSpan={6}>
                      <pre className="admin-pre">
                        {JSON.stringify(
                          {
                            request_meta: call.request_meta,
                            response_meta: call.response_meta,
                          },
                          null,
                          2,
                        )}
                      </pre>
                    </td>
                  </tr>
                ) : null}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </section>

      <section className="admin-panel">
        <h2>Gauntlet jobs</h2>
        <p className="admin-muted">
          Counts: {Object.entries(counts).map(([k, v]) => `${k}=${v}`).join(' · ') || 'none'}
        </p>
        <table className="admin-table">
          <thead>
            <tr>
              <th>Kind</th>
              <th>Status</th>
              <th>Tries</th>
              <th>Turn</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>{job.kind}</td>
                <td>{job.status}</td>
                <td>{job.tries}</td>
                <td>{job.turn_id ? `${String(job.turn_id).slice(0, 8)}…` : '—'}</td>
                <td>{job.last_error || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}

export default function AdminApp() {
  const [authed, setAuthed] = useState(() => Boolean(getAdminKey()));
  const [tab, setTab] = useState('decks');

  useEffect(() => {
    const onUnauthorized = () => setAuthed(false);
    window.addEventListener('ddf:admin-unauthorized', onUnauthorized);
    return () => window.removeEventListener('ddf:admin-unauthorized', onUnauthorized);
  }, []);

  const body = useMemo(() => {
    if (tab === 'decks') return <DecksPanel />;
    if (tab === 'metrics') return <MetricsPanel />;
    if (tab === 'traces') return <TracesPanel />;
    return <TunePanel />;
  }, [tab]);

  if (!authed) {
    return <AuthGate onReady={() => setAuthed(true)} />;
  }

  return (
    <div className="admin-root">
      <header className="admin-header">
        <h1 className="admin-brand">Operator Admin</h1>
        <nav className="admin-nav" aria-label="Admin sections">
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-current={tab === item.id ? 'page' : undefined}
              onClick={() => setTab(item.id)}
            >
              {item.label}
            </button>
          ))}
          <button
            type="button"
            className="admin-btn secondary"
            onClick={() => {
              clearAdminKey();
              setAuthed(false);
            }}
          >
            Lock
          </button>
        </nav>
      </header>
      <PrivacyNotice />
      {body}
    </div>
  );
}
