import React, { useState } from "react";

const SLOT_LABEL = { primary: "Primary", backup: "Backup", alternative: "Alternative" };
const KIND = { based_here: "Based in the area", formed_here: "From or formed in the area",
  based_elsewhere: "Based elsewhere", formed_elsewhere: "Began elsewhere", inactive: "May no longer be active", played_here: "Played the area", context: "Elsewhere", none: "No connection" };
const STATUS = { verified: "Verified", unverified: "Unverified" };
const SOURCE = { qloo: "Qloo", musicbrainz: "MusicBrainz", public: "Public page" };

function EvidenceItem({ e }) {
  return (
    <li className={`ev ev-${e.status}`}>
      <div className="ev-head">
        <span className={`src src-${e.source_label}`}>{SOURCE[e.source_label] || e.source_label}</span>
        <strong>{e.kind === "context" ? "For context"
          : ["based_elsewhere", "formed_elsewhere", "inactive"].includes(e.kind) ? `Conflicting source: ${KIND[e.kind]}${e.place ? ` (${e.place})` : ""}`
          : `${STATUS[e.status] || e.status}: ${KIND[e.kind] || e.kind}`}</strong>
        {e.source_year && <span className="ev-year">{e.source_year}</span>}
      </div>
      {e.quote && e.source_label === "public" && <blockquote>“{e.quote}”</blockquote>}
      {e.source_label !== "public" && e.kind !== "context" && <p className="ev-reason">{e.claim}</p>}
      {e.kind === "context" ? <p className="ev-reason">{e.claim}. This may conflict with other sources.</p>
        : e.status !== "verified" && <p className="ev-reason">{e.reason}</p>}
      {e.source_url && <a href={e.source_url} target="_blank" rel="noreferrer">{e.source_title || e.source_url}</a>}
    </li>
  );
}

function Score({ label, value, note }) {
  const pct = Math.round((value ?? 0) * 100);
  return (
    <div className="score" title={note}>
      <span className="score-label">{label}</span>
      <span className="score-track"><span className="score-fill" style={{ width: `${pct}%` }} /></span>
      <span className="score-num">{value == null ? "–" : value.toFixed(2)}</span>
    </div>
  );
}

const STATUS_LABEL = {
  based_now: "Local: based here (recent source)",
  based_as_of: "Local: based here (older or undated source)",
  roots: "Local roots (current base unconfirmed)",
  disputed: "Sources disagree",
  moved: "Moved away",
};

const GATE_ORDER = [["fit", "Taste fit"], ["local", "Local connection"], ["size", "Size for this room"], ["activity", "Activity"]];
const MARK = { pass: "✓", caution: "!", fail: "✕" };
const GATE_HEAD = { qualified: "Qualified: every booking check satisfied", provisional: "Provisional: no check failed", blocked: "Blocked by the booking gate" };
const GATE_SHORT = { fit: "taste fit", local: "current base", size: "size", activity: "activity" };

function proofLabel(c) {
  return STATUS_LABEL[c.local_status] || "Verified local";
}

