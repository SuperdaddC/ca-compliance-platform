/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'brand-blue': '#1a2744',
        'brand-blue-light': '#f0f7ff',
        'brand-gold': '#e8821a',
        'brand-gold-dark': '#d0741a',
      },
    },
  },
  plugins: [],
}
