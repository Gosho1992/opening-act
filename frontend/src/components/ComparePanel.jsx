import React, { useEffect, useState } from "react";
import { api, RUNNING } from "../api.js";

const ARMS = [
  ["llm_only", "LLM on its own", "An LLM with no tools, asked for support acts for this show."],
  ["qloo_no_city", "Qloo, no city", "Qloo's related artists without the location signal."],
  ["qloo_city", "Qloo with the city", "The same Qloo query weighted to the show city."],
  ["opening_act", "Opening Act", "The agent's shortlist after the size filter and your vetoes."],
];
const METRICS = [
  ["qualified", "Local, within size limit and a Qloo taste fit"],
  ["verified_local_and_right_size", "Verified local, within size limit"],
  ["verified_local", "Verified as based in the area"],
  ["played_city_only", "Played the city, not local"],
  ["too_big_for_room", "Above the room's popularity limit"],
  ["not_found_in_qloo", "Not found in Qloo's catalog"],
];

export default function ComparePanel({ brief }) {
  const [cmp, setCmp] = useState(null);
  const [cmpErr, setCmpErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [city, setCity] = useState("");
  const [other, setOther] = useState(null);
  const [cityErr, setCityErr] = useState(null);

  async function runCompare() {
    setBusy(true);
    setCmpErr(null);
    try {
      setCmp(await api.compareLlm(brief.id));
    } catch (e) {
      setCmpErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function runCity(e) {
    e.preventDefault();
    if (city.trim().length < 2) return setCityErr("Enter a city to compare with.");
    setCityErr(null);
    try {
      setOther(await api.cityCompare(brief.id, city.trim()));
    } catch (err) {
      setCityErr(err.message);
    }
  }

  useEffect(() => {
    if (!other || !(RUNNING.includes(other.status) || other.is_running)) return;
    const t = setTimeout(() => api.getBrief(other.id).then(setOther).catch((e) => setCityErr(e.message)), 1500);
    return () => clearTimeout(t);
  }, [other]);

  const names = (b) => b.candidates.filter((c) => c.status !== "vetoed" && !c.excluded_reason).slice(0, 10).map((c) => c.artist_name);

  return (
    <section className="compare">
      <div className="panel">
        <h3>What does each step add?</h3>
        <p className="muted">Four shortlists for the same show, every artist checked with the same evidence rules and the same size rule. This takes a minute because every artist is checked.</p>
        <button className="btn" onClick={runCompare} disabled={busy}>{busy ? "Building four lists and checking every artist…" : "Compare four ways"}</button>
        {cmpErr && <p className="alert alert-error">{cmpErr}</p>}
        {cmp && (
          <>
            <div className="table-wrap">
              <table className="cmp-table">
                <thead>
                  <tr><th scope="col">Out of each list</th>{ARMS.map(([k, label]) => <th key={k} scope="col">{label}</th>)}</tr>
                </thead>
                <tbody>
                  <tr className="muted"><th scope="row">Artists in the list</th>{ARMS.map(([k]) => <td key={k}>{cmp.summary[k].n}</td>)}</tr>
                  {METRICS.map(([m, label]) => (
                    <tr key={m}><th scope="row">{label}</th>{ARMS.map(([k]) => <td key={k}>{cmp.summary[k][m]}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="compare-cols four">
              {ARMS.map(([k, label, about]) => <CompareList key={k} title={label} about={about} rows={cmp.arms[k]} showQloo={k === "llm_only"} />)}
            </div>
            <p className="note">{cmp.method}</p>
          </>
        )}
      </div>

      <div className="panel">
        <h3>Same headliner, another city</h3>
        <p className="muted">Run the same brief for a second city and see which names change.</p>
        <form className="inline-form" onSubmit={runCity}>
          <input value={city} onChange={(e) => setCity(e.target.value)} placeholder="Los Angeles, California" aria-label="City to compare" />
          <button className="btn" disabled={other && RUNNING.includes(other.status)}>Compare cities</button>
        </form>
        {cityErr && <p className="alert alert-error">{cityErr}</p>}
        {other && (RUNNING.includes(other.status) ? (
          <p className="muted">Running the agent for {other.city}…</p>
        ) : other.status === "failed" ? (
          <p className="alert alert-error">{other.city}: {other.status_reason}</p>
        ) : (
          <CityDiff a={brief.city} b={other.city} aNames={names(brief)} bNames={names(other)} otherId={other.id} />
        ))}
      </div>
    </section>
  );
}

function CompareList({ title, about, rows, showQloo }) {
  return (
    <div>
      <h4>{title}</h4>
      <p className="muted small">{about}</p>
      {rows.length === 0 ? <p className="muted">No artists returned.</p> : (
        <ol className="cmp-list">
          {rows.map((r) => (
            <li key={r.artist_name}>
              <span>{r.artist_name}</span>
              <span className="cmp-tags">
                {showQloo && r.in_qloo === false && <span className="tag tag-veto">Not in Qloo</span>}
                {r.verified_local ? <span className="tag tag-proof">Local</span>
                  : r.plays_market ? <span className="tag tag-up">Played the city</span>
                  : <span className="tag tag-gap">Not verified</span>}
                {r.too_big && <span className="tag tag-gap">Above size limit</span>}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function CityDiff({ a, b, aNames, bNames, otherId }) {
  const onlyA = aNames.filter((n) => !bNames.includes(n));
  const onlyB = bNames.filter((n) => !aNames.includes(n));
  const both = aNames.filter((n) => bNames.includes(n));
  return (
    <div className="city-diff">
      <div><h4>Only for {a.split(",")[0]}</h4><ul>{onlyA.map((n) => <li key={n}>{n}</li>)}</ul></div>
      <div><h4>In both</h4><ul className="muted">{both.map((n) => <li key={n}>{n}</li>)}</ul></div>
      <div><h4>Only for {b.split(",")[0]}</h4><ul>{onlyB.map((n) => <li key={n}>{n}</li>)}</ul></div>
      <a href={`/b/${otherId}`}>Open the {b.split(",")[0]} brief</a>
    </div>
  );
}
