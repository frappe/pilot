import tailwindcss from "tailwindcss";
import autoprefixer from "autoprefixer";

const rootToHost = {
  postcssPlugin: "root-to-host",

  Rule: (rule) => {
    rule.selector = rule.selector.replaceAll(":root", ":host");
  },
  Declaration: (declaration) => {
    // Shadow-root rem units still use the host page's root font size.
    declaration.value = declaration.value.replace(/(-?[\d.]+)rem\b/g, (_, value) => `${Number(value) * 16}px`);
  },
};

export default {
  plugins: [tailwindcss, autoprefixer, rootToHost],
};
