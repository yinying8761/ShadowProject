/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        companion: {
          bg: '#0f0f14',
          sidebar: '#16161e',
          card: '#1c1c28',
          border: '#2a2a3a',
          accent: '#7c6ff0',
          'accent-hover': '#6b5edb',
          text: '#e0e0ec',
          'text-muted': '#8888a0',
          // Floating widget tokens
          overlay: 'rgba(15, 15, 20, 0.78)',
          'overlay-strong': 'rgba(10, 10, 16, 0.92)',
          nametag: '#ec5a92',
          'nametag-bg': 'rgba(236, 90, 146, 0.18)',
        },
      },
      animation: {
        'pulse-soft': 'pulse-soft 2s ease-in-out infinite',
        'fade-in': 'fade-in 0.3s ease-out',
        'slide-up': 'slide-up 0.3s ease-out',
        'cursor-blink': 'cursor-blink 1s step-end infinite',
        'breathe': 'breathe 4s ease-in-out infinite',
      },
      keyframes: {
        'pulse-soft': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.6' },
        },
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(10px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'cursor-blink': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0' },
        },
        'breathe': {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-4px)' },
        },
      },
    },
  },
  plugins: [],
}
