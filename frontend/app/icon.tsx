import { ImageResponse } from 'next/og';

// Stormy Petrel favicon — abstract wave glyph in the brand sea-blue on
// the dark ink background. 32×32 PNG produced at request time by
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
          background: '#04080f',
          color: '#3da9d7',
          fontSize: 24,
          fontWeight: 800,
          letterSpacing: '-0.05em',
        }}
      >
        ~
      </div>
    ),
    { ...size },
  );
}
