import React from "react";

const MARK = { step: "→", result: "✓", gate: "◆", decision: "↗", warning: "!", failed: "×", error: "×", buyer: "●", info: "i", needs_input: "?" };

export default function AgentLog({ events, running }) {
  if (!events?.length) return null;
  const shown = events.slice(-14);
  return (
    <section className="log" aria-live="polite">
      <h3>What the agent did</h3>
      <ol>
        {shown.map((e) => (
          <li key={e.id} className={`log-${e.type}`}>
            <span className="log-mark" aria-hidden="true">{MARK[e.type] || "·"}</span>
            <span>{e.message}</span>
          </li>
        ))}
        {running && (
          <li className="log-working"><span className="log-mark pulse" aria-hidden="true">●</span><span>Working…</span></li>
        )}
      </ol>
    </section>
  );
}
