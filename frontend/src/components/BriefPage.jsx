import React, { useCallback, useEffect, useState } from "react";
import { api, READY, RUNNING, STATUS_TEXT } from "../api.js";
import AgentLog from "./AgentLog.jsx";
import Bill from "./Bill.jsx";
import CandidateCard from "./CandidateCard.jsx";

const LIST_N = 15;
import ComparePanel from "./ComparePanel.jsx";
import Controls from "./Controls.jsx";

export default function BriefPage({ id }) {
  const [brief, setBrief] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("shortlist");
  const [showHidden, setShowHidden] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [finalized, setFinalized] = useState(null);

  const load = useCallback(async () => {
    try {
      setBrief(await api.getBrief(id));
      setLoadError(null);
    } catch (e) {
      setLoadError(e);
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  const running = brief && (brief.is_running || RUNNING.includes(brief.status));
  useEffect(() => {
    if (!running) return;
    const t = setTimeout(load, 1500);
    return () => clearTimeout(t);
  }, [running, brief, load]);

  async function act(payload) {
    setBusy(true);
    setActionError(null);
    try {
      setBrief(await api.negotiate(id, payload));
    } catch (e) {
      setActionError(e.message);
      load();
    } finally {
      setBusy(false);
    }
  }

  async function confirm(qlooId) {
    setBusy(true);
    setActionError(null);
    try {
      setBrief(await api.confirm(id, qlooId));
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function finalize() {
    setBusy(true);
    setActionError(null);
    try {
      setFinalized(await api.finalize(id));
      load();
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (loadError && !brief) {
    return (
      <main className="page">
        <div className="panel empty">
          <h2>{loadError.status === 404 ? "This brief doesn't exist" : "Couldn't load this brief"}</h2>
          <p>{loadError.status === 404 ? "The link may be mistyped, or the brief was created on another server." : loadError.message}</p>
          {loadError.status !== 404 && <button className="btn" onClick={load}>Try again</button>}
          <a href="/">Start a new brief</a>
        </div>
      </main>
    );
  }
  if (!brief) return <main className="page"><p className="muted">Loading the brief…</p></main>;

  // the bill shows the buyer's own picks, else the picks of the latest saved booking brief
  const saved = brief.versions?.[0];
  const pick = (s) => brief.candidates.find((c) => c.slot === s)?.artist_name || saved?.picks?.[s];
  const allVisible = brief.candidates.filter((c) => showHidden || (c.status !== "vetoed" && !c.excluded_reason));
  const visible = showAll ? allVisible : allVisible.slice(0, LIST_N);
  const hiddenCount = brief.candidates.length - brief.candidates.filter((c) => c.status !== "vetoed" && !c.excluded_reason).length;
  const isReady = READY.includes(brief.status);
  const disabled = busy || running;

  return (
    <main className="page">
      <section className="hero">
        <Bill headliner={brief.headliner_resolved_name || brief.headliner_name} city={brief.city}
          capacity={brief.venue_capacity} date={brief.show_date}
          picks={{ primary: pick("primary"), backup: pick("backup"), alternative: pick("alternative") }} />
        <div className={`status status-${brief.status}`} role="status">
          <span className={running ? "status-dot pulse" : "status-dot"} />
          <div>
            <strong>{STATUS_TEXT[brief.status] || brief.status}</strong>
            {brief.status_reason && <p>{brief.status_reason}</p>}
            {brief.headliner_tags?.length > 0 && (
              <p className="muted">Qloo taste profile of {brief.headliner_resolved_name}: {brief.headliner_tags.slice(0, 8).join(", ")}</p>
            )}
            {isReady && !brief.status_reason && <p>{brief.qualified_count} of {brief.candidates.filter((c) => c.status !== "vetoed" && !c.excluded_reason).length} live candidates pass the booking gate ({brief.proven_count} have a verified local connection).</p>}
          </div>
        </div>
        {isReady && !brief.use_popularity_cap && <p className="note">Exploration mode: the size filter is off, so bigger artists are listed. Automatic picks still have to pass the size check for a {brief.venue_capacity}-capacity room.</p>}
        {brief.demo_mode && <p className="note">Demo mode: results are real Qloo responses recorded on 15 Oct 2026.</p>}
      </section>

      {actionError && <p className="alert alert-error" role="alert">{actionError}</p>}

      {brief.status === "needs_confirmation" && (
        <section className="panel confirm">
          <h2>Which artist is headlining?</h2>
          <p>Qloo has more than one match for "{brief.headliner_name}". Pick the right one so every recommendation is built on the correct artist.</p>
          <div className="confirm-options">
            {brief.resolution_options.map((o) => (
              <button key={o.id} className="btn" disabled={busy} onClick={() => confirm(o.id)}>{o.name}</button>
            ))}
          </div>
          <a href="/">None of these, start over</a>
        </section>
      )}

      {brief.status === "failed" && (
        <section className="panel">
          <h2>The agent stopped</h2>
          <p>{brief.status_reason}</p>
          <div className="row-actions">
            {brief.headliner_qloo_id && <button className="btn" disabled={disabled} onClick={() => act({ action: "rerun" })}>Run again</button>}
            <a href="/">Start a new brief</a>
          </div>
        </section>
      )}

      <div className="work">
        <div className="work-main">
          {isReady && (
            <nav className="tabs" role="tablist">
              <button role="tab" aria-selected={tab === "shortlist"} className={tab === "shortlist" ? "tab on" : "tab"} onClick={() => setTab("shortlist")}>Shortlist</button>
              <button role="tab" aria-selected={tab === "compare"} className={tab === "compare" ? "tab on" : "tab"} onClick={() => setTab("compare")}>Compare</button>
            </nav>
          )}

          {tab === "compare" && isReady ? (
            <ComparePanel brief={brief} />
          ) : (
            <>
              {running && brief.candidates.length === 0 && (
                <div className="panel empty"><p>The agent is working. Candidates appear here as soon as Qloo and the evidence search return.</p></div>
              )}
              {visible.map((c) => <CandidateCard key={c.id} c={c} onAction={act} disabled={disabled} />)}
              {allVisible.length > LIST_N && (
                <button className="linkish" onClick={() => setShowAll(!showAll)}>
                  {showAll ? `Show the top ${LIST_N} only` : `Show all ${allVisible.length} candidates`}
                </button>
              )}
              {hiddenCount > 0 && (
                <button className="linkish" onClick={() => setShowHidden(!showHidden)}>
                  {showHidden ? "Hide vetoed and filtered artists" : `Show ${hiddenCount} vetoed or filtered artist${hiddenCount > 1 ? "s" : ""}`}
                </button>
              )}
            </>
          )}
        </div>

        <aside className="work-side">
          {isReady && (
            <section className="panel finalize">
              <h3>Booking brief</h3>
              <p className="muted">Opening Act is a research agent, not a booking agent. It fills a pick only when an act passes all four checks of the booking gate: taste fit, local connection, size for this room and activity. Three such acts make a recommendation, one or two a partial one, none a research shortlist. When the evidence doesn't support a pick it won't guess: you get the trade-offs, the sources and what is still unknown, and you make the call. Saving creates a new version; earlier links keep working.</p>
              <button className="btn btn-primary" disabled={disabled} onClick={finalize}>
                {busy ? "Saving…" : brief.versions.length ? "Save a new version" : "Save booking brief"}
              </button>
              {(finalized || brief.versions[0]) && (
                <p className="share-link">
                  Share link, version {(finalized || brief.versions[0]).version}:{" "}
                  <a href={`/s/${(finalized || brief.versions[0]).share_token}`} target="_blank" rel="noreferrer">
                    {window.location.origin}/s/{(finalized || brief.versions[0]).share_token}
                  </a>
                </p>
              )}
            </section>
          )}
          {isReady && <Controls brief={brief} onAction={act} disabled={disabled} />}
          <AgentLog events={brief.events} running={running} />
        </aside>
      </div>
    </main>
  );
}
