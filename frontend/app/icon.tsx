import { ImageResponse } from 'next/og';

// Stormy Petrel favicon — two stacked wave curves in cyan on the
// brand-dark background. 32×32 PNG produced at request time by
// next/og's edge runtime; cached aggressively by browsers + CDNs.
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
          background: '#0B1426',
          borderRadius: 6,
        }}
      >
        <svg
          width="22"
          height="22"
          viewBox="0 0 24 24"
          fill="none"
          stroke="#00B4D8"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M2 12 C 5 8, 8 8, 12 12 S 19 16, 22 12" />
          <path d="M2 17 C 5 13, 8 13, 12 17 S 19 21, 22 17" opacity="0.55" />
        </svg>
      </div>
    ),
    { ...size },
  );
}
