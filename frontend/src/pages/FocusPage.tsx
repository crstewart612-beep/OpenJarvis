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
interface FocusCapture { id: string; text: string; at: string }
interface FocusState {
  tasks: FocusTask[];
  captures: FocusCapture[];
  current: FocusTask | null;
  mode: 'calm' | 'deadline';
  risk_per_trade: number;
}
interface SizingRow { symbol: string; point_value: number; max_stop_points: number }

const SPRINT_SECONDS = 15 * 60;
const CALM_LINE = "Take a breath. One thing at a time. I'm right here with you.";

const fmtTimer = (s: number) =>
  `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
const fmtDue = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

async function send(path: string, method: string, body?: unknown): Promise<Response> {
  return apiFetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
}

let audio: HTMLAudioElement | null = null;
async function speak(text: string, mode: 'calm' | 'deadline'): Promise<void> {
  try {
    const res = await send('/v1/focus/speak', 'POST', { text, mode });
    if (!res.ok) return;
    const url = URL.createObjectURL(await res.blob());
    audio?.pause();
    audio = new Audio(url);
    audio.onended = () => URL.revokeObjectURL(url);
    await audio.play();
  } catch {
    // Voice is optional: the screen still shows the same words.
  }
}

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
  const [capture, setCapture] = useState('');
  const [adding, setAdding] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const leftRef = useRef(left);
  const lastMode = useRef<string>('calm');
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
    if (due) {
      const d = new Date();
      const [h, m] = due.split(':').map(Number);
      d.setHours(h, m, 0, 0);
      dueIso = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    }
    const res = await send('/v1/focus/task', 'POST', { title: t, due: dueIso }).catch(() => null);
    setAdding(false);
    if (!res?.ok) { setSay('I could not save that task. Is the Jarvis server running?'); return; }
    setTitle(''); setDue('');
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

  const toggleSprint = () => {
    if (!running && left === 0) setLeft(SPRINT_SECONDS);
    if (!running) setSay('Sprint started. Just this one step.');
    setRunning(!running);
  };

  const status = running ? 'Focus sprint' : mode === 'deadline' ? 'Overdue' : left < SPRINT_SECONDS && left > 0 ? 'Paused' : 'Ready';

  return (
    <div className="focus-hud" data-mode={mode} ref={rootRef}>
      <div className="wrap">
        <div className="top">
          <span className="brand">FOCUS HUD</span>
          <span><span className={offline ? 'dot off' : 'dot'} />Jarvis server: <b>{offline ? 'not reachable' : 'connected'}</b></span>
          <span>Tasks, notes and voice stay on this Mac</span>
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
              <h2>Today <span>{state ? `${state.tasks.filter((t) => !t.done).length} open` : ''}</span></h2>
              <form className="row" onSubmit={addTask}>
                <input id="focus-task" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Add a to-do" aria-label="New to-do" autoComplete="off" />
                <input id="focus-due" type="time" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due time today" />
                <button type="submit" disabled={adding}>{adding ? '…' : 'Add'}</button>
              </form>
              <ul style={{ marginTop: 10 }}>
                {state?.tasks.map((t) => (
                  <li key={t.id}>
                    <span className={t.done ? 'struck' : t.overdue ? 'late' : ''}>{t.title}</span>
                    <span className="end">
                      <span className={`pill ${t.overdue ? 'hot' : 'ok'}`}>{t.overdue ? 'overdue' : t.done ? 'done' : t.due ? fmtDue(t.due) : 'today'}</span>
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
                    <span>{c.text}</span>
                    <span className="end">
                      <span className="m">{c.at.slice(11, 16)}</span>
                      <button className="small" onClick={() => void remove(`/v1/focus/capture/${c.id}`)}>Done</button>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
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
            </div>
          </div>

          <div className="col">
            <div className="panel">
              <h2>Trade sizing <span>you place every trade</span></h2>
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
              <p className="note" style={{ margin: '10px 0 0' }}>Live market feed: not connected yet, so no entry or exit calls</p>
            </div>
            <div className="panel">
              <h2>Job hunt</h2>
              <p className="note" style={{ margin: 0 }}>Not connected yet</p>
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
