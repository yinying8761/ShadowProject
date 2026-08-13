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
          // Ice-blue holographic palette
          bg: '#080c14',
          sidebar: '#0f1420',
          card: '#141a28',
          border: 'rgba(255, 255, 255, 0.06)',
          accent: '#00c6ff',
          'accent-hover': '#00b0e6',
          'accent-muted': 'rgba(0, 198, 255, 0.12)',
          'accent-glow': 'rgba(0, 198, 255, 0.25)',
          text: '#e8ecf4',
          'text-muted': '#7a8498',
          // Functional
          nametag: '#ff7eb3',
          'nametag-bg': 'rgba(255, 126, 179, 0.15)',
          success: '#00e5bf',
          warning: '#f59e0b',
          error: '#ef4444',
          // Overlays
          overlay: 'rgba(8, 12, 20, 0.78)',
          'overlay-strong': 'rgba(8, 12, 20, 0.94)',
        },
      },
      animation: {
        'pulse-soft': 'pulse-soft 2s ease-in-out infinite',
        'fade-in': 'fade-in 0.3s ease-out',
        'slide-up': 'slide-up 0.3s ease-out',
        'scale-in': 'scale-in 0.22s cubic-bezier(0.34, 1.56, 0.64, 1)',
        'cursor-blink': 'cursor-blink 1s step-end infinite',
        'breathe': 'breathe 4s ease-in-out infinite',
        'glow': 'glow 3s ease-in-out infinite',
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
        'scale-in': {
          from: { opacity: '0', transform: 'scale(0.95) translateY(8px)' },
          to: { opacity: '1', transform: 'scale(1) translateY(0)' },
        },
        'cursor-blink': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0' },
        },
        'breathe': {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-4px)' },
        },
        'glow': {
          '0%, 100%': { boxShadow: '0 0 8px rgba(0, 198, 255, 0.15)' },
          '50%': { boxShadow: '0 0 20px rgba(0, 198, 255, 0.35)' },
        },
      },
    },
  },
  plugins: [],
}
