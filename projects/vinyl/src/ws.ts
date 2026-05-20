/**
 * M4 — WebSocket transport adapter. A thin layer over `ws` in noServer mode,
 * driven by node:http upgrade events. The library is bring-your-own-router /
 * bring-your-own-db; this module only owns the socket lifecycle and the push
 * fn. Per-connection state (authed user, DB handle, action bus) is whatever
 * `opts.context(req)` returns.
 *
 * The framing layer is `./oob.js` — `push(...elements)` joins the OOB strings
 * and sends one WS text message; htmx applies all top-level elements in
 * source order with a single settle pass per frame (see ARCHITECTURE.md §7).
 */
import type { IncomingMessage } from "node:http";
import type { Duplex } from "node:stream";
import { WebSocketServer } from "ws";
import type { RawData, WebSocket } from "ws";

export interface VinylConnection<C = unknown> {
  readonly socket: WebSocket;
  readonly req: IncomingMessage;
  readonly context: C;
  send(text: string): void;
  push(...elements: string[]): void;
  close(code?: number, reason?: string): void;
}

export interface CreateWSServerOptions<C> {
  /** Resolves per-conn context at upgrade time. Throw to reject the upgrade
   * (the socket is closed with HTTP 401 before any WS handshake). */
  context(req: IncomingMessage): C | Promise<C>;
  /** Fires once after the WS handshake completes. */
  onConnect?(conn: VinylConnection<C>): void | Promise<void>;
  /** Fires for each inbound TEXT message (binary frames are ignored). */
  onMessage?(conn: VinylConnection<C>, data: string): void | Promise<void>;
  /** Fires once when the socket closes for any reason. */
  onClose?(conn: VinylConnection<C>): void;
  /** When set, upgrades on a different URL pathname respond with 404. */
  path?: string;
}

export interface VinylWSServer {
  handleUpgrade(req: IncomingMessage, socket: Duplex, head: Buffer): void;
  close(): Promise<void>;
}

function rawToText(data: RawData): string {
  if (typeof data === "string") return data;
  if (Array.isArray(data)) return Buffer.concat(data).toString("utf8");
  if (Buffer.isBuffer(data)) return data.toString("utf8");
  return Buffer.from(data).toString("utf8");
}

export function createWSServer<C>(
  opts: CreateWSServerOptions<C>,
): VinylWSServer {
  const wss = new WebSocketServer({ noServer: true });

  return {
    handleUpgrade(req, socket, head) {
      if (opts.path !== undefined) {
        const pathname = new URL(req.url ?? "/", "http://x").pathname;
        if (pathname !== opts.path) {
          // The socket is still raw HTTP at this point — no WS frame yet.
          socket.write("HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n");
          socket.destroy();
          return;
        }
      }

      void (async () => {
        let context: C;
        try {
          context = await opts.context(req);
        } catch (err) {
          console.error(err);
          socket.write(
            "HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n",
          );
          socket.destroy();
          return;
        }

        wss.handleUpgrade(req, socket, head, (ws) => {
          const conn: VinylConnection<C> = {
            socket: ws,
            req,
            context,
            send(text) {
              ws.send(text);
            },
            push(...elements) {
              if (elements.length === 0) return;
              ws.send(elements.join(""));
            },
            close(code, reason) {
              ws.close(code, reason);
            },
          };

          ws.on("message", (data: RawData, isBinary: boolean) => {
            if (isBinary) return;
            const text = rawToText(data);
            void Promise.resolve(opts.onMessage?.(conn, text)).catch((err) => {
              console.error(err);
              ws.close(1011);
            });
          });

          ws.on("close", () => {
            opts.onClose?.(conn);
          });

          ws.on("error", (err) => {
            console.error(err);
          });

          void Promise.resolve(opts.onConnect?.(conn)).catch((err) => {
            console.error(err);
            ws.close(1011);
          });
        });
      })();
    },

    close(): Promise<void> {
      return new Promise((resolve) => wss.close(() => resolve()));
    },
  };
}
