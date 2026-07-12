"""Local live dashboard: watch delegations in a browser, zero supervisor tokens.

The MCP server (already resident for the whole Claude Code session) starts a
tiny stdlib HTTP server on 127.0.0.1 and streams task events over SSE:

- ``GET /``        one-page dashboard (inline HTML/CSS/JS, no CDN);
- ``GET /tasks``   JSON snapshot of every job this server knows;
- ``GET /events``  Server-Sent Events: recent history replay, then live feed.

Progress therefore reaches the USER's eyes without a single supervisor
token: the model only needs get_task_status at decision points, while the
human watches every shell command live.

Default port 45673 so the URL is stable across sessions; falls back to an
ephemeral port when busy (second concurrent session). The bound URL is
echoed in run_dev_task / get_task_status responses and written to
``~/.cc-delegate/dashboard.json``.
"""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import events

DEFAULT_PORT = 45673

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cc-delegate — live</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #121214; color: #d6d6d9; font: 14px/1.5 ui-monospace, 'Cascadia Code', Consolas, monospace; }
  header { padding: 14px 20px; border-bottom: 1px solid #2a2a2e; display: flex; gap: 10px; align-items: baseline; }
  header b { color: #cd7b5b; font-size: 16px; }
  header span { color: #77777d; font-size: 12px; }
  main { max-width: 980px; margin: 0 auto; padding: 16px 20px 60px; }
  .task { border: 1px solid #2a2a2e; border-radius: 8px; margin: 14px 0; overflow: hidden; }
  .task-head { display: flex; gap: 12px; align-items: center; padding: 10px 14px; background: #19191c; flex-wrap: wrap; }
  .tid { color: #d9b06c; }
  .chip { font-size: 11px; padding: 2px 9px; border-radius: 999px; border: 1px solid transparent; }
  .chip.running     { color: #7ec2f2; border-color: #2b4a63; background: #16222d; }
  .chip.needs_input { color: #e8c46a; border-color: #6b5a26; background: #2b2413; animation: pulse 1.2s infinite; }
  .chip.succeeded   { color: #7eba78; border-color: #33512f; background: #16220f; }
  .chip.failed, .chip.timeout { color: #e07a6e; border-color: #63302b; background: #2d1614; }
  .chip.cancelled   { color: #9a9aa0; border-color: #3a3a3e; background: #1d1d20; }
  @keyframes pulse { 50% { opacity: .55; } }
  .meta { color: #77777d; font-size: 12px; margin-left: auto; }
  .question { padding: 10px 14px; background: #2b2413; color: #e8c46a; border-top: 1px solid #4a3f1c; white-space: pre-wrap; }
  .log { max-height: 300px; overflow-y: auto; padding: 8px 14px; }
  .log div { white-space: pre-wrap; word-break: break-word; }
  .t   { color: #55555b; margin-right: 8px; }
  .k-shell    { color: #7ec2f2; }
  .k-report   { color: #7eba78; }
  .k-question, .k-blocker { color: #e8c46a; }
  .k-answer   { color: #b48ec9; }
  .k-succeeded { color: #7eba78; font-weight: bold; }
  .k-failed, .k-timeout { color: #e07a6e; font-weight: bold; }
  .k-cancelled { color: #9a9aa0; }
  #empty { color: #77777d; text-align: center; padding: 60px 0; }
</style></head><body>
<header><b>cc-delegate</b><span>live delegation feed — this page costs zero supervisor tokens</span>
<span id="conn" style="margin-left:auto">connecting…</span></header>
<main><div id="tasks"></div><div id="empty">No delegation yet in this session.</div></main>
<script>
const tasksEl = document.getElementById('tasks'), emptyEl = document.getElementById('empty');
const cards = {};
function esc(s) { const d = document.createElement('span'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
function card(id) {
  if (cards[id]) return cards[id];
  emptyEl.style.display = 'none';
  const el = document.createElement('div'); el.className = 'task';
  el.innerHTML = `<div class="task-head"><span class="tid">${esc(id)}</span>` +
    `<span class="chip running" data-chip>running</span><span class="meta" data-meta></span></div>` +
    `<div class="question" data-q style="display:none"></div><div class="log" data-log></div>`;
  tasksEl.prepend(el);
  return cards[id] = el;
}
function setStatus(el, s) { const c = el.querySelector('[data-chip]'); c.textContent = s; c.className = 'chip ' + s; }
function upsertTask(t) {
  const el = card(t.taskId || t.task_id);
  if (t.status) setStatus(el, t.status);
  const bits = [];
  if (t.progress) bits.push(t.progress);
  if (t.costUsd != null) bits.push('$' + Number(t.costUsd).toFixed(3));
  el.querySelector('[data-meta]').innerHTML = esc(bits.join(' · '));
  const q = el.querySelector('[data-q]');
  if (t.status === 'needs_input' && t.question) { q.style.display = ''; q.textContent = '⚠ worker asks: ' + t.question.message; }
  else q.style.display = 'none';
}
function addEvent(e) {
  const el = card(e.task_id);
  const log = el.querySelector('[data-log]');
  const kind = e.kind || 'progress';
  const when = new Date(e.ts * 1000).toLocaleTimeString();
  let text = e.note || e.message || e.error || e.command || '';
  if (kind === 'shell' && e.command) text = '$ ' + e.command;
  if (kind === 'succeeded') text = '✓ succeeded' + (e.cost_usd != null ? ` — $${Number(e.cost_usd).toFixed(3)}` : '');
  if (kind === 'failed') text = '✗ failed — ' + (e.error || '');
  if (kind === 'timeout') text = '✗ timeout — ' + (e.error || '');
  if (kind === 'cancelled') text = '⊘ ' + (e.error || 'cancelled');
  if (kind === 'question' || kind === 'blocker') text = '? ' + (e.message || '');
  if (kind === 'answer') text = '↩ supervisor: ' + (e.answer || '');
  const row = document.createElement('div');
  row.innerHTML = `<span class="t">${esc(when)}</span><span class="k-${esc(kind)}">${esc(text)}</span>`;
  log.appendChild(row);
  while (log.childNodes.length > 400) log.removeChild(log.firstChild);
  log.scrollTop = log.scrollHeight;
  if (['succeeded','failed','timeout','cancelled','question','blocker'].includes(kind))
    setStatus(el, kind === 'question' || kind === 'blocker' ? 'needs_input' : kind);
  refreshTasks();
}
let refreshTimer = null;
function refreshTasks() {
  if (refreshTimer) return;
  refreshTimer = setTimeout(() => { refreshTimer = null;
    fetch('/tasks').then(r => r.json()).then(list => list.forEach(upsertTask)).catch(() => {});
  }, 300);
}
refreshTasks();
const es = new EventSource('/events');
es.onopen = () => document.getElementById('conn').textContent = '● live';
es.onerror = () => document.getElementById('conn').textContent = '○ reconnecting…';
es.onmessage = m => { try { addEvent(JSON.parse(m.data)); } catch (e) {} };
</script></body></html>
"""


def _make_handler(snapshot_fn: Callable[[], list[dict[str, Any]]]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # stdio is the MCP channel; stay silent
            pass

        def _send(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, "text/html; charset=utf-8", _PAGE.encode("utf-8"))
            elif path == "/tasks":
                body = json.dumps(snapshot_fn()).encode("utf-8")
                self._send(200, "application/json", body)
            elif path == "/events":
                self._stream_events()
            else:
                self._send(404, "text/plain", b"not found")

        def _stream_events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

            def emit(obj: dict[str, Any]) -> None:
                self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
                self.wfile.flush()

            q = events.subscribe()
            try:
                # Replay recent history for tasks the server knows, oldest
                # first, so a freshly opened page isn't blank mid-run.
                replayed = []
                for job in snapshot_fn():
                    if job.get("repo") and job.get("workDir"):
                        replayed.extend(
                            events.read_log(job["repo"], job["taskId"], job["workDir"], limit=100)
                        )
                replayed.sort(key=lambda e: e.get("ts", 0))
                for ev in replayed[-300:]:
                    emit(ev)
                while True:
                    try:
                        emit(q.get(timeout=15))
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionError, OSError):
                pass  # client went away — normal lifecycle
            finally:
                events.unsubscribe(q)

    return Handler


def start_dashboard(snapshot_fn: Callable[[], list[dict[str, Any]]]) -> str | None:
    """Start the dashboard thread; returns its URL, or None when disabled/failed."""
    import os

    if os.environ.get("DELEGATE_DASHBOARD", "1") == "0":
        return None
    handler = _make_handler(snapshot_fn)
    server = None
    for port in (int(os.environ.get("DELEGATE_DASHBOARD_PORT", DEFAULT_PORT)), 0):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), handler)
            break
        except OSError:
            continue
    if server is None:
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="cc-delegate-dashboard").start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        info_path = Path.home() / ".cc-delegate" / "dashboard.json"
        info_path.parent.mkdir(parents=True, exist_ok=True)
        info_path.write_text(json.dumps({"url": url, "pid": os.getpid()}), encoding="utf-8")
    except OSError:
        pass
    return url
