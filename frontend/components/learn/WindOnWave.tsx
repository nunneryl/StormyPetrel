// Three-panel illustration of how wind reshapes a breaking wave.
// Pure SVG, no client interactivity. Renders side-by-side on
// tablet+ and stacks on mobile. Wave/foam colors are intentionally
// constant (this is a physical-color illustration); only the panel
// background tracks the rest of the site's chrome.
//
// Each wave is layered: a paler back-shading polygon (#A8D0E8), the
// main wave body (#5DA8E0), and a separate lip-detail path so the
// curl behavior reads clearly without depending on the body path
// for shape definition.

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
          <path d="M 5 148 C 30 144 55 138 75 130 C 92 122 105 116 112 116 L 112 148 Z" fill="#A8D0E8" />
          <path d="M 5 148 C 30 146 55 142 75 135 C 95 128 108 122 118 122 C 128 124 134 130 136 136 C 136 142 130 144 124 142 L 158 148 L 175 148 L 175 180 L 5 180 Z" fill="#5DA8E0" />
          <path d="M 116 122 C 126 118 136 122 138 130 C 138 138 132 142 126 140 C 121 138 119 132 122 128 Z" fill="#5DA8E0" />
          <circle cx="146" cy="135" r="2.5" fill="#FFFFFF" />
          <circle cx="154" cy="141" r="2" fill="#FFFFFF" />
          <circle cx="162" cy="138" r="1.5" fill="#FFFFFF" />
          <circle cx="142" cy="143" r="1.8" fill="#FFFFFF" opacity="0.8" />
        </Panel>

        <Panel title="Calm" caption="Standard plunging break" windLabel="No wind">
          {/* Calm — clean plunger, mid-pitch */}
          <path d="M 5 148 C 30 144 55 136 75 122 C 90 108 100 95 110 90 L 110 148 Z" fill="#A8D0E8" />
          <path d="M 5 148 C 30 146 55 140 75 130 C 92 118 105 100 115 92 C 125 86 134 90 138 100 C 142 112 140 124 132 130 C 124 134 116 132 114 126 C 113 120 118 116 124 117 C 130 119 132 124 130 128 L 152 142 L 175 148 L 175 180 L 5 180 Z" fill="#5DA8E0" />
          <path d="M 125 92 C 138 90 144 100 142 112 C 138 122 130 124 126 120 C 124 114 128 108 132 108 Z" fill="#5DA8E0" />
          <circle cx="122" cy="128" r="1.5" fill="#FFFFFF" opacity="0.8" />
          <circle cx="128" cy="132" r="1" fill="#FFFFFF" opacity="0.6" />
        </Panel>

        <Panel
          title="Offshore wind"
          caption="Lip held up, hollow barrel"
          windLabel="Wind direction"
          windDirection="rtl"
        >
          {/* Offshore — tall held-up lip with spray feathering off the top */}
          <path d="M 5 148 C 25 144 45 134 65 120 C 80 100 92 75 102 65 L 102 148 Z" fill="#A8D0E8" />
          <path d="M 5 148 C 25 146 45 140 65 130 C 82 118 92 100 100 80 C 106 65 115 60 122 62 C 130 66 136 80 138 100 C 140 118 136 132 128 138 C 120 142 114 138 114 130 C 115 124 122 122 126 126 C 128 130 126 134 124 134 L 148 144 L 175 148 L 175 180 L 5 180 Z" fill="#5DA8E0" />
          <path d="M 118 62 C 130 60 138 72 140 88 C 140 98 134 102 130 100 C 126 96 124 88 126 78 C 128 70 124 66 120 68 Z" fill="#5DA8E0" />
          <circle cx="110" cy="58" r="2" fill="#FFFFFF" />
          <circle cx="100" cy="55" r="1.8" fill="#FFFFFF" />
          <circle cx="90" cy="56" r="1.4" fill="#FFFFFF" opacity="0.9" />
          <circle cx="80" cy="60" r="1.1" fill="#FFFFFF" opacity="0.7" />
          <circle cx="70" cy="64" r="0.9" fill="#FFFFFF" opacity="0.5" />
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
        viewBox="0 0 180 180"
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
          y={windDirection ? 22 : 48}
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
