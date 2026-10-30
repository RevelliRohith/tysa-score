import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        tysa: {
          bg: "#F0F4F8",
          surface: "#FFFFFF",
          navy: "#0F2137",
          text: "#1E293B",
          muted: "#64748B",
          border: "#E2E8F0",
          primary: "#0284C7",
          primaryHover: "#0369A1",
          teal: "#0D9488",
          tealHover: "#0F766E",
          success: "#10B981",
          warning: "#F59E0B",
          danger: "#EF4444",
        },
      },
    },
  },
  plugins: [],
};
export default config;
