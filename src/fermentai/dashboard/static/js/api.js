// REST + SSE client for the local FermentAI API (same origin; never contacts anything else).

const TOKEN_KEY = "fermentai.token";
export const API = "/api/v1";

// ------------------------------------------------------------------ token
function readStoredToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
let token = readStoredToken();

/** Read ?token= once, persist it, and strip it from the address bar. */
export function captureTokenFromURL() {
  const url = new URL(window.location.href);
  const t = url.searchParams.get("token");
  if (t) {
    setToken(t);
    url.searchParams.delete("token");
    try { history.replaceState(null, "", url.pathname + (url.search ? url.search : "") + url.hash); } catch { /* ignore */ }
  }
  return token;
}
export function getToken() { return token; }
export function setToken(t) {
  token = (t || "").trim();
  try { token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ }
  document.dispatchEvent(new CustomEvent("fermentai:token", { detail: { hasToken: !!token } }));
}

// ------------------------------------------------------------------ fetch helpers
export class ApiError extends Error {
  constructor(status, detail, path) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status; this.detail = detail; this.path = path;
  }
}

async function request(method, path, body, { timeoutMs = 20000, raw = false } = {}) {
  const headers = { Accept: raw ? "*/*" : "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && token) headers.Authorization = `Bearer ${token}`;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(path.startsWith("/") ? path : `${API}/${path}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctl.signal, cache: "no-store",
    });
  } catch (e) {
    throw new ApiError(0, e.name === "AbortError" ? "request timed out" : `network error: ${e.message}`, path);
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = j.detail ?? j; } catch { /* not json */ }
    if (res.status === 401 || res.status === 403) {
      document.dispatchEvent(new CustomEvent("fermentai:auth-failed", { detail: { status: res.status } }));
    }
    throw new ApiError(res.status, detail, path);
  }
  if (raw) return res.text();
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}

export const get = (path, opts) => request("GET", path, undefined, opts);
export const getText = (path, opts) => request("GET", path, undefined, { ...opts, raw: true });
export const post = (path, body = {}, opts) => request("POST", path, body, opts);
export const del = (path, opts) => request("DELETE", path, undefined, opts);

// ------------------------------------------------------------------ SSE with reconnect + backoff
/**
 * EventSource wrapper. We manage reconnection ourselves (instead of the browser's built-in retry)
 * so the resume point is always `since=<last id seen>`; duplicate ids are dropped.
 */
export class EventStream {
  constructor(types) {
    this.types = types;
    this.handlers = new Map();
    this.lastId = null;
    this.seen = new Set();
    this.attempt = 0;
    this.es = null;
    this.timer = null;
    this.state = "connecting";
    this.lastMessageAt = 0;
  }
  on(type, fn) {
    if (!this.handlers.has(type)) this.handlers.set(type, []);
    this.handlers.get(type).push(fn);
    return this;
  }
  emit(type, payload) { (this.handlers.get(type) || []).forEach((fn) => { try { fn(payload); } catch (e) { console.error(e); } }); }
  setState(s, info) { this.state = s; this.emit("_state", { state: s, attempt: this.attempt, ...info }); }

  connect() {
    clearTimeout(this.timer);
    if (this.es) { this.es.close(); this.es = null; }
    const qs = new URLSearchParams();
    if (this.lastId !== null) qs.set("since", String(this.lastId));
    let es;
    try {
      const base = "/api/v1/events/stream";
      es = new EventSource(qs.toString() ? `${base}?${qs}` : base);
    } catch (e) {
      this.scheduleReconnect();
      return;
    }
    this.es = es;
    this.setState(this.attempt ? "reconnecting" : "connecting");
    es.onopen = () => { this.attempt = 0; this.lastMessageAt = Date.now(); this.setState("connected"); };
    es.onerror = () => {
      // Close and resume ourselves from lastId (the server ignores Last-Event-ID when ?since is set).
      es.close();
      if (this.es === es) this.es = null;
      this.scheduleReconnect();
    };
    for (const t of this.types) {
      es.addEventListener(t, (ev) => this.handleFrame(t, ev));
    }
    es.onmessage = (ev) => this.handleFrame("message", ev); // ?format=plain fallback (unused)
  }
  handleFrame(type, ev) {
    this.lastMessageAt = Date.now();
    let frame;
    try { frame = JSON.parse(ev.data); } catch { return; }
    const id = Number(frame.id ?? ev.lastEventId);
    if (Number.isFinite(id)) {
      if (this.seen.has(id)) return;
      this.seen.add(id);
      if (this.seen.size > 5000) this.seen = new Set([...this.seen].slice(-2500));
      this.lastId = Math.max(this.lastId ?? 0, id);
    }
    this.emit(frame.type || type, frame);
    this.emit("*", frame);
  }
  scheduleReconnect() {
    this.attempt += 1;
    const delay = Math.min(30000, 1000 * 2 ** Math.min(this.attempt - 1, 5)) * (0.75 + Math.random() * 0.5);
    this.setState("reconnecting", { retryInMs: Math.round(delay) });
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.connect(), delay);
  }
  close() { clearTimeout(this.timer); if (this.es) this.es.close(); this.es = null; this.setState("closed"); }
}
