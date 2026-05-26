import { ImageResponse } from 'next/og';

export const alt = 'Stormy Petrel — Free US Surf Forecasts';
export const revalidate = 86400;
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

export default function OGImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          padding: 80,
          background: 'linear-gradient(135deg, #04080f 0%, #0f1a2c 60%, #1f3151 100%)',
          color: '#e5edf5',
          fontFamily: 'system-ui, sans-serif',
        }}
      >
        <div
          style={{
            color: '#3da9d7',
            fontSize: 32,
            fontWeight: 700,
            letterSpacing: '0.3em',
            textTransform: 'uppercase',
            marginBottom: 24,
          }}
        >
          Stormy Petrel
        </div>
        <div
          style={{
            fontSize: 96,
            fontWeight: 800,
            lineHeight: 1.05,
            letterSpacing: '-0.03em',
            color: '#ffffff',
          }}
        >
          Free surf forecasts.
        </div>
        <div
          style={{
            fontSize: 96,
            fontWeight: 800,
            lineHeight: 1.05,
            letterSpacing: '-0.03em',
            color: '#3da9d7',
          }}
        >
          No paywall. No ads.
        </div>
        <div
          style={{
            marginTop: 32,
            fontSize: 28,
            color: '#8aa3c0',
          }}
        >
          ~500 US spots · NOAA-powered · stormypetrel.surf
        </div>
      </div>
    ),
    { ...size },
  );
}
