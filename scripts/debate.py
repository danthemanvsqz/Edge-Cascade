"""Configurable cross-tier / cross-model PERSONA DEBATE.

A qualitative `experiment`-skill probe (NOT a pass/fail benchmark). Two debaters,
slots **a** and **b**, each take a persona, a side of a motion, and a backend:
  - backend "openvino": the 1.5B INT4 model on the Intel NPU (Tier 1).
  - backend "ollama":   any local Ollama model on the NVIDIA GPU (e.g.
                        deepseek-r1:14b, qwen2.5-coder:14b).
They debate turn by turn; `a` speaks first. Handicaps are optional (`--handicaps`):
the "underdog" can be given the bookend (opens AND closes) and agent-coached
repairs -- useful for an NPU-vs-GPU mismatch, off for a symmetric GPU-vs-GPU duel.

Personas live in PERSONAS; the motion can carry a scholarly CITATION (`--citation`).

Per-turn subcommands persist a JSON state file so an external agent can drive the
debate one turn at a time (`new -> a -> b -> ... -> judge`) and decide when to grant
a repair. `run` does a deterministic no-agent sweep for reproduction.

Output (all under runs/experiment-debate/<run-id>/):
  live.md     human-readable transcript, STREAMED token-by-token -> tail it live:
              Get-Content -Path .\\runs\\experiment-debate\\<id>\\live.md -Wait -Tail 60
  state.json  full structured record: every turn, any <think> trace, latencies,
              token counts, repairs, and the agent's verdict.
And the segregated telemetry lane runs/experiment-debate.rec (make_experiment_recorder).

Local-only, $0 (OpenVINO + Intel NPU/iGPU for an openvino slot; Ollama for ollama slots).
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

# The streamed echo goes to stdout; a Windows console defaults to cp1252, which
# raises UnicodeEncodeError on characters a model may emit (emoji, special
# quotes) and would abort a turn. Force UTF-8 with replacement on the echo
# streams (the live.md file is written UTF-8 directly and is unaffected).
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cascade import npu_worker  # noqa: E402  (_compile, _gen, _CHAT)
from cascade.config import CONFIG  # noqa: E402
from mcp_servers._rec import make_experiment_recorder  # noqa: E402

BASE = ROOT / "runs" / "experiment-debate"
MOTION = (
    "Because global connectivity erases the excuse of distance, failing to give "
    "until it hurts to save lives abroad is a total moral failure."
)
# Singer's argument that physical proximity/distance is morally irrelevant.
FAM_1972 = (
    'Peter Singer, "Famine, Affluence, and Morality," Philosophy & Public Affairs '
    "1, no. 3 (1972) -- the argument that physical proximity and distance are "
    "morally irrelevant to our duty to prevent suffering (the drowning-child case)."
)


# --- personas ---------------------------------------------------------------
@dataclass(frozen=True)
class Persona:
    """A debater identity: a display name + the system prompt that conditions it.
    Pure data -- slot/side/backend/model are assigned at `new` time, not here."""

    key: str
    name: str
    system: str


_SINGER = (
    "You are Peter Singer, the contemporary moral philosopher, in a formal public "
    "debate. Speak ONLY as Singer, in clear, plain, rigorously argued prose. Make "
    "your ACTUAL argument from 'Famine, Affluence, and Morality': if it is in our "
    "power to prevent something very bad from happening without thereby sacrificing "
    "anything of comparable moral importance, we ought to do it. Use the drowning-"
    "child-in-the-pond analogy; insist that physical distance and national borders "
    "are morally irrelevant; appeal to the diminishing marginal utility of wealth "
    "for the affluent versus its life-saving value for the poor. Address the motion "
    "directly and answer your opponent. Never write code. Keep to 2-4 tight paragraphs."
)
_KANT = (
    "You are Immanuel Kant, the 18th-century philosopher, in a formal public "
    "debate. Speak ONLY as Kant, in measured, rigorous prose. Reason from the "
    "categorical imperative, duty, the universalizability of maxims, the dignity "
    "of persons as ends in themselves, and the kingdom of ends. Address the "
    "motion directly and answer your opponent. Never write code. Keep to 2-4 "
    "tight paragraphs."
)
_TRUMP = (
    "You are Donald J. Trump at a campaign rally, in a debate. Speak ONLY as "
    "Trump, in your unmistakable voice: short punchy sentences, superlatives, "
    "repetition, tangents, nicknames, 'believe me', 'tremendous', 'nobody knows "
    "X better than me', America First. Address the motion and hit back at your "
    "opponent. Never write code. A few punchy paragraphs."
)
PERSONAS = {
    "singer": Persona("singer", "Peter Singer", _SINGER),
    "kant": Persona("kant", "Immanuel Kant", _KANT),
    "trump": Persona("trump", "Donald J. Trump", _TRUMP),
}

# An openvino (NPU) slot has a small static-shape input window; feed it only the
# motion + a trimmed view of the opponent's last turn, never the whole history.
NPU_INPUT_CHARS = 1200
OLLAMA_TIMEOUT_S = 600  # r1's <think> easily blows the stock 180s timeout
DEFAULT_MAX_TOKENS = 1024  # symmetric default; r1 needs room for <think>+answer


# --- sleep-safe (laptop) ----------------------------------------------------
@contextlib.contextmanager
def keep_awake() -> Iterator[None]:
    """Hold a wake-lock for the duration (idle-sleep would drop an in-flight
    Ollama call). ES_CONTINUOUS | ES_SYSTEM_REQUIRED = 0x80000001; release with
    ES_CONTINUOUS = 0x80000000. A lid-close still halts the CPU regardless."""
    set_state = getattr(getattr(ctypes, "windll", None), "kernel32", None)
    if set_state is not None:
        with contextlib.suppress(Exception):
            set_state.SetThreadExecutionState(0x80000001)
    try:
        yield
    finally:
        if set_state is not None:
            with contextlib.suppress(Exception):
                set_state.SetThreadExecutionState(0x80000000)


# --- live transcript sink ---------------------------------------------------
@contextlib.contextmanager
def live_sink(live_path: Path, header: str) -> Iterator[Callable[[str], None]]:
    """Append `header`, then yield a `sink(chunk)` that writes each streamed
    chunk to the live transcript AND stdout, flushing immediately so a
    `Get-Content -Wait` tail shows the debate forming in real time."""
    with open(live_path, "a", encoding="utf-8") as fh:
        fh.write(header)
        fh.flush()

        def sink(chunk: str) -> None:
            fh.write(chunk)
            fh.flush()
            sys.stdout.write(chunk)
            sys.stdout.flush()

        yield sink


# --- generation -------------------------------------------------------------
def npu_generate(
    system: str, user: str, max_new_tokens: int, sink: Callable[[str], None]
) -> tuple[str, float, str]:
    """Compile Tier-1 (NPU probe -> iGPU -> CPU) and stream a turn. Returns
    (text, latency_s, device). Falls back to a non-streamed generate if this
    OpenVINO build's streamer signature differs."""
    import openvino_genai as ov  # lazy: module imports without the accel extra

    device, pipe = npu_worker._compile()
    cfg = ov.GenerationConfig()
    cfg.max_new_tokens = max_new_tokens
    cfg.stop_strings = {"<|im_end|>"}
    cfg.include_stop_str_in_output = False
    prompt = npu_worker._CHAT.format(system=system, user=user)
    chunks: list[str] = []

    def streamer(subword: str):
        chunks.append(subword)
        sink(subword)
        return ov.StreamingStatus.RUNNING

    t0 = time.perf_counter()
    try:
        out = pipe.generate(prompt, cfg, streamer)
        text = ("".join(chunks) or str(out)).strip()
    except Exception:  # noqa: BLE001 - streamer API mismatch: degrade to blocking
        out = pipe.generate(prompt, cfg)
        text = str(out).strip()
        sink(text)
    text = text.replace("<|im_end|>", "").strip()
    return text, time.perf_counter() - t0, f"NPU/{device}"


