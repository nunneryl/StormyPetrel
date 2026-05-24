// Static comparison chart. Four break types on a shared low-to-high
// tide axis, each with a teal band marking the preferred window.
export function BreakTypeTideWindows() {
  const rows = [
    { name: 'Reef break', spots: 'Pipeline, Mavericks', y: 70, bandX: 400, bandW: 90 },
    { name: 'Point break', spots: 'Malibu, Rincon', y: 130, bandX: 300, bandW: 240 },
    { name: 'Beach break', spots: 'Outer Banks, Huntington', y: 190, bandX: 380, bandW: 140 },
    { name: 'Shorebreak slab', spots: "The Wedge, Sandy's", y: 250, bandX: 250, bandW: 80 },
  ];

  return (
    <div className="my-6 rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5">
      <svg viewBox="0 0 680 320" className="block w-full h-auto" role="img">
        <title>Preferred tide window by break type</title>
        <desc>
          Four break types and the range of tide stages each one tends to fire in.
        </desc>
        {rows.map((r) => (
          <g key={r.name}>
            <text x="40" y={r.y - 8} fontSize="14" fontWeight="500" fill="#1E293B">
              {r.name}
            </text>
            <text x="40" y={r.y + 12} fontSize="12" fill="#94A3B8">
              {r.spots}
            </text>
            <line x1="240" y1={r.y} x2="640" y2={r.y} stroke="#E2E8F0" strokeWidth="1" />
            <rect
              x={r.bandX}
              y={r.y - 14}
              width={r.bandW}
              height="28"
              rx="6"
              fill="#1D9E75"
              fillOpacity="0.55"
              stroke="#0F6E56"
              strokeWidth="0.5"
            />
          </g>
        ))}
        <line x1="240" y1="282" x2="240" y2="288" stroke="#94A3B8" strokeWidth="0.5" />
        <line x1="440" y1="282" x2="440" y2="288" stroke="#94A3B8" strokeWidth="0.5" />
        <line x1="640" y1="282" x2="640" y2="288" stroke="#94A3B8" strokeWidth="0.5" />
        <text x="240" y="302" fontSize="12" textAnchor="middle" fill="#94A3B8">
          Low
        </text>
        <text x="440" y="302" fontSize="12" textAnchor="middle" fill="#94A3B8">
          Mid
        </text>
        <text x="640" y="302" fontSize="12" textAnchor="middle" fill="#94A3B8">
          High
        </text>
      </svg>
    </div>
  );
}
