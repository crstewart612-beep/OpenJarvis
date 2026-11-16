import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import {
  ArrowRight, Bell, Brain, CircleUser, FileText, Star, Clock3, BriefcaseBusiness, CalendarDays, Car, ChartCandlestick, ChartNoAxesColumn, Check, CircleAlert,
  CircleCheck, Flame, GraduationCap, House, Layers, Lightbulb, Mail, MessageCircleMore, MessagesSquare, Mic, MicOff,
  Pause, Play, Plus, Settings, Sparkles, SquareCheck, Target, TriangleAlert, X,
} from 'lucide-react';
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
interface CalEvent { title: string; start: string; all_day: boolean; calendar: string }
interface CalState { loading?: boolean; available: boolean; events: CalEvent[]; calendars: string[]; write_to: string; source: string; note: string; error: string }
interface MailState { loading?: boolean; available: boolean; unread: number; accounts: { name: string; unread: number }[]; latest: { from: string; subject: string; date: string; account: string }[]; error: string }
interface SizingRow { symbol: string; point_value: number; max_stop_points: number }

// Shown in the top bar so a screenshot tells which version is running.
const BUILD = 'build 27';
// What the assistant is called on screen and out loud.
const NAME = 'Morpheus';
const NAV: [string, string][] = [
  ['overview', 'Overview'],
  ['focus', 'Focus'],
  ['tasks', 'Tasks'],
  ['ideas', 'Brain Dump'],
  ['calendar', 'Calendar'],
  ['trading', 'Trading'],
  ['car', 'Car Goal'],
  ['spanish', 'Spanish'],
  ['jobs', 'Job Hunt'],
  ['email', 'Email'],
  ['business', 'Business Builder'],
];
const FOCUS_SECONDS = 25 * 60;
const BREAK_SECONDS = 5 * 60;
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

let speechPrimed = false;
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
  try {
    if (!speechPrimed && 'speechSynthesis' in window) {
      speechPrimed = true;
      window.speechSynthesis.speak(new SpeechSynthesisUtterance(' '));
    }
  } catch { /* built-in speech is optional */ }
}

function audioLocked(): boolean {
  return !out || out.state !== 'running';
}

function stopSpeaking(): void {
  try { playing?.stop(); } catch { /* already stopped */ }
  playing = null;
  try { window.speechSynthesis?.cancel(); } catch { /* not available */ }
}

// The everyday voice. The named ones are generated on this Mac (natural, a
// second or two per new sentence); "builtin" is the computer's own speech
// (instant, more robotic). Exactly one of them speaks any given line.
const VOICES: [string, string][] = [
  ['bm_george', 'George: deep, calm'],
  ['am_onyx', 'Onyx: deepest'],
  ['am_michael', 'Michael: warm'],
  ['bm_lewis', 'Lewis: low, steady'],
  ['builtin', 'Built-in: fastest'],
];
let voiceChoice = 'bm_george';
try {
  const saved = localStorage.getItem('focus-voice-choice');
  if (saved && VOICES.some(([id]) => id === saved)) voiceChoice = saved;
} catch { /* default */ }

function pickVoice(lang: string, preferred: RegExp): SpeechSynthesisVoice | undefined {
  const voices = window.speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith(lang));
  return voices.find((v) => preferred.test(v.name)) ?? voices[0];
}

/** Speak with the built-in voice. Resolves false if it could not start. */
function fastSpeak(text: string, mode: 'calm' | 'deadline' | 'es', signal?: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    if (!('speechSynthesis' in window) || typeof SpeechSynthesisUtterance === 'undefined') { resolve(false); return; }
    const u = new SpeechSynthesisUtterance(text);
    if (mode === 'es') {
      u.lang = 'es-MX';
      u.rate = 0.85;
      const v = pickVoice('es', /paulina|juan|m[oó]nica|diego|jorge/i);
      if (v) u.voice = v;
    } else {
      u.lang = 'en-US';
      u.rate = mode === 'deadline' ? 1.08 : 0.92;
      u.pitch = mode === 'deadline' ? 0.7 : 0.85;
      const v = pickVoice('en', /daniel|alex|evan|tom|aaron|fred/i);
      if (v) u.voice = v;
    }
    let settled = false;
    const finish = (ok: boolean) => { if (!settled) { settled = true; resolve(ok); } };
    u.onend = () => finish(true);
    u.onerror = () => finish(true);
    signal?.addEventListener('abort', () => { window.speechSynthesis.cancel(); finish(true); }, { once: true });
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
    // Safety net if the end of speech is never reported.
    setTimeout(() => finish(true), 3000 + text.length * 110);
  });
}

// Lines Morpheus says often ("Correct.", "What does that mean?") are kept after
// the first time so they play at once instead of being generated again.
const spoken = new Map<string, AudioBuffer>();
// Why the last line could not be spoken, for showing on screen.
let speakProblem = '';

/** Speak text in one of the voices. Resolves when playback has finished. */
async function speak(text: string, mode: 'calm' | 'deadline' | 'es', signal?: AbortSignal): Promise<void> {
  try {
    if (!text.trim() || signal?.aborted) return;
    // One engine per line, never both: the built-in voice only falls back to
    // the generated one when this browser has no built-in speech at all.
    if (voiceChoice === 'builtin' && await fastSpeak(text, mode, signal)) { speakProblem = ''; return; }
    if (!out) return;
    const voice = voiceChoice === 'builtin' ? 'bm_george' : voiceChoice;
    const key = `${mode}:${voice}:${text}`;
    let buffer = spoken.get(key);
    if (!buffer) {
      const res = await send('/v1/focus/speak', 'POST', { text, mode, voice }, signal);
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
    // Played a little under full speed, the calm voice sits lower and slower.
    const rate = mode === 'calm' ? 0.94 : 1;
    node.playbackRate.value = rate;
    node.connect(out.destination);
    playing = node;
    await new Promise<void>((resolve) => {
      const done = () => { if (playing === node) playing = null; resolve(); };
      node.onended = done;
      // Safety net in case the browser never reports the end of playback.
      setTimeout(done, (buffer.duration / rate) * 1000 + 1500);
      signal?.addEventListener('abort', () => { try { node.stop(); } catch { done(); } }, { once: true });
      node.start();
    });
  } catch {
    // Voice is optional: the screen still shows the same words.
  }
}

/** A short tone: instant feedback that Morpheus heard its name or is listening. */
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

/** Plain words for a microphone failure. */
function micMessage(err: unknown, fallback: string): string {
  const name = err instanceof Error ? err.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Safari blocked the microphone. Click the Ready card to try again and choose Allow.';
  }
  if (name === 'NotFoundError') return 'No microphone was found on this Mac.';
  return err instanceof Error && err.message ? err.message : fallback;
}

async function errorDetail(res: Response | null, fallback: string): Promise<string> {
  if (!res) return 'The server did not answer.';
  const body = await res.json().catch(() => ({}));
  return typeof body.detail === 'string' ? body.detail : fallback;
}

