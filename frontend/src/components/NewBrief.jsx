import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { navigate } from "../App.jsx";

const EXAMPLES = [
  { headliner: "Khruangbin", city: "Nashville, Tennessee", venue_capacity: 800 },
  { headliner: "Japanese Breakfast", city: "Atlanta, Georgia", venue_capacity: 650 },
  { headliner: "St. Vincent", city: "Austin, Texas", venue_capacity: 1200 },
];

export default function NewBrief() {
  const [form, setForm] = useState({ headliner: "", city: "", venue_capacity: 800, show_date: "", budget: "", use_popularity_cap: true });
  const [errors, setErrors] = useState({});
  const [submitError, setSubmitError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  function validate() {
    const e = {};
    if (!form.headliner.trim()) e.headliner = "Enter the headlining artist.";
    if (form.city.trim().length < 2) e.city = "Enter the show's city, ideally as City, State.";
    const cap = Number(form.venue_capacity);
    if (!Number.isInteger(cap) || cap < 50 || cap > 20000) e.venue_capacity = "Capacity must be a whole number from 50 to 20,000.";
    if (form.budget !== "" && (isNaN(Number(form.budget)) || Number(form.budget) < 0)) e.budget = "Budget must be a positive number, or leave it empty.";
    setErrors(e);
    return Object.keys(e).length === 0;
  }

  async function submit(ev) {
    ev.preventDefault();
    if (!validate()) return;
    setBusy(true);
    setSubmitError(null);
    try {
      const b = await api.createBrief({
        headliner: form.headliner.trim(),
        city: form.city.trim(),
        venue_capacity: Number(form.venue_capacity),
        show_date: form.show_date || null,
        budget: form.budget === "" ? null : Number(form.budget),
        use_popularity_cap: form.use_popularity_cap,
      });
      navigate(`/b/${b.id}`);
    } catch (e) {
      setSubmitError(e.message);
      setBusy(false);
    }
  }

  return (
    <main className="new">
      <section className="new-intro">
        <h1 className="bill-headliner">Who opens for your headliner?</h1>
        <p className="lede">
          Enter the show. Opening Act asks Qloo which artists this headliner's fans like <em>in your city</em>, checks
          public sources for a local connection, and gives you a shortlist you can defend to the headliner's agent.
        </p>
        <ol className="how">
          <li><strong>Discover.</strong> Qloo returns related artists with and without your city's taste signal, so you can see who the city adds.</li>
          <li><strong>Check.</strong> Each artist is searched for a hometown, local shows or local press, with the source linked.</li>
          <li><strong>Decide.</strong> Re-weight, veto, widen the area, then save a booking brief to share.</li>
        </ol>
      </section>

      <form className="panel brief-form" onSubmit={submit} noValidate>
        <h2>Show brief</h2>
        <Field label="Headliner" error={errors.headliner}>
          <input value={form.headliner} onChange={set("headliner")} placeholder="Khruangbin" autoFocus />
        </Field>
        <Field label="City" hint="City, State lets the agent widen to the region if it needs to" error={errors.city}>
          <input value={form.city} onChange={set("city")} placeholder="Nashville, Tennessee" />
        </Field>
        <div className="row2">
          <Field label="Venue capacity" error={errors.venue_capacity}>
            <input type="number" min="50" max="20000" value={form.venue_capacity} onChange={set("venue_capacity")} />
          </Field>
          <Field label="Show date" hint="Optional">
            <input type="date" min={new Date().toISOString().slice(0, 10)} value={form.show_date} onChange={set("show_date")} />
          </Field>
        </div>
        <Field label="Support budget (USD)" hint="Optional, kept on the brief; fees are never guessed" error={errors.budget}>
          <input inputMode="decimal" value={form.budget} onChange={set("budget")} placeholder="e.g. 1500" />
        </Field>
        <label className="check">
          <input type="checkbox" checked={form.use_popularity_cap} onChange={set("use_popularity_cap")} />
          <span>Prefer smaller acts (hide artists in Qloo's top popularity band)</span>
        </label>
        {submitError && <p className="alert alert-error" role="alert">{submitError}</p>}
        <button className="btn btn-primary" disabled={busy}>{busy ? "Starting the agent…" : "Find support acts"}</button>

        <div className="examples">
          <span>Try a recorded example:</span>
          {EXAMPLES.map((ex) => (
            <button type="button" key={ex.headliner} className="chip" onClick={() => setForm({ ...form, ...ex })}>
              {ex.headliner} in {ex.city.split(",")[0]}
            </button>
          ))}
        </div>
        {health?.demo_mode && (
          <p className="note">Demo mode is on: the server replays real Qloo responses recorded on 15 Oct 2026, so only those examples return results.</p>
        )}
        {health && !health.demo_mode && !health.providers?.evidence && (
          <p className="note">Evidence search is not configured on this server, so local proof is limited to Qloo data.</p>
        )}
      </form>
    </main>
  );
}

export function Field({ label, hint, error, children }) {
  return (
    <label className={`field${error ? " has-error" : ""}`}>
      <span className="field-label">{label}</span>
      {children}
      {error ? <span className="field-error">{error}</span> : hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}
