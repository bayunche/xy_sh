/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(214 32% 91%)",
        muted: "hsl(210 40% 96%)",
        mutedfg: "hsl(215 16% 47%)",
        foreground: "hsl(222 47% 11%)",
        primary: { DEFAULT: "hsl(173 80% 30%)", fg: "hsl(0 0% 98%)" },
        card: "hsl(0 0% 100%)",
      },
      borderRadius: { xl: "0.9rem" },
      boxShadow: { soft: "0 1px 3px 0 rgb(15 23 42 / 0.06), 0 4px 14px -6px rgb(15 23 42 / 0.08)" },
    },
  },
  plugins: [],
};
