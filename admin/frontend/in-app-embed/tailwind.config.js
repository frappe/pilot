import frappeUIPreset, { content } from "frappe-ui/tailwind";

/** @type {import('tailwindcss').Config} */
export default {
  presets: [frappeUIPreset],
  content: [
    "./src/**/*.{vue,js,ts}",
    ...content,
  ],
  theme: {
    extend: {
      borderColor: {
        "outline-gray-1.5": "var(--outline-gray-1-5)",
      },
    },
  },
};
