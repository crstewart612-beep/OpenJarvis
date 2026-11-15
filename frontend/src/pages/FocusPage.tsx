import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { apiFetch } from '../lib/api';
import './focus.css';

interface FocusTask {
  id: string;
  title: string;
  due: string | null;
  steps: string[];
  step: number;
  done: boolean;
  overdue: boolean;
}
interface FocusCapture { id: string; text: string; at: string; kind?: 'note' | 'idea' }
interface FocusSignal { id: string; symbol: string; action: 'long' | 'short' | 'exit' | 'wait'; say: string; at: string }
interface FocusJob { id: string; title: string; company: string; url: string; salary: string; status: 'found' | 'drafted' | 'submitted' | 'skipped'; draft: string }
interface GoalEntry { id: string; amount: number; note: string; at: string }
interface FocusGoal { name: string; target: number; saved: number; entries: GoalEntry[] }
interface FocusState {
  tasks: FocusTask[];
  captures: FocusCapture[];
  signals: FocusSignal[];
  jobs: FocusJob[];
  goal: FocusGoal;
  job_profile: { titles: string[]; min_salary: number; has_resume: boolean };
  current: FocusTask | null;
  mode: 'calm' | 'deadline';
  risk_per_trade: number;
}
interface SpanishCard { item_id: string; unit: string; es: string; en: string; kind: 'intro' | 'choose' | 'type'; choices?: string[] }
interface SpanishProgress {
  words_mastered: number; words_total: number; minutes_today: number; goal_minutes: number; day_streak: number;
  unit: string; unit_number: number; unit_count: number; unit_mastered?: number; unit_total?: number;
}
interface SpanishLesson { card: SpanishCard | null; progress: SpanishProgress }
interface SizingRow { symbol: string; point_value: number; max_stop_points: number }

// Shown in the top bar so a screenshot tells which version is running.
const BUILD = 'build 17';
const SPRINT_SECONDS = 15 * 60;
const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const CALM_LINE = "Take a breath. One thing at a time. I'm right here with you.";

const money = (n: number) =>
  `${n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 })}`;
