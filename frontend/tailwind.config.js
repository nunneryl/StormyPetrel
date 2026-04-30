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
        // Light surface tokens — used for body / cards / dividers /
        // borders. Inverted from the original dark scheme so naming
        // stays consistent across the codebase: ink-950 = page bg,
        // higher numbers go more "elevated" (subtler card / hover).
        ink: {
          950: '#FFFFFF',  // page background
          900: '#F8FAFC',  // primary card / panel
          800: '#F1F5F9',  // elevated card / table stripe
          700: '#E2E8F0',  // hover
          600: '#E2E8F0',  // border
          500: '#CBD5E1',  // subtle divider
        },
        // Dark surface tokens — kept for the nav bar (MSW-style dark
        // header on light body) and any "inverse" surfaces we add
        // later (e.g. footer, modal).
        deep: {
          950: '#0B1426',
          900: '#111B2E',
          800: '#162034',
          700: '#1C2840',
          600: '#1E3048',
          500: '#243653',
        },
        // Brand accent — ocean blue. The 500 step is the primary link
        // / button color on light backgrounds; 400 is its lighter sibling
        // for use on dark backgrounds (the nav bar).
        cyan: {
          400: '#0EA5E9',
          500: '#0284C7',
          600: '#0369A1',
        },
        // Distinct arrow palettes so swell vs wind reads at a glance
        // on light backgrounds (darker, higher-contrast hues than the
        // earlier dark-theme blues / limes).
        swell: { DEFAULT: '#0369A1' },
        wind:  { DEFAULT: '#15803D' },
        // Rating tier scale — kept identical to before so chart fills
        // and Leaflet markers don't recolor.
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
        // Wind-quality coloring — darker shades to read on white.
        wind_q: {
          offshore: '#15803D',
          cross:    '#CA8A04',
          onshore:  '#DC2626',
        },
        // Text scale for *light* backgrounds.
        text: {
          primary:   '#0F172A',
          secondary: '#475569',
          muted:     '#94A3B8',
        },
        // Inverse text scale — used on the dark nav bar / dark cards.
        text_inv: {
          primary:   '#F1F5F9',
          secondary: '#CBD5E1',
          muted:     '#94A3B8',
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
        // Subtle 1px-style shadow used on cards over the white page.
        card: '0 1px 2px 0 rgba(15, 23, 42, 0.04), 0 0 0 1px rgba(15, 23, 42, 0.04)',
        liftsm: '0 6px 18px -8px rgba(2, 132, 199, 0.25)',
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
