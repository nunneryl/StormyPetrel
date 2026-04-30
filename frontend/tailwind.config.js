/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './content/**/*.{md,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // Ocean-inspired surface tokens (used throughout layout + cards).
        // The naming intentionally reads top-down: ink-950 is the deepest
        // background, surface-300 the most-elevated card hover.
        ink: {
          950: '#0B1426',  // page background
          900: '#111B2E',  // primary card background
          800: '#162034',  // elevated card background
          700: '#1C2840',  // hover state
          600: '#1E3048',  // border
          500: '#243653',  // subtle divider
        },
        // Brand accent. cyan is the active-state / link-hover color;
        // the 400 step is for icons + arrows that need to read on dark
        // backgrounds without being shouty.
        cyan: {
          400: '#38BDF8',
          500: '#00B4D8',
          600: '#0096B7',
        },
        // Distinct arrow palettes so swell vs wind reads at a glance.
        swell: { DEFAULT: '#38BDF8' },
        wind:  { DEFAULT: '#A3E635' },
        // Rating tier scale — kept here so the same hex appears in CSS
        // (Tailwind class) AND in JS (lib/ratings.ts hex) without drift.
        rating: {
          flat:     '#6B7280',
          poor:     '#EF4444',
          poorfair: '#F97316',
          fair:     '#EAB308',
          fairgood: '#84CC16',
          good:     '#22C55E',
          goodepic: '#14B8A6',
          epic:     '#8B5CF6',
        },
        // Wind-quality coloring for the on-shore / off-shore label.
        wind_q: {
          offshore: '#22C55E',
          cross:    '#EAB308',
          onshore:  '#EF4444',
        },
        // Slate text scale (neutral; lifted from Tailwind's defaults but
        // pinned here so the design tokens are readable from one place).
        text: {
          primary:   '#F1F5F9',
          secondary: '#94A3B8',
          muted:     '#64748B',
        },
      },
      fontFamily: {
        sans: [
          'Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif',
        ],
        mono: [
          'ui-monospace', 'SF Mono', 'SFMono-Regular', 'Menlo', 'monospace',
        ],
      },
      letterSpacing: {
        tightish: '-0.015em',
        widest2:  '0.18em',
      },
      boxShadow: {
        // Card lift — used on hover and on the prominent rating badge.
        card: '0 1px 0 0 rgba(255,255,255,0.04) inset, 0 0 0 1px rgba(255,255,255,0.04)',
        liftsm: '0 6px 18px -8px rgba(0, 180, 216, 0.25)',
      },
      keyframes: {
        pulseSubtle: {
          '0%, 100%': { opacity: '1' },
          '50%':      { opacity: '0.6' },
        },
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
      animation: {
        pulseSubtle: 'pulseSubtle 2s ease-in-out infinite',
        shimmer:     'shimmer 1.6s linear infinite',
      },
    },
  },
  plugins: [],
};
