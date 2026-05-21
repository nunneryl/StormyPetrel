// Three-panel illustration of how wind reshapes a breaking wave.
// Pure SVG, no client interactivity. Renders side-by-side on
// tablet+ and stacks on mobile. Wave/foam colors are intentionally
// constant (this is a physical-color illustration); only the panel
// background tracks the rest of the site's chrome.
//
// Each wave is a single path with a darker stroke (#3D7FB8) over
// the body fill (#5DA8E0) so the silhouette reads sharply without
// needing a separate back-shading layer.

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
          {/* Onshore — short crumbling crest with foam tumbling forward */}
          <path
            d="M 5 145 Q 30 144 55 138 Q 80 130 105 122 Q 122 119 132 124 Q 140 130 137 136 Q 130 140 126 138 L 175 145 L 175 170 L 5 170 Z"
            fill="#5DA8E0"
            stroke="#3D7FB8"
            strokeWidth="1"
          />
          <circle cx="148" cy="140" r="2.2" fill="#FFFFFF" />
          <circle cx="156" cy="143" r="1.8" fill="#FFFFFF" />
          <circle cx="164" cy="141" r="1.5" fill="#FFFFFF" />
        </Panel>

        <Panel title="Calm" caption="Standard plunging break" windLabel="No wind">
          {/* Calm — clean plunger pitching forward */}
          <path
            d="M 5 145 Q 30 144 55 137 Q 80 128 105 108 Q 118 95 130 98 Q 138 102 138 115 Q 135 124 127 124 Q 120 122 122 116 Q 127 114 128 118 L 134 128 Q 152 138 175 145 L 175 170 L 5 170 Z"
            fill="#5DA8E0"
            stroke="#3D7FB8"
            strokeWidth="1"
          />
          <circle cx="124" cy="120" r="1.2" fill="#FFFFFF" />
        </Panel>

        <Panel
          title="Offshore wind"
          caption="Lip held up, hollow barrel"
          windLabel="Wind direction"
          windDirection="rtl"
        >
          {/* Offshore — tall held-up lip with spray feathering off the top */}
          <path
            d="M 5 145 Q 25 144 50 138 Q 75 128 90 115 Q 102 95 110 78 Q 116 65 125 63 Q 134 65 138 85 Q 140 105 134 118 Q 126 126 120 123 Q 116 118 119 113 Q 124 111 126 116 L 132 126 Q 152 138 175 145 L 175 170 L 5 170 Z"
            fill="#5DA8E0"
            stroke="#3D7FB8"
            strokeWidth="1"
          />
          <circle cx="110" cy="60" r="2" fill="#FFFFFF" />
          <circle cx="100" cy="58" r="1.6" fill="#FFFFFF" />
          <circle cx="90" cy="60" r="1.3" fill="#FFFFFF" opacity="0.9" />
          <circle cx="80" cy="64" r="1" fill="#FFFFFF" opacity="0.7" />
          <circle cx="70" cy="68" r="0.8" fill="#FFFFFF" opacity="0.5" />
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
            <line x1="38" y1="40" x2="102" y2="40" stroke="#94A3B8" strokeWidth="1.2" markerEnd="url(#wow-arrow-ltr)" />
            <line x1="55" y1="55" x2="128" y2="55" stroke="#94A3B8" strokeWidth="1.2" markerEnd="url(#wow-arrow-ltr)" />
          </>
        )}
        {windDirection === 'rtl' && (
          <>
            <line x1="142" y1="40" x2="78" y2="40" stroke="#94A3B8" strokeWidth="1.2" markerEnd="url(#wow-arrow-rtl)" />
            <line x1="125" y1="55" x2="52" y2="55" stroke="#94A3B8" strokeWidth="1.2" markerEnd="url(#wow-arrow-rtl)" />
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
