import tailwindcss from "tailwindcss";
import autoprefixer from "autoprefixer";

const rootToHost = {
  postcssPlugin: "root-to-host",

  Rule: (rule) => {
    rule.selector = rule.selector.replaceAll(":root", ":host");
  },
};

export default {
  plugins: [tailwindcss, autoprefixer, rootToHost],
};
