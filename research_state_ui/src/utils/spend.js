// Shared helpers for the compute-spend readouts (desktop panel + mobile strip).

// Fill calendar gaps in the ledger's daily series so idle days read as idle
// instead of vanishing from the time axis.
export function densifyDaily(daily) {
  if (!Array.isArray(daily) || daily.length < 2) return daily || [];
  const out = [];
  const day = 24 * 3600 * 1000;
  const end = Date.parse(daily[daily.length - 1].date + 'T00:00:00Z');
  const byDate = new Map(daily.map(d => [d.date, d]));
  for (let t = Date.parse(daily[0].date + 'T00:00:00Z'); t <= end; t += day) {
    const date = new Date(t).toISOString().slice(0, 10);
    out.push(byDate.get(date) || { date, usd: 0, hours: 0 });
  }
  return out;
}
