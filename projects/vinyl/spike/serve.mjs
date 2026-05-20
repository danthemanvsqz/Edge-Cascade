// htmx-ws spike harness — reproducible evidence for ARCHITECTURE.md §7.
//
// Run:   node spike/serve.mjs       (then open http://localhost:8787)
// What you should see: four numbered demonstrations, each replacing the
// matching <vinyl-slot> in the page. Open DevTools → Network → WS to see the
// raw frames going out. The findings in ARCHITECTURE.md §7 were derived from
// the htmx 2.x source (spike/htmx-ws.ref.js, spike/htmx.ref.js) and are
// re-verifiable by hand here.
//
// No npm deps on purpose: the WS frame encoder is the smallest text-only
// implementation that satisfies RFC 6455 §5.2 for payloads up to 65535 bytes.
// That is enough for this spike and documents the actual wire contract.

import { createHash } from "node:crypto";
import { createServer } from "node:http";

const PORT = 8787;
const WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

const PAGE = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>vinyl · htmx-ws spike</title>
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
  <script src="https://unpkg.com/htmx-ext-ws@2.0.2/ws.js"></script>
</head>
<body hx-ext="ws" ws-connect="/ws">
  <h1>vinyl htmx-ws spike</h1>
  <p>Four <code>&lt;vinyl-slot&gt;</code>s start with their "pending" text.
     The server pushes four frames; each replaces the matching slot via
     <code>hx-swap-oob</code>. Watch the Network → WS tab for the raw HTML.</p>

  <section>
    <h2>1. Implicit OOB (no hx-swap-oob attr, just id)</h2>
    <vinyl-slot id="case-1">pending #1</vinyl-slot>
  </section>

  <section>
    <h2>2. Explicit hx-swap-oob="true"</h2>
    <vinyl-slot id="case-2">pending #2</vinyl-slot>
  </section>

  <section>
    <h2>3. Multiple OOB elements in one frame</h2>
    <vinyl-slot id="case-3a">pending #3a</vinyl-slot>
    <vinyl-slot id="case-3b">pending #3b</vinyl-slot>
  </section>

  <section>
    <h2>4. Missing target (#case-missing) → htmx:oobErrorNoTarget, no crash</h2>
    <vinyl-slot id="case-4">stays "pending #4" — the frame targets an id that isn't on the page</vinyl-slot>
  </section>
</body>
</html>
`;

const FRAMES = [
  // (1) No hx-swap-oob attribute. Confirms ws-ext defaults to "true".
  `<vinyl-slot id="case-1">resolved #1 (implicit OOB)</vinyl-slot>`,
  // (2) Explicit hx-swap-oob="true" — what M3 emits today.
  `<vinyl-slot id="case-2" hx-swap-oob="true">resolved #2 (explicit OOB)</vinyl-slot>`,
  // (3) Two top-level elements concatenated in one WS message.
  `<vinyl-slot id="case-3a">resolved #3a</vinyl-slot><vinyl-slot id="case-3b">resolved #3b</vinyl-slot>`,
  // (4) Targets a non-existent id. Expect htmx:oobErrorNoTarget in console;
  //     the socket stays open and subsequent frames still apply.
  `<vinyl-slot id="case-missing">orphan — no matching id on page</vinyl-slot>`,
];

function encodeTextFrame(text) {
  const payload = Buffer.from(text, "utf8");
  const len = payload.length;
  if (len <= 125) {
    return Buffer.concat([Buffer.from([0x81, len]), payload]);
  }
  if (len <= 0xffff) {
    const header = Buffer.alloc(4);
    header[0] = 0x81;
    header[1] = 126;
    header.writeUInt16BE(len, 2);
    return Buffer.concat([header, payload]);
  }
  throw new Error("spike harness only encodes frames ≤ 65535 bytes");
}

function acceptKey(secWsKey) {
  return createHash("sha1").update(secWsKey + WS_GUID).digest("base64");
}

const server = createServer((req, res) => {
  if (req.url === "/" || req.url === "/index.html") {
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(PAGE);
    return;
  }
  res.writeHead(404).end("not found");
});

server.on("upgrade", (req, socket) => {
  if (req.url !== "/ws") {
    socket.destroy();
    return;
  }
  const key = req.headers["sec-websocket-key"];
  if (typeof key !== "string") {
    socket.destroy();
    return;
  }
  socket.write(
    "HTTP/1.1 101 Switching Protocols\r\n" +
      "Upgrade: websocket\r\n" +
      "Connection: Upgrade\r\n" +
      `Sec-WebSocket-Accept: ${acceptKey(key)}\r\n\r\n`,
  );

  let i = 0;
  const interval = setInterval(() => {
    if (i >= FRAMES.length) {
      clearInterval(interval);
      return;
    }
    const frame = FRAMES[i++];
    process.stdout.write(`→ push frame #${i}: ${frame.slice(0, 60)}…\n`);
    socket.write(encodeTextFrame(frame));
  }, 500);

  socket.on("close", () => clearInterval(interval));
  socket.on("error", () => clearInterval(interval));
});

server.listen(PORT, () => {
  process.stdout.write(`spike: http://localhost:${PORT}\n`);
});
