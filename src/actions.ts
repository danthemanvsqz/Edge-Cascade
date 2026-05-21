/**
 * M5 — inbound actions (the state model's write half).
 *
 * htmx `ws-send` serializes the triggering form/element into a JSON text frame
 * and tucks its own metadata under a `HEADERS` key. `parseMessage` splits that
 * into `{ input, headers, raw }`; `createActionRouter` resolves an action name
 * and dispatches to a registered handler. The handler (user code) reads/writes
 * the DB, then re-renders affected live regions via `ctx.refresh(...)` (push to
 * the acting connection) or a `SignalHub.emit` (push to every subscriber).
 *
 * The router is what you hand to `createWSServer({ onMessage })`. It never
 * throws into the socket: parse failures, unknown actions, and handler errors
 * all route to overridable callbacks (defaulting to console) so one bad frame
 * never kills the connection.
 */
import type { VinylConnection } from "./ws.js";
import type { LiveRegion } from "./live.js";

export type ActionInput = Record<string, unknown>;

export interface ParsedMessage {
  readonly raw: unknown;
  /** Form fields from the frame, minus the htmx `HEADERS` envelope. */
  readonly input: ActionInput;
  /** htmx metadata (`HX-Trigger`, `HX-Trigger-Name`, `HX-Target`, …). */
  readonly headers: Record<string, string | null>;
}

export interface ActionContext<C> {
  readonly conn: VinylConnection<C>;
  /** Convenience alias for `conn.context` (DB handle, authed user, …). */
  readonly context: C;
  readonly input: ActionInput;
  readonly headers: Record<string, string | null>;
  readonly name: string;
  /** Re-render the given regions from current context and push one frame to
   * THIS connection. For cross-connection updates, use a `SignalHub`. */
  refresh(...regions: LiveRegion<C>[]): void;
}

export type ActionHandler<C> = (ctx: ActionContext<C>) => void | Promise<void>;

export interface ActionDef<C> {
  readonly name: string;
  readonly handler: ActionHandler<C>;
}

export function defineAction<C>(
  name: string,
  handler: ActionHandler<C>,
): ActionDef<C> {
  return { name, handler };
}

function coerceHeaders(value: unknown): Record<string, string | null> {
  const headers: Record<string, string | null> = {};
  if (typeof value === "object" && value !== null) {
    for (const [k, v] of Object.entries(value)) {
      headers[k] = typeof v === "string" ? v : null;
    }
  }
  return headers;
}

export function parseMessage(data: string): ParsedMessage {
  const raw: unknown = JSON.parse(data);
  if (typeof raw !== "object" || raw === null) {
    return { raw, input: {}, headers: {} };
  }
  const input: ActionInput = {};
  let headers: Record<string, string | null> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (key === "HEADERS") {
      headers = coerceHeaders(value);
    } else {
      input[key] = value;
    }
  }
  return { raw, input, headers };
}

export interface ActionRouterOptions<C> {
  readonly actions: ReadonlyArray<ActionDef<C>>;
  /** Override how the action name is read from a frame. Default: a non-empty
   * `input.action`, else a non-empty `HEADERS["HX-Trigger-Name"]`. */
  nameFrom?(msg: ParsedMessage): string | undefined;
  onUnknown?(name: string | undefined, conn: VinylConnection<C>): void;
  onParseError?(raw: string, err: unknown, conn: VinylConnection<C>): void;
  onError?(err: unknown, name: string, conn: VinylConnection<C>): void;
}

function nonEmpty(v: unknown): v is string {
  return typeof v === "string" && v.trim() !== "";
}

function defaultNameFrom(msg: ParsedMessage): string | undefined {
  if (nonEmpty(msg.input.action)) return msg.input.action;
  const trigger = msg.headers["HX-Trigger-Name"];
  if (nonEmpty(trigger)) return trigger;
  return undefined;
}

/**
 * Build the `onMessage` handler for `createWSServer`. Parses each inbound text
 * frame, resolves the action name, and dispatches. Errors are reported, never
 * thrown — the socket survives a bad frame.
 */
export function createActionRouter<C>(
  opts: ActionRouterOptions<C>,
): (conn: VinylConnection<C>, data: string) => Promise<void> {
  const table = new Map<string, ActionHandler<C>>(
    opts.actions.map((a) => [a.name, a.handler]),
  );
  const nameFrom = opts.nameFrom ?? defaultNameFrom;

  return async (conn, data) => {
    let msg: ParsedMessage;
    try {
      msg = parseMessage(data);
    } catch (err) {
      if (opts.onParseError) opts.onParseError(data, err, conn);
      else console.error("vinyl: failed to parse ws frame", data, err);
      return;
    }

    const name = nameFrom(msg);
    const handler = name !== undefined ? table.get(name) : undefined;
    if (handler === undefined || name === undefined) {
      if (opts.onUnknown) opts.onUnknown(name, conn);
      else console.warn("vinyl: no action for frame", name);
      return;
    }

    const ctx: ActionContext<C> = {
      conn,
      context: conn.context,
      input: msg.input,
      headers: msg.headers,
      name,
      refresh(...regions) {
        conn.push(...regions.map((r) => r.frame(conn.context)));
      },
    };

    try {
      await handler(ctx);
    } catch (err) {
      if (opts.onError) opts.onError(err, name, conn);
      else console.error("vinyl: action handler threw", name, err);
    }
  };
}
