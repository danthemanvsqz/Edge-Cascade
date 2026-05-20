/**
 * HTTP shell → WS handoff. Streams the initial paint to a node:http
 * ServerResponse using `Transfer-Encoding: chunked`. After this returns, the
 * browser's htmx-ws extension opens the WS connection declared in the shell
 * (`<body hx-ext="ws" ws-connect="/ws">`) and the WS adapter takes over.
 *
 * The library is bring-your-own-router: this is the smallest useful adapter,
 * not the only one. Express/Fastify wrappers land in M6.
 */
import type { ServerResponse } from "node:http";
import { renderToStream } from "./render.js";
import type { RenderContext } from "./render.js";

export interface StreamShellOptions {
  status?: number;
  headers?: Record<string, string>;
}

/**
 * Stream `node` to `res` as `text/html`. Honors backpressure: pauses the
 * iterator when `res.write` returns `false` and resumes on `drain`. Resolves
 * once `res.end()` is called.
 */
export async function streamShell(
  res: ServerResponse,
  node: unknown,
  ctx?: RenderContext,
  opts?: StreamShellOptions,
): Promise<void> {
  res.writeHead(opts?.status ?? 200, {
    "content-type": "text/html; charset=utf-8",
    "cache-control": "no-store",
    ...opts?.headers,
  });

  for await (const chunk of renderToStream(node, ctx)) {
    if (!res.write(chunk)) {
      await new Promise<void>((resolve) => res.once("drain", resolve));
    }
  }
  res.end();
}
