import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import Bill from "./Bill.jsx";

const KIND = { based_here: "Based in the area", formed_here: "From or formed in the area", played_here: "Played the area" };

export default function SharePage({ token }) {
  const [snap, setSnap] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.share(token).then(setSnap).catch(setErr);
  }, [token]);

  if (err) {
    return (
      <main className="share-page">
        <div className="panel empty">
          <h1>{err.status === 404 ? "This booking brief link doesn't work" : "Couldn't load the booking brief"}</h1>
          <p>{err.status === 404 ? "Check that the whole link was copied. Ask the sender for a new link if it still fails." : err.message}</p>
        </div>
      </main>
    );
  }
  if (!snap) return <main className="share-page"><p className="muted">Loading…</p></main>;

  const b = snap.brief;
  const p = snap.picks;
  return (
    <main className="share-page">
      <Bill headliner={b.headliner_resolved_name || b.headliner_name} city={b.city} capacity={b.venue_capacity} date={b.show_date}
        picks={{ primary: p.primary?.artist_name, backup: p.backup?.artist_name, alternative: p.alternative?.artist_name }} />

      <section className="share-body">
        {snap.kind === "research_shortlist" && <p className="research-banner">Research shortlist, not a booking recommendation</p>}
        {snap.kind === "partial_recommendation" && <p className="research-banner">Partial recommendation: only acts that pass all four booking checks are picked</p>}
        {snap.pitch_line && <p className="pitch">{snap.pitch_line}</p>}
        {snap.warnings?.length > 0 && (
          <ul className="warnings">{snap.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        )}
        <p>{snap.narrative}</p>

        {["primary", "backup", "alternative"].map((s) => p[s] && (
          <article key={s} className={`share-pick share-${s}`}>
            <h2>{p[s].artist_name}<span className="share-slot">{s}{p[s].auto_selected ? (p[s].gate?.status === "provisional" ? ", suggested by the agent, provisional" : ", suggested by the agent, qualified") : ", chosen by the buyer"}</span></h2>
            <p>
              {p[s].found_via === "scene" ? `Found through the local act ${p[s].scene_seed}.`
                : p[s].found_via === "llm_local" ? "Named by the AI as a local act, then found in Qloo and checked against sources."
                : <>Qloo rank #{p[s].city_rank} with the {b.city.split(",")[0]} signal
                  {p[s].new_in_city ? "; does not appear without it." : p[s].noloc_rank ? `; #${p[s].noloc_rank} without it.` : "."}</>}
              {p[s].practical_note ? ` ${p[s].practical_note}.` : ""}
            </p>
            {p[s].shared_tags?.length > 0 && <p className="muted">Shares with the headliner in Qloo: {p[s].shared_tags.slice(0, 6).join(", ")}.</p>}
            {p[s].local_note && <p className="muted">Local connection: {p[s].local_note}.</p>}
            {(() => {
              const ev = (p[s].evidence || []).filter((e) => e.status === "verified");
              return ev.length ? (
                <ul className="share-ev">
                  {ev.map((e, i) => (
                    <li key={i}>
                      {KIND[e.kind]}{e.source_year ? ` (${e.source_year})` : ""}: {e.source_label === "public" ? `“${e.quote}”` : `${e.claim}.`}{" "}
                      {e.source_url ? <a href={e.source_url} target="_blank" rel="noreferrer">{e.source_label === "musicbrainz" ? "MusicBrainz" : e.source_title || "source"}</a> : "Source: Qloo."}
                    </li>
                  ))}
                </ul>
              ) : <p className="muted">No verified source says this artist is from or played in the area.</p>;
            })()}
          </article>
        ))}

        {snap.tradeoffs && (snap.tradeoffs.local_but_weak_fit?.length > 0 || snap.tradeoffs.fit_but_not_verified_local?.length > 0 || snap.tradeoffs.local_and_fit_but_blocked?.length > 0) && (
          <div className="tradeoffs">
            <h3>Trade-offs for you to decide</h3>
            {snap.tradeoffs.local_but_weak_fit?.length > 0 && (<>
              <p className="muted">Local, but little Qloo taste fit with the headliner (not picked automatically):</p>
              <ul className="share-ev">{snap.tradeoffs.local_but_weak_fit.map((c) => (
                <li key={c.artist_name}><strong>{c.artist_name}</strong>: {c.local_note}; {c.fit_note}.</li>))}</ul>
            </>)}
            {snap.tradeoffs.local_and_fit_but_blocked?.length > 0 && (<>
              <p className="muted">Local and a taste fit, but blocked by the size or activity check:</p>
              <ul className="share-ev">{snap.tradeoffs.local_and_fit_but_blocked.map((c) => (
                <li key={c.artist_name}><strong>{c.artist_name}</strong>: {Object.values(c.gate?.checks || {}).filter((g) => g.state === "fail").map((g) => g.note).join("; ")}.</li>))}</ul>
            </>)}
            {snap.tradeoffs.fit_but_not_verified_local?.length > 0 && (<>
              <p className="muted">Strong Qloo taste fit, local connection not verified (confirm before pitching as local):</p>
              <ul className="share-ev">{snap.tradeoffs.fit_but_not_verified_local.map((c) => (
                <li key={c.artist_name}><strong>{c.artist_name}</strong>: {c.fit_note}{c.plays_market ? "; has played the city" : ""}.</li>))}</ul>
            </>)}
          </div>
        )}
        {snap.policy && <p className="muted">{snap.policy}</p>}

        {snap.research_candidates?.length > 0 && (
          <>
            <h3>{snap.kind === "research_shortlist" ? "Leads to research" : "Other candidates"}</h3>
            <ul className="share-ev">
              {snap.research_candidates.map((c) => (
                <li key={c.artist_name}><strong>{c.artist_name}</strong>: {c.local_note || (c.plays_market ? "has played the city; not verified as local" : "local connection not verified")}
                  {c.shared_tags?.length > 0 && <> Shares {c.shared_tags.slice(0, 3).join(", ")} with the headliner.</>}</li>
              ))}
            </ul>
          </>
        )}
        {snap.next_actions?.length > 0 && (<><h3>Next steps</h3><ul>{snap.next_actions.map((a) => <li key={a}>{a}</li>)}</ul></>)}
        <h3>Still unknown</h3>
        <ul>{snap.unknowns.map((u) => <li key={u}>{u}</li>)}</ul>
        <h3>How this was made</h3>
        <p className="muted">{snap.method} {snap.qloo_calls} Qloo calls recorded. Search area reached: {snap.scope_reached}.</p>
        <p className="muted">Version {snap.version}, saved {new Date(snap.created_at).toLocaleString()}. This page does not change if the brief is edited later.</p>
        <button className="btn no-print" onClick={() => window.print()}>Print or save as PDF</button>
      </section>
    </main>
  );
}
