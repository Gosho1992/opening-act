import React, { useEffect, useState } from "react";

const LABELS = { taste: "Taste match", local: "Verified local", practical: "Room size fit" };

export default function Controls({ brief, onAction, disabled }) {
  const [w, setW] = useState(brief.weights);
  useEffect(() => setW(brief.weights), [brief.weights]);
  const sum = Object.values(w).reduce((a, b) => a + Number(b), 0);
  const changed = Object.keys(w).some((k) => Math.abs(Number(w[k]) - brief.weights[k]) > 0.001);
  const scopes = ["city", "metro area", "state"];

  return (
    <section className="panel controls">
      <h3>Steer the agent</h3>
      <div className="weights">
        {Object.keys(LABELS).map((k) => (
          <label key={k} className="slider">
            <span>{LABELS[k]}</span>
            <input type="range" min="0" max="1" step="0.05" value={w[k]} disabled={disabled}
              onChange={(e) => setW({ ...w, [k]: Number(e.target.value) })} />
            <output>{sum > 0 ? Math.round((w[k] / sum) * 100) : 0}%</output>
          </label>
        ))}
        <button className="btn btn-small" disabled={disabled || !changed || sum <= 0}
          onClick={() => onAction({ action: "reweight", weights: w })}>Re-rank with these weights</button>
        {sum <= 0 && <p className="field-error">At least one weight must be above zero.</p>}
      </div>
      <div className="control-row">
        <div>
          <strong>Search area:</strong> {scopes[brief.widen_level]}{brief.scope ? ` (${brief.scope})` : ""}
        </div>
        <button className="btn btn-small" disabled={disabled || brief.widen_level >= 2}
          onClick={() => onAction({ action: "widen" })}>
          {brief.widen_level >= 2 ? "Already at the widest area" : `Widen to ${scopes[brief.widen_level + 1]}`}
        </button>
      </div>
      <div className="control-row">
        <div>
          <strong>Size filter:</strong>{" "}
          {brief.use_popularity_cap
            ? brief.popularity_cap ? `on (Qloo popularity up to ${brief.popularity_cap} for this room size)` : "on (no limit for a room this size)"
            : "off"}
        </div>
        <button className="btn btn-small" disabled={disabled} onClick={() => onAction({ action: "toggle_cap" })}>
          {brief.use_popularity_cap ? "Turn off size filter" : "Turn on size filter"}
        </button>
      </div>
      <div className="control-row">
        <div className="muted">Rerun Qloo and the evidence search with the current settings.</div>
        <button className="btn btn-small btn-quiet" disabled={disabled} onClick={() => onAction({ action: "rerun" })}>Run again</button>
      </div>
    </section>
  );
}
