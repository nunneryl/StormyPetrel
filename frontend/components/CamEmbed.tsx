import type { Cam } from '@/lib/cams';
import { camDarkness, providerLabel } from '@/lib/cams';

// 16:9 responsive iframe pinned to the top of the spot page. The
// component is a server component — it runs at request time so the
// dark-hours hint reflects "now" for that visitor without needing
// client JS. (Force-dynamic on the spot page makes the surrounding
// render server-time too.)

export function CamEmbed({
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
    <section className="rounded-xl overflow-hidden border border-ink-600 bg-black shadow-card">
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
    </section>
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

function CameraIcon({ className = '' }: { className?: string }) {
  return (
    <svg
      width="13"
      height="13"
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
