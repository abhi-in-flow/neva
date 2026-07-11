/**
 * Gemma training-proof and inference controls for the protected admin surface.
 *
 * This component is intentionally a renderer and job launcher: FastAPI owns
 * readiness decisions, while the host supervisor owns all GPU execution. The
 * browser never receives model paths or commands. Held-out audio is fetched
 * with the admin header as a Blob, and microphone audio is temporary.
 */

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { AdminApiError, adminApi } from '../lib/adminApi.js';
import {
  ADMIN_TUNE_FALLBACK_LANGUAGES,
  ADMIN_TUNE_POLL_MS,
  ADMIN_TUNE_RECORDING_MAX_MS,
  ADMIN_TUNE_RECORDING_MIN_MS,
} from '../lib/constants.js';
import '../styles/tune-admin.css';

const TERMINAL_JOB_STATUSES = new Set(['completed', 'failed', 'cancelled']);
const TRAINING_STEPS = Object.freeze([
  { id: 'corpus', label: 'Corpus frozen' },
  { id: 'preflight', label: 'Preflight' },
  { id: 'model_load', label: 'Model load' },
  { id: 'optimizer_step', label: 'One optimizer step' },
  { id: 'adapter_save', label: 'Adapter save' },
]);

/**
 * Return the first value that is neither null nor undefined.
 *
 * @param {...unknown} values Candidate values in preference order.
 * @returns {unknown} First present value, or undefined.
 */
function firstPresent(...values) {
  return values.find((value) => value !== null && value !== undefined);
}

/**
 * Convert unknown text into a short, single-line, path-redacted UI value.
 *
 * @param {unknown} value Server-provided display value.
 * @param {number} limit Maximum output length.
 * @returns {string} Sanitized display text.
 */
function safeText(value, limit = 220) {
  if (typeof value !== 'string') return '';
  return value
    .replace(/[\u0000-\u001f\u007f]+/g, ' ')
    .replace(/(?:\/[\w.@+-]+){2,}/g, '[path redacted]')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, limit);
}

/**
 * Shorten a content hash without pretending it is an identifier.
 *
 * @param {unknown} value Hash-like server value.
 * @returns {string} Short hash or an em dash.
 */
function shortHash(value) {
  const text = safeText(value, 80);
  if (!text) return '—';
  return text.length > 12 ? `${text.slice(0, 12)}…` : text;
}

/**
 * Format a finite number while preserving a useful amount of precision.
 *
 * @param {unknown} value Numeric API value.
 * @param {number} digits Maximum fractional digits.
 * @returns {string} Human-readable number or an em dash.
 */
function formatNumber(value, digits = 2) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '—';
  return numeric.toLocaleString(undefined, { maximumFractionDigits: digits });
}

/**
 * Format elapsed seconds for a compact metric card.
 *
 * @param {unknown} value Elapsed seconds from the supervisor.
 * @returns {string} Duration or an em dash.
 */