export default function CandidateCard({ c, onAction, disabled }) {
  const [open, setOpen] = useState(false);
  const vetoed = c.status === "vetoed";
  const excluded = !!c.excluded_reason;
  const kept = (c.evidence || []).filter((e) => e.status !== "rejected");
  const rejected = (c.evidence || []).filter((e) => e.status === "rejected");

  let signal;
  if (c.found_via === "scene") signal = <span className="tag tag-new">Found through local act {c.scene_seed}</span>;
  else if (c.found_via === "llm_local") signal = <span className="tag tag-new" title="Named by the language model, then found in Qloo and checked against sources like every other lead">Named by the AI as local; checked like the rest</span>;
  else if (c.new_in_city) signal = <span className="tag tag-new" title="Same Qloo query with and without the location signal">Qloo city signal: not in the top 50 without the city, #{c.city_rank} with it</span>;
  else if (c.rank_delta > 0) signal = <span className="tag tag-up" title="Same Qloo query with and without the location signal">Qloo city signal: #{c.noloc_rank} → #{c.city_rank} (up {c.rank_delta})</span>;
  else if (c.rank_delta < 0) signal = <span className="tag" title="Same Qloo query with and without the location signal">Qloo city signal: #{c.noloc_rank} → #{c.city_rank} (down {-c.rank_delta})</span>;
  else signal = <span className="tag">Qloo city signal: same rank (#{c.city_rank}) with or without the city</span>;

  return (
    <article className={`cand${c.slot ? ` cand-${c.slot}` : ""}${vetoed ? " cand-vetoed" : ""}${excluded ? " cand-excluded" : ""}`}>
      <div className="cand-head">
        <div className="cand-rank" aria-label={c.city_rank == null ? "Not ranked in the headliner's Qloo list" : `Qloo rank ${c.city_rank}`}>
          {c.city_rank == null ? "+" : `#${c.city_rank}`}
        </div>
        <div className="cand-title">
          <h3>{c.artist_name}</h3>
          <div className="cand-sub">
            {c.genres?.length ? c.genres.join(", ") : "Genre not listed"}
            {c.found_level > 0 && <>; found when the search widened to {c.found_scope}</>}
            {c.found_via === "qloo_deeper" && <>; found lower in Qloo's list</>}
          </div>
        </div>
        {c.slot && <span className={`slot-badge slot-${c.slot}`}>{SLOT_LABEL[c.slot]}</span>}
      </div>

      <div className="cand-tags">
        {signal}
        {c.local_proof ? <span className="tag tag-proof" title={c.local_note || ""}>{proofLabel(c)}</span>
          : ["moved", "disputed"].includes(c.local_status) ? <span className="tag tag-gap" title={c.local_note || ""}>{STATUS_LABEL[c.local_status]}</span>
          : c.plays_market ? <span className="tag tag-up">Has played the city, not verified as local</span>
          : <span className="tag tag-gap">Not verified as local</span>}
        {c.fit_ok === true && <span className="tag tag-up" title={c.fit_note || ""}>Qloo taste fit</span>}
        {c.fit_ok === false && <span className="tag tag-gap" title={c.fit_note || ""}>Little Qloo taste fit</span>}
        {vetoed && <span className="tag tag-veto">Vetoed</span>}
        {excluded && <span className="tag tag-gap">{c.excluded_reason}</span>}
      </div>

      {c.gate && !excluded && (
        <div className={`gate gate-${c.gate.status}`}>
          <strong>{GATE_HEAD[c.gate.status]}{c.gate.status === "provisional" ? `: confirm ${c.gate.to_confirm.map((k) => GATE_SHORT[k]).join(", ")}` : ""}</strong>
          <ul>
            {GATE_ORDER.map(([k, label]) => {
              const g = c.gate.checks[k];
              return <li key={k} className={`gate-${g.state}`}><span className="gate-mark" aria-hidden="true">{MARK[g.state]}</span>
                <span className="sr-only">{g.state}: </span><b>{label}</b> {g.note}</li>;
            })}
          </ul>
        </div>
      )}
      {c.shared_tags?.length > 0 && (
        <p className="shared">
          <span className="shared-label">Shares with the headliner in Qloo</span>{" "}
          {c.shared_tags.slice(0, 6).join(", ")}
        </p>
      )}

      <div className="scores">
        <Score label="Taste" value={c.taste_score} note={`Qloo affinity to the headliner (when Qloo ranked it for the headliner) combined with Qloo taste-tag overlap${c.tag_overlap != null ? ` (${Math.round(c.tag_overlap * 100)}% of tags shared)` : ""}`} />
        <Score label="Local" value={c.local_score} note="1.00 = based here per a recent source; 0.75 = based here per an older or undated source; 0.60 = from or formed here; sources disagree 0.35; moved 0.20; playing the city scores low" />
        <Score label="Size" value={c.practical_score} note={c.practical_note} />
      </div>

      <button type="button" className="linkish" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "Hide evidence" : `Evidence (${kept.length}${rejected.length ? `, ${rejected.length} rejected` : ""})`}
      </button>
      {open && (
        <div className="evidence">
          {kept.length ? (
            <ul>{kept.map((e, i) => <EvidenceItem key={i} e={e} />)}</ul>
          ) : (
            <p className="muted">No source says this artist is from or played in the area. That doesn't rule out a connection, only that none was found.</p>
          )}
          {rejected.length > 0 && (
            <details className="rejected">
              <summary>Rejected sources ({rejected.length})</summary>
              <ul>
                {rejected.map((e, i) => (
                  <li key={i}><a href={e.source_url} target="_blank" rel="noreferrer">{e.source_title || e.source_url}</a> <span className="ev-reason">{e.reason}</span></li>
                ))}
              </ul>
            </details>
          )}
          <dl className="facts">
            <div><dt>Qloo rank with city</dt><dd>#{c.city_rank}</dd></div>
            <div><dt>Without city</dt><dd>{c.noloc_rank ? `#${c.noloc_rank}` : "not in the list"}</dd></div>
            <div><dt>Monthly listeners</dt><dd>{c.monthly_listeners ? c.monthly_listeners.toLocaleString() : c.listeners_flag === "implausible" ? "Spotify match looks wrong" : "unknown"}</dd></div>
            <div><dt>Availability, fee</dt><dd>unknown</dd></div>
          </dl>
        </div>
      )}

      <div className="cand-actions">
        {vetoed ? (
          <button className="btn btn-small" disabled={disabled} onClick={() => onAction({ action: "unveto", candidate_id: c.id })}>Undo veto</button>
        ) : excluded ? null : (
          <>
            {["primary", "backup", "alternative"].map((s) =>
              c.slot === s ? (
                <button key={s} className={`btn btn-small btn-slot-on slot-${s}`} disabled={disabled} onClick={() => onAction({ action: "clear_slot", candidate_id: c.id })} aria-pressed="true">
                  {SLOT_LABEL[s]} ✓
                </button>
              ) : (
                <button key={s} className="btn btn-small" disabled={disabled} onClick={() => onAction({ action: "set_slot", candidate_id: c.id, slot: s })}>
                  {SLOT_LABEL[s]}
                </button>
              )
            )}
            <button className="btn btn-small btn-quiet" disabled={disabled} onClick={() => onAction({ action: "veto", candidate_id: c.id })}>Veto</button>
          </>
        )}
      </div>
    </article>
  );
}
