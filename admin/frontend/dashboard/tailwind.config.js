import fs from 'node:fs'
import path from 'node:path'

import frappeUIPreset from 'frappe-ui/tailwind'
import typography from '@tailwindcss/typography'
import plugin from 'tailwindcss/plugin'

const iconsDir = path.resolve('src/icons')

const pilotIcons = plugin(({ matchComponents }) => {
  const names = fs.readdirSync(iconsDir).map((file) => file.replace(/\.svg$/, ''))

  const iconRule = (name) => {
    const svg = fs.readFileSync(path.join(iconsDir, `${name}.svg`), 'utf8').replace(/\s+/g, ' ').trim()
    const uri = `url("data:image/svg+xml;utf8,${encodeURIComponent(svg)}")`

    return {
      '&:where(:not(svg))': {
        display: 'block',
        width: '1em',
        height: '1em',
        'flex-shrink': '0',
        'background-color': 'currentColor',
        'mask-image': uri,
        'mask-repeat': 'no-repeat',
        'mask-position': 'center',
        'mask-size': 'contain',
      },
    }
  }

  matchComponents(
    { lucide: iconRule },
    { values: Object.fromEntries(names.map((name) => [name, name])), type: 'any' },
  )
})

/** @type {import('tailwindcss').Config} */
export default {
  presets: [frappeUIPreset],
  content: [
    './index.html',
    './src/**/*.{vue,js,ts}',
    './node_modules/frappe-ui/src/**/*.{vue,js,ts}',
    '../../../pilot/tasks/**/*.py',
  ],
  theme: {
    extend: {
      screens: {
        '2xl': '1536px',
      },
      backgroundColor: {
        'surface-gray-0': 'var(--surface-gray-0)',
      },
      borderColor: {
        'outline-gray-1.5': 'var(--outline-gray-1-5)',
      },
    },
  },
  plugins: [typography, pilotIcons],
}
