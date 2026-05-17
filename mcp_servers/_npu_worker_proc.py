"""Isolated OpenVINO worker for edge-npu.

WHY THIS EXISTS (measured, not guessed): openvino_genai.LLMPipeline compile
returns in ~3.5s in a normal process -- and even with the bare MCP stdio
transport up -- but NEVER returns when run inside the full FastMCP
request-dispatch path, on any thread (process-global). So OpenVINO must not
live in the edge-npu MCP server process at all. It lives here, in a process
that has none of that machinery, talking to the server over a PRIVATE pipe.

Protocol: one JSON request per line on stdin -> one JSON response per line on
stdout. stdout is reserved for that protocol ONLY: NPUWorker / OpenVINO emit
bare print()s and native chatter, so fd 1 is duplicated aside for the
protocol and the real fd 1 is pointed at stderr before anything else runs.

Requests:  {"op":"status"} | {"op":"route","prompt":..} |
            {"op":"draft","prompt":..,"max_tokens":N|null} | {"op":"shutdown"}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Reserve fd 1 for the protocol BEFORE importing anything that may print().
_proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
os.dup2(2, 1)            # native/C stdout (fd 1) -> stderr
sys.stdout = sys.stderr  # Python print() -> stderr

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _send(obj: dict) -> None:
    _proto.write(json.dumps(obj) + "\n")
    _proto.flush()


def main() -> None:
    try:
        from cascade.npu_worker import NPUWorker

        w = NPUWorker()  # the OpenVINO compile -- safe here (clean process)
    except Exception as e:  # noqa: BLE001 - report and exit; server marks down
        _send({"ok": False, "event": "ready",
               "error": f"{type(e).__name__}: {e}"})
        return
    _send({"ok": True, "event": "ready", "device": w.device})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            op = req.get("op")
            if op == "status":
                _send({"ok": True, "device": w.device})
            elif op == "route":
                r = w.route(req["prompt"])
                _send({"ok": True, "difficulty": r.difficulty,
                       "category": r.category, "latency_s": r.latency_s,
                       "device": r.device})
            elif op == "draft":
                d = w.draft(req["prompt"], max_new_tokens=req.get("max_tokens"))
                _send({"ok": True, "text": d.text,
                       "latency_s": d.latency_s, "device": d.device})
            elif op == "shutdown":
                _send({"ok": True})
                return
            else:
                _send({"ok": False, "error": f"unknown op {op!r}"})
        except Exception as e:  # noqa: BLE001 - one bad request must not kill it
            _send({"ok": False, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
