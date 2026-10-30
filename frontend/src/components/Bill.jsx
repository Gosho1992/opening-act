import React from "react";

// The show bill: headliner on top, support slots stepping down like a gig poster.
export default function Bill({ headliner, city, capacity, date, picks, compact }) {
  const fmtDate = date ? new Date(date + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "long", year: "numeric" }) : null;
  return (
    <div className={`bill${compact ? " bill-compact" : ""}`}>
      <div className="bill-headliner">{headliner}</div>
      <div className="bill-with">with support from</div>
      <div className="bill-support">
        <BillSlot slot="primary" name={picks?.primary} />
        <BillSlot slot="backup" name={picks?.backup} />
        <BillSlot slot="alternative" name={picks?.alternative} />
      </div>
      <div className="bill-foot">
        {city}{capacity ? `, ${capacity.toLocaleString()} capacity` : ""}{fmtDate ? `, ${fmtDate}` : ", show date to be confirmed"}
      </div>
    </div>
  );
}

function BillSlot({ slot, name }) {
  const label = { primary: "Primary", backup: "Backup", alternative: "Alternative" }[slot];
  return (
    <div className={`bill-slot bill-${slot}${name ? "" : " bill-empty"}`}>
      <span className="bill-slot-name">{name || "open slot"}</span>
      <span className="bill-slot-label">{label}</span>
    </div>
  );
}
