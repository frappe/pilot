import frappeUIPreset from 'frappe-ui/tailwind'
import typography from '@tailwindcss/typography'

/** @type {import('tailwindcss').Config} */
export default {
  presets: [frappeUIPreset],
  content: [
    './index.html',
    './src/**/*.{vue,js,ts}',
    './node_modules/frappe-ui/src/**/*.{vue,js,ts}',
  ],
  theme: {
    extend: {
      backgroundColor: {
        'surface-gray-0': 'var(--surface-gray-0)',
      },
      borderColor: {
        'outline-gray-1.5': 'var(--outline-gray-1-5)',
      },
    },
  },
  plugins: [typography],
}
