/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          950: '#04080f',
          900: '#0a1220',
          800: '#0f1a2c',
          700: '#162338',
          600: '#1f3151',
          500: '#2c4773',
        },
        sea: {
          400: '#3da9d7',
          500: '#1e8bbf',
          600: '#136a96',
        },
        rating: {
          flat: '#5a6a7a',
          poor: '#c2362f',
          poorfair: '#d97a2b',
          fair: '#d8b13a',
          fairgood: '#9bbf3e',
          good: '#3aa55c',
          goodepic: '#1ea098',
          epic: '#8b5fbf',
        },
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
};
