import React from "react";
import ReactDOM from "react-dom/client";
// The framework-agnostic React entrypoint, not `@vercel/analytics/next` — this
// app is Vite + React with no Next.js, and the /next subpath imports
// `next/navigation`, which would fail the build.
import { Analytics } from "@vercel/analytics/react";
import App from "./App.jsx";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
    {/* Injects the tracking script only on Vercel; a no-op in dev and in tests
        (which render App directly and never mount this file). */}
    <Analytics />
  </React.StrictMode>
);