interface TalkTurn { role: 'you' | 'tutor'; text: string; meaning?: string; correction?: string }

export function FocusPage() {
  const [state, setState] = useState<FocusState | null>(null);
  const [sizing, setSizing] = useState<SizingRow[]>([]);
  const [offline, setOffline] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const [phase, setPhase] = useState<'focus' | 'break'>('focus');
  const phaseSeconds = phase === 'focus' ? FOCUS_SECONDS : BREAK_SECONDS;
  const [left, setLeft] = useState(FOCUS_SECONDS);
  const [cycles, setCycles] = useState(() => {
    try { return Number(localStorage.getItem(`focus-cycles-${localDate(new Date())}`)) || 0; } catch { return 0; }
  });
  const totalRef = useRef(FOCUS_SECONDS);
  totalRef.current = phaseSeconds;
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
  const [section, setSection] = useState('overview');
  const [voice, setVoice] = useState(voiceChoice);
  const [online, setOnline] = useState<{ configured: boolean; model: string } | null>(null);
  const [keyOpen, setKeyOpen] = useState(false);
  const [keyText, setKeyText] = useState('');
  const [keyNote, setKeyNote] = useState('');
  const [cal, setCal] = useState<CalState | null>(null);
  const [mail, setMail] = useState<MailState | null>(null);
  const [plans, setPlans] = useState<{ name: string; why: string; steps: string[] }[]>([]);
  const [bizNote, setBizNote] = useState('');
  const [bizBusy, setBizBusy] = useState(false);
  const [jobNote, setJobNote] = useState('');
  const [jobBusy, setJobBusy] = useState(false);
  const [jobTitles, setJobTitles] = useState('');
  const [jobFloor, setJobFloor] = useState('');
  const [newJob, setNewJob] = useState({ title: '', company: '', url: '' });
  const rainRef = useRef<HTMLCanvasElement>(null);
  const [taskTab, setTaskTab] = useState<'all' | 'today' | 'upcoming' | 'done'>('all');
  const [dumpText, setDumpText] = useState('');
  const [dumpKind, setDumpKind] = useState<'task' | 'idea' | 'reminder' | 'later'>('task');
  const [ask, setAsk] = useState('');
  const askPending = useRef('');
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
      // These three are independent and may be slow; none may hold up the rest.
      void send('/v1/focus/calendar', 'GET').then(async (r) => { if (r.ok) setCal(await r.json()); }).catch(() => {});
      void send('/v1/focus/mail', 'GET').then(async (r) => { if (r.ok) setMail(await r.json()); }).catch(() => {});
      void send('/v1/focus/business', 'GET').then(async (r) => { if (r.ok) setPlans((await r.json()).plans ?? []); }).catch(() => {});
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
      let line = 'Break is over. Ready for the next one?';
      if (phase === 'focus') {
        line = 'Focus sprint done. Stand up and stretch for five.';
        setCycles((n) => {
          try { localStorage.setItem(`focus-cycles-${localDate(new Date())}`, String(n + 1)); } catch { /* not remembered */ }
          return n + 1;
        });
        setPhase('break');
        setLeft(BREAK_SECONDS);
      } else {
        setPhase('focus');
        setLeft(FOCUS_SECONDS);
      }
      setSay(line);
      void speak(line, 'calm');
    }
  }, [left, running]); // eslint-disable-line react-hooks/exhaustive-deps

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
    const root = rootRef.current;
    if (!root) return;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let frame = 0;
    let timer: ReturnType<typeof setInterval> | undefined;
    const draw = (ts: number) => {
      // The orb is only on some sections, so look it up on every frame.
      const cv = canvasRef.current;
      const cx = cv?.getContext('2d');
      if (!cv || !cx) { if (!still) frame = requestAnimationFrame(draw); return; }
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
      cx.arc(0, 0, R * 0.78, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * (leftRef.current / totalRef.current));
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
      if (root.dataset.section === 'overview') {
        // Voice bars in the middle: taller and quicker while listening.
        const loud = root.dataset.voice === 'on';
        cx.fillStyle = '#eaffff';
        for (let k = -2; k <= 2; k++) {
          const h = R * (0.07 + (loud ? 0.16 : 0.05) * Math.abs(Math.sin(t * (loud ? 6 : 1.5) + k * 0.9)));
          cx.fillRect(k * R * 0.07 - R * 0.016, -h, R * 0.032, h * 2);
        }
      }
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
    if (!res?.ok) { setSay('I could not save that task. Is the server running?'); return; }
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
      setFeedback({ ok: false, text: micMessage(err, 'The microphone is not available.') });
    } finally {
      closeMic();
      busyRef.current = false;
      setMicBusy(false);
    }
  };

  // Hands-free conversation: Morpheus speaks, listens, corrects, and repeats.
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
      setTalkStatus(abort.signal.aborted ? '' : micMessage(err, 'The conversation stopped.'));
    } finally {
      talkRef.current = false;
      setTalking(false);
      closeMic();
    }
  };

  // Voice lesson: Morpheus reads each card aloud and checks the spoken answer,
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
              await tell(`I will pause the lesson. Say, hey ${NAME}, teach me Spanish, when you are ready.`);
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
      // "Hey Morpheus, ..." said during the lesson: leave the lesson and do it.
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
      setTalkStatus(abort.signal.aborted ? '' : micMessage(err, 'The lesson stopped.'));
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

  // "Hey Morpheus": listen all the time, act only on speech that names Morpheus.
  const runWake = async () => {
    let awakeUntil = 0;
    let pending = '';
    try {
      await openMic();
      while (wakeRef.current) {
        if (talkRef.current || busyRef.current) { await sleep(400); continue; }
        const awake = Date.now() < awakeUntil;
        if (!awake) pending = '';
        setWakeStatus(awake ? 'Yes? Go ahead.' : `Say "Hey ${NAME}", then what you need.`);
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
      setWakeStatus(micMessage(err, 'Listening stopped.'));
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

  // With the box ticked, start listening for "Hey Morpheus" as soon as speech
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

  // The online model's key is kept in a file on this computer by the local
  // server. It is sent only to that server, and is never shown again.
  const loadKeyStatus = useCallback(async () => {
    const res = await send('/v1/focus/key', 'GET').catch(() => null);
    if (res?.ok) setOnline(await res.json());
  }, []);
  useEffect(() => { void loadKeyStatus(); }, [loadKeyStatus]);

  const testKey = async () => {
    setKeyNote('Testing…');
    const res = await send('/v1/focus/key/test', 'POST').catch(() => null);
    const out = res?.ok ? await res.json() as { ok: boolean; model?: string; error?: string } : null;
    setKeyNote(out?.ok ? `Working: ${out.model}` : out?.error || 'The test could not run.');
  };

  const saveKey = async (e: FormEvent) => {
    e.preventDefault();
    const key = keyText.trim();
    if (!key) return;
    setKeyNote('Saving…');
    const res = await send('/v1/focus/key', 'PUT', { key }).catch(() => null);
    setKeyText('');
    if (!res?.ok) { setKeyNote(await errorDetail(res, 'The key could not be saved.')); return; }
    setOnline(await res.json());
    setKeyOpen(false);
    await testKey();
  };

  const removeKey = async () => {
    const res = await send('/v1/focus/key', 'PUT', { key: '' }).catch(() => null);
    if (res?.ok) { setOnline(await res.json()); setKeyNote('Key removed.'); }
  };

  // ---- business builder ----
  const suggestBusinesses = async () => {
    setBizBusy(true);
    setBizNote('Thinking about what fits you…');
    const res = await send('/v1/focus/business/suggest', 'POST').catch(() => null);
    setBizBusy(false);
    if (!res?.ok) { setBizNote(await errorDetail(res, 'The suggestions did not arrive.')); return; }
    setPlans((await res.json()).plans ?? []);
    setBizNote('These are suggestions to test, not promises of income.');
  };

  const stepToTask = async (step: string) => {
    await send('/v1/focus/task', 'POST', { title: step, steps: [step] }).catch(() => null);
    setBizNote(`Added to your tasks: ${step}`);
    await refresh();
  };

  // ---- job hunt ----
  const uploadResume = async (file: File | undefined) => {
    if (!file) return;
    setJobNote('Reading your resume…');
    const form = new FormData();
    form.append('file', file, file.name);
    const res = await apiFetch('/v1/focus/jobs/resume', { method: 'POST', body: form }).catch(() => null);
    if (!res?.ok) { setJobNote(await errorDetail(res, 'The resume could not be read.')); return; }
    const out = await res.json() as { titles: string[] };
    setJobTitles(out.titles.join(', '));
    setJobNote(out.titles.length ? 'Resume loaded. I suggested titles from it; change them if you like.' : 'Resume loaded. Add the titles to search for.');
    await refresh();
  };

  const saveJobProfile = async () => {
    const titles = jobTitles.split(',').map((t) => t.trim()).filter(Boolean);
    const floor = Number(jobFloor) || 0;
    const res = await send('/v1/focus/jobs/profile', 'PUT', { titles, min_salary: floor }).catch(() => null);
    setJobNote(res?.ok ? 'Search settings saved.' : 'The settings could not be saved.');
    await refresh();
  };

  const findJobs = async () => {
    setJobBusy(true);
    setJobNote('Searching the web for matching jobs. This can take a few minutes…');
    const res = await send('/v1/focus/jobs/search', 'POST').catch(() => null);
    setJobBusy(false);
    if (!res?.ok) { setJobNote(await errorDetail(res, 'The search did not finish.')); return; }
    const out = await res.json() as { added: number; found: number };
    setJobNote(out.added ? `Added ${out.added} new job${out.added === 1 ? '' : 's'} to your list.` : 'No new matching jobs this time.');
    await refresh();
  };

  const addJobByLink = async (e: FormEvent) => {
    e.preventDefault();
    if (!newJob.title.trim() || !newJob.url.trim()) return;
    const res = await send('/v1/focus/jobs', 'POST', { ...newJob, title: newJob.title.trim(), url: newJob.url.trim() }).catch(() => null);
    if (!res?.ok) { setJobNote(await errorDetail(res, 'That link was not accepted. It must start with https://')); return; }
    setNewJob({ title: '', company: '', url: '' });
    setJobNote('Job added.');
    await refresh();
  };

  // Fill the search boxes from saved settings the first time they load.
  const profileLoaded = useRef(false);
  useEffect(() => {
    if (!state || profileLoaded.current) return;
    profileLoaded.current = true;
    setJobTitles(state.job_profile.titles.join(', '));
    setJobFloor(state.job_profile.min_salary ? String(state.job_profile.min_salary) : '');
  }, [state]);

  const chooseCalendar = async (name: string) => {
    await send('/v1/focus/calendar', 'PUT', { write_to: name }).catch(() => null);
    await refresh();
  };

  const chooseVoice = (next: string) => {
    voiceChoice = next;
    setVoice(next);
    try { localStorage.setItem('focus-voice-choice', next); } catch { /* not remembered */ }
    unlockAudio();
    void speak('This is how I sound. One thing at a time.', 'calm');
  };

  // Falling code behind the header.
  useEffect(() => {
    const cv = rainRef.current;
    const cx = cv?.getContext('2d');
    if (!cv || !cx) return;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const size = 14;
    let drops: number[] = [];
    const fit = () => {
      cv.width = cv.clientWidth;
      cv.height = cv.clientHeight;
      drops = Array.from({ length: Math.ceil(cv.width / size) }, () => Math.random() * -40);
    };
    fit();
    const step = () => {
      cx.fillStyle = 'rgba(1, 10, 12, 0.14)';
      cx.fillRect(0, 0, cv.width, cv.height);
      cx.font = `${size}px ui-monospace, Menlo, monospace`;
      drops.forEach((y, k) => {
        cx.fillStyle = Math.random() < 0.08 ? '#b6ffd6' : '#19c96b';
        cx.fillText(Math.random() < 0.5 ? '0' : '1', k * size, y * size);
        drops[k] = y * size > cv.height && Math.random() < 0.06 ? 0 : y + 1;
      });
    };
    if (still) { for (let n = 0; n < 60; n++) step(); return; }
    const timer = setInterval(step, 90);
    window.addEventListener('resize', fit);
    return () => { clearInterval(timer); window.removeEventListener('resize', fit); };
  }, []);

  const toggleSprint = () => {
    if (!running && left === 0) setLeft(phaseSeconds);
    if (!running) setSay(phase === 'focus' ? 'Sprint started. Just this one step.' : 'Break started. Step away for five.');
    setRunning(!running);
  };

  const choosePhase = (next: 'focus' | 'break') => {
    if (running) return;
    setPhase(next);
    setLeft(next === 'focus' ? FOCUS_SECONDS : BREAK_SECONDS);
  };

  actionsRef.current = {
    startSprint: () => { if (!running) toggleSprint(); },
    stepDone: () => { void stepDone(); },
    startTalk: () => { setSection('spanish'); startTalk('lesson'); },
  };

  // The typed command box does what "Hey Morpheus" does, without the microphone.
  const askTyped = async (e: FormEvent) => {
    e.preventDefault();
    const text = ask.trim();
    if (!text) return;
    setAsk('');
    unlockAudio();
    const res = await send('/v1/focus/command', 'POST', { text, awake: true, pending: askPending.current }).catch(() => null);
    if (!res?.ok) { setSay('I could not reach the server.'); return; }
    const out = await res.json() as { say?: string; action?: string | null; pending?: string };
    askPending.current = out.pending || '';
    if (out.say) { setSay(out.say); void speak(out.say, 'calm'); }
    await refresh();
    if (out.action === 'sprint') actionsRef.current.startSprint();
    if (out.action === 'step_done') actionsRef.current.stepDone();
    if (out.action === 'spanish') actionsRef.current.startTalk();
  };

  const status = running ? 'Focus sprint' : mode === 'deadline' ? 'Overdue' : left < phaseSeconds && left > 0 ? 'Paused' : 'Ready';

  const tasksPanel = (
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
  );
  const capturePanel = (
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
  );
  const goalPanel = state && (
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
            );
  const corePanel = (
            <div className="panel core">
              <div className="orbbox">
                <canvas ref={canvasRef} aria-hidden="true" />
                <div className="orbtext">
                  <div className="timer">{fmtTimer(left)}</div>
                  <div className="state">{status}</div>
                </div>
              </div>
              <div>
                <div className="now-label">Your one next step</div>
                <p className="now">{current ? current.steps[current.step] : 'Nothing open. Add a to-do.'}</p>
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
                <button className="primary" onClick={toggleSprint}>{running ? 'Pause' : left < phaseSeconds && left > 0 ? 'Resume' : phase === 'focus' ? 'Start focus sprint' : 'Start break'}</button>
                <button onClick={() => void stepDone()} disabled={!current}>Step done</button>
              </div>
            </div>
  );
  const spanishPanel = (
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
                            <span><b>{t.role === 'you' ? 'You' : NAME}:</b> {t.text}</span>
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
  );
  const tradesPanel = (
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
  );
  const jobsPanel = (
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
                <p className="note" style={{ margin: 0 }}>No jobs waiting. Use “Find jobs now”, or add one yourself.</p>
              )}
              {state && !state.job_profile.has_resume && (
                <p className="note" style={{ margin: '8px 0 0' }}>Resume not loaded yet, so drafts are off.</p>
              )}
            </div>
  );
  const soon = (title: string, text: string) => (
    <div className="panel">
      <h2>{title}</h2>
      <p className="note" style={{ margin: 0 }}>{text}</p>
    </div>
  );
  // ---- figures the dashboard shows, all from saved data ----
  const todayKey = localDate(now);
  const tasks = state?.tasks ?? [];
  const openTasks = tasks.filter((t) => !t.done);
  const overdueTasks = openTasks.filter((t) => t.overdue);
  const dueToday = openTasks.filter((t) => !t.overdue && t.due?.slice(0, 10) === todayKey);
  const upcoming = openTasks.filter((t) => t.due && t.due.slice(0, 10) > todayKey);
  const doneTasks = tasks.filter((t) => t.done);
  const overdueCount = overdueTasks.length;
  const openCount = openTasks.length;
  const shownTasks = taskTab === 'all' ? openTasks
    : taskTab === 'today' ? [...overdueTasks, ...dueToday]
      : taskTab === 'upcoming' ? upcoming : doneTasks;
  const priorities = [...overdueTasks, ...openTasks.filter((t) => !t.overdue)].slice(0, 3);
  const schedule = openTasks.filter((t) => t.due).sort((a, b) => (a.due ?? '').localeCompare(b.due ?? '')).slice(0, 6);
  const agenda = [
    ...schedule.map((t) => ({ key: `t-${t.id}`, when: t.due ?? '', title: t.title, tag: 'Task', late: t.overdue, allDay: false })),
    ...(cal?.events ?? []).map((e, k) => ({ key: `e-${k}-${e.start}`, when: e.start, title: e.title, tag: e.calendar, late: false, allDay: e.all_day })),
  ].sort((a, b) => new Date(a.when).getTime() - new Date(b.when).getTime()).slice(0, 8);
  const entries = state?.goal.entries ?? [];
  const todayPnl = entries.filter((e) => e.at.slice(0, 10) === todayKey).reduce((sum, e) => sum + e.amount, 0);
  const goalPct = state ? Math.max(0, Math.min(100, (state.goal.saved / state.goal.target) * 100)) : 0;
  const ideas = (state?.captures ?? []).filter((c) => c.kind === 'idea');
  const curve = [...entries].reverse().reduce<number[]>((acc, e) => { acc.push((acc[acc.length - 1] ?? 0) + e.amount); return acc; }, [0]);
  const curveMin = Math.min(...curve);
  const curveMax = Math.max(...curve);
  const curvePoints = curve.map((v, k) => {
    const x = curve.length > 1 ? (k / (curve.length - 1)) * 240 : 0;
    const y = 64 - ((v - curveMin) / (curveMax - curveMin || 1)) * 58;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const draftedJobs = openJobs.filter((j) => j.status === 'drafted').length;
  const listening = wake || talking;
  const headline = talking ? 'Lesson in progress' : wake ? 'Listening…' : 'Ready';

  const setDone = async (task: FocusTask, done: boolean) => {
    await send(`/v1/focus/task/${task.id}/done`, 'POST', { done }).catch(() => null);
    if (done) setSay(`${task.title} is done. Well done.`);
    await refresh();
  };

  // Brain dump: one box, then choose what the thought is.
  const captureDump = async (e: FormEvent) => {
    e.preventDefault();
    const text = dumpText.trim();
    if (!text) return;
    setDumpText('');
    if (dumpKind === 'task' || dumpKind === 'reminder') {
      const res = await send('/v1/focus/command', 'POST', { text, pending: 'task' }).catch(() => null);
      const out = res?.ok ? await res.json() as { say?: string } : null;
      setSay(out?.say || 'I could not save that.');
    } else {
      await send('/v1/focus/capture', 'POST', { text, kind: dumpKind === 'idea' ? 'idea' : 'note' }).catch(() => null);
      setSay(dumpKind === 'idea' ? 'Idea saved.' : 'Saved for later. Back to your step.');
    }
    await refresh();
  };

  const taskRow = (t: FocusTask) => (
    <li key={t.id} className={t.overdue ? 'trow overdue' : 'trow'}>
      <button className={t.done ? 'cb on' : 'cb'} onClick={() => void setDone(t, !t.done)} aria-label={t.done ? `Reopen ${t.title}` : `Mark ${t.title} done`}>{t.done && <Check size={13} />}</button>
      <span className="tmain">
        <span className={t.done ? 'struck' : ''}>{t.title}</span>
        <small>{t.overdue ? `Overdue · ${fmtDue(t.due ?? '')}` : t.due ? fmtDue(t.due) : 'No date'}</small>
      </span>
      {t.overdue && <CircleAlert size={16} className="alert" />}
      <button className="x" onClick={() => void remove(`/v1/focus/task/${t.id}`)} aria-label={`Remove ${t.title}`}><X size={13} /></button>
    </li>
  );

  const ICONS: Record<string, typeof House> = {
    overview: House, focus: Target, tasks: SquareCheck, ideas: Brain, calendar: CalendarDays, trading: ChartCandlestick,
    car: Car, spanish: MessageCircleMore, jobs: BriefcaseBusiness, email: Mail, business: Layers,
  };

  const overview = (
    <>
      <div className="hero">
        <div className="focus-card">
          <Target size={30} />
          <span>
            <b>Focus Mode</b>
            <small>{running ? (phase === 'focus' ? 'Sprint running' : 'On a break') : phase === 'focus' ? 'Deep Work' : 'Break'} • {fmtTimer(left)}</small>
            {current && <small className="next">Next: <em>{current.steps[current.step]}</em> <button className="small" onClick={() => void stepDone()}>Step done</button></small>}
          </span>
          <button className="play" onClick={toggleSprint} aria-label={running ? 'Pause' : 'Start'}>{running ? <Pause size={18} /> : <Play size={18} />}</button>
        </div>
        <div className="orb-wrap">
          <div className="orbbox"><canvas ref={canvasRef} aria-hidden="true" /></div>
          <div className="status-card">
            <button className="listen" onClick={toggleWake} disabled={!sttReady && !wake} title={wake ? `Stop listening for “Hey ${NAME}”` : `Listen for “Hey ${NAME}”`}>
              <span className={listening ? 'led on' : 'led'} />
              <b>{headline}</b>
              {wake ? <Mic size={18} /> : <MicOff size={18} />}
            </button>
            <ul>
              <li><CircleCheck size={15} /> {sttReady ? (wake ? `Say “Hey ${NAME}”` : 'Voice ready: click to listen') : 'Voice is loading'}</li>
              <li><CircleCheck size={15} /> {dueToday.length} due today</li>
              <li className={overdueCount ? 'alert' : ''}><TriangleAlert size={15} /> {overdueCount} overdue task{overdueCount === 1 ? '' : 's'}</li>
            </ul>
          </div>
        </div>
        <div className="quote">
          <p>“Focus on the next right action. Progress compounds.”</p>
          <small>— {NAME.toUpperCase()}</small>
        </div>
      </div>

      <div className="askbar">
        <form className="row" onSubmit={askTyped}>
          <input id="focus-ask" value={ask} onChange={(e) => setAsk(e.target.value)} placeholder={`Tell ${NAME} what you need… e.g. add pick up laundry tomorrow at 3pm`} aria-label={`Message for ${NAME}`} autoComplete="off" />
          <button className="primary" type="submit">Ask {NAME}</button>
        </form>
        <p className="say" aria-live="polite">{say}</p>
        <div className="wake">
          {wakeStatus && <span className="note" aria-live="polite">{wakeStatus}</span>}
          <label className="check"><input id="focus-auto-listen" type="checkbox" checked={autoListen} onChange={(e) => changeAutoListen(e.target.checked)} /> Listen for “Hey {NAME}” when this page opens</label>
        </div>
      </div>

      <div className="cards">
        <section className="card">
          <h2><CalendarDays size={20} /> Today <span>{now.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })}</span></h2>
          <h3>Top 3 Priorities</h3>
          {priorities.length ? <ul className="tlist">{priorities.map(taskRow)}</ul> : <p className="note">Nothing open. Add a task or tell {NAME}.</p>}
          <div className="session">
            <span className="ringlet" />
            <span><small>Focus Session</small><b>{fmtTimer(left)}</b></span>
            <button className="play" onClick={toggleSprint} aria-label={running ? 'Pause sprint' : 'Start sprint'}>{running ? <Pause size={16} /> : <Play size={16} />}</button>
          </div>
          <div className="phases">
            <button className={phase === 'focus' ? 'on' : ''} onClick={() => choosePhase('focus')} disabled={running}><b>25 min</b><small>Focus</small></button>
            <button className={phase === 'break' ? 'on' : ''} onClick={() => choosePhase('break')} disabled={running}><b>5 min</b><small>Break</small></button>
            <span><b>{cycles} cycle{cycles === 1 ? '' : 's'}</b><small>Deep Work today</small></span>
          </div>
        </section>

        <section className="card">
          <h2><SquareCheck size={20} /> Tasks &amp; Reminders {overdueCount > 0 && <span className="badge">{overdueCount} Overdue</span>}</h2>
          <div className="tabs">
            {([['all', `All (${openTasks.length})`], ['today', `Today (${overdueTasks.length + dueToday.length})`], ['upcoming', `Upcoming (${upcoming.length})`], ['done', 'Done']] as const).map(([key, label]) => (
              <button key={key} className={taskTab === key ? 'tab on' : 'tab'} onClick={() => setTaskTab(key)}>{label}</button>
            ))}
          </div>
          {shownTasks.length ? <ul className="tlist scroll">{shownTasks.map(taskRow)}</ul> : <p className="note">Nothing here.</p>}
          <form className="row addrow" onSubmit={addTask}>
            <input id="focus-task" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Add a task" aria-label="New task" autoComplete="off" />
            <input id="focus-due-date" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} aria-label="Due date" />
            <input id="focus-due" type="time" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due time" />
            <button type="submit" disabled={adding} aria-label="Add task"><Plus size={15} /></button>
          </form>
        </section>

        <section className="card">
          <h2><Brain size={20} /> Brain Dump</h2>
          <p className="note">Get it out of your head → into action.</p>
          <form onSubmit={captureDump} className="dump">
            <textarea id="focus-dump" value={dumpText} onChange={(e) => setDumpText(e.target.value)} placeholder="Ideas, tasks, worries, reminders… (type, or say “Hey Morpheus”)" aria-label="Brain dump" rows={2} />
            <div className="chips">
              {([['task', 'Task'], ['idea', 'Idea'], ['reminder', 'Reminder'], ['later', 'Later']] as const).map(([key, label]) => (
                <button type="button" key={key} className={dumpKind === key ? 'chip on' : 'chip'} onClick={() => setDumpKind(key)}>+ {label}</button>
              ))}
            </div>
            <button className="primary wide" type="submit">Capture &amp; Organize <Sparkles size={15} /></button>
          </form>
          <ul className="tlist scroll short">
            {(state?.captures ?? []).slice(0, 4).map((c) => (
              <li key={c.id} className="trow">
                <span className="tmain"><span>{c.text}</span><small>{c.kind === 'idea' ? 'Idea' : 'Later'} · {c.at.slice(11, 16)}</small></span>
                <button className="x" onClick={() => void remove(`/v1/focus/capture/${c.id}`)} aria-label="Remove"><X size={13} /></button>
              </li>
            ))}
          </ul>
        </section>

        <section className="card">
          <h2><CalendarDays size={20} /> Calendar <span>Next up</span></h2>
          {agenda.length ? (
            <ul className="agenda">
              {agenda.map((a) => (
                <li key={a.key} className={a.late ? 'overdue' : ''}>
                  <time>{a.allDay ? new Date(a.when).toLocaleDateString([], { month: 'short', day: 'numeric' }) : fmtDue(a.when)}</time>
                  <span>{a.title}<small>{a.tag}</small></span>
                </li>
              ))}
            </ul>
          ) : <p className="note">Nothing coming up. Say “Hey {NAME}, add … for Friday at 3”.</p>}
          <p className="note foot">{!cal || cal.loading ? 'Loading your Mac calendars. The first time can take a minute or two…' : cal.error ? cal.error : cal.available ? (cal.write_to ? `New dated tasks also go to “${cal.write_to}”.` : 'Showing your Mac calendars.') : 'Calendars can only be read on a Mac.'}</p>
        </section>

        <section className="card">
          <h2><Mail size={20} /> Email {mail && mail.unread > 0 && <span className="badge">{mail.unread}</span>}</h2>
          {!mail || mail.loading ? (
            <p className="note">Loading your mail. The first time can take a minute or two…</p>
          ) : !mail.available ? (
            <p className="note">Mail can only be read on a Mac.</p>
          ) : mail.error ? (
            <p className="note late">{mail.error}</p>
          ) : (
            <>
              <p className="note">{mail.unread} unread{mail.accounts.length ? ` across ${mail.accounts.length} account${mail.accounts.length === 1 ? '' : 's'}` : ''}</p>
              <ul className="tlist scroll">
                {mail.latest.slice(0, 5).map((m, k) => (
                  <li key={`${k}-${m.date}`} className="trow">
                    <span className="tmain"><span>{m.from.replace(/\s*<.*>/, '') || m.from}</span><small>{m.subject}</small></span>
                  </li>
                ))}
              </ul>
            </>
          )}
          <p className="note foot">Read live from the Mail app. Nothing is saved or changed.</p>
        </section>

        <section className="card">
          <h2><ChartCandlestick size={20} /> Futures Trading <span className="badge soft">Manual</span></h2>
          <div className="two">
            <div>
              <small>Today P&amp;L</small>
              <b className={todayPnl < 0 ? 'big late' : 'big gain'}>{todayPnl > 0 ? '+' : ''}{money(todayPnl)}</b>
              <svg className="spark" viewBox="0 0 240 70" preserveAspectRatio="none" aria-hidden="true">
                {curve.length > 1 && <polyline points={curvePoints} fill="none" stroke="currentColor" strokeWidth="2" />}
              </svg>
              {curve.length <= 1 && <small>Record trade results to draw your curve.</small>}
            </div>
            <div>
              <small>Risk per trade</small>
              <b>{money(state?.risk_per_trade ?? 20)}</b>
              <small style={{ marginTop: 6 }}>Current Goal</small>
              <b>{money(state?.goal.saved ?? 0)} <span className="note">/ {money(state?.goal.target ?? 0)}</span></b>
              <div className="bar-track thin"><div className="bar-fill" style={{ width: `${goalPct}%` }} /></div>
            </div>
          </div>
          <form className="row addrow one" onSubmit={addGoalEntry}>
            <input id="focus-goal-amount" type="number" step="0.01" value={goalAmount} onChange={(e) => setGoalAmount(e.target.value)} placeholder="Trade result, e.g. 45 or -20" aria-label="Trade result in dollars" />
            <button type="submit">Record</button>
          </form>
          <p className="note foot"><Car size={14} /> {state?.goal.name ?? 'Car'} Fund Allocation: 100% of recorded results → {state?.goal.name ?? 'Car'} Goal</p>
        </section>

        <section className="card">
          <h2><Car size={20} /> {state?.goal.name ?? 'Car'} Goal</h2>
          <div className="goalline"><b className="big">{money(state?.goal.saved ?? 0)}</b><span>/ {money(state?.goal.target ?? 0)}</span><em>{Math.round(goalPct)}%</em></div>
          <div className="bar-track"><div className="bar-fill" style={{ width: `${goalPct}%` }} /></div>
          <Car className="carart" size={150} strokeWidth={0.8} aria-hidden="true" />
          <div className="kv"><span>Target</span><span>{money(state?.goal.target ?? 0)}</span></div>
          <div className="kv"><span>Saved</span><span>{money(state?.goal.saved ?? 0)}</span></div>
          <div className="kv"><span>From trading today</span><span className={todayPnl < 0 ? 'late' : 'gain'}>{todayPnl > 0 ? '+' : ''}{money(todayPnl)}</span></div>
          <button className="linkbtn" onClick={() => setSection('car')}>Change the goal <ArrowRight size={14} /></button>
        </section>

        <section className="card">
          <h2><MessageCircleMore size={20} /> Spanish Coach</h2>
          <div className="two">
            <div className="streak"><Flame size={20} /><b>{lesson?.progress.day_streak ?? 0}</b><small>Day Streak</small></div>
            <div>
              <small>Today’s Lesson</small>
              <b>{lesson?.progress.unit ?? '…'}</b>
              <small>{lesson ? `${lesson.progress.words_mastered}/${lesson.progress.words_total} words learned` : ''}</small>
              <small>{lesson ? `~ ${Math.max(0, lesson.progress.goal_minutes - lesson.progress.minutes_today)} min left today` : ''}</small>
            </div>
          </div>
          {talkStatus && <p className="note" aria-live="polite">{talkStatus}</p>}
          {talking
            ? <button className="primary wide" onClick={stopTalk}>Stop lesson</button>
            : <button className="primary wide" onClick={() => startTalk('lesson')} disabled={micBusy || !sttReady}><Mic size={16} /> Start Speaking Practice</button>}
          <button className="linkbtn" onClick={() => setSection('spanish')}>Open the cards <ArrowRight size={14} /></button>
        </section>

        <section className="card">
          <h2><BriefcaseBusiness size={20} /> Job Hunt</h2>
          <div className="kv row2"><span><FileText size={16} /> Applications Sent</span><b>{sentJobs}</b></div>
          <div className={draftedJobs ? 'kv row2 warnrow' : 'kv row2'}><span><Clock3 size={16} /> Drafts to Review</span><b>{draftedJobs}</b></div>
          <div className="kv row2"><span><Star size={16} /> New Opportunities</span><b>{openJobs.length - draftedJobs}</b></div>
          <button className="wide" onClick={() => setSection('jobs')}>Find &amp; Apply <ArrowRight size={15} /></button>
        </section>

        <section className="card">
          <h2><Layers size={20} /> Build a Business</h2>
          <small>Idea Queue ({ideas.length})</small>
          {ideas.length ? (
            <ul className="tlist short">{ideas.slice(0, 3).map((c) => <li key={c.id} className="trow"><Lightbulb size={14} /><span className="tmain"><span>{c.text}</span></span></li>)}</ul>
          ) : <p className="note">Save a thought as an Idea in Brain Dump and it lands here.</p>}
          <div className="kv row2"><span>Next Step</span><span className="note">{plans[0]?.steps[0] ?? 'Open Business Builder'}</span></div>
          <button className="wide" onClick={() => setSection('business')}>Open Business Builder <ArrowRight size={15} /></button>
        </section>
      </div>
    </>
  );

  return (
    <div className="focus-hud" data-mode={mode} data-section={section} data-voice={listening ? 'on' : 'off'} ref={rootRef}>
      <canvas className="rain" ref={rainRef} aria-hidden="true" />
      <header className="hdr">
        <div className="brand">
          <span className="ring" />
          <div><div className="name">{NAME.toUpperCase()}</div><div className="tagname">Personal Command Center</div></div>
          <span className="tagline">Your AI partner for focus, growth, and freedom</span>
        </div>
        <div className="hdr-right">
          {soundOff && <span className="late">Sound is off: click anywhere to turn it on</span>}
          {voiceProblem && <span className="late">{voiceProblem}</span>}
          {offline && <span className="late">Server not reachable</span>}
          <div className="clock2"><b>{now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</b><small>{now.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })}</small></div>
          <button className="bell" onClick={() => setSection('tasks')} aria-label={`${overdueCount} overdue tasks`}><Bell size={22} />{overdueCount > 0 && <i>{overdueCount}</i>}</button>
          <Link className="bell" to="/settings" aria-label="Settings"><CircleUser size={30} /></Link>
        </div>
      </header>

      <div className="body">
        <aside className="sidebar">
          <nav className="navlist">
            {NAV.map(([key, label]) => {
              const Icon = ICONS[key];
              return (
                <button key={key} className={section === key ? 'nav active' : 'nav'} onClick={() => setSection(key)}>
                  <Icon size={20} /> <span>{label}</span>
                  {key === 'tasks' && overdueCount > 0 && <i>{overdueCount}</i>}
                </button>
              );
            })}
            <hr />
            <Link className="nav" to="/settings"><Settings size={20} /> <span>Settings</span></Link>
            <Link className="nav" to="/"><MessagesSquare size={20} /> <span>Chat</span></Link>
          </nav>
          <label className="voicepick">
            <small>Voice</small>
            <select id="focus-voice" value={voice} onChange={(e) => chooseVoice(e.target.value)}>
              {VOICES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
          </label>
          <div className="keybox">
            <small>Online model</small>
            <span className="keyline"><i className={online?.configured ? 'led on' : 'led'} /> {online?.configured ? 'Key saved' : 'No key yet'}</span>
            {keyOpen ? (
              <form onSubmit={saveKey}>
                <input id="focus-key" type="password" value={keyText} onChange={(e) => setKeyText(e.target.value)} placeholder="sk-ant-…" aria-label="Anthropic API key" autoComplete="off" spellCheck={false} />
                <div className="row">
                  <button className="small primary" type="submit">Save</button>
                  <button className="small" type="button" onClick={() => { setKeyOpen(false); setKeyText(''); }}>Cancel</button>
                </div>
              </form>
            ) : (
              <div className="row">
                <button className="small" onClick={() => { setKeyOpen(true); setKeyNote(''); }}>{online?.configured ? 'Replace key' : 'Add key'}</button>
                {online?.configured && <button className="small" onClick={() => void testKey()}>Test</button>}
                {online?.configured && <button className="small" onClick={() => void removeKey()}>Remove</button>}
              </div>
            )}
            {keyNote && <small aria-live="polite">{keyNote}</small>}
          </div>
          <p className="note">Tasks, notes and voice stay on this Mac. {BUILD}</p>
          <svg className="chair" viewBox="0 0 260 240" aria-hidden="true">
            <defs>
              <radialGradient id="leather" cx="45%" cy="30%" r="80%">
                <stop offset="0" stopColor="#8e1f1c" /><stop offset="0.55" stopColor="#5a100f" /><stop offset="1" stopColor="#210505" />
              </radialGradient>
              <linearGradient id="arm" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stopColor="#9c2420" /><stop offset="0.5" stopColor="#5e1110" /><stop offset="1" stopColor="#1c0404" />
              </linearGradient>
              <pattern id="tuft" width="44" height="44" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <path d="M0 0H44M0 0V44" stroke="#1a0303" strokeWidth="3" opacity="0.55" />
                <circle cx="0" cy="0" r="3.4" fill="#170202" /><circle cx="44" cy="0" r="3.4" fill="#170202" />
                <circle cx="0" cy="44" r="3.4" fill="#170202" /><circle cx="44" cy="44" r="3.4" fill="#170202" />
              </pattern>
            </defs>
            <path d="M18 240V96C18 40 62 10 130 10s112 30 112 86v144z" fill="url(#leather)" />
            <path d="M18 240V96C18 40 62 10 130 10s112 30 112 86v144z" fill="url(#tuft)" />
            <path d="M18 96C18 40 62 10 130 10s112 30 112 86" fill="none" stroke="#b33a33" strokeWidth="3" opacity="0.5" />
            <path d="M0 240V168c0-26 16-40 38-40s34 14 34 40v72z" fill="url(#arm)" />
            <path d="M260 240V168c0-26-16-40-38-40s-34 14-34 40v72z" fill="url(#arm)" />
            <ellipse cx="36" cy="140" rx="24" ry="9" fill="#c2463e" opacity="0.28" />
            <ellipse cx="224" cy="140" rx="24" ry="9" fill="#c2463e" opacity="0.18" />
            <path d="M66 240v-34c0-12 10-18 22-18h84c12 0 22 6 22 18v34z" fill="#4a0d0c" />
            <path d="M70 196c12-6 108-6 120 0" fill="none" stroke="#a5332d" strokeWidth="2.5" opacity="0.45" />
          </svg>
        </aside>

        <main className="main">
          {section === 'overview' && overview}
          {section !== 'overview' && (
            <div className="askbar">
              <form className="row" onSubmit={askTyped}>
                <input id="focus-ask" value={ask} onChange={(e) => setAsk(e.target.value)} placeholder={`Tell ${NAME} what you need…`} aria-label={`Message for ${NAME}`} autoComplete="off" />
                <button className="primary" type="submit">Ask {NAME}</button>
                <button type="button" className={wake ? 'primary' : ''} onClick={toggleWake} disabled={!sttReady && !wake}>{wake ? <Mic size={15} /> : <MicOff size={15} />} Hey {NAME}: {wake ? 'on' : 'off'}</button>
              </form>
              <p className="say" aria-live="polite">{say}</p>
            </div>
          )}
          {section === 'focus' && <div className="grid12"><div className="span4">{corePanel}</div><div className="span8">{tasksPanel}</div></div>}
          {section === 'tasks' && <div className="grid12"><div className="span8">{tasksPanel}</div><div className="span4">{corePanel}</div></div>}
          {section === 'ideas' && <div className="grid12"><div className="span12">{capturePanel}</div></div>}
          {section === 'spanish' && <div className="grid12"><div className="span8">{spanishPanel}</div></div>}
          {section === 'trading' && <div className="grid12"><div className="span6">{tradesPanel}</div><div className="span6">{goalPanel}</div></div>}
          {section === 'car' && <div className="grid12"><div className="span8">{goalPanel}</div></div>}
          {section === 'jobs' && (
            <div className="grid12">
              <div className="span8">{jobsPanel}</div>
              <div className="span4">
                <div className="panel">
                  <h2>Find jobs</h2>
                  <label className="voicepick" style={{ padding: 0 }}>
                    <small>{state?.job_profile.has_resume ? 'Resume loaded. Choose a file to replace it.' : 'Your resume (Word or text file)'}</small>
                    <input id="focus-resume" type="file" accept=".docx,.txt,.md" onChange={(e) => void uploadResume(e.target.files?.[0])} />
                  </label>
                  <label className="voicepick" style={{ padding: '10px 0 0' }}>
                    <small>Job titles to search for, separated by commas</small>
                    <input id="focus-job-titles" value={jobTitles} onChange={(e) => setJobTitles(e.target.value)} placeholder="Process Improvement Manager, Change Manager" autoComplete="off" />
                  </label>
                  <label className="voicepick" style={{ padding: '10px 0 0' }}>
                    <small>Lowest yearly pay to accept ($)</small>
                    <input id="focus-job-floor" type="number" min="0" step="1000" value={jobFloor} onChange={(e) => setJobFloor(e.target.value)} placeholder="65000" />
                  </label>
                  <div className="row" style={{ marginTop: 10 }}>
                    <button onClick={() => void saveJobProfile()}>Save</button>
                    <button className="primary" onClick={() => void findJobs()} disabled={jobBusy || !online?.configured}>{jobBusy ? 'Searching…' : 'Find jobs now'}</button>
                  </div>
                  {!online?.configured && <p className="note" style={{ marginTop: 8 }}>Searching needs the online model key.</p>}
                  {jobNote && <p className="note" style={{ marginTop: 8 }} aria-live="polite">{jobNote}</p>}
                  <h2 style={{ marginTop: 16 }}>Add a job yourself</h2>
                  <form className="voicepick" style={{ padding: 0 }} onSubmit={addJobByLink}>
                    <input id="focus-newjob-title" value={newJob.title} onChange={(e) => setNewJob({ ...newJob, title: e.target.value })} placeholder="Job title" aria-label="Job title" autoComplete="off" />
                    <input id="focus-newjob-company" value={newJob.company} onChange={(e) => setNewJob({ ...newJob, company: e.target.value })} placeholder="Company" aria-label="Company" autoComplete="off" />
                    <input id="focus-newjob-url" value={newJob.url} onChange={(e) => setNewJob({ ...newJob, url: e.target.value })} placeholder="https://… link to the posting" aria-label="Link to the posting" autoComplete="off" />
                    <button type="submit">Add to list</button>
                  </form>
                </div>
              </div>
            </div>
          )}
          {section === 'calendar' && (
            <div className="grid12">
              <div className="span8">{tasksPanel}</div>
              <div className="span4">
                <div className="panel">
                  <h2>Mac calendars</h2>
                  {!cal || cal.loading ? (
                    <p className="note">Loading your Mac calendars. The first time can take a minute or two…</p>
                  ) : !cal.available ? (
                    <p className="note">Calendars can only be read on a Mac.</p>
                  ) : (
                    <>
                      {cal.error && <p className="fb bad" style={{ textAlign: 'left' }}>{cal.error}</p>}
                      <p className="note">{cal.events.length} upcoming event{cal.events.length === 1 ? '' : 's'} from the Calendar app{cal.source === 'database' ? ', including repeating ones' : ''}.</p>
                      {cal.note && <p className="note">{cal.note}</p>}
                      <label className="voicepick" style={{ padding: '10px 0 0' }}>
                        <small>Also put my dated tasks on</small>
                        <select id="focus-calendar" value={cal.write_to} onChange={(e) => void chooseCalendar(e.target.value)}>
                          <option value="">No calendar (tasks only)</option>
                          {cal.calendars.map((name) => <option key={name} value={name}>{name}</option>)}
                        </select>
                      </label>
                      <ul style={{ marginTop: 10 }}>
                        {cal.events.slice(0, 12).map((e, k) => (
                          <li key={`${k}-${e.start}`}>
                            <span>{e.title}</span>
                            <span className="m">{e.all_day ? new Date(e.start).toLocaleDateString([], { month: 'short', day: 'numeric' }) : fmtDue(e.start)} · {e.calendar}</span>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              </div>
            </div>
          )}
          {section === 'email' && (
            <div className="grid12"><div className="span8">
              <div className="panel">
                <h2>Email <span>{mail?.available && !mail.error ? `${mail.unread} unread` : ''}</span></h2>
                {!mail || mail.loading ? <p className="note">Loading your mail…</p> : !mail.available ? <p className="note">Mail can only be read on a Mac.</p> : mail.error ? <p className="fb bad" style={{ textAlign: 'left' }}>{mail.error}</p> : (
                  <>
                    {mail.accounts.map((a) => <div className="kv" key={a.name}><span>{a.name}</span><span>{a.unread} unread</span></div>)}
                    <ul style={{ marginTop: 10 }}>
                      {mail.latest.map((m, k) => (
                        <li key={`${k}-${m.date}`}>
                          <span><b>{m.from.replace(/\s*<.*>/, '') || m.from}</b> · {m.subject}</span>
                          <span className="m">{m.account}</span>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                <p className="note" style={{ marginTop: 10 }}>Read live from the Mac's Mail app. Nothing is saved, moved or deleted. Sorting and clean-up are not built yet.</p>
              </div>
            </div></div>
          )}
          {section === 'business' && (
            <div className="grid12"><div className="span8">
              <div className="panel">
                <h2>Business Builder <span>{ideas.length} idea{ideas.length === 1 ? '' : 's'} saved</span></h2>
                <p className="note">Suggestions come from your resume and the ideas you have saved in Brain Dump.</p>
                <div className="row" style={{ margin: '10px 0' }}>
                  <button className="primary" onClick={() => void suggestBusinesses()} disabled={bizBusy || !online?.configured}>{bizBusy ? 'Thinking…' : plans.length ? 'Suggest again' : 'Suggest businesses for me'}</button>
                </div>
                {!online?.configured && <p className="note">This needs the online model key.</p>}
                {bizNote && <p className="note" aria-live="polite">{bizNote}</p>}
                {plans.map((plan) => (
                  <div key={plan.name} className="plan">
                    <b>{plan.name}</b>
                    <p className="note">{plan.why}</p>
                    <ul>
                      {plan.steps.map((step) => (
                        <li key={step}><span>{step}</span><button className="small" onClick={() => void stepToTask(step)}>Add as task</button></li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </div></div>
          )}
        </main>
      </div>

      <footer className="statusbar">
        <span><i className={offline ? 'led' : 'led on'} /><b>System Status</b><small>{offline ? 'Server offline' : 'All systems ready'}</small></span>
        <span><Target size={20} /><b>Focus</b><small>{running ? `Sprint ${fmtTimer(left)}` : 'Available'}</small></span>
        <span><SquareCheck size={20} /><b>Tasks</b><small>{doneTasks.length} / {tasks.length} done</small></span>
        <span><ChartNoAxesColumn size={20} /><b>Trading</b><small className={todayPnl < 0 ? 'late' : 'gain'}>{todayPnl > 0 ? '+' : ''}{money(todayPnl)} today</small></span>
        <span><Car size={20} /><b>{state?.goal.name ?? 'Car'} Goal</b><small>{Math.round(goalPct)}% to target</small></span>
        <span><GraduationCap size={20} /><b>Spanish</b><small>{lesson?.progress.day_streak ?? 0}-day streak</small></span>
        <span><BriefcaseBusiness size={20} /><b>Job Hunt</b><small>{openJobs.length} waiting</small></span>
        <span><Layers size={20} /><b>Business</b><small>{ideas.length} ideas</small></span>
      </footer>
    </div>
  );
}
