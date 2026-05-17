"""Side-by-side web chat: NPU model vs NVIDIA/GPU model.

Same prompt to both processors, answers + latency rendered side by side so you
can compare the small OpenVINO model on the Intel NPU against qwen2.5-coder:14b
on the NVIDIA GPU. Stdlib HTTP only -- no extra deps.

  python webchat.py            # then open http://127.0.0.1:8080

Single-user local tool. Multi-turn history is kept client-side and capped so
it stays within the NPU's static-shape prompt limit.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cascade.gpu_worker import make_gpu_worker
from cascade.npu_worker import NPUWorker

_NPU_LOCK = threading.Lock()  # openvino pipeline is not reentrant
_W: dict = {}


def _workers() -> dict:
    """Lazily build the workers once (keeps `import webchat` side-effect free)."""
    if not _W:
        npu, gpu = NPUWorker(), make_gpu_worker()
        _W.update(npu=npu, gpu=gpu, gpu_ok=gpu.available())
    return _W

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>NPU vs GPU</title><style>
body{font:14px system-ui;margin:0;background:#0f1115;color:#e6e6e6}
header{padding:10px 16px;background:#171a21;border-bottom:1px solid #2a2f3a}
.wrap{display:flex;gap:0;height:calc(100vh - 112px)}
.pane{flex:1;display:flex;flex-direction:column;border-right:1px solid #2a2f3a;min-width:0}
.pane h2{margin:0;padding:8px 14px;font-size:13px;background:#1b1f27;color:#9ad}
.log{flex:1;overflow:auto;padding:12px 14px}
.msg{margin:0 0 12px}.u{color:#7fb}.a pre{white-space:pre-wrap;background:#161922;
border:1px solid #2a2f3a;border-radius:6px;padding:10px;margin:4px 0;overflow:auto}
.meta{color:#889;font-size:12px}
form{display:flex;gap:8px;padding:10px 16px;background:#171a21;border-top:1px solid #2a2f3a}
textarea{flex:1;background:#0f1115;color:#e6e6e6;border:1px solid #2a2f3a;
border-radius:6px;padding:8px;font:13px ui-monospace;resize:none;height:46px}
button{background:#2d6cdf;color:#fff;border:0;border-radius:6px;padding:0 18px;cursor:pointer}
button:disabled{background:#555}
</style></head><body>
<header><b>NPU vs NVIDIA GPU</b> &mdash; same prompt, side by side</header>
<div class="wrap">
 <div class="pane"><h2 id="hn">NPU</h2><div class="log" id="ln"></div></div>
 <div class="pane" style="border-right:0"><h2 id="hg">NVIDIA GPU</h2>
   <div class="log" id="lg"></div></div>
</div>
<form id="f"><textarea id="q" placeholder="Ask both models... (Enter to send)"></textarea>
<button id="b">Send</button></form>
<script>
const HN=[],HG=[],MAX=6;
fetch('/api/health').then(r=>r.json()).then(h=>{
 hn.textContent='NPU — '+h.npu_device+' · '+h.npu_model;
 hg.textContent='NVIDIA GPU — '+(h.gpu_ok?h.gpu_model:'unavailable');});
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function draw(el,hist){el.innerHTML=hist.map(m=>m.role=='u'
 ?`<p class="msg u">› ${esc(m.text)}</p>`
 :`<div class="msg a"><pre>${esc(m.text)}</pre><div class="meta">${m.meta}</div></div>`
 ).join('');el.scrollTop=el.scrollExp=1e9;}
async function ask(path,hist,el,prompt){
 hist.push({role:'a',text:'thinking...',meta:''});draw(el,hist);
 const t=performance.now();
 try{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({prompt})});const d=await r.json();
  hist[hist.length-1]={role:'a',text:d.text,meta:d.meta};}
 catch(e){hist[hist.length-1]={role:'a',text:'[error] '+e,meta:''};}
 draw(el,hist);}
function transcript(hist){return hist.filter(m=>m.text!='thinking...').slice(-MAX*2)
 .map(m=>(m.role=='u'?'User: ':'Assistant: ')+m.text).join('\\n')+'\\nAssistant:';}
f.onsubmit=async e=>{e.preventDefault();const v=q.value.trim();if(!v)return;
 b.disabled=true;q.value='';
 HN.push({role:'u',text:v});HG.push({role:'u',text:v});draw(ln,HN);draw(lg,HG);
 await Promise.all([ask('/api/npu',HN,ln,transcript(HN)),
                    ask('/api/gpu',HG,lg,transcript(HG))]);
 b.disabled=false;q.focus();};
q.addEventListener('keydown',e=>{if(e.key=='Enter'&&!e.shiftKey){e.preventDefault();
 f.requestSubmit();}});
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/health":
            w = _workers()
            self._send(200, json.dumps({
                "npu_device": w["npu"].device,
                "npu_model": "qwen2.5-coder-1.5b",
                "gpu_ok": w["gpu_ok"],
                "gpu_model": w["gpu"].model if w["gpu_ok"] else "",
            }))
        else:
            self._send(200, PAGE, "text/html; charset=utf-8")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        prompt = json.loads(self.rfile.read(n) or b"{}").get("prompt", "")
        w = _workers()
        t = time.perf_counter()
        try:
            if self.path == "/api/npu":
                with _NPU_LOCK:
                    d = w["npu"].draft(prompt, max_new_tokens=512)
                out = {"text": d.text,
                       "meta": f"NPU · {d.latency_s:.2f}s"}
            elif self.path == "/api/gpu":
                if not w["gpu_ok"]:
                    out = {"text": "[GPU/Ollama unavailable]", "meta": ""}
                else:
                    g = w["gpu"].generate(prompt)
                    out = {"text": g.text,
                           "meta": f"NVIDIA · {g.latency_s:.2f}s · "
                                   f"{g.tokens_per_s:.0f} tok/s"}
            else:
                return self._send(404, json.dumps({"text": "not found"}))
        except Exception as e:  # surface model/runtime errors to the pane
            out = {"text": f"[error] {type(e).__name__}: {e}", "meta": ""}
        out["meta"] += f"  ({time.perf_counter() - t:.2f}s wall)"
        self._send(200, json.dumps(out))

    def log_message(self, *a):  # quiet
        pass


def main() -> None:
    w = _workers()
    print(f"NPU ready on {w['npu'].device} | GPU available: {w['gpu_ok']}")
    srv = ThreadingHTTPServer(("127.0.0.1", 8080), H)
    print("Open http://127.0.0.1:8080  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
