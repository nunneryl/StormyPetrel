// Vertical stack of five layer cards showing the forecast chain
// from atmospheric model down to the final star rating. No
// interactivity — pure SVG, server-renderable.

type LayerProps = {
  num: number;
  color: string;
  title: string;
  description: string;
};

function Layer({ num, color, title, description }: LayerProps) {
  return (
    <g>
      <rect
        x="20"
        y="0"
        width="640"
        height="60"
        rx="8"
        fill="#F8FAFC"
        stroke="#E2E8F0"
        strokeWidth="0.5"
      />
      <circle cx="55" cy="30" r="18" fill={color} />
      <text
        x="55"
        y="35"
        textAnchor="middle"
        fill="#FFFFFF"
        fontSize="14"
        fontWeight="500"
      >
        {num}
      </text>
      <text x="90" y="24" fontSize="15" fontWeight="500" fill="#1E293B">
        {title}
      </text>
      <text x="90" y="42" fontSize="12" fill="#64748B">
        {description}
      </text>
    </g>
  );
}

function FlowLabel({ y, text }: { y: number; text: string }) {
  return (
    <g transform={`translate(340, ${y})`}>
      <text
        x="0"
        y="0"
        textAnchor="middle"
        fontSize="11"
        fontStyle="italic"
        fill="#64748B"
      >
        {text}
      </text>
      <line x1="0" y1="5" x2="0" y2="20" stroke="#CBD5E1" strokeWidth="1.5" />
      <polygon points="-4,15 0,22 4,15" fill="#CBD5E1" />
    </g>
  );
}

const LAYERS = [
  {
    num: 1,
    color: '#185FA5',
    title: 'Weather model',
    description: 'GFS, ECMWF, HRRR — predicts wind across the ocean',
    flow: 'wind forecast',
  },
  {
    num: 2,
    color: '#1D9E75',
    title: 'Global wave model',
    description: 'WaveWatch III, WAM — grows and propagates waves across the ocean',
    flow: 'open-ocean wave spectrum',
  },
  {
    num: 3,
    color: '#0F6E56',
    title: 'Nearshore transformation',
    description: 'SWAN, CDIP MOP — bends and shoals the swell through your local bathymetry',
    flow: 'spot-specific wave height',
  },
  {
    num: 4,
    color: '#BA7517',
    title: 'Wind + tide blend',
    description: 'Adds local wind quality and tide stage to the wave forecast',
    flow: 'conditions forecast',
  },
  {
    num: 5,
    color: '#D85A30',
    title: 'Human overlay',
    description: 'Forecaster judgment + ML corrections (Surfline LOTUS) — free apps skip this',
    flow: 'star rating + verdict',
  },
];

export function ForecastPipelineDiagram() {
  return (
    <div className="my-6 rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5">
      <svg viewBox="0 0 680 590" className="block w-full h-auto" role="img">
        <title>Forecast pipeline</title>
        <desc>
          Vertical flow showing how a surf forecast is built from atmospheric
          models down to a star rating.
        </desc>
        {LAYERS.map((layer, i) => (
          <g key={layer.num}>
            <g transform={`translate(0, ${20 + i * 100})`}>
              <Layer
                num={layer.num}
                color={layer.color}
                title={layer.title}
                description={layer.description}
              />
            </g>
            <FlowLabel y={95 + i * 100} text={layer.flow} />
          </g>
        ))}
        <rect
          x="200"
          y="540"
          width="280"
          height="38"
          rx="19"
          fill="#185FA5"
          stroke="#0F4B85"
          strokeWidth="0.5"
        />
        <text
          x="340"
          y="564"
          textAnchor="middle"
          fontSize="15"
          fontWeight="500"
          fill="#FFFFFF"
        >
          ⭐ &ldquo;Head-high, 3.2 stars&rdquo;
        </text>
      </svg>
    </div>
  );
}
