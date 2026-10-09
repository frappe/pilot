import frappeUIPreset, { content } from "frappe-ui/tailwind";

/** @type {import('tailwindcss').Config} */
export default {
  presets: [frappeUIPreset],
  content: [
    "./src/**/*.{vue,js,ts}",
    ...content,
  ],
};
