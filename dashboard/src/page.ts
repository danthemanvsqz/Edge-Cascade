/**
 * The top-level page shell. Initial HTTP paint: the htmx + htmx-ws bootstrap,
 * the live regions mounted inline behind their stable `<vinyl-slot id>`
 * wrappers (Vinyl M5), and a placeholder for the cascade-flow SVG that
 * Slice 6 will fill in.
 *
 * No client JS state. Every interaction is a server round-trip; every update
 * is a `hx-swap-oob` frame from the SignalHub.
 */
import { h } from "@danthemanvsqz/vinyl";
import type { VNode } from "@danthemanvsqz/vinyl";

import type { DashContext } from "./app.js";
import { nowPlayingRegion, rateMeterRegion } from "./panels.js";

export function page(ctx: DashContext): VNode {
  return h(
    "html",
    { lang: "en" },
    h(
      "head",
      null,
      h("meta", { charset: "utf-8" }),
      h("meta", { name: "viewport", content: "width=device-width,initial-scale=1" }),
      h("title", null, "edge-cascade · live"),
      h("link", { rel: "stylesheet", href: "/style.css" }),
      h("script", { src: "https://unpkg.com/htmx.org@2.0.4" }),
      h("script", { src: "https://unpkg.com/htmx-ext-ws@2.0.2" }),
    ),
    h(
      "body",
      { "hx-ext": "ws", "ws-connect": "/ws", class: "dashboard" },
      h(
        "header",
        { class: "topbar" },
        h("h1", null, "edge-cascade"),
        rateMeterRegion.mount(ctx),
      ),
      h(
        "main",
        { class: "stage" },
        // Slice 6 replaces this placeholder with the full <CascadeFlow>
        // component (SVG topology + particle live region + per-tier
        // sparklines/stats).
        h(
          "div",
          { id: "cascade-flow-placeholder", class: "placeholder" },
          "cascade-flow SVG lands in slice 6",
        ),
      ),
      h(
        "aside",
        { class: "side" },
        h("h2", null, "now playing"),
        nowPlayingRegion.mount(ctx),
      ),
    ),
  );
}
