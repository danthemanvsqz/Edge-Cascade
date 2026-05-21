/**
 * TodoMVC demo — the Vinyl wiring. The proof of thesis: every interaction is a
 * server round-trip, all state is in sqlite, and the browser ships ZERO state.
 * htmx `ws-send` forms post their fields over the WebSocket; an action mutates
 * the DB; `hub.emit(TODOS)` re-renders the bound live regions from the DB and
 * pushes `hx-swap-oob` frames to every connected client (multi-tab live).
 *
 * Authored with the hyperscript `h` (the demo is build-free); the same tree
 * could be written as JSX. Demo code — not part of the published surface.
 */
import {
  h,
  liveRegion,
  defineAction,
  createActionRouter,
  createSignalHub,
  createWSServer,
} from "../../src/index.js";
import type {
  VNode,
  ActionDef,
  SignalHub,
  VinylWSServer,
} from "../../src/index.js";
import type { DB } from "./db.js";
import {
  listTodos,
  addTodo,
  toggleTodo,
  clearCompleted,
  activeCount,
} from "./db.js";

/** Per-connection context: the shared DB handle and the signal hub. There is
 * no per-user state in this demo, so every connection sees the same context. */
export interface Ctx {
  db: DB;
  hub: SignalHub<Ctx>;
}

/** The single signal both regions hang off of. */
export const TODOS = "todos";

/** The list of todos — re-rendered from the DB on every change. */
export const todosRegion = liveRegion<Ctx>("todos", (ctx) =>
  h(
    "ul",
    { class: "todo-list" },
    ...listTodos(ctx.db).map((t) =>
      h(
        "li",
        t.done ? { class: "completed" } : null,
        h(
          "form",
          { "ws-send": "", class: "toggle" },
          h("input", { type: "hidden", name: "action", value: "toggle" }),
          h("input", { type: "hidden", name: "id", value: String(t.id) }),
          h("button", { type: "submit" }, t.done ? "✓" : "○"),
        ),
        h("span", { class: "text" }, t.text),
      ),
    ),
  ),
);

/** The "N left" counter — also derived purely from the DB. */
export const countRegion = liveRegion<Ctx>("count", (ctx) =>
  h("span", { class: "count" }, `${String(activeCount(ctx.db))} left`),
);

/** Action handlers. Each reads input, writes the DB, then emits the signal so
 * all subscribers re-render. The handler NEVER touches client state. */
export const actions: ActionDef<Ctx>[] = [
  defineAction<Ctx>("add", (a) => {
    const text = String(a.input.text ?? "").trim();
    if (text === "") return;
    addTodo(a.context.db, text);
    a.context.hub.emit(TODOS);
  }),
  defineAction<Ctx>("toggle", (a) => {
    const id = Number(a.input.id);
    if (!Number.isInteger(id)) return;
    toggleTodo(a.context.db, id);
    a.context.hub.emit(TODOS);
  }),
  defineAction<Ctx>("clear", (a) => {
    clearCompleted(a.context.db);
    a.context.hub.emit(TODOS);
  }),
];

/** The initial HTTP paint: the shell htmx connects the WS from, plus the live
 * regions mounted inline behind their stable `<vinyl-slot id>` wrappers. */
export function page(ctx: Ctx): VNode {
  return h(
    "html",
    { lang: "en" },
    h(
      "head",
      null,
      h("meta", { charset: "utf-8" }),
      h("title", null, "Vinyl · TodoMVC"),
      h("script", { src: "https://unpkg.com/htmx.org@2.0.4" }),
      h("script", { src: "https://unpkg.com/htmx-ext-ws@2.0.2" }),
    ),
    h(
      "body",
      { "hx-ext": "ws", "ws-connect": "/ws" },
      h("h1", null, "todos"),
      h(
        "form",
        { "ws-send": "", class: "add" },
        h("input", { type: "hidden", name: "action", value: "add" }),
        h("input", {
          name: "text",
          placeholder: "What needs doing?",
          autofocus: true,
        }),
      ),
      todosRegion.mount(ctx),
      countRegion.mount(ctx),
      h(
        "form",
        { "ws-send": "", class: "clear" },
        h("input", { type: "hidden", name: "action", value: "clear" }),
        h("button", { type: "submit" }, "Clear completed"),
      ),
    ),
  );
}

export interface TodoApp {
  /** WS adapter — wire to `http.on("upgrade", …)`. */
  vws: VinylWSServer;
  /** The shared context (also what `page()` renders from). */
  ctx: Ctx;
  /** Render the initial shell for an HTTP GET. */
  page(): VNode;
}

/** Wire the DB into a ready-to-serve TodoMVC app: a WS server whose inbound
 * frames route to the actions, with each connection's regions subscribed to
 * the TODOS signal (and unsubscribed on close). */
export function createTodoApp(db: DB): TodoApp {
  const hub = createSignalHub<Ctx>();
  const ctx: Ctx = { db, hub };
  const route = createActionRouter<Ctx>({ actions });

  const vws = createWSServer<Ctx>({
    path: "/ws",
    context: () => ctx,
    onConnect(conn) {
      hub.subscribe(TODOS, conn, todosRegion, countRegion);
    },
    onMessage: route,
    onClose(conn) {
      hub.remove(conn);
    },
  });

  return { vws, ctx, page: () => page(ctx) };
}