def ollama_generate(
    model: str, system: str, user: str, max_new_tokens: int,
    sink: Callable[[str], None],
) -> tuple[str, float, float, int]:
    """Stream a turn from an Ollama model on the GPU. Returns (raw_text,
    latency_s, tokens_per_s, eval_count). raw_text includes any <think> block;
    the caller splits it. Long timeout: a reasoning model thinks before it speaks.
    Alternating two 14B models forces an Ollama model swap each turn (12GB VRAM
    can't hold both) -- expected, just adds load time."""
    url = CONFIG.ollama_base_url.rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": True,
        "options": {"num_predict": max_new_tokens},
    }
    full: list[str] = []
    eval_count, eval_ns = 0, 1
    t0 = time.perf_counter()
    with httpx.stream(
        "POST", f"{url}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT_S
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            d = json.loads(line)
            piece = d.get("message", {}).get("content", "")
            if piece:
                full.append(piece)
                sink(piece)
            if d.get("done"):
                eval_count = d.get("eval_count", 0)
                eval_ns = d.get("eval_duration", 0) or 1
    raw = "".join(full).strip()
    tok_s = eval_count / (eval_ns / 1e9) if eval_ns else 0.0
    return raw, time.perf_counter() - t0, tok_s, eval_count


def split_think(raw: str) -> tuple[str, str]:
    """Separate an r1 <think>...</think> reasoning trace from the spoken answer.
    Returns (think, answer); think is '' when the model emitted no trace."""
    if "</think>" in raw:
        think, _, answer = raw.partition("</think>")
        return think.replace("<think>", "").strip(), answer.strip()
    return "", raw.strip()


# --- state ------------------------------------------------------------------
def resolve_run(run: str | None) -> Path:
    if run in (None, "latest"):
        dirs = sorted(p for p in BASE.iterdir() if p.is_dir()) if BASE.is_dir() else []
        if not dirs:
            sys.exit("no debate runs yet -- start one with `debate.py new`")
        return dirs[-1]
    p = BASE / run
    if not p.is_dir():
        sys.exit(f"no such run: {run}")
    return p


def load_state(run_dir: Path) -> dict:
    return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))


