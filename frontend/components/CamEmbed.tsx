import type { Cam } from '@/lib/cam-utils';
import {
  camDarkness,
  camWatchUrl,
  isCamLive,
  providerLabel,
  youtubeChannelUrl,
} from '@/lib/cam-utils';

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
  // YouTube cams stay status='active' even when the channel isn't
  // broadcasting; isCamLive() reads resolved_video_id for those, and
  // status + embed_url for everyone else.
  const live = isCamLive(cam);
  const channelUrl = youtubeChannelUrl(cam);
  const { isDark, sunriseLabel } = camDarkness(lat, lng);
  return (
    <div className="rounded-xl overflow-hidden border border-ink-600 bg-black shadow-card">
      {live ? (
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
      ) : (
        <OfflinePane
          providerName={providerLabel(cam.provider)}
          // For YouTube cams we know the channel URL — surface it so
          // the visitor can check upstream themselves. Falls through
          // to the generic "isn't broadcasting" message otherwise.
          channelUrl={channelUrl}
        />
      )}
      <footer className="flex items-center justify-between gap-3 px-3.5 py-2.5 text-sm bg-white">
        <div className="flex items-center gap-3 min-w-0 flex-wrap">
          {/* One line: cam name + source + Watch-live link. The
              attribution is the operator's preferred byline (e.g.
              "USGS CoastCam", "SurfChex"), so we use it directly
              rather than the generic provider label — falls back to
              the provider label when attribution is missing. */}
          <span className="font-bold text-text-primary truncate min-w-0">
            📹 {cam.cam_name}
            <span className="font-normal text-text-secondary">
              {' '}— via {cam.attribution ?? providerLabel(cam.provider)}
            </span>
          </span>
          {cam.attribution_url && (
            <a
              href={cam.attribution_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-bold text-cyan-600 hover:underline whitespace-nowrap"
            >
              Watch live →
            </a>
          )}
        </div>
        {isDark && sunriseLabel && (
          <span className="text-[11px] text-text-muted whitespace-nowrap">
            ☾ may be dark — sunrise at {sunriseLabel}
          </span>
        )}
      </footer>
    </div>
  );
}

function CamLinkCard({ cam }: { cam: Cam }) {
  const watchUrl = camWatchUrl(cam);
  // Source byline — operator's preferred attribution where available
  // (e.g. "USGS CoastCam", "SurfChex"), provider label otherwise so
  // the line still reads "via Live Cam" instead of "via undefined".
  const source = cam.attribution ?? providerLabel(cam.provider);
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
      <div className="flex-1 min-w-0 px-3.5 py-3 flex flex-col justify-center gap-1">
        <div className="font-bold text-text-primary truncate">
          📹 {cam.cam_name}
          <span className="font-normal text-text-secondary">
            {' '}— via {source}
          </span>
        </div>
        <span className="text-xs font-bold text-cyan-600 group-hover:underline">
          Watch live →
        </span>
      </div>
    </a>
  );
}

function OfflinePane({
  providerName,
  channelUrl,
}: {
  providerName: string;
  channelUrl: string | null;
}) {
  return (
    <div className="relative w-full bg-ink-900" style={{ paddingTop: '56.25%' }}>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center px-4">
        <OfflineIcon className="text-text-muted mb-2" />
        <div className="text-sm font-bold text-text-secondary">
          Stream offline right now
        </div>
        <div className="text-xs text-text-muted">
          {providerName} isn&rsquo;t broadcasting at the moment.
        </div>
        {channelUrl && (
          <a
            href={channelUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-2 inline-block text-xs font-bold text-cyan-400 hover:underline"
          >
            Check the {providerName} channel →
          </a>
        )}
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
