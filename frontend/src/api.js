// Every request checks response.ok and turns failures into a readable message.
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body, timeoutMs = 60000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
  } catch (e) {
    throw new ApiError(
      e.name === "AbortError" ? "The server took too long to answer. Try again." : "Can't reach the Opening Act server. Check your connection and try again.",
      0
    );
  } finally {
    clearTimeout(timer);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!res.ok) {
    let msg = data?.detail;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg?.replace(/^Value error, /, "")).join("; ");
    throw new ApiError(msg || `Request failed (HTTP ${res.status})`, res.status);
  }
  return data;
}

export const api = {
  health: () => request("GET", "/api/health"),
  createBrief: (b) => request("POST", "/api/briefs", b),
  getBrief: (id) => request("GET", `/api/briefs/${id}`),
  confirm: (id, qlooId) => request("POST", `/api/briefs/${id}/confirm`, { qloo_id: qlooId }),
  negotiate: (id, payload) => request("POST", `/api/briefs/${id}/negotiate`, payload),
  finalize: (id) => request("POST", `/api/briefs/${id}/finalize`, null, 90000),
  compareLlm: (id) => request("POST", `/api/briefs/${id}/compare`, null, 120000),
  cityCompare: (id, city) => request("POST", `/api/briefs/${id}/city-compare`, { city }),
  share: (token) => request("GET", `/api/share/${token}`),
};

export const RUNNING = ["pending", "resolving", "discovering", "investigating"];
export const READY = ["ready", "ready_low_evidence", "finalized"];

export const STATUS_TEXT = {
  pending: "Queued",
  resolving: "Finding the headliner in Qloo",
  needs_confirmation: "Waiting for you to confirm the headliner",
  discovering: "Asking Qloo who this audience likes here",
  investigating: "Checking public sources for local ties",
  ready: "Shortlist ready",
  ready_low_evidence: "Shortlist ready, but local proof is thin",
  failed: "Stopped",
  finalized: "Booking brief saved",
};