def save_state(run_dir: Path, st: dict) -> None:
    (run_dir / "state.json").write_text(
        json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def side_word(side: str) -> str:
    return "FOR" if side == "pro" else "AGAINST"


def role_for(slot_key: str, turns: list[dict], plan: list[str]) -> str:
    """First turn of a slot = opening, its last scheduled turn = closing, else
    rebuttal. Works for symmetric plans and bookend (handicap) plans alike."""
    done = sum(1 for t in turns if t["slot"] == slot_key)
    total = plan.count(slot_key)
    if done == 0:
        return "opening"
    if done >= total - 1:
        return "closing"
    return "rebuttal"


def opponent_last(turns: list[dict], slot_key: str) -> tuple[str, str]:
    """(opponent_name, opponent_spoken_text) of the most recent other-slot turn,
    or ('', '') if this slot opens the debate."""
    for t in reversed(turns):
        if t["slot"] != slot_key:
            return t["persona_name"], t["text"]
    return "", ""


def build_user(
    motion: str, citation: str, role: str, side: str, opp_name: str,
    opp_text: str, feedback: str | None,
) -> str:
    sw = side_word(side)
    ref = f"\n\n(Reference: {citation})" if citation else ""
    if role == "opening" or not opp_text:
        body = (
            f'The debate motion is:\n"{motion}"{ref}\n\nYou are arguing {sw} the '
            "motion. Deliver your OPENING statement, in character."
        )
    else:
        kind = "CLOSING statement" if role == "closing" else "rebuttal"
        body = (
            f'The debate motion is:\n"{motion}"{ref}\n\nYou are arguing {sw} the '
            f'motion. Your opponent, {opp_name}, just said:\n\n"{opp_text}"\n\n'
            f"Give your {kind}, in character -- rebut them and advance your case."
        )
    if feedback:
        body += (
            "\n\n[Debate-coach note on your previous draft -- address this and "
            f"do better: {feedback}]"
        )
    return body


# --- turn driver (shared by per-turn subcommands and `run`) -----------------
def do_turn(
    run_dir: Path, st: dict, slot_key: str, emit, *, max_tokens: int,
    feedback: str | None = None, replace_last: bool = False,
) -> dict:
    slot = st[slot_key]
    role = (
        st["turns"][-1]["role"]
        if replace_last
        else role_for(slot_key, st["turns"], st["sequence_plan"])
    )
    history = st["turns"][:-1] if replace_last else st["turns"]
    opp_name, opp_text = opponent_last(history, slot_key)
    if slot["backend"] == "openvino":
        opp_text = opp_text[-NPU_INPUT_CHARS:]  # respect the NPU input window
    user = build_user(st["motion"], st.get("citation", ""), role, slot["side"],
                      opp_name, opp_text, feedback)

    tag = "repair" if replace_last else role
    header = f"\n\n## {slot['persona_name']} - {slot['label']} ({tag})\n\n"
    if feedback:
        header += f"> _coach feedback: {feedback}_\n\n"
    think, tok_s, eval_count = "", None, None
    with keep_awake(), live_sink(run_dir / "live.md", header) as sink:
        if slot["backend"] == "openvino":
            text, latency, device = npu_generate(
                slot["system"], user, max_tokens, sink)
            sink(f"\n\n_({device}, {latency:.1f}s, {len(text)} chars)_\n")
        else:
            raw, latency, tok_s, eval_count = ollama_generate(
                slot["model"], slot["system"], user, max_tokens, sink)
            think, text = split_think(raw)
            device = slot["label"]
            sink(f"\n\n_({device}, {latency:.1f}s, {eval_count} tok @ "
                 f"{tok_s:.1f} tok/s)_\n")

    turn = {
        "idx": st["turns"][-1]["idx"] if replace_last else len(st["turns"]),
        "slot": slot_key, "persona": slot["persona"],
        "persona_name": slot["persona_name"], "backend": slot["backend"],
        "model": slot["model"], "device": device, "role": role, "text": text,
        "think": think, "latency_s": latency, "tokens_per_s": tok_s,
        "eval_count": eval_count, "max_tokens": max_tokens, "chars": len(text),
        "repaired_from": [], "feedback": feedback, "ts": time.time(),
    }
    if replace_last:
        prev = st["turns"][-1]
        turn["repaired_from"] = [*prev.get("repaired_from", []), prev["text"]]
        st["turns"][-1] = turn
    else:
        st["turns"].append(turn)
    save_state(run_dir, st)
    emit("repair" if replace_last else "turn", {
        "debate_run": run_dir.name, "slot": slot_key,
        "persona": slot["persona_name"], "device": device, "role": role,
        "latency_ms": f"{latency * 1000:.0f}", "chars": str(len(text)),
        "answer": text, "think": think,
    })
    return turn


# --- subcommands ------------------------------------------------------------
def _result(d: dict) -> None:
    """Emit a greppable one-line JSON the orchestrating agent can parse past the
    OpenVINO/native chatter on stdout."""
    print("\nRESULT_JSON: " + json.dumps(d, ensure_ascii=False))


def _slot(persona_key: str, side: str, model_spec: str) -> dict:
    """Build a debater slot. model_spec 'npu' -> the OpenVINO NPU tier; any other
    value -> that Ollama model tag on the GPU."""
    p = PERSONAS[persona_key]
    if model_spec == "npu":
        backend, model, label = "openvino", "", "NPU"
    else:
        backend, model, label = "ollama", model_spec, f"GPU {model_spec}"
    return {"persona": p.key, "persona_name": p.name, "system": p.system,
            "side": side, "backend": backend, "model": model, "label": label}


def _build_debate(args: argparse.Namespace) -> dict:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    a_side = args.a_side
    b_side = "con" if a_side == "pro" else "pro"
    plan = ["a", "b"] * args.rounds
    if args.handicaps == "a":
        plan.append("a")  # the underdog bookends: opens and closes
    repairable = {"none": [], "a": ["a"], "both": ["a", "b"]}[args.handicaps]
    citation = FAM_1972 if args.citation == "fam1972" else args.citation
    return {
        "run_id": run_id, "created": time.time(), "motion": MOTION,
        "citation": citation, "rounds": args.rounds, "handicaps": args.handicaps,
        "repairable": repairable,
        "a": _slot(args.a_persona, a_side, args.a_model),
        "b": _slot(args.b_persona, b_side, args.b_model),
        "sequence_plan": plan, "turns": [], "verdict": None,
    }


def cmd_new(args: argparse.Namespace) -> Path:
    st = _build_debate(args)
    run_dir = BASE / st["run_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    save_state(run_dir, st)
    a, b = st["a"], st["b"]
    header = (
        f"# Debate: {a['persona_name']} ({a['label']}) vs "
        f"{b['persona_name']} ({b['label']})\n\n"
        f"**Motion:** {MOTION}\n\n"
        + (f"**Citation:** {st['citation']}\n\n" if st["citation"] else "")
        + f"- **{a['persona_name']}** ({a['label']}) argues "
        f"**{side_word(a['side'])}**\n"
        f"- **{b['persona_name']}** ({b['label']}) argues "
        f"**{side_word(b['side'])}**\n"
        f"- Handicaps: {st['handicaps']} | Plan: {' -> '.join(st['sequence_plan'])}\n"
        f"- Started {datetime.now(UTC):%Y-%m-%d %H:%M:%SZ} | run `{st['run_id']}`\n"
    )
    (run_dir / "live.md").write_text(header, encoding="utf-8")
    print(f"RUN_ID={st['run_id']}")
    print(f"RUN_DIR={run_dir}")
    print(f"MATCHUP=a: {a['persona_name']} ({a['label']}, {side_word(a['side'])})"
          f"  vs  b: {b['persona_name']} ({b['label']}, {side_word(b['side'])})")
    print(f"PLAN={' -> '.join(st['sequence_plan'])}")
    print("WATCH (PowerShell, separate terminal):")
    print(f"  Get-Content -Path {run_dir / 'live.md'} -Wait -Tail 60")
    _result({"run_id": st["run_id"], "run_dir": str(run_dir),
             "plan": st["sequence_plan"], "a": a["persona_name"],
             "b": b["persona_name"], "handicaps": st["handicaps"]})
    return run_dir


def _turn_cmd(slot_key: str):
    def cmd(args: argparse.Namespace) -> None:
        run_dir = resolve_run(args.run)
        st = load_state(run_dir)
        emit = make_experiment_recorder("debate")
        turn = do_turn(run_dir, st, slot_key, emit, max_tokens=args.max_tokens)
        _result({"slot": slot_key, "persona": turn["persona_name"],
                 "device": turn["device"], "role": turn["role"],
                 "latency_s": round(turn["latency_s"], 1), "chars": turn["chars"],
                 "think_chars": len(turn["think"]), "text": turn["text"]})
    return cmd


def cmd_repair(args: argparse.Namespace) -> None:
    run_dir = resolve_run(args.run)
    st = load_state(run_dir)
    if not st["turns"]:
        sys.exit("no turns yet")
    last = st["turns"][-1]["slot"]
    if last not in st.get("repairable", []):
        sys.exit(f"slot {last!r} is not repairable in this debate "
                 f"(handicaps={st.get('handicaps')})")
    emit = make_experiment_recorder("debate")
    turn = do_turn(run_dir, st, last, emit, max_tokens=args.max_tokens,
                   feedback=args.feedback, replace_last=True)
    _result({"slot": last, "persona": turn["persona_name"], "repaired": True,
             "role": turn["role"], "latency_s": round(turn["latency_s"], 1),
             "chars": turn["chars"], "text": turn["text"]})


def cmd_judge(args: argparse.Namespace) -> None:
    run_dir = resolve_run(args.run)
    st = load_state(run_dir)
    winner = st[args.winner]  # "a" | "b"
    verdict = {
        "winner": args.winner, "persona": winner["persona_name"],
        "criterion": args.criterion, "rationale": args.rationale, "ts": time.time(),
    }
    st["verdict"] = verdict
    save_state(run_dir, st)
    with open(run_dir / "live.md", "a", encoding="utf-8") as fh:
        fh.write(
            f"\n\n---\n\n## Verdict (agent) - criterion: {args.criterion}\n\n"
            f"**Winner: {winner['persona_name']} ({args.winner})**\n\n"
            f"{args.rationale}\n"
        )
    make_experiment_recorder("debate")("verdict", {
        "debate_run": run_dir.name, "winner": args.winner,
        "persona": winner["persona_name"], "criterion": args.criterion,
        "rationale": args.rationale,
    })
    print(f"verdict recorded: {winner['persona_name']} ({args.winner}) wins "
          f"on {args.criterion}")
    _result(verdict)


def cmd_run(args: argparse.Namespace) -> None:
    """Deterministic no-agent reproduction sweep (no repairs)."""
    run_dir = cmd_new(args)
    st = load_state(run_dir)
    emit = make_experiment_recorder("debate")
    for slot_key in st["sequence_plan"]:
        do_turn(run_dir, st, slot_key, emit, max_tokens=args.max_tokens)
        st = load_state(run_dir)
    print(f"\n\ndebate complete -> {run_dir / 'live.md'}")
    _result({"run_dir": str(run_dir), "turns": len(st["turns"])})


def _add_new_args(p: argparse.ArgumentParser) -> None:
    keys = list(PERSONAS)
    p.add_argument("--rounds", type=int, default=3, help="a/b exchanges")
    p.add_argument("--a-persona", choices=keys, default="kant")
    p.add_argument("--a-model", default="qwen2.5-coder:14b",
                   help="'npu' for the OpenVINO NPU tier, else an Ollama tag")
    p.add_argument("--a-side", choices=["pro", "con"], default="pro")
    p.add_argument("--b-persona", choices=keys, default="trump")
    p.add_argument("--b-model", default="deepseek-r1:14b",
                   help="'npu' for the OpenVINO NPU tier, else an Ollama tag")
    p.add_argument("--handicaps", choices=["none", "a", "both"], default="none")
    p.add_argument("--citation", default="",
                   help="'fam1972' for Singer's paper, or literal citation text")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="start a debate run")
    _add_new_args(p)
    p.set_defaults(func=cmd_new)

    for key in ("a", "b"):
        p = sub.add_parser(key, help=f"run the next {key.upper()} turn")
        p.add_argument("--run", default="latest")
        p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
        p.set_defaults(func=_turn_cmd(key))

    p = sub.add_parser("repair", help="regenerate the last turn with feedback")
    p.add_argument("--run", default="latest")
    p.add_argument("--feedback", required=True)
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.set_defaults(func=cmd_repair)

    p = sub.add_parser("judge", help="record the agent's verdict")
    p.add_argument("--run", default="latest")
    p.add_argument("--winner", choices=["a", "b"], required=True)
    p.add_argument("--criterion", default="persona_fidelity")
    p.add_argument("--rationale", required=True)
    p.set_defaults(func=cmd_judge)

    p = sub.add_parser("run", help="deterministic no-agent reproduction sweep")
    _add_new_args(p)
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.set_defaults(func=cmd_run)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