const fmtTimer = (s: number) =>
  `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
const localDate = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
/** "3:00 PM" for today, "Nov 16, 3:00 PM" for another day, "Nov 16" with no time. */
const fmtDue = (iso: string) => {
  const d = new Date(iso);
  const today = localDate(d) === localDate(new Date());
  const day = d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  // 11:59 PM is how "that day, no particular time" is stored.
  if (d.getHours() === 23 && d.getMinutes() === 59) return today ? 'today' : day;
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  return today ? time : `${day}, ${time}`;
};

async function send(path: string, method: string, body?: unknown, signal?: AbortSignal): Promise<Response> {
  return apiFetch(path, {
    method,
    signal,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
}

// Safari only lets a page make sound in direct response to a click, unless the
// sound goes through an audio context that a click has already switched on.
// So one shared context is switched on by the first click anywhere on the page
// and every spoken line is played through it.
let out: AudioContext | null = null;
let playing: AudioBufferSourceNode | null = null;

function unlockAudio(): void {
  try {
    if (!out) {
      const Ctx: typeof AudioContext = window.AudioContext
        ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      out = new Ctx();
    }
    if (out.state === 'suspended') void out.resume();
  } catch {
    out = null;
  }
}

function audioLocked(): boolean {
  return !out || out.state !== 'running';
}

function stopSpeaking(): void {
  try { playing?.stop(); } catch { /* already stopped */ }
  playing = null;
}

// Lines Jarvis says often ("Correct.", "What does that mean?") are kept after
// the first time so they play at once instead of being generated again.
const spoken = new Map<string, AudioBuffer>();
// Why the last line could not be spoken, for showing on screen.
let speakProblem = '';

/** Speak text in one of the voices. Resolves when playback has finished. */
async function speak(text: string, mode: 'calm' | 'deadline' | 'es', signal?: AbortSignal): Promise<void> {
  try {
    if (!out || !text.trim()) return;
    const key = `${mode}:${text}`;
    let buffer = spoken.get(key);
    if (!buffer) {
      const res = await send('/v1/focus/speak', 'POST', { text, mode }, signal);
      if (!res.ok) { speakProblem = `The voice engine answered with error ${res.status}`; return; }
      speakProblem = '';
      buffer = await out.decodeAudioData(await res.arrayBuffer());
      if (spoken.size > 150) spoken.clear();
      spoken.set(key, buffer);
    }
    if (signal?.aborted) return;
    // Never wait forever on sound: a browser that has not been clicked yet
    // can leave resume() pending, which would freeze whatever is speaking.
    if (out.state !== 'running') await Promise.race([out.resume(), sleep(1200)]);
    if (out.state !== 'running') return;
    stopSpeaking();
    const node = out.createBufferSource();
    node.buffer = buffer;
    node.connect(out.destination);
    playing = node;
    await new Promise<void>((resolve) => {
      const done = () => { if (playing === node) playing = null; resolve(); };
      node.onended = done;
      // Safety net in case the browser never reports the end of playback.
      setTimeout(done, buffer.duration * 1000 + 1500);
      signal?.addEventListener('abort', () => { try { node.stop(); } catch { done(); } }, { once: true });
      node.start();
    });
  } catch {
    // Voice is optional: the screen still shows the same words.
  }
}

/** A short tone: instant feedback that Jarvis heard its name or is listening. */
function chime(freq = 880): void {
  try {
    if (!out || out.state !== 'running') return;
    const osc = out.createOscillator();
    const gain = out.createGain();
    osc.frequency.value = freq;
    gain.gain.value = 0.06;
    osc.connect(gain).connect(out.destination);
    osc.start();
    osc.stop(out.currentTime + 0.11);
  } catch {
    // No tone is fine.
  }
}

interface Mic { stream: MediaStream; ctx: AudioContext; analyser: AnalyserNode }
let mic: Mic | null = null;

async function openMic(): Promise<Mic> {
  if (mic) return mic;
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
    throw new Error('This browser is not giving this page a microphone.');
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true },
  });
  const Ctx: typeof AudioContext = window.AudioContext
    ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const ctx = new Ctx();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  ctx.createMediaStreamSource(stream).connect(analyser);
  mic = { stream, ctx, analyser };
  return mic;
}

function closeMic(): void {
  mic?.stream.getTracks().forEach((t) => t.stop());
  void mic?.ctx.close().catch(() => {});
  mic = null;
}

interface Recording { blob: Blob; ext: string }

/**
 * Record one spoken phrase. Starts recording at once so the first syllable is
 * kept, and stops after a pause. Returns null when nobody spoke.
 */
async function recordUtterance(opts: {
  waitMs: number; silenceMs: number; maxMs: number; cancelled?: () => boolean;
}): Promise<Recording | null> {
  const m = await openMic();
  if (m.ctx.state === 'suspended') await m.ctx.resume();
  const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find((t) => MediaRecorder.isTypeSupported(t));
  const rec = new MediaRecorder(m.stream, mime ? { mimeType: mime } : undefined);
  const chunks: Blob[] = [];
  rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  const stopped = new Promise<void>((resolve) => { rec.onstop = () => resolve(); });
  rec.start();
  const buf = new Float32Array(m.analyser.fftSize);
  const t0 = performance.now();
  let loudTicks = 0;
  let spoke = false;
  let lastLoud = t0;
  await new Promise<void>((resolve) => {
    const tick = () => {
      m.analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) sum += v * v;
      const now = performance.now();
      if (Math.sqrt(sum / buf.length) > 0.02) {
        loudTicks += 1;
        lastLoud = now;
        if (loudTicks >= 3) spoke = true;
      } else if (!spoke) {
        loudTicks = 0;
      }
      const stop = opts.cancelled?.()
        || (!spoke && now - t0 > opts.waitMs)
        || (spoke && now - lastLoud > opts.silenceMs)
        || now - t0 > opts.maxMs;
      if (stop) resolve(); else setTimeout(tick, 50);
    };
    tick();
  });
  if (rec.state !== 'inactive') rec.stop();
  await stopped;
  if (!spoke || opts.cancelled?.()) return null;
  const type = rec.mimeType || mime || 'audio/webm';
  return { blob: new Blob(chunks, { type }), ext: type.includes('mp4') ? 'mp4' : 'webm' };
}

function audioForm(rec: Recording, fields: Record<string, string> = {}): FormData {
  const form = new FormData();
  form.append('file', rec.blob, `speech.${rec.ext}`);
  for (const [k, v] of Object.entries(fields)) form.append(k, v);
  return form;
}

async function errorDetail(res: Response | null, fallback: string): Promise<string> {
  if (!res) return 'The Jarvis server did not answer.';
  const body = await res.json().catch(() => ({}));
  return typeof body.detail === 'string' ? body.detail : fallback;
}

interface TalkTurn { role: 'you' | 'tutor'; text: string; meaning?: string; correction?: string }

export function FocusPage() {
  const [state, setState] = useState<FocusState | null>(null);
  const [sizing, setSizing] = useState<SizingRow[]>([]);
  const [offline, setOffline] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const [left, setLeft] = useState(SPRINT_SECONDS);
  const [running, setRunning] = useState(false);
  const [say, setSay] = useState(CALM_LINE);
  const [title, setTitle] = useState('');
  const [due, setDue] = useState('');
  const [dueDate, setDueDate] = useState(() => localDate(new Date()));
  const [capture, setCapture] = useState('');
  const [adding, setAdding] = useState(false);
  const [lesson, setLesson] = useState<SpanishLesson | null>(null);
  const [typed, setTyped] = useState('');
  const [feedback, setFeedback] = useState<{ ok: boolean; text: string } | null>(null);
  const [talking, setTalking] = useState(false);
  const [talkStatus, setTalkStatus] = useState('');
  const [turns, setTurns] = useState<TalkTurn[]>([]);
  const [micBusy, setMicBusy] = useState(false);
  const talkRef = useRef(false);
  const talkAbort = useRef<AbortController | null>(null);
  const [goalAmount, setGoalAmount] = useState('');
  const [goalNote, setGoalNote] = useState('');
  const [autoListen, setAutoListen] = useState(() => {
    try { return localStorage.getItem('focus-auto-listen') === '1'; } catch { return false; }
  });
  const autoStarted = useRef(false);
  const [wake, setWake] = useState(false);
  const [soundOff, setSoundOff] = useState(true);
  const [voiceProblem, setVoiceProblem] = useState('');
  const [wakeStatus, setWakeStatus] = useState('');
  const wakeRef = useRef(false);
  const busyRef = useRef(false);
  const actionsRef = useRef({ startSprint: () => {}, stepDone: () => {}, startTalk: () => {} });
  const [sttReady, setSttReady] = useState(false);
  const sttReadyRef = useRef(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const leftRef = useRef(left);
  const lastMode = useRef<string>('calm');
  const lastSignal = useRef<string | null>(null);
  leftRef.current = left;

  const mode = state?.mode ?? 'calm';
  const current = state?.current ?? null;

  const refresh = useCallback(async () => {
    try {
      const [s, z] = await Promise.all([
        send('/v1/focus/state', 'GET'),
        send('/v1/focus/sizing', 'GET'),
      ]);
      if (!s.ok || !z.ok) throw new Error('bad status');
      setState(await s.json());
      setSizing((await z.json()).contracts);
      setOffline(false);
      const sp = await send('/v1/focus/spanish', 'GET');
      if (sp.ok) setLesson(await sp.json());
    } catch {
      setOffline(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const poll = setInterval(() => void refresh(), 15000);
    const clock = setInterval(() => setNow(new Date()), 10000);
    return () => { clearInterval(poll); clearInterval(clock); };
  }, [refresh]);

  useEffect(() => {
    // Safari only counts some events as "the person clicked", so listen to all.
    const gestures = ['click', 'mousedown', 'pointerdown', 'touchend', 'keydown'];
    gestures.forEach((name) => window.addEventListener(name, unlockAudio, true));
    const watch = setInterval(() => { setSoundOff(audioLocked()); setVoiceProblem(speakProblem); }, 1000);
    return () => {
      gestures.forEach((name) => window.removeEventListener(name, unlockAudio, true));
      clearInterval(watch);
      talkRef.current = false;
      talkAbort.current?.abort();
      closeMic();
    };
  }, []);

  // Load speech recognition in the background so the first spoken answer is not a long wait.
  useEffect(() => {
    let stop = false;
    const check = async () => {
      if (stop || sttReadyRef.current) return;
      const res = await send('/v1/focus/listen/warm', 'POST').catch(() => null);
      if (res?.ok && (await res.json()).ready) { sttReadyRef.current = true; setSttReady(true); return; }
      setTimeout(check, 5000);
    };
    void check();
    return () => { stop = true; };
  }, []);

  // Sprint countdown.
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setLeft((v) => Math.max(0, v - 1)), 1000);
    return () => clearInterval(t);
  }, [running]);
  useEffect(() => {
    if (running && left === 0) {
      setRunning(false);
      const line = 'Fifteen minutes done. Stand up and stretch for two.';
      setSay(line);
      void speak(line, 'calm');
    }
  }, [left, running]);

  // Switch voice and words when a deadline passes, and back when it clears.
  useEffect(() => {
    if (mode === lastMode.current) return;
    lastMode.current = mode;
    const line = mode === 'deadline' && current
      ? `${current.title} is overdue. Stop what you are doing. ${current.steps[current.step]}. Now.`
      : CALM_LINE;
    setSay(line);
    void speak(line, mode);
  }, [mode, current]);

  // Read a new trade call aloud once. Calls already on screen at load stay silent.
  const newestSignal = state?.signals[0];
  useEffect(() => {
    if (!state) return;
    const id = newestSignal?.id ?? '';
    if (lastSignal.current === null) { lastSignal.current = id; return; }
    if (!newestSignal || id === lastSignal.current) return;
    lastSignal.current = id;
    setSay(newestSignal.say);
    void speak(newestSignal.say, newestSignal.action === 'exit' ? 'deadline' : 'calm');
  }, [state, newestSignal]);

  // The orb.
  useEffect(() => {
    const cv = canvasRef.current;
    const root = rootRef.current;
    if (!cv || !root) return;
    const cx = cv.getContext('2d');
    if (!cx) return;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let frame = 0;
    let timer: ReturnType<typeof setInterval> | undefined;
    const draw = (ts: number) => {
      const css = getComputedStyle(root);
      const accent = css.getPropertyValue('--accent').trim();
      const line = css.getPropertyValue('--line').trim();
      const hot = root.dataset.mode === 'deadline';
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = cv.clientWidth;
      if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = w * dpr; }
      const R = cv.width / 2;
      const t = still ? 0 : ts / 1000;
      const speed = hot ? 2.2 : 1;
      cx.clearRect(0, 0, cv.width, cv.height);
      cx.save();
      cx.translate(R, R);
      for (let i = 0; i < 60; i++) {
        const a = (i / 60) * Math.PI * 2;
        const major = i % 5 === 0;
        cx.strokeStyle = major ? accent : line;
        cx.lineWidth = dpr * (major ? 2 : 1);
        cx.beginPath();
        cx.moveTo(Math.cos(a) * R * 0.93, Math.sin(a) * R * 0.93);
        cx.lineTo(Math.cos(a) * R * (major ? 0.86 : 0.89), Math.sin(a) * R * (major ? 0.86 : 0.89));
        cx.stroke();
      }
      cx.lineWidth = dpr * 6;
      cx.strokeStyle = line;
      cx.beginPath(); cx.arc(0, 0, R * 0.78, 0, Math.PI * 2); cx.stroke();
      cx.strokeStyle = accent; cx.shadowColor = accent; cx.shadowBlur = dpr * 12;
      cx.beginPath();
      cx.arc(0, 0, R * 0.78, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * (leftRef.current / SPRINT_SECONDS));
      cx.stroke();
      cx.lineWidth = dpr * 3;
      for (const [r, v, n, len] of [[0.66, 0.5, 3, 1.4], [0.58, -0.8, 2, 0.9], [0.5, 1.3, 4, 0.5]]) {
        for (let k = 0; k < n; k++) {
          const a = t * v * speed + (k / n) * Math.PI * 2;
          cx.beginPath(); cx.arc(0, 0, R * r, a, a + len); cx.stroke();
        }
      }
      const pulse = 0.5 + 0.5 * Math.sin(t * (hot ? 5 : 1.6));
      const cr = R * (0.3 + 0.04 * pulse);
      const g = cx.createRadialGradient(0, 0, 0, 0, 0, cr * 1.5);
      g.addColorStop(0, `${accent}cc`); g.addColorStop(0.6, `${accent}33`); g.addColorStop(1, `${accent}00`);
      cx.shadowBlur = 0; cx.fillStyle = g;
      cx.beginPath(); cx.arc(0, 0, cr * 1.5, 0, Math.PI * 2); cx.fill();
      cx.restore();
      if (!still) frame = requestAnimationFrame(draw);
    };
    if (still) { draw(0); timer = setInterval(() => draw(0), 1000); }
    else frame = requestAnimationFrame(draw);
    return () => { cancelAnimationFrame(frame); if (timer) clearInterval(timer); };
  }, []);

  const addTask = async (e: FormEvent) => {
    e.preventDefault();
    const t = title.trim();
    if (!t || adding) return;
    setAdding(true);
    setSay('Breaking that into small steps…');
    let dueIso: string | null = null;
    const today = localDate(new Date());
    // A date with no time means "by the end of that day".
    if (due || (dueDate && dueDate !== today)) dueIso = `${dueDate || today}T${due || '23:59'}`;
    const res = await send('/v1/focus/task', 'POST', { title: t, due: dueIso }).catch(() => null);
    setAdding(false);
    if (!res?.ok) { setSay('I could not save that task. Is the Jarvis server running?'); return; }
    setTitle(''); setDue(''); setDueDate(localDate(new Date()));
    setSay('Added. Here is your first small step.');
    await refresh();
  };

  const stepDone = async () => {
    if (!current) return;
    const res = await send(`/v1/focus/task/${current.id}/step`, 'POST').catch(() => null);
    if (!res?.ok) return;
    const task: FocusTask = await res.json();
    const line = task.done ? `${task.title} is finished. Well done.` : 'Good. Next one.';
    setSay(line);
    void speak(line, 'calm');
    await refresh();
  };

  const addCapture = async (e: FormEvent) => {
    e.preventDefault();
    const text = capture.trim();
    if (!text) return;
    setCapture('');
    await send('/v1/focus/capture', 'POST', { text }).catch(() => null);
    setSay('Caught it. Back to your step.');
    await refresh();
  };

  const remove = async (path: string) => {
    await send(path, 'DELETE').catch(() => null);
    await refresh();
  };

  const setRisk = async (value: string) => {
    const n = Number(value);
    if (!(n > 0)) return;
    await send('/v1/focus/risk', 'PUT', { risk_per_trade: n }).catch(() => null);
    await refresh();
  };

  const draftJob = async (job: FocusJob) => {
    setSay(`Writing a cover note for ${job.title}…`);
    const res = await send(`/v1/focus/jobs/${job.id}/draft`, 'POST').catch(() => null);
    if (!res?.ok) {
      const detail = res ? (await res.json().catch(() => ({}))).detail : '';
      setSay(detail || 'I could not write that draft.');
      return;
    }
    setSay('Draft ready. Read it, then open the posting and submit.');
    await refresh();
  };

  const jobStatus = async (job: FocusJob, status: FocusJob['status']) => {
    await send(`/v1/focus/jobs/${job.id}/status`, 'POST', { status }).catch(() => null);
    await refresh();
  };

  const copyDraft = async (job: FocusJob) => {
    try {
      await navigator.clipboard.writeText(job.draft);
      setSay('Copied. Paste it into the application.');
    } catch {
      setSay('Copy failed. Select the text and copy it by hand.');
    }
  };

  const openJobs = state?.jobs.filter((j) => j.status === 'found' || j.status === 'drafted') ?? [];
  const sentJobs = state?.jobs.filter((j) => j.status === 'submitted').length ?? 0;

  const spanishAnswer = async (action: 'seen' | 'answer' | 'skip' | 'skip_unit', text = '') => {
    const card = lesson?.card;
    if (!card) return;
    const res = await send('/v1/focus/spanish/answer', 'POST', { item_id: card.item_id, action, text }).catch(() => null);
    if (!res?.ok) { setFeedback({ ok: false, text: 'Could not save that answer.' }); return; }
    const data = await res.json();
    setTyped('');
    if (data.result.correct === true) setFeedback({ ok: true, text: `Correct: ${data.result.es} = ${data.result.en}` });
    else if (data.result.correct === false) setFeedback({ ok: false, text: `Not yet: ${data.result.es} = ${data.result.en}` });
    else setFeedback(null);
    setLesson({ card: data.card, progress: data.progress });
  };

  // Say the card aloud and have it checked against what was heard.
  const sayCard = async () => {
    const card = lesson?.card;
    if (!card || micBusy || talking) return;
    unlockAudio();
    busyRef.current = true;
    setMicBusy(true);
    setFeedback({ ok: true, text: 'Listening… say it now.' });
    try {
      const rec = await recordUtterance({ waitMs: 6000, silenceMs: 900, maxMs: 9000 });
      if (!rec) { setFeedback({ ok: false, text: 'I did not hear anything. Try again, a little louder.' }); return; }
      setFeedback({ ok: true, text: 'Checking…' });
      const res = await apiFetch('/v1/focus/spanish/say', { method: 'POST', body: audioForm(rec, { item_id: card.item_id }) }).catch(() => null);
      if (!res?.ok) { setFeedback({ ok: false, text: await errorDetail(res, 'I could not check that.') }); return; }
      const data = await res.json();
      setLesson({ card: data.card, progress: data.progress });
      if (data.correct) {
        setFeedback({ ok: true, text: `I heard "${data.heard}". That is right.` });
      } else {
        setFeedback({ ok: false, text: `I heard "${data.heard || 'nothing clear'}". Listen, then try again: ${data.es}` });
        await speak(data.es, 'es');
      }
    } catch (err) {
      setFeedback({ ok: false, text: err instanceof Error ? err.message : 'The microphone is not available.' });
    } finally {
      closeMic();
      busyRef.current = false;
      setMicBusy(false);
    }
  };

  // Hands-free conversation: Jarvis speaks, listens, corrects, and repeats.
  const runTalk = async () => {
    const abort = new AbortController();
    talkAbort.current = abort;
    let history: TalkTurn[] = [];
    const push = (turn: TalkTurn) => { history = [...history, turn].slice(-12); setTurns(history); };
    const converse = async (text: string) => {
      setTalkStatus('Thinking…');
      const res = await send('/v1/focus/spanish/converse', 'POST', {
        text, history: history.map(({ role, text: t }) => ({ role, text: t })),
      }, abort.signal).catch(() => null);
      if (!res?.ok) throw new Error(await errorDetail(res, 'The tutor could not answer.'));
      return res.json() as Promise<{ say: string; meaning: string; correction: string; progress: SpanishProgress }>;
    };
    try {
      await openMic();
      let reply = await converse('');
      let said = '';
      while (talkRef.current) {
        if (said) push({ role: 'you', text: said, correction: reply.correction });
        push({ role: 'tutor', text: reply.say, meaning: reply.meaning });
        setLesson((cur) => (cur ? { ...cur, progress: reply.progress } : cur));
        setTalkStatus('Speaking…');
        if (said && reply.correction) await speak(reply.correction, 'calm', abort.signal);
        if (!talkRef.current) break;
        await speak(reply.say, 'es', abort.signal);
        said = '';
        while (talkRef.current && !said) {
          setTalkStatus('Listening… your turn.');
          const rec = await recordUtterance({ waitMs: 20000, silenceMs: 1100, maxMs: 15000, cancelled: () => !talkRef.current });
          if (!rec) continue;
          setTalkStatus('Working out what you said…');
          const res = await apiFetch('/v1/focus/listen', { method: 'POST', body: audioForm(rec), signal: abort.signal }).catch(() => null);
          if (!res?.ok) throw new Error(await errorDetail(res, 'I could not hear that.'));
          said = ((await res.json()).text as string).trim();
        }
        if (!talkRef.current) break;
        reply = await converse(said);
      }
      setTalkStatus('');
    } catch (err) {
      setTalkStatus(abort.signal.aborted ? '' : err instanceof Error ? err.message : 'The conversation stopped.');
    } finally {
      talkRef.current = false;
      setTalking(false);
      closeMic();
    }
  };

  // Voice lesson: Jarvis reads each card aloud and checks the spoken answer,
  // following the same mastery rules as the buttons.
  const runLesson = async () => {
    const abort = new AbortController();
    talkAbort.current = abort;
    const tell = (text: string, mode: 'calm' | 'es' = 'calm') => speak(text, mode, abort.signal);
    const cancelled = () => !talkRef.current;
    let history: TalkTurn[] = [];
    const push = (turn: TalkTurn) => { history = [...history, turn].slice(-12); setTurns(history); };
    try {
      await openMic();
      const first = await send('/v1/focus/spanish', 'GET', undefined, abort.signal).catch(() => null);
      if (!first?.ok) throw new Error(await errorDetail(first, 'The lesson could not load.'));
      let current: SpanishLesson = await first.json();
      setLesson(current);
      await tell('Picking up where you left off.');
      let silent = 0;
      let handOff = '';
      while (talkRef.current) {
        const card = current.card;
        if (!card) { await tell('You have mastered every unit. Well done.'); break; }
        const prompt = async () => {
          setTalkStatus('Speaking…');
          // Every card is answered by saying the Spanish out loud.
          if (card.kind !== 'intro') { await tell(`How do you say, ${card.en}?`); return; }
          await tell(card.es, 'es');
          await tell(`That means ${card.en}. Now you say it.`);
        };
        await prompt();
        let attempts = 0;
        let next: SpanishLesson | null = null;
        while (talkRef.current && !next) {
          setTalkStatus(`Listening… say it in Spanish: ${card.en}`);
          chime();
          const rec = await recordUtterance({ waitMs: 9000, silenceMs: 900, maxMs: 10000, cancelled });
          if (!talkRef.current) break;
          if (!rec) {
            silent += 1;
            if (silent >= 3) {
              await tell('I will pause the lesson. Say, hey Jarvis, teach me Spanish, when you are ready.');
              talkRef.current = false;
              break;
            }
            await tell('I did not hear you.');
            await prompt();
            continue;
          }
          silent = 0;
          attempts += 1;
          setTalkStatus('Checking…');
          const res = await apiFetch('/v1/focus/spanish/voice', {
            method: 'POST', signal: abort.signal,
            body: audioForm(rec, { item_id: card.item_id, kind: card.kind, final: attempts >= 2 ? '1' : '0' }),
          }).catch(() => null);
          if (!res?.ok) throw new Error(await errorDetail(res, 'I could not check that.'));
          const data = await res.json() as { heard: string; correct: boolean; command: string | null; wake_text: string; card: SpanishCard | null; progress: SpanishProgress };
          const after = { card: data.card, progress: data.progress };
          push({ role: 'you', text: data.heard || '(nothing clear)' });
          if (data.command === 'stop') { talkRef.current = false; break; }
          if (data.command === 'wake') { talkRef.current = false; handOff = data.wake_text; break; }
          if (data.command === 'unclear') {
            attempts -= 1;
            setFeedback({ ok: false, text: 'I did not catch that. Say it again, a little louder.' });
            await tell('I did not catch that. Say it again.');
            continue;
          }
          if (data.command === 'repeat') { attempts -= 1; await prompt(); continue; }
          if (data.command === 'skip') { setFeedback(null); await tell('Skipped.'); next = after; break; }
          if (data.correct) {
            setFeedback({ ok: true, text: `Correct: ${card.es} = ${card.en}` });
            chime(1320);
            await tell('Correct.');
            next = after;
          } else if (attempts < 2) {
            setFeedback({ ok: false, text: `I heard "${data.heard || 'nothing clear'}". One more try.` });
            await tell('Not quite. Listen, then try once more.');
            await tell(card.es, 'es');
          } else {
            setFeedback({ ok: false, text: `Not yet: ${card.es} = ${card.en}` });
            await tell('It is,');
            await tell(card.es, 'es');
            next = after;
          }
        }
        if (next) { current = next; setLesson(next); }
      }
      setTalkStatus('');
      // "Hey Jarvis, ..." said during the lesson: leave the lesson and do it.
      if (handOff) {
        const res = await send('/v1/focus/command', 'POST', { text: handOff }).catch(() => null);
        if (res?.ok) {
          const out = await res.json() as { say?: string; action?: string | null };
          if (out.say) { setSay(out.say); await speak(out.say, 'calm'); }
          await refresh();
          if (out.action === 'sprint') actionsRef.current.startSprint();
          if (out.action === 'step_done') actionsRef.current.stepDone();
        }
      }
    } catch (err) {
      setTalkStatus(abort.signal.aborted ? '' : err instanceof Error ? err.message : 'The lesson stopped.');
    } finally {
      talkRef.current = false;
      setTalking(false);
      closeMic();
    }
  };

  const startTalk = (mode: 'lesson' | 'free' = 'lesson') => {
    if (talkRef.current) return;
    unlockAudio();
    talkRef.current = true;
    setTalking(true);
    setTurns([]);
    void (mode === 'lesson' ? runLesson() : runTalk());
  };

  const stopTalk = () => {
    talkRef.current = false;
    talkAbort.current?.abort();
    stopSpeaking();
    setTalkStatus('');
  };

  const changeAutoListen = (on: boolean) => {
    setAutoListen(on);
    try { localStorage.setItem('focus-auto-listen', on ? '1' : '0'); } catch { /* not remembered */ }
  };

  // "Hey Jarvis": listen all the time, act only on speech that names Jarvis.
  const runWake = async () => {
    let awakeUntil = 0;
    let pending = '';
    try {
      await openMic();
      while (wakeRef.current) {
        if (talkRef.current || busyRef.current) { await sleep(400); continue; }
        const awake = Date.now() < awakeUntil;
        if (!awake) pending = '';
        setWakeStatus(awake ? 'Yes? Go ahead.' : 'Say "Hey Jarvis", then what you need.');
        const rec = await recordUtterance({
          waitMs: 30000, silenceMs: 900, maxMs: 12000,
          cancelled: () => !wakeRef.current || talkRef.current || busyRef.current,
        });
        if (!rec) continue;
        const heard = await apiFetch('/v1/focus/listen', { method: 'POST', body: audioForm(rec, { language: 'en' }) }).catch(() => null);
        if (!heard?.ok) throw new Error(await errorDetail(heard, 'I could not hear that.'));
        const text = ((await heard.json()).text as string).trim();
        if (!text || !wakeRef.current) continue;
        const stillAwake = Date.now() < awakeUntil;
        const res = await send('/v1/focus/command', 'POST', { text, awake: stillAwake, pending: stillAwake ? pending : '' }).catch(() => null);
        if (!res?.ok) continue;
        const out = await res.json() as { wake: boolean; heard: string; intent?: string; say?: string; action?: string | null; pending?: string };
        if (!out.wake) continue;
        chime();
        pending = out.pending || '';
        awakeUntil = out.intent === 'none' || pending ? Date.now() + 15000 : 0;
        setWakeStatus(`Heard: ${out.heard}`);
        if (out.say) {
          setSay(out.say);
          if (audioLocked()) setWakeStatus('Click anywhere on the page so I can speak.');
          await speak(out.say, 'calm');
        }
        await refresh();
        if (out.action === 'sprint') actionsRef.current.startSprint();
        if (out.action === 'step_done') actionsRef.current.stepDone();
        if (out.action === 'spanish') actionsRef.current.startTalk();
        if (out.action === 'stop') break;
      }
      setWakeStatus('');
    } catch (err) {
      setWakeStatus(err instanceof Error ? err.message : 'Listening stopped.');
    } finally {
      wakeRef.current = false;
      setWake(false);
      if (!talkRef.current && !busyRef.current) closeMic();
    }
  };

  const startWake = () => {
    if (wakeRef.current) return;
    unlockAudio();
    wakeRef.current = true;
    setWake(true);
    void runWake();
  };

  const toggleWake = () => {
    if (wake) { wakeRef.current = false; setWakeStatus(''); return; }
    startWake();
  };

  // With the box ticked, start listening for "Hey Jarvis" as soon as speech
  // recognition is ready. Browsers stay silent until the page has been clicked
  // once, so if sound is still locked it starts on the first click instead.
  useEffect(() => {
    if (!autoListen || !sttReady || autoStarted.current) return;
    const begin = () => {
      if (autoStarted.current) return;
      autoStarted.current = true;
      window.removeEventListener('click', begin);
      window.removeEventListener('keydown', begin);
      startWake();
    };
    unlockAudio();
    if (out?.state === 'running') { begin(); return; }
    setWakeStatus('Click anywhere once and I will start listening.');
    window.addEventListener('click', begin);
    window.addEventListener('keydown', begin);
    return () => {
      window.removeEventListener('click', begin);
      window.removeEventListener('keydown', begin);
    };
  }, [autoListen, sttReady]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveGoal = async (patch: { name?: string; target?: number }) => {
    await send('/v1/focus/goal', 'PUT', patch).catch(() => null);
    await refresh();
  };

  const addGoalEntry = async (e: FormEvent) => {
    e.preventDefault();
    const amount = Number(goalAmount);
    if (!goalAmount.trim() || !Number.isFinite(amount) || amount === 0) return;
    const res = await send('/v1/focus/goal/entry', 'POST', { amount, note: goalNote.trim() }).catch(() => null);
    if (!res?.ok) { setSay('I could not record that amount.'); return; }
    setGoalAmount(''); setGoalNote('');
    setSay(amount > 0 ? `${money(amount)} closer to your goal.` : `Recorded ${money(amount)}. It counts against the goal.`);
    await refresh();
  };

  const toggleSprint = () => {
    if (!running && left === 0) setLeft(SPRINT_SECONDS);
    if (!running) setSay('Sprint started. Just this one step.');
    setRunning(!running);
  };

  actionsRef.current = {
    startSprint: () => { if (!running) toggleSprint(); },
    stepDone: () => { void stepDone(); },
    startTalk: () => startTalk('lesson'),
  };

  const status = running ? 'Focus sprint' : mode === 'deadline' ? 'Overdue' : left < SPRINT_SECONDS && left > 0 ? 'Paused' : 'Ready';

  return (
    <div className="focus-hud" data-mode={mode} ref={rootRef}>
      <div className="wrap">
        <div className="top">
          <span className="brand">FOCUS HUD</span>
          <span><span className={offline ? 'dot off' : 'dot'} />Jarvis server: <b>{offline ? 'not reachable' : 'connected'}</b></span>
          <span>Tasks, notes and voice stay on this Mac · {BUILD}</span>
          {soundOff && <span className="late">Sound is off: click anywhere on this page to turn it on</span>}
          {voiceProblem && <span className="late">{voiceProblem}</span>}
          <Link className="btn" to="/">Chat</Link>
        </div>

        <div className="grid">
          <div className="col">
            <div className="panel">
              <h2>Now</h2>
              <div className="clock">{now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
              <div className="date">{now.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' })}</div>
            </div>
            <div className="panel">
              <h2>To-dos <span>{state ? `${state.tasks.filter((t) => !t.done).length} open` : ''}</span></h2>
              <form className="row" onSubmit={addTask}>
                <input id="focus-task" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Add a to-do" aria-label="New to-do" autoComplete="off" />
                <input id="focus-due-date" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} aria-label="Due date" />
                <input id="focus-due" type="time" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due time" />
                <button type="submit" disabled={adding}>{adding ? '…' : 'Add'}</button>
              </form>
              <ul style={{ marginTop: 10 }}>
                {state?.tasks.map((t) => (
                  <li key={t.id}>
                    <span className={t.done ? 'struck' : t.overdue ? 'late' : ''}>{t.title}</span>
                    <span className="end">
                      <span className={`pill ${t.overdue ? 'hot' : 'ok'}`}>{t.overdue ? 'overdue' : t.done ? 'done' : t.due ? fmtDue(t.due) : 'no date'}</span>
                      <button className="small" onClick={() => void remove(`/v1/focus/task/${t.id}`)} aria-label={`Remove ${t.title}`}>×</button>
                    </span>
                  </li>
                ))}
              </ul>
              <p className="note" style={{ margin: '10px 0 0' }}>Outlook and Google calendars: not connected yet</p>
            </div>
            <div className="panel">
              <h2>Quick capture <span>{state ? `${state.captures.length} caught` : ''}</span></h2>
              <form className="row" onSubmit={addCapture}>
                <input id="focus-capture" value={capture} onChange={(e) => setCapture(e.target.value)} placeholder="Type the thought, then get back to work" aria-label="Quick capture" autoComplete="off" />
                <button type="submit">Catch</button>
              </form>
              <ul style={{ marginTop: 10 }}>
                {state?.captures.map((c) => (
                  <li key={c.id}>
                    <span>{c.kind === 'idea' && <span className="pill warn">idea</span>} {c.text}</span>
                    <span className="end">
                      <span className="m">{c.at.slice(11, 16)}</span>
                      <button className="small" onClick={() => void remove(`/v1/focus/capture/${c.id}`)}>Done</button>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            {state && (
              <div className="panel">
                <h2>Goal <span>{Math.max(0, Math.min(100, Math.round((state.goal.saved / state.goal.target) * 100)))}%</span></h2>
                <div className="kv">
                  <input id="focus-goal-name" key={`n-${state.goal.name}`} defaultValue={state.goal.name} aria-label="Goal name"
                    onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== state.goal.name) void saveGoal({ name: v }); }} style={{ width: 'auto', flex: 1, textAlign: 'left' }} />
                  <span>
                    $ <input id="focus-goal-target" type="number" min="1" step="100" key={`t-${state.goal.target}`} defaultValue={state.goal.target} aria-label="Goal amount in dollars"
                      onBlur={(e) => { const v = Number(e.target.value); if (v > 0 && v !== state.goal.target) void saveGoal({ target: v }); }} />
                  </span>
                </div>
                <div className="goal-amount"><b className={state.goal.saved < 0 ? 'late' : ''}>{money(state.goal.saved)}</b> of {money(state.goal.target)}</div>
                <div className="bar-track" role="progressbar" aria-valuemin={0} aria-valuemax={state.goal.target} aria-valuenow={Math.max(0, state.goal.saved)}>
                  <div className="bar-fill" style={{ width: `${Math.max(0, Math.min(100, (state.goal.saved / state.goal.target) * 100))}%` }} />
                </div>
                <form className="row" onSubmit={addGoalEntry} style={{ marginTop: 10 }}>
                  <input id="focus-goal-amount" type="number" step="0.01" value={goalAmount} onChange={(e) => setGoalAmount(e.target.value)} placeholder="Trade result, e.g. 45 or -20" aria-label="Trade result in dollars" />
                  <input id="focus-goal-note" value={goalNote} onChange={(e) => setGoalNote(e.target.value)} placeholder="Note (optional)" aria-label="Note" autoComplete="off" />
                  <button type="submit">Record</button>
                </form>
                <ul style={{ marginTop: 10 }}>
                  {state.goal.entries.slice(0, 5).map((g) => (
                    <li key={g.id}>
                      <span><b className={g.amount < 0 ? 'late' : 'gain'}>{g.amount > 0 ? '+' : ''}{money(g.amount)}</b>{g.note ? ` · ${g.note}` : ''}</span>
                      <span className="end">
                        <span className="m">{g.at.slice(5, 10)}</span>
                        <button className="small" onClick={() => void remove(`/v1/focus/goal/entry/${g.id}`)} aria-label="Remove entry">×</button>
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="note" style={{ margin: '8px 0 0' }}>Enter each trade's profit, or a loss as a minus number.</p>
              </div>
            )}
          </div>


          <div className="col mid">
            <div className="panel core">
              <div className="orbbox">
                <canvas ref={canvasRef} aria-hidden="true" />
                <div className="orbtext">
                  <div className="timer">{fmtTimer(left)}</div>
                  <div className="state">{status}</div>
                </div>
              </div>
              <div className="say" aria-live="polite">{say}</div>
              <div>
                <div className="now-label">Your one next step</div>
                <p className="now">{current ? current.steps[current.step] : 'Nothing open. Add a to-do on the left.'}</p>
                {current && (
                  <div className="task">{current.title} (step {current.step + 1} of {current.steps.length})</div>
                )}
              </div>
              {current && (
                <div className="steps">
                  {current.steps.map((s, i) => (
                    <i key={`${i}-${s}`} className={i < current.step ? 'done' : i === current.step ? 'cur' : ''} />
                  ))}
                </div>
              )}
              <div className="row" style={{ justifyContent: 'center' }}>
                <button className="primary" onClick={toggleSprint}>{running ? 'Pause' : left < SPRINT_SECONDS && left > 0 ? 'Resume sprint' : 'Start 15-min sprint'}</button>
                <button onClick={() => void stepDone()} disabled={!current}>Step done</button>
              </div>
              <div className="wake">
                <button className={wake ? 'primary' : ''} onClick={toggleWake} disabled={!sttReady && !wake}>{wake ? 'Hey Jarvis: on' : 'Hey Jarvis: off'}</button>
                {wakeStatus && <p className="note" aria-live="polite">{wakeStatus}</p>}
                {!sttReady && <p className="note">Speech recognition is loading.</p>}
                <label className="check"><input id="focus-auto-listen" type="checkbox" checked={autoListen} onChange={(e) => changeAutoListen(e.target.checked)} /> Turn on when this page opens</label>
              </div>
            </div>
          </div>

          <div className="col">
            <div className="panel">
              <h2>Spanish <span>{lesson ? `unit ${lesson.progress.unit_number} of ${lesson.progress.unit_count}` : ''}</span></h2>
              {lesson ? (
                <>
                  <div className="kv"><span>{lesson.progress.unit}</span><span>{lesson.progress.unit_total ? `${lesson.progress.unit_mastered}/${lesson.progress.unit_total}` : 'done'}</span></div>
                  <div className="kv"><span>Today</span><span>{lesson.progress.minutes_today}/{lesson.progress.goal_minutes} min</span></div>
                  <div className="kv"><span>Day streak · words learned</span><span>{lesson.progress.day_streak} · {lesson.progress.words_mastered}/{lesson.progress.words_total}</span></div>
                  {lesson.card ? (
                    <div className="lesson">
                      {lesson.card.kind === 'type' ? (
                        <>
                          <div className="now-label">Type it in Spanish</div>
                          <div className="word">{lesson.card.en}</div>
                          <form className="row" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) void spanishAnswer('answer', typed); }}>
                            <input id="focus-spanish" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Accents are optional" aria-label="Your answer in Spanish" autoComplete="off" autoCapitalize="off" spellCheck={false} />
                            <button type="submit">Check</button>
                          </form>
                        </>
                      ) : (
                        <>
                          <div className="now-label">{lesson.card.kind === 'intro' ? 'New' : 'What does it mean?'}</div>
                          <div className="word">{lesson.card.es}</div>
                          {lesson.card.kind === 'intro' && <div className="task">{lesson.card.en}</div>}
                          {lesson.card.kind === 'choose' && (
                            <div className="choices">
                              {lesson.card.choices?.map((c) => (
                                <button key={c} onClick={() => void spanishAnswer('answer', c)}>{c}</button>
                              ))}
                            </div>
                          )}
                        </>
                      )}
                      <div className="row" style={{ justifyContent: 'center' }}>
                        {lesson.card.kind !== 'type' && <button className="small" onClick={() => void speak(lesson.card!.es, 'es')}>Hear it</button>}
                        {lesson.card.kind !== 'choose' && <button className="small" onClick={() => void sayCard()} disabled={micBusy || talking || !sttReady}>{micBusy ? 'Listening…' : 'Say it'}</button>}
                        {lesson.card.kind === 'intro' && <button className="small primary" onClick={() => void spanishAnswer('seen')}>Got it</button>}
                        <button className="small" onClick={() => void spanishAnswer('skip')}>I know this</button>
                        <button className="small" onClick={() => void spanishAnswer('skip_unit')}>Skip unit</button>
                      </div>
                    </div>
                  ) : (
                    <p className="note" style={{ margin: '8px 0 0' }}>Every unit is mastered. More units are not written yet.</p>
                  )}
                  {feedback && <p className={feedback.ok ? 'fb ok' : 'fb bad'} aria-live="polite">{feedback.text}</p>}
                  <div className="talk">
                    {talking ? (
                      <button className="primary" onClick={stopTalk}>Stop</button>
                    ) : (
                      <div className="row" style={{ justifyContent: 'center' }}>
                        <button className="primary" onClick={() => startTalk('lesson')} disabled={micBusy || !sttReady}>Voice lesson</button>
                        <button onClick={() => startTalk('free')} disabled={micBusy || !sttReady}>Free talk</button>
                      </div>
                    )}
                    {!sttReady && <p className="note">Speech recognition is loading. The first time can take several minutes.</p>}
                    {talkStatus && <p className="note" aria-live="polite">{talkStatus}</p>}
                    {turns.length > 0 && (
                      <ul className="turns">
                        {turns.slice(-4).map((t, i) => (
                          <li key={`${i}-${t.text}`} className={t.role}>
                            <span><b>{t.role === 'you' ? 'You' : 'Jarvis'}:</b> {t.text}</span>
                            {t.meaning && <span className="m">{t.meaning}</span>}
                            {t.correction && <span className="fix">{t.correction}</span>}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </>
              ) : (
                <p className="note" style={{ margin: 0 }}>Lesson not loaded.</p>
              )}
            </div>
            <div className="panel">
              <h2>Trades <span>you place every trade</span></h2>
              <div className="kv">
                <span>Most you can lose per trade ($)</span>
                <input id="focus-risk" type="number" min="1" step="1" key={state?.risk_per_trade} defaultValue={state?.risk_per_trade ?? 20}
                  onBlur={(e) => void setRisk(e.target.value)} aria-label="Risk per trade in dollars" />
              </div>
              <ul style={{ marginTop: 8 }}>
                {sizing.map((c) => (
                  <li key={c.symbol}>
                    <span>{c.symbol}</span>
                    <span className="m">${c.point_value}/pt · 1 contract, stop up to {c.max_stop_points} pts</span>
                  </li>
                ))}
              </ul>
              <h2 style={{ marginTop: 12 }}>Latest calls <span>from your chart alerts</span></h2>
              {state?.signals.length ? (
                <ul>
                  {state.signals.map((g) => (
                    <li key={g.id} style={{ flexDirection: 'column', gap: 2 }}>
                      <span className="end">
                        <span className={`pill ${g.action === 'exit' ? 'hot' : g.action === 'wait' ? 'warn' : 'ok'}`}>
                          {g.action === 'long' || g.action === 'short' ? `go ${g.action}` : g.action === 'exit' ? 'get out' : 'skip'}
                        </span>
                        <span className="m">{g.at.slice(11, 16)}</span>
                      </span>
                      <span>{g.say}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="note" style={{ margin: 0 }}>No calls yet. Chart alerts are not connected.</p>
              )}
            </div>
            <div className="panel">
              <h2>Job hunt <span>{openJobs.length} to do · {sentJobs} sent</span></h2>
              {openJobs.length ? (
                <ul>
                  {openJobs.map((j) => (
                    <li key={j.id} style={{ flexDirection: 'column', gap: 6 }}>
                      <span>{j.title}{j.company ? ` · ${j.company}` : ''}{j.salary ? ` · ${j.salary}` : ''}</span>
                      {j.draft && <span className="draft">{j.draft}</span>}
                      <span className="end" style={{ flexWrap: 'wrap' }}>
                        <button className="small" onClick={() => void draftJob(j)}>{j.draft ? 'Redraft' : 'Draft'}</button>
                        {j.draft && <button className="small" onClick={() => void copyDraft(j)}>Copy</button>}
                        <a className="btn small" href={j.url} target="_blank" rel="noreferrer noopener">Open</a>
                        <button className="small" onClick={() => void jobStatus(j, 'submitted')}>Sent</button>
                        <button className="small" onClick={() => void jobStatus(j, 'skipped')}>Skip</button>
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="note" style={{ margin: 0 }}>No jobs waiting. Automatic job search is not connected yet.</p>
              )}
              {state && !state.job_profile.has_resume && (
                <p className="note" style={{ margin: '8px 0 0' }}>Resume not loaded yet, so drafts are off.</p>
              )}
            </div>
            <div className="panel">
              <h2>Inbox</h2>
              <p className="note" style={{ margin: 0 }}>Gmail, Yahoo and Outlook: not connected yet</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
