import type { Cam } from '@/lib/cams';
import { camDarkness, camWatchUrl, providerLabel } from '@/lib/cams';

// Renders ALL active cams attached to a spot. Embed-mode cams (one
// or more) go up top as a 16:9 iframe each; link-mode cams render
// below as compact banner cards with a "Watch live on …" button.
//
// Server component. Computes the dark-hours hint at request time so
// the visitor sees fresh "sunrise at …" copy without a hydration shim.

export function CamSection({
  cams,
  lat,
  lng,
}: {
  cams: Cam[];
  lat: number | null;
  lng: number | null;
}) {
  if (cams.length === 0) return null;
  const embeds = cams.filter((c) => c.display_mode === 'embed');
  const links = cams.filter((c) => c.display_mode === 'link');
  return (
    <section className="space-y-3">
      {embeds.map((c) => (
        <CamEmbedCard key={c.id} cam={c} lat={lat} lng={lng} />
      ))}
      {links.map((c) => (
        <CamLinkCard key={c.id} cam={c} />
      ))}
    </section>
  );
}

function CamEmbedCard({
  cam,
  lat,
  lng,
}: {
  cam: Cam;
  lat: number | null;
  lng: number | null;
}) {
  const offline = cam.status !== 'active' || !cam.embed_url;
  const { isDark, sunriseLabel } = camDarkness(lat, lng);
  return (
    <div className="rounded-xl overflow-hidden border border-ink-600 bg-black shadow-card">
      {offline ? (
        <OfflinePane providerName={providerLabel(cam.provider)} />
      ) : (
        <div className="relative w-full" style={{ paddingTop: '56.25%' }}>
          <iframe
            src={cam.embed_url ?? undefined}
            title={cam.cam_name}
            allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
            allowFullScreen
            frameBorder={0}
            className="absolute inset-0 w-full h-full"
          />
        </div>
      )}
      <footer className="flex items-center justify-between gap-3 px-3.5 py-2 text-[11px] bg-white">
        <div className="flex items-center gap-2 min-w-0">
          <CameraIcon className="text-cyan-600 shrink-0" />
          <span className="text-text-secondary truncate">
            {cam.cam_name}
            {cam.attribution && (
              <>
                {' '}— Cam by{' '}
                {cam.attribution_url ? (
                  <a
                    href={cam.attribution_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-cyan-600 hover:underline"
                  >
                    {cam.attribution}
                  </a>
                ) : (
                  <span className="text-text-primary">{cam.attribution}</span>
                )}
              </>
            )}
          </span>
        </div>
        {isDark && sunriseLabel && (
          <span className="text-text-muted whitespace-nowrap">
            ☾ may be dark — sunrise at {sunriseLabel}
          </span>
        )}
      </footer>
    </div>
  );
}

function CamLinkCard({ cam }: { cam: Cam }) {
  const watchUrl = camWatchUrl(cam);
  const provider = providerLabel(cam.provider);
  const thumb =
    cam.provider === 'youtube' && cam.resolved_video_id
      ? `https://img.youtube.com/vi/${cam.resolved_video_id}/hqdefault.jpg`
      : null;
  return (
    <a
      href={watchUrl ?? '#'}
      target="_blank"
      rel="noopener noreferrer"
      className="flex items-stretch rounded-xl border border-ink-600 bg-white shadow-card hover:border-cyan-500 transition overflow-hidden group"
    >
      <div className="relative w-32 sm:w-44 shrink-0 bg-ink-800 flex items-center justify-center">
        {thumb ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={thumb}
            alt={cam.cam_name}
            className="absolute inset-0 w-full h-full object-cover"
            loading="lazy"
          />
        ) : (
          <CameraIcon size={28} className="text-text-muted" />
        )}
      </div>
      <div className="flex-1 min-w-0 px-3.5 py-3 flex flex-col justify-between gap-1">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-bold text-text-primary truncate">
              {cam.cam_name}
            </span>
            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-widest2 bg-ink-800 text-text-secondary">
              {provider}
            </span>
          </div>
          {cam.attribution && (
            <div className="text-xs text-text-muted truncate mt-0.5">
              Cam by {cam.attribution}
            </div>
          )}
        </div>
        <span className="text-xs font-bold text-cyan-600 group-hover:underline">
          Watch live on {provider} →
        </span>
      </div>
    </a>
  );
}

function OfflinePane({ providerName }: { providerName: string }) {
  return (
    <div className="relative w-full bg-ink-900" style={{ paddingTop: '56.25%' }}>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center px-4">
        <OfflineIcon className="text-text-muted mb-2" />
        <div className="text-sm font-bold text-text-secondary">
          Cam currently offline
        </div>
        <div className="text-xs text-text-muted">
          {providerName} stream isn&rsquo;t broadcasting right now. Check back later.
        </div>
      </div>
    </div>
  );
}

function CameraIcon({
  className = '',
  size = 13,
}: {
  className?: string;
  size?: number;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <path d="M23 7l-7 5 7 5V7z" />
      <rect x="1" y="5" width="15" height="14" rx="2" ry="2" />
    </svg>
  );
}

function OfflineIcon({ className = '' }: { className?: string }) {
  return (
    <svg
      width="32"
      height="32"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <path d="M23 7l-7 5 7 5V7z" />
      <rect x="1" y="5" width="15" height="14" rx="2" ry="2" />
      <line x1="2" y1="2" x2="22" y2="22" />
    </svg>
  );
}
