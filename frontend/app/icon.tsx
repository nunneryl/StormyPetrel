import { ImageResponse } from 'next/og';

// Stormy Petrel favicon — stylized petrel silhouette in the brand
// navy on a light background. Programmatic so we don't have to bundle
// a binary PNG. To swap in a hand-drawn cropped version of the actual
// logo, drop a PNG at app/icon.png — Next.js auto-prefers static
// icon files over the programmatic icon.tsx.

export const size = { width: 32, height: 32 };
export const contentType = 'image/png';

export default function Icon() {
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
          borderRadius: 6,
        }}
      >
        {/* Compact petrel silhouette — body + outstretched wing — in the
            same navy color as the wordmark. */}
        <svg width="26" height="26" viewBox="0 0 32 32" fill="none">
          <path
            d="M3 19 C 7 14, 13 11, 18 14 L 22 9 L 23 16 L 28 18 C 24 20, 17 22, 13 21 L 9 24 Z"
            fill="#0F172A"
          />
          <circle cx="14" cy="16.5" r="1.2" fill="#FFFFFF" />
        </svg>
      </div>
    ),
    { ...size },
  );
}
