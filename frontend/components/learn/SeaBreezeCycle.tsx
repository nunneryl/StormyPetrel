// Static 24-hour wind cycle illustration. Teal bars above midline =
// offshore (good for surf), coral bars below = onshore (textured to
// blown out). Annotations call out the three windows surfers care
// about: dawn patrol, sea breeze peak, evening glass-off.

export function SeaBreezeCycle() {
  return (
    <div
      className="my-6 rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5"
      role="figure"
      aria-label="Typical 24-hour wind cycle at a coastal spot"
    >
      <svg viewBox="0 0 680 280" className="block w-full h-auto">
        {/* Plot background */}
        <rect x="30" y="50" width="620" height="180" rx="8" fill="#F1F5F9" />
        <line
          x1="30"
          y1="140"
          x2="650"
          y2="140"
          stroke="#CBD5E1"
          strokeWidth="0.5"
          strokeDasharray="4 4"
        />

        {/* Axis labels above/below the midline */}
        <text x="40" y="68" fontSize="12" fill="#15803D">
          Offshore — good
        </text>
        <text x="40" y="225" fontSize="12" fill="#B45309">
          Onshore — blown out
        </text>

        {/* Offshore bars (teal, above midline) */}
        <g fill="#5DCAA5">
          <rect x="42" y="124" width="22" height="16" />
          <rect x="66" y="124" width="22" height="16" />
          <rect x="90" y="124" width="22" height="16" />
          <rect x="114" y="120" width="22" height="20" />
          <rect x="138" y="120" width="22" height="20" />
          <rect x="162" y="120" width="22" height="20" />
          <rect x="186" y="124" width="22" height="16" />
          <rect x="210" y="132" width="22" height="8" />
          <rect x="546" y="136" width="22" height="4" />
          <rect x="570" y="128" width="22" height="12" />
          <rect x="594" y="128" width="22" height="12" />
        </g>

        {/* Onshore bars (coral, below midline) */}
        <g fill="#F0997B">
          <rect x="258" y="140" width="22" height="8" />
          <rect x="282" y="140" width="22" height="20" />
          <rect x="306" y="140" width="22" height="32" />
          <rect x="330" y="140" width="22" height="44" />
          <rect x="354" y="140" width="22" height="52" />
          <rect x="378" y="140" width="22" height="56" />
          <rect x="402" y="140" width="22" height="56" />
          <rect x="426" y="140" width="22" height="52" />
          <rect x="450" y="140" width="22" height="40" />
          <rect x="474" y="140" width="22" height="24" />
          <rect x="498" y="140" width="22" height="12" />
          <rect x="522" y="140" width="22" height="4" />
        </g>

        {/* Time-of-day ticks */}
        <text x="53" y="250" textAnchor="middle" fontSize="12" fill="#94A3B8">
          12 am
        </text>
        <text x="197" y="250" textAnchor="middle" fontSize="12" fill="#94A3B8">
          6 am
        </text>
        <text x="341" y="250" textAnchor="middle" fontSize="12" fill="#94A3B8">
          noon
        </text>
        <text x="485" y="250" textAnchor="middle" fontSize="12" fill="#94A3B8">
          6 pm
        </text>
        <text x="629" y="250" textAnchor="middle" fontSize="12" fill="#94A3B8">
          12 am
        </text>

        {/* Window callouts */}
        <text x="150" y="100" textAnchor="middle" fontSize="14" fontWeight="600" fill="#15803D">
          Dawn patrol
        </text>
        <text x="150" y="115" textAnchor="middle" fontSize="12" fill="#94A3B8">
          light offshore window
        </text>
        <text x="390" y="215" textAnchor="middle" fontSize="14" fontWeight="600" fill="#B45309">
          Sea breeze peak
        </text>
        <text x="580" y="100" textAnchor="middle" fontSize="14" fontWeight="600" fill="#15803D">
          Evening glass-off
        </text>
        <text x="580" y="115" textAnchor="middle" fontSize="12" fill="#94A3B8">
          brief offshore return
        </text>
      </svg>
    </div>
  );
}
