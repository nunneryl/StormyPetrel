import { ImageResponse } from 'next/og';
import { readFileSync } from 'fs';
import { join } from 'path';

// Stormy Petrel favicon — SP monogram, sourced from the brand asset
// at public/brand/sp-initials.png. The PNG is read at request time
// (Node runtime — Edge can't fs.readFile from the public dir) and
// inlined as a data URL so ImageResponse can render it.
//
// If the asset is missing (e.g. a fresh checkout before the file was
// uploaded), we fall back to a programmatic SP wordmark so the build
// + favicon route never 500.

export const runtime = 'nodejs';
export const size = { width: 32, height: 32 };
export const contentType = 'image/png';

function loadMonogramDataUrl(): string | null {
  try {
    const filePath = join(process.cwd(), 'public', 'brand', 'sp-initials.png');
    const bytes = readFileSync(filePath);
    return `data:image/png;base64,${bytes.toString('base64')}`;
  } catch {
    return null;
  }
}

export default function Icon() {
  const dataUrl = loadMonogramDataUrl();
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#FFFFFF',
        }}
      >
        {dataUrl ? (
          // eslint-disable-next-line @next/next/no-img-element, jsx-a11y/alt-text
          <img
            src={dataUrl}
            width={32}
            height={32}
            style={{ width: 32, height: 32, objectFit: 'contain' }}
          />
        ) : (
          <span
            style={{
              fontSize: 18,
              fontWeight: 800,
              letterSpacing: '-0.02em',
              color: '#0F172A',
              fontFamily: 'system-ui, sans-serif',
            }}
          >
            SP
          </span>
        )}
      </div>
    ),
    { ...size },
  );
}
