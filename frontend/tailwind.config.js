/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        cyan: {
          400: '#00d4ff',
          500: '#00b4d8',
          600: '#0096c7',
        },
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
};

