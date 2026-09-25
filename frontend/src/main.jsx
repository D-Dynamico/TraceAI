import React from "react";
import ReactDOM from "react-dom/client";
// The framework-agnostic React entrypoint, not `@vercel/analytics/next` — this
// app is Vite + React with no Next.js, and the /next subpath imports
// `next/navigation`, which would fail the build.
import { Analytics } from "@vercel/analytics/react";
import App from "./App.jsx";
import "./index.css";
import { themeCss } from "./categories";

// The category mark colors, light and dark, as CSS variables. Defined in JS
// because categories.js is where each hue is decided and validated; injected
// before the first render so no mark ever paints unresolved.
const marks = document.createElement("style");
marks.textContent = themeCss();
document.head.appendChild(marks);

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
    {/* Injects the tracking script only on Vercel; a no-op in dev and in tests
        (which render App directly and never mount this file). */}
    <Analytics />
  </React.StrictMode>
);