function formatDuration(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return '—';
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

/**
 * Normalize a supervisor stage into the five-stage presentation contract.
 *
 * @param {unknown} value Raw job stage.
 * @returns {string} Canonical stage identifier.
 */
function normalizeStage(value) {
  const stage = String(value || '').toLowerCase().replace(/[\s-]+/g, '_');
  if (stage.includes('corpus') || stage.includes('manifest')) return 'corpus';
  if (stage.includes('preflight')) return 'preflight';
  if (stage.includes('model') || stage.includes('load')) return 'model_load';
  if (stage.includes('optim') || stage.includes('train') || stage.includes('step')) {
    return 'optimizer_step';
  }
  if (stage.includes('adapter') || stage.includes('save') || stage.includes('complete')) {
    return 'adapter_save';
  }
  return '';
}

/**
 * Resolve a stable job ID from overview or mutation payloads.
 *
 * @param {unknown} value Job object or identifier.
 * @returns {string} Job identifier, if available.
 */
function jobIdOf(value) {
  if (typeof value === 'string') return value;
  if (!value || typeof value !== 'object') return '';
  return String(value.job_id || value.sample_id || value.id || '');
}

/**
 * Resolve the current operation label from a job detail.
 *
 * @param {object|null} job Tune job.
 * @returns {string} Normalized operation name.
 */
function jobOperation(job) {
  return String(job?.operation || job?.kind || '').replaceAll('-', '_');
}

/**
 * Determine the visual status of one step without inventing backend progress.
 *
 * @param {object} step Step definition.
 * @param {object|null} job Current job detail.
 * @param {boolean} corpusReady Backend-declared corpus readiness.
 * @returns {'pending'|'active'|'complete'|'failed'} Presentation status.
 */
function trainingStepStatus(step, job, corpusReady) {
  if (step.id === 'corpus' && !job) return corpusReady ? 'complete' : 'pending';
  if (!job || jobOperation(job) !== 'train_smoke') {
    return step.id === 'corpus' && corpusReady ? 'complete' : 'pending';
  }
  const status = String(job.status || '').toLowerCase();
  if (status === 'completed') return 'complete';
  const events = Array.isArray(job.events) ? job.events : [];
  const stage = normalizeStage(firstPresent(
    job.stage,
    job.current_stage,
    events.at(-1)?.stage,
    events.at(-1)?.event,
  ));
  const activeIndex = Math.max(
    0,
    TRAINING_STEPS.findIndex((candidate) => candidate.id === stage),
  );
  const stepIndex = TRAINING_STEPS.findIndex((candidate) => candidate.id === step.id);
  if (stepIndex < activeIndex) return 'complete';
  if (stepIndex === activeIndex) return status === 'failed' ? 'failed' : 'active';
  return 'pending';
}

/**
 * Render one compact, accessible metric card.
 *
 * @param {{label: string, value: React.ReactNode, hint?: string}} props Card data.
 * @returns {React.ReactElement} Metric card.
 */
function MetricCard({ label, value, hint = '' }) {
  return (
    <div className="tune-metric">
      <span>{label}</span>
      <strong title={typeof value === 'string' ? value : undefined}>{value}</strong>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}

/**
 * Render target, base, and tuned text without numerical quality claims.
 *
 * @param {{comparison: object|null, emptyMessage: string, liveInput?: boolean}} props Comparison data.
 * @returns {React.ReactElement} Qualitative comparison.
 */
function Comparison({ comparison, emptyMessage, liveInput = false }) {
  if (!comparison) return <p className="admin-muted">{emptyMessage}</p>;
  const target = liveInput
    ? 'No ground truth is available for an unscripted live clip.'
    : firstPresent(
      comparison.target_text,
      comparison.target,
      comparison.expected_text,
    );
  const base = firstPresent(
    comparison.base_output,
    comparison.base_text,
    comparison.base,
  );
  const tuned = firstPresent(
    comparison.tuned_output,
    comparison.tuned_text,
    comparison.tuned,
  );
  return (
    <div className="tune-comparison">
      <article>
        <span>{liveInput ? 'Ground truth' : 'Target'}</span>
        <p>{safeText(target, 600) || 'Not published'}</p>
      </article>
      <article>
        <span>Base Gemma</span>
        <p>{safeText(base, 600) || 'Unavailable'}</p>
      </article>
      <article className="tuned">
        <span>Verified adapter</span>
        <p>{safeText(tuned, 600) || 'Unavailable'}</p>
      </article>
    </div>
  );
}

/**
 * Render the five-stage smoke-training sequence.
 *
 * @param {{job: object|null, corpusReady: boolean}} props Current proof state.
 * @returns {React.ReactElement} Ordered progress list.
 */
function TrainingStepper({ job, corpusReady }) {
  return (
    <ol className="tune-stepper" aria-label="Live training proof progress">
      {TRAINING_STEPS.map((step) => {
        const status = trainingStepStatus(step, job, corpusReady);
        return (
          <li className={status} key={step.id}>
            <span aria-hidden="true">
              {status === 'complete' ? '✓' : status === 'failed' ? '!' : ''}
            </span>
            <div>
              <strong>{step.label}</strong>
              <small>{status}</small>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Render only bounded, sanitized event fields supplied by the tune API.
 *
 * @param {{job: object|null}} props Current job detail.
 * @returns {React.ReactElement} Safe event list.
 */
function EventList({ job }) {
  const events = Array.isArray(job?.events) ? job.events.slice(-12) : [];
  return (
    <section className="tune-events" aria-live="polite">
      <h3>Sanitized supervisor events</h3>
      {events.length === 0 ? (
        <p className="admin-muted">No live events yet.</p>
      ) : (
        <ol>
          {events.map((event, index) => {
            const stage = safeText(event.stage || event.event || 'update', 40);
            const message = safeText(event.message || event.detail || '', 220);
            const timestamp = safeText(event.timestamp || event.created_at || '', 40);
            return (
              <li key={`${timestamp}-${stage}-${index}`}>
                <div>
                  <strong>{stage || 'update'}</strong>
                  {timestamp ? <time>{timestamp}</time> : null}
                </div>
                {message ? <p>{message}</p> : null}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

/**
 * Resolve MediaRecorder's safest supported audio format.
 *
 * @returns {string} Supported MIME type, or an empty string for browser default.
 */
function supportedRecordingMime() {
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'];
  return candidates.find((mime) => MediaRecorder.isTypeSupported?.(mime)) || '';
}

/**
 * Return a compact file extension for a recorded MIME type.
 *
 * @param {string} mime MIME type.
 * @returns {string} Filename extension.
 */
function recordingExtension(mime) {
  return mime.includes('mp4') ? 'm4a' : 'webm';
}

/**
 * Main Tune tab. Polls safe admin state and launches only fixed backend jobs.
 *
 * @returns {React.ReactElement} Training and inference operator interface.
 */
export default function TunePanel() {
  const [view, setView] = useState('training');
  const [overview, setOverview] = useState(null);
  const [job, setJob] = useState(null);
  const [trackedJobId, setTrackedJobId] = useState('');
  const [overviewError, setOverviewError] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [startingProof, setStartingProof] = useState(false);
  const [selectedSampleId, setSelectedSampleId] = useState('');
  const [sampleAudioUrl, setSampleAudioUrl] = useState('');
  const [sampleAudioError, setSampleAudioError] = useState('');
  const [nativeLanguage, setNativeLanguage] = useState('');
  const [recording, setRecording] = useState(false);
  const [recordingBlob, setRecordingBlob] = useState(null);
  const [recordingUrl, setRecordingUrl] = useState('');
  const [recordingError, setRecordingError] = useState('');
  const [submittingInference, setSubmittingInference] = useState(false);

  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);
  const recordingStartedRef = useRef(0);
  const stopTimerRef = useRef(null);
  const recordingUrlRef = useRef('');
  const mountedRef = useRef(true);

  const refreshOverview = useCallback(async () => {
    try {
      const data = await adminApi.tuneOverview();
      setOverview(data);
      setOverviewError('');
      const current = data?.current_job || data?.active_job || data?.current_job_id;
      const currentId = jobIdOf(current);
      if (currentId) setTrackedJobId(currentId);
    } catch (error) {
      const message = error instanceof AdminApiError && error.status === 503
        ? 'Tune supervisor overview is unavailable.'
        : safeText(error?.detail || error?.message) || 'Tune overview failed to load.';
      setOverviewError(message);
    }
  }, []);

  const refreshJob = useCallback(async (jobId) => {
    if (!jobId) return;
    try {
      const detail = await adminApi.tuneJob(jobId);
      setJob(detail);
    } catch (error) {
      if (error instanceof AdminApiError && error.status === 404) {
        setTrackedJobId('');
      }
    }
  }, []);

  useEffect(() => {
    refreshOverview();
    const timer = window.setInterval(refreshOverview, ADMIN_TUNE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [refreshOverview]);

  useEffect(() => {
    if (!trackedJobId) return undefined;
    const trackedStatus = jobIdOf(job) === trackedJobId
      ? String(job?.status || '').toLowerCase()
      : '';
    if (TERMINAL_JOB_STATUSES.has(trackedStatus)) return undefined;
    refreshJob(trackedJobId);
    const timer = window.setInterval(
      () => refreshJob(trackedJobId),
      ADMIN_TUNE_POLL_MS,
    );
    return () => window.clearInterval(timer);
  }, [job?.job_id, job?.status, refreshJob, trackedJobId]);

  const corpus = overview?.corpus || {};
  const supervisor = overview?.supervisor || {};
  const verifiedAdapter = overview?.full_artifact || {};
  const supervisorAvailable = supervisor.healthy === true;
  const corpusReady = corpus.ready === true;
  const verifiedReady = overview?.full_adapter_ready === true;
  const readinessReason = safeText(overview?.readiness_reason, 240)
    || 'Inference readiness was not approved by the backend.';
  const rawSamples = overview?.heldout_samples || [];
  const rawComparisons = overview?.heldout_comparisons || [];
  const heldoutSamples = useMemo(
    () => (Array.isArray(rawSamples)
      ? rawSamples
        .map((sample) => {
          const exactMatches = Array.isArray(rawComparisons)
            ? rawComparisons.filter(
              (candidate) => candidate.sample_id === sample.sample_id,
            )
            : [];
          return exactMatches.length === 1
            ? { ...sample, ...exactMatches[0] }
            : null;
        })
        .filter(Boolean)
      : []),
    [rawComparisons, rawSamples],
  );

  useEffect(() => {
    setSelectedSampleId((current) => {
      const stillPublished = heldoutSamples.some(
        (sample) => jobIdOf(sample) === current,
      );
      if (stillPublished) return current;
      return heldoutSamples.length > 0 ? jobIdOf(heldoutSamples[0]) : '';
    });
  }, [heldoutSamples]);

  const selectedSample = useMemo(
    () => heldoutSamples.find((sample) => jobIdOf(sample) === selectedSampleId) || null,
    [heldoutSamples, selectedSampleId],
  );
  const selectedAudioAvailable = selectedSample?.audio_available;

  useEffect(() => {
    let cancelled = false;
    setSampleAudioError('');
    setSampleAudioUrl('');
    if (!verifiedReady || !selectedSampleId || selectedAudioAvailable === false) {
      return undefined;
    }
    let objectUrl = '';
    adminApi.tuneSampleAudio(selectedSampleId)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSampleAudioUrl(objectUrl);
      })
      .catch((error) => {
        if (!cancelled) {
          setSampleAudioError(
            safeText(error?.detail || error?.message) || 'Audio is unavailable.',
          );
        }
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [selectedAudioAvailable, selectedSampleId, verifiedReady]);

  const languageOptions = useMemo(() => {
    const corpusLanguageTags = corpus.language_counts
      ? Object.keys(corpus.language_counts)
      : [];
    const source = corpusLanguageTags.length > 0
      ? corpusLanguageTags
      : ADMIN_TUNE_FALLBACK_LANGUAGES;
    return source.map((language) => (
      typeof language === 'string'
        ? { value: language, label: language }
        : {
          value: language.value || language.tag || language.code,
          label: language.label || language.name || language.value,
        }
    )).filter((language) => language.value);
  }, [corpus.language_counts]);

  useEffect(() => {
    if (!nativeLanguage && languageOptions.length > 0) {
      setNativeLanguage(languageOptions[0].value);
    }
  }, [languageOptions, nativeLanguage]);

  const clearRecording = useCallback(() => {
    setRecordingBlob(null);
    if (recordingUrlRef.current) {
      URL.revokeObjectURL(recordingUrlRef.current);
      recordingUrlRef.current = '';
    }
    setRecordingUrl('');
  }, []);

  const releaseRecordingResources = useCallback(() => {
    if (stopTimerRef.current) {
      window.clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const stopRecording = useCallback(() => {
    const recorder = recorderRef.current;
    if (recorder?.state === 'recording') recorder.stop();
  }, []);

  useEffect(() => {
    if (view !== 'inference' && recording) stopRecording();
  }, [recording, stopRecording, view]);

  const startRecording = useCallback(async () => {
    setRecordingError('');
    clearRecording();
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setRecordingError('This browser does not support microphone recording.');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      chunksRef.current = [];
      const mimeType = supportedRecordingMime();
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data?.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        const elapsed = Date.now() - recordingStartedRef.current;
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || mimeType || 'application/octet-stream',
        });
        setRecording(false);
        releaseRecordingResources();
        recorderRef.current = null;
        if (elapsed < ADMIN_TUNE_RECORDING_MIN_MS || blob.size === 0) {
          setRecordingError('Record for at least 1 second.');
          return;
        }
        setRecordingBlob(blob);
        const objectUrl = URL.createObjectURL(blob);
        recordingUrlRef.current = objectUrl;
        setRecordingUrl(objectUrl);
      };
      recordingStartedRef.current = Date.now();
      recorder.start();
      setRecording(true);
      stopTimerRef.current = window.setTimeout(
        stopRecording,
        ADMIN_TUNE_RECORDING_MAX_MS,
      );
    } catch (error) {
      releaseRecordingResources();
      setRecording(false);
      setRecordingError(
        error?.name === 'NotAllowedError'
          ? 'Microphone permission was not granted.'
          : 'Could not start microphone recording.',
      );
    }
  }, [clearRecording, releaseRecordingResources, stopRecording]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const recorder = recorderRef.current;
      if (recorder?.state === 'recording') {
        recorder.ondataavailable = null;
        recorder.onstop = null;
        recorder.stop();
      }
      releaseRecordingResources();
      if (recordingUrlRef.current) {
        URL.revokeObjectURL(recordingUrlRef.current);
        recordingUrlRef.current = '';
      }
    };
  }, [releaseRecordingResources]);

  const startProof = useCallback(async () => {
    setStartingProof(true);
    setActionMessage('');
    try {
      const created = await adminApi.startTuneSmoke();
      const createdId = jobIdOf(created);
      setJob(created);
      setTrackedJobId(createdId);
      setActionMessage('Live one-step training proof queued.');
    } catch (error) {
      if (error instanceof AdminApiError && error.status === 409) {
        setActionMessage('GPU is busy with another tune job. Showing its current state.');
        await refreshOverview();
      } else if (error instanceof AdminApiError && error.status === 503) {
        setActionMessage('Supervisor unavailable. The verified adapter remains the inference path.');
      } else {
        setActionMessage(
          safeText(error?.detail || error?.message) || 'Could not start training proof.',
        );
      }
    } finally {
      setStartingProof(false);
    }
  }, [refreshOverview]);

  const submitInference = useCallback(async () => {
    if (!recordingBlob || !nativeLanguage) return;
    setSubmittingInference(true);
    setActionMessage('');
    try {
      const form = new FormData();
      form.append(
        'audio',
        recordingBlob,
        `temporary-demo.${recordingExtension(recordingBlob.type)}`,
      );
      form.append('native_language', nativeLanguage);
      const created = await adminApi.inferTuneLive(form);
      const createdId = jobIdOf(created);
      setJob(created);
      setTrackedJobId(createdId);
      setActionMessage('Live inference queued. This temporary clip will not enter the corpus.');
    } catch (error) {
      if (error instanceof AdminApiError && error.status === 409) {
        setActionMessage('GPU is busy. Wait for the current job, then retry.');
        await refreshOverview();
      } else if (error instanceof AdminApiError && error.status === 503) {
        setActionMessage('Verified full-adapter inference is unavailable.');
      } else {
        setActionMessage(
          safeText(error?.detail || error?.message) || 'Live inference could not start.',
        );
      }
    } finally {
      setSubmittingInference(false);
    }
  }, [nativeLanguage, recordingBlob, refreshOverview]);

  const liveProofJob = jobOperation(job) === 'train_smoke'
    ? job
    : null;
  const smokeArtifact = liveProofJob
    ? liveProofJob?.result?.training_proof || null
    : overview?.smoke_artifact || null;
  const proofJob = liveProofJob || (smokeArtifact?.available ? {
    kind: 'train_smoke',
    status: 'completed',
    stage: 'adapter_save',
    result: { training_proof: smokeArtifact },
    events: [],
  } : null);
  const latestProofEvent = Array.isArray(proofJob?.events)
    ? proofJob.events.at(-1) || {}
    : {};
  const proofMetrics = {
    ...(smokeArtifact || {}),
    ...latestProofEvent,
    ...(proofJob?.result?.metrics || {}),
    ...(proofJob?.metrics || {}),
  };
  const manifestModel = firstPresent(
    corpus.model_id,
    verifiedAdapter.model_id,
  );
  const sampleCount = firstPresent(
    corpus.sample_counts?.total,
  );
  const languageCount = firstPresent(
    corpus.language_counts ? Object.keys(corpus.language_counts).length : undefined,
  );
  const currentStatus = String(job?.status || '').toLowerCase();
  const trackedJobStatus = jobIdOf(job) === trackedJobId ? currentStatus : '';
  const jobBusy = Boolean(
    trackedJobId && !TERMINAL_JOB_STATUSES.has(trackedJobStatus),
  );
  const trainingFailed = String(liveProofJob?.status || '').toLowerCase() === 'failed';
  const inferenceResult = jobOperation(job) === 'infer_live' && currentStatus === 'completed'
    ? job.result || job
    : null;

  return (
    <div className="tune-admin">
      <section className="admin-panel tune-hero">
        <div>
          <p className="tune-eyebrow">Gemma · local operator demo</p>
          <h2>Training and inference</h2>
          <p className="admin-muted">
            Live GPU work is supervised outside FastAPI. Readiness and artifact
            compatibility shown here come from the backend.
          </p>
        </div>
        <div className="tune-supervisor" aria-live="polite">
          <span className={`admin-badge ${supervisorAvailable ? 'healthy' : 'unhealthy'}`}>
            {supervisorAvailable ? 'supervisor available' : 'supervisor unavailable'}
          </span>
          {jobBusy ? <span className="admin-badge generating">GPU job active</span> : null}
        </div>
      </section>

      {overviewError ? <div className="admin-error" role="alert">{overviewError}</div> : null}
      {actionMessage ? <div className="tune-notice" aria-live="polite">{actionMessage}</div> : null}

      <div className="tune-view-tabs" role="tablist" aria-label="Tune views">
        <button
          id="tune-training-tab"
          type="button"
          role="tab"
          aria-selected={view === 'training'}
          aria-controls="tune-training-panel"
          onClick={() => setView('training')}
        >
          Training proof
        </button>
        <button
          id="tune-inference-tab"
          type="button"
          role="tab"
          aria-selected={view === 'inference'}
          aria-controls="tune-inference-panel"
          onClick={() => setView('inference')}
        >
          Inference
        </button>
      </div>

      {view === 'training' ? (
        <div
          id="tune-training-panel"
          role="tabpanel"
          aria-labelledby="tune-training-tab"
        >
          <div className="tune-proof-grid">
            <section className="admin-panel tune-proof live-proof">
              <p className="tune-eyebrow">Disposable smoke artifact</p>
              <h2>Live training proof — not used for quality</h2>
              <p>
                Runs preflight and exactly one optimizer step to prove the local
                QLoRA pipeline. Its adapter is never selected for comparisons.
              </p>
              <button
                className="admin-btn"
                type="button"
                disabled={!supervisorAvailable || startingProof || jobBusy || !corpusReady}
                onClick={startProof}
              >
                {startingProof ? 'Starting…' : 'Run one-step training proof'}
              </button>
              {!corpusReady ? (
                <p className="tune-unavailable">Corpus manifest is not ready.</p>
              ) : null}
            </section>

            <section className={`admin-panel tune-proof verified-proof ${verifiedReady ? 'ready' : ''}`}>
              <p className="tune-eyebrow">Approved artifact</p>
              <h2>Verified full adapter — used for inference</h2>
              {verifiedReady ? (
                <>
                  <p>
                    Compatible full-profile manifest published. Held-out and live
                    inference use this adapter, never the smoke artifact.
                  </p>
                  <span className="admin-badge healthy">verified and ready</span>
                </>
              ) : (
                <>
                  <p className="tune-unavailable">
                    <strong>Backend inference gate closed.</strong>
                    {' '}
                    {readinessReason}
                  </p>
                  <span className="admin-badge unhealthy">inference unavailable</span>
                </>
              )}
            </section>
          </div>

          <section className="admin-panel">
            <p className="tune-eyebrow">Technical publication · not a readiness override</p>
            <h2>Published full-artifact metrics</h2>
            <p className="admin-muted">
              These facts remain visible for diagnosis. A compatible artifact
              does not enable inference unless the backend explicitly publishes
              <code> full_adapter_ready=true</code>.
            </p>
            <div className="tune-metrics">
              <MetricCard
                label="Artifact model"
                value={safeText(verifiedAdapter.model_id, 100) || '—'}
              />
              <MetricCard
                label="Profile / status"
                value={[
                  safeText(verifiedAdapter.profile, 24),
                  safeText(verifiedAdapter.status, 40),
                ].filter(Boolean).join(' · ') || '—'}
              />
              <MetricCard
                label="Artifact samples"
                value={formatNumber(verifiedAdapter.sample_counts?.total, 0)}
              />
              <MetricCard
                label="Artifact languages"
                value={formatNumber(
                  verifiedAdapter.language_counts
                    ? Object.keys(verifiedAdapter.language_counts).length
                    : undefined,
                  0,
                )}
              />
              <MetricCard
                label="Artifact LoRA rank"
                value={formatNumber(verifiedAdapter.lora_rank, 0)}
              />
              <MetricCard
                label="Artifact step / loss"
                value={`${formatNumber(
                  verifiedAdapter.completed_steps,
                  0,
                )} / ${formatNumber(verifiedAdapter.final_loss, 4)}`}
              />
              <MetricCard
                label="Artifact elapsed"
                value={formatDuration(verifiedAdapter.duration_seconds)}
              />
              <MetricCard
                label="Artifact peak VRAM"
                value={verifiedAdapter.peak_vram_gib != null
                  ? `${formatNumber(verifiedAdapter.peak_vram_gib)} GiB`
                  : '—'}
              />
              <MetricCard
                label="Artifact corpus hash"
                value={shortHash(verifiedAdapter.source_corpus_sha256)}
              />
              <MetricCard
                label="Adapter hash"
                value={shortHash(verifiedAdapter.adapter_sha256)}
              />
            </div>
          </section>

          {trainingFailed ? (
            <div className="tune-fallback" role="status">
              <strong>Live proof failed.</strong>
              {' '}
              {verifiedReady
                ? 'Inference remains on the verified full adapter; the failed smoke artifact is ignored.'
                : 'There is no verified adapter fallback, so tuned inference remains unavailable.'}
            </div>
          ) : null}

          <section className="admin-panel">
            <h2>Live proof progress</h2>
            <TrainingStepper job={proofJob} corpusReady={corpusReady} />
            <div className="tune-metrics">
              <MetricCard
                label="Corpus samples"
                value={formatNumber(sampleCount, 0)}
                hint={corpus.sample_counts
                  ? `${formatNumber(corpus.sample_counts.train, 0)} train · ${formatNumber(
                    corpus.sample_counts.holdout,
                    0,
                  )} held out`
                  : ''}
              />
              <MetricCard label="Languages" value={formatNumber(languageCount, 0)} />
              <MetricCard
                label="Exact manifest model"
                value={safeText(manifestModel, 100) || '—'}
                hint="Not a frontend model alias"
              />
              <MetricCard
                label="LoRA rank"
                value={formatNumber(firstPresent(
                  proofMetrics.lora_rank,
                  proofJob?.lora_rank,
                  verifiedAdapter.lora_rank,
                ), 0)}
              />
              <MetricCard
                label="Step / loss"
                value={`${formatNumber(firstPresent(
                  proofMetrics.step,
                  proofMetrics.global_step,
                  proofMetrics.completed_steps,
                ), 0)} / ${formatNumber(firstPresent(
                  proofMetrics.loss,
                  proofMetrics.final_loss,
                ), 4)}`}
              />
              <MetricCard
                label="Elapsed"
                value={formatDuration(firstPresent(
                  proofMetrics.elapsed_seconds,
                  proofMetrics.elapsed_s,
                  proofMetrics.duration_seconds,
                ))}
              />
              <MetricCard
                label="Peak VRAM"
                value={firstPresent(
                  proofMetrics.peak_vram_gib,
                  proofMetrics.peak_vram_gb,
                  proofMetrics.peak_gpu_memory_gb,
                ) != null
                  ? `${formatNumber(firstPresent(
                    proofMetrics.peak_vram_gib,
                    proofMetrics.peak_vram_gb,
                    proofMetrics.peak_gpu_memory_gb,
                  ))} GB`
                  : '—'}
              />
              <MetricCard
                label="Corpus hash"
                value={shortHash(firstPresent(
                  corpus.source_corpus_sha256,
                  corpus.dataset_manifest_sha256,
                  corpus.hash,
                  corpus.manifest_hash,
                  corpus.corpus_hash,
                ))}
              />
              <MetricCard
                label="Verified adapter hash"
                value={shortHash(firstPresent(
                  verifiedAdapter.adapter_sha256,
                  verifiedAdapter.hash,
                  verifiedAdapter.adapter_hash,
                  verifiedAdapter.manifest_hash,
                ))}
              />
            </div>
          </section>
          <EventList job={proofJob} />
        </div>
      ) : (
        <div
          id="tune-inference-panel"
          role="tabpanel"
          aria-labelledby="tune-inference-tab"
        >
          <section className="admin-panel">
            <p className="tune-eyebrow">Precomputed · approved held-out sample</p>
            <h2>Held-out comparison</h2>
            {!verifiedReady ? (
              <div className="tune-readiness" role="status">
                <strong>Held-out inference disabled.</strong>
                {' '}
                {readinessReason}
              </div>
            ) : null}
            {heldoutSamples.length > 0 ? (
              <>
                <label className="admin-label" htmlFor="heldout-sample">
                  Approved held-out sample
                </label>
                <select
                  id="heldout-sample"
                  className="admin-select"
                  value={selectedSampleId}
                  onChange={(event) => setSelectedSampleId(event.target.value)}
                  disabled={!verifiedReady}
                >
                  {heldoutSamples.map((sample, index) => {
                    const id = jobIdOf(sample);
                    const label = safeText(
                      sample.label || sample.title || sample.native_language,
                      100,
                    );
                    return <option key={id} value={id}>{label || `Sample ${index + 1}`}</option>;
                  })}
                </select>
                <div className="tune-audio">
                  <span>Protected source audio</span>
                  {!verifiedReady ? (
                    <p className="admin-muted">Playback disabled by the inference gate.</p>
                  ) : sampleAudioUrl ? (
                    <audio controls preload="metadata" src={sampleAudioUrl}>
                      <track kind="captions" />
                    </audio>
                  ) : (
                    <p className="admin-muted">
                      {sampleAudioError || 'Loading authenticated audio…'}
                    </p>
                  )}
                </div>
                <Comparison
                  comparison={verifiedReady ? selectedSample : null}
                  emptyMessage={`Held-out inference unavailable: ${readinessReason}`}
                />
              </>
            ) : (
              <p className="tune-unavailable">
                <strong>No approved qualitative comparison is published.</strong>
                {!verifiedReady ? ` ${readinessReason}` : ''}
              </p>
            )}
          </section>

          <section className="admin-panel">
            <p className="tune-eyebrow">Live · optional microphone inference</p>
            <h2>Try a temporary clip</h2>
            <p className="tune-privacy">
              Privacy: microphone audio is temporary, deleted by the demo
              workflow, and never enters training data.
            </p>
            {!verifiedReady ? (
              <p className="tune-unavailable">
                <strong>Live inference disabled.</strong>
                {' '}
                {readinessReason}
              </p>
            ) : null}
            {!supervisorAvailable ? (
              <p className="tune-unavailable">
                The local supervisor is unavailable. Precomputed comparisons
                remain viewable, but microphone inference cannot run.
              </p>
            ) : null}
            <div className="tune-mic-controls">
              <div>
                <label className="admin-label" htmlFor="live-native-language">
                  Spoken language
                </label>
                <select
                  id="live-native-language"
                  className="admin-select"
                  value={nativeLanguage}
                  onChange={(event) => setNativeLanguage(event.target.value)}
                  disabled={
                    !verifiedReady
                    || !supervisorAvailable
                    || recording
                    || submittingInference
                  }
                >
                  {languageOptions.map((language) => (
                    <option key={language.value} value={language.value}>
                      {language.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="tune-record-actions">
                {!recording ? (
                  <button
                    className="admin-btn secondary"
                    type="button"
                    disabled={
                      !verifiedReady
                      || !supervisorAvailable
                      || jobBusy
                      || submittingInference
                    }
                    onClick={startRecording}
                  >
                    Record 1–8 seconds
                  </button>
                ) : (
                  <button
                    className="admin-btn tune-stop"
                    type="button"
                    onClick={stopRecording}
                  >
                    Stop recording
                  </button>
                )}
                <span aria-live="polite">{recording ? 'Recording… auto-stops at 8 seconds' : ''}</span>
              </div>
            </div>
            {recordingError ? <div className="admin-error" role="alert">{recordingError}</div> : null}
            {recordingUrl ? (
              <div className="tune-audio">
                <span>Temporary preview</span>
                <audio controls src={recordingUrl}>
                  <track kind="captions" />
                </audio>
              </div>
            ) : null}
            <button
              className="admin-btn"
              type="button"
              disabled={
                !verifiedReady
                || !supervisorAvailable
                || !recordingBlob
                || !nativeLanguage
                || recording
                || submittingInference
                || jobBusy
              }
              onClick={submitInference}
            >
              {submittingInference ? 'Submitting…' : 'Run live base vs tuned'}
            </button>

            <div className="tune-live-result" aria-live="polite">
              <p className="tune-eyebrow">Live result · actual model output</p>
              {jobOperation(job) === 'infer_live' && currentStatus !== 'completed' ? (
                <p className="admin-muted">
                  Job {safeText(job?.status || 'queued', 30)}
                  {job?.stage ? ` · ${safeText(job.stage, 50)}` : ''}
                </p>
              ) : null}
              <Comparison
                comparison={inferenceResult}
                emptyMessage="Record and submit a clip to show actual base and verified-adapter output."
                liveInput
              />
            </div>
          </section>
          {jobOperation(job) === 'infer_live' ? <EventList job={job} /> : null}
        </div>
      )}
    </div>
  );
}
