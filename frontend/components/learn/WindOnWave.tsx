// Three-panel illustration of how wind reshapes a breaking wave.
// Pure SVG, no client interactivity. Renders side-by-side on
// tablet+ and stacks on mobile. Wave/foam colors are intentionally
// constant (this is a physical-color illustration); only the panel
// background tracks the rest of the site's chrome.

export function WindOnWave() {
  return (
    <div
      className="my-6 rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5"
      role="figure"
      aria-label="Three wave states under different wind conditions"
    >
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Panel
          title="Onshore wind"
          caption="Lip crumbles forward"
          windLabel="Wind direction"
          windDirection="ltr"
        >
          {/* Crumbling wave — short, foamy crest collapsing landward */}
          <path
            d="M 5 140 Q 40 138 75 130 Q 105 115 120 110 Q 132 108 140 116 Q 145 124 142 132 L 165 138 L 175 140 L 175 170 L 5 170 Z"
            fill="#5DA8E0"
          />
          <circle cx="150" cy="121" r="2" fill="#FFFFFF" />
          <circle cx="156" cy="128" r="2" fill="#FFFFFF" />
          <circle cx="164" cy="133" r="1.5" fill="#FFFFFF" />
        </Panel>

        <Panel title="Calm" caption="Standard plunging break" windLabel="No wind">
          {/* Cleanly tossed lip, mid-pitch */}
          <path
            d="M 5 140 Q 35 138 60 130 Q 80 115 95 90 Q 110 80 125 90 Q 138 105 135 122 Q 128 130 120 126 Q 115 120 122 113 Q 130 110 135 118 L 145 130 Q 162 138 175 140 L 175 170 L 5 170 Z"
            fill="#5DA8E0"
          />
        </Panel>

        <Panel
          title="Offshore wind"
          caption="Lip held up, hollow barrel"
          windLabel="Wind direction"
          windDirection="rtl"
        >
          {/* Hollow lip thrown forward with spray feathering off the top */}
          <path
            d="M 5 140 Q 30 138 55 130 Q 70 118 85 95 Q 95 75 110 70 Q 125 75 132 95 Q 130 115 118 115 Q 108 110 112 100 Q 122 98 130 105 L 142 122 Q 160 137 175 140 L 175 170 L 5 170 Z"
            fill="#5DA8E0"
          />
          <circle cx="100" cy="68" r="1.5" fill="#FFFFFF" />
          <circle cx="90" cy="65" r="1.5" fill="#FFFFFF" />
          <circle cx="80" cy="67" r="1" fill="#FFFFFF" />
          <circle cx="70" cy="70" r="0.8" fill="#FFFFFF" />
        </Panel>
      </div>
    </div>
  );
}

function Panel({
  title,
  caption,
  windLabel,
  windDirection,
  children,
}: {
  title: string;
  caption: string;
  windLabel: string;
  /** ltr = wind arrows point right (onshore); rtl = arrows point left
   *  (offshore); undefined = no arrows (calm). */
  windDirection?: 'ltr' | 'rtl';
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg bg-ink-900/60 border border-ink-600 px-3 pt-3 pb-3.5">
      <svg
        viewBox="0 0 180 170"
        className="block w-full h-auto"
        aria-hidden
      >
        <defs>
          <marker
            id={`wow-arrow-${windDirection ?? 'none'}`}
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path
              d="M2 1L8 5L2 9"
              fill="none"
              stroke="#94A3B8"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </marker>
        </defs>
        <text
          x="90"
          y={windDirection ? 22 : 42}
          textAnchor="middle"
          fontSize="11"
          fill="#94A3B8"
        >
          {windLabel}
        </text>
        {windDirection === 'ltr' && (
          <>
            <line x1="35" y1="40" x2="105" y2="40" stroke="#94A3B8" strokeWidth="1" markerEnd="url(#wow-arrow-ltr)" />
            <line x1="55" y1="55" x2="125" y2="55" stroke="#94A3B8" strokeWidth="1" markerEnd="url(#wow-arrow-ltr)" />
          </>
        )}
        {windDirection === 'rtl' && (
          <>
            <line x1="145" y1="40" x2="75" y2="40" stroke="#94A3B8" strokeWidth="1" markerEnd="url(#wow-arrow-rtl)" />
            <line x1="125" y1="55" x2="55" y2="55" stroke="#94A3B8" strokeWidth="1" markerEnd="url(#wow-arrow-rtl)" />
          </>
        )}
        {children}
      </svg>
      <div className="mt-2 text-center">
        <div className="text-sm font-bold text-text-primary">{title}</div>
        <div className="text-xs text-text-secondary">{caption}</div>
      </div>
    </div>
  );
}
