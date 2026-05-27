"""Deep-dive analysis of the persona-debate logs: feature-per-turn + clustering.

Reads every runs/experiment-debate/*/state.json, builds an interpretable feature
vector per turn (loop/repetition, lexical diversity, persona-marker densities for
the speaker's OWN persona vs the OPPONENT's = a capture proxy, utilitarian-marker
leakage, third-person self-reference, length, latency, throughput), then:
  * k-means clusters the turns on CONTENT features and profiles each cluster,
  * correlates capacity (model size) against the pathologies,
  * leaderboards for capture / looping / third-person breaks / utilitarian-in-Kant,
  * compares repair drafts vs their final turn.

Pure stdlib + numpy (no sklearn). Seeded -> reproducible. $0, reads logs only.
Run:  .venv/Scripts/python scripts/debate_analysis.py
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Pure metric helpers live in cascade.degeneration so the persona-debate
# analyzer and the live PD-1 detector share one source of truth.
from cascade.degeneration import _count, _sentences, _words, text_features

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "runs" / "experiment-debate"
SEED = 7

# Persona marker lexicons (case-insensitive substring counts).
MARKERS = {
    "kant": ["categorical imperative", "maxim", "universaliz", "ends in themselves",
             "end in itself", "kingdom of ends", "duty", "dignity", "rational being",
             "moral law"],
    "singer": ["drowning child", "drowning", "pond", "marginal utility",
               "comparable moral importance", "morally irrelevant", "proximity",
               "affluent", "without sacrificing", "expanding circle"],
    "trump": ["believe me", "tremendous", "america first", "folks", "border",
              "nobody knows", "great again", "china", "mexico", "deal", "fake news",
              "huge", "best "],
}
UTIL = ["utility", "maximiz", "greatest good", "happiness", "consequence",
        "outcome", "good for all", "well-being of all"]
SURNAME = {"kant": "kant", "singer": "singer", "trump": "trump"}
# Model -> capacity proxy (billions of params). "" == the 1.5B NPU export.
SIZE = {"": 1.5, "qwen2.5-coder:14b": 14.0, "deepseek-r1:14b": 14.0}
# The persona field changed across harness versions (name in r1/r2, key in r3).
NAME2KEY = {"immanuel kant": "kant", "peter singer": "singer",
            "donald j. trump": "trump"}


@dataclass
class Turn:
    run: str
    idx: int
    speaker: str            # persona key: kant|singer|trump
    opponent: str           # persona key of the other slot
    model: str              # "" == NPU 1.5B
    size: float
    role: str
    is_repair: bool
    text: str
    think_chars: int
    latency_s: float
    tok_s: float | None
    feats: dict = field(default_factory=dict)


def features(t: Turn) -> dict:
    # The four shared metrics (trigram_repeat / max_sent_repeat / ttr /
    # distinct_sent_ratio) come from text_features() so this analyzer and the
    # PD-1 detector stay in lockstep. Persona-specific metrics below.
    nw = max(len(_words(t.text)), 1)
    ns = max(len(_sentences(t.text)), 1)
    own = _count(t.text, MARKERS[t.speaker])
    opp = _count(t.text, MARKERS[t.opponent])
    util = _count(t.text, UTIL)
    # third-person self-reference: own surname mentions minus "I am <name>" intros.
    name = SURNAME[t.speaker]
    self_name = t.text.lower().count(name)
    intros = len(re.findall(rf"\b(i am|i,|as)\s+(peter\s+)?{name}", t.text.lower()))
    third_person_self = max(0, self_name - intros)
    return {
        "n_words": float(nw),
        "n_sents": float(ns),
        **text_features(t.text),
        "own_per100w": 100 * own / nw,
        "opp_per100w": 100 * opp / nw,
        "capture_ratio": opp / (own + opp) if (own + opp) else 0.0,
        "util_per100w": 100 * util / nw,
        "third_person_self": float(third_person_self),
    }


def _pkey(raw: dict) -> str | None:
    """Persona KEY (kant|singer|trump) from any harness-version schema."""
    p = (raw.get("persona") or "").strip()
    if p.lower() in MARKERS:
        return p.lower()
    name = (raw.get("persona_name") or p).strip().lower()
    return NAME2KEY.get(name)


def _model(raw: dict) -> str:
    """Model tag; '' for the 1.5B NPU. r1/r2 turns lack a 'model' field: the NPU
    slot is the 1.5B, and the GPU slot in those rounds was always deepseek-r1."""
    if raw.get("model") is not None:
        return raw["model"]
    dev = raw.get("device", "")
    return "" if (dev.startswith("NPU") or raw.get("tier") == 1) else "deepseek-r1:14b"


def load() -> list[Turn]:
    turns: list[Turn] = []
    for d in sorted(BASE.glob("*/")):
        st = json.loads((d / "state.json").read_text(encoding="utf-8"))
        raws = st["turns"]
        keys = [_pkey(r) for r in raws]
        present = [k for k in dict.fromkeys(keys) if k]  # the run's two personas
        for raw, k in zip(raws, keys, strict=True):
            opp = next((o for o in present if o != k), k)
            model = _model(raw)
            base = dict(run=d.name, idx=raw["idx"], speaker=k, opponent=opp,
                        model=model, size=SIZE.get(model, 1.5),
                        role=raw.get("role", "?"),
                        think_chars=len(raw.get("think", "")),
                        latency_s=raw.get("latency_s", 0.0),
                        tok_s=raw.get("tokens_per_s"))
            for draft in (raw.get("repaired_from") or []):  # superseded, oldest first
                turns.append(Turn(**base, is_repair=True, text=draft))
            turns.append(Turn(**base, is_repair=False, text=raw["text"]))
    for t in turns:
        t.feats = features(t)
    return turns


def kmeans(x: np.ndarray, k: int, seed: int = SEED, iters: int = 100):
    rng = np.random.default_rng(seed)
    cent = x[rng.choice(len(x), k, replace=False)]
    lab = np.zeros(len(x), dtype=int)
    for _ in range(iters):
        d = np.linalg.norm(x[:, None] - cent[None], axis=2)
        new = d.argmin(1)
        if (new == lab).all():
            break
        lab = new
        for j in range(k):
            if (lab == j).any():
                cent[j] = x[lab == j].mean(0)
    return lab


def tag(t: Turn) -> str:
    m = "1.5B-NPU" if t.size < 2 else t.model
    r = "/rep" if t.is_repair else ""
    return f"{t.run[9:15]} {t.speaker[:6]:<6} {m:<17}{r}"


CLUSTER_FEATS = ["distinct_sent_ratio", "trigram_repeat", "ttr", "own_per100w",
                 "opp_per100w", "util_per100w", "max_sent_repeat"]


def main() -> None:
    turns = load()
    finals = [t for t in turns if not t.is_repair]
    print(f"loaded {len(turns)} text samples "
          f"({len(finals)} final turns + {len(turns) - len(finals)} repair drafts) "
          f"from {len(set(t.run for t in turns))} runs\n")

    # ---- cluster final turns on standardized content features ----
    x = np.array([[t.feats[f] for f in CLUSTER_FEATS] for t in finals])
    xz = (x - x.mean(0)) / (x.std(0) + 1e-9)
    k = 3
    lab = kmeans(xz, k)

    print("=" * 78)
    print(f"K-MEANS (k={k}) on content features {CLUSTER_FEATS}")
    print("=" * 78)
    for j in range(k):
        members = [t for t, c in zip(finals, lab, strict=True) if c == j]
        if not members:
            continue
        mz = x[lab == j].mean(0)
        prof = dict(zip(CLUSTER_FEATS, mz, strict=True))
        sizes = Counter("1.5B" if m.size < 2 else m.model.split(":")[0]
                        for m in members)
        print(f"\n-- cluster {j}: n={len(members)} | models={dict(sizes)}")
        print(f"   distinct_sent={prof['distinct_sent_ratio']:.2f} "
              f"trigram_rep={prof['trigram_repeat']:.2f} ttr={prof['ttr']:.2f} "
              f"own/100w={prof['own_per100w']:.1f} opp/100w={prof['opp_per100w']:.1f} "
              f"util/100w={prof['util_per100w']:.1f} "
              f"max_sent_rep={prof['max_sent_repeat']:.1f}")
        for m in members:
            print(f"     {tag(m)} {m.role:<8} "
                  f"dsr={m.feats['distinct_sent_ratio']:.2f} "
                  f"opp/100w={m.feats['opp_per100w']:.1f}")

    # ---- capacity vs pathology correlations ----
    print("\n" + "=" * 78)
    print("CORRELATION with model capacity (size in B params), final turns")
    print("=" * 78)
    size = np.array([t.size for t in finals])
    for f in ["distinct_sent_ratio", "trigram_repeat", "ttr", "max_sent_repeat",
              "own_per100w", "opp_per100w", "third_person_self", "n_words"]:
        v = np.array([t.feats[f] for t in finals])
        r = np.corrcoef(size, v)[0, 1]
        bar = ("+" if r >= 0 else "-") * int(round(abs(r) * 20))
        print(f"  size vs {f:<20} r={r:+.2f}  {bar}")

    # ---- leaderboards ----
    def board(title, items, key, fmt, reverse=True, n=6):
        print(f"\n-- {title}")
        for t in sorted(items, key=key, reverse=reverse)[:n]:
            print(f"     {tag(t)} {t.role:<8} {fmt(t)}")

    print("\n" + "=" * 78)
    print("LEADERBOARDS")
    print("=" * 78)
    philo = [t for t in finals if t.speaker in ("kant", "singer")]
    board("ARGUMENTATIVE CAPTURE (philosopher turns, most opponent/Trump markers)",
          philo, lambda t: t.feats["opp_per100w"],
          lambda t: f"opp/100w={t.feats['opp_per100w']:.1f} "
                    f"own/100w={t.feats['own_per100w']:.1f} "
                    f"capture={t.feats['capture_ratio']:.2f}")
    board("LOOPING (lowest distinct-sentence ratio)",
          finals, lambda t: t.feats["distinct_sent_ratio"],
          lambda t: f"distinct_sent={t.feats['distinct_sent_ratio']:.2f} "
                    f"max_repeat={t.feats['max_sent_repeat']:.0f}",
          reverse=False)
    board("THIRD-PERSON SELF-REFERENCE (persona break)",
          finals, lambda t: t.feats["third_person_self"],
          lambda t: f"self_3p={t.feats['third_person_self']:.0f}")
    board("UTILITARIAN MARKERS (conflation if speaker is Kant; correct if Singer)",
          finals, lambda t: t.feats["util_per100w"],
          lambda t: f"util/100w={t.feats['util_per100w']:.1f} (speaker={t.speaker})")

    # ---- repair effect ----
    print("\n" + "=" * 78)
    print("REPAIR EFFECT (superseded drafts -> final, same turn)")
    print("=" * 78)
    by_turn: dict[tuple[str, int], list[Turn]] = {}
    for t in turns:
        by_turn.setdefault((t.run, t.idx), []).append(t)
    for (run, idx), group in sorted(by_turn.items()):
        if len(group) < 2:
            continue
        seq = [g for g in group if g.is_repair] + [g for g in group if not g.is_repair]
        print(f"\n  {run[9:15]} idx{idx} {seq[-1].speaker}:")
        for i, g in enumerate(seq):
            kind = f"draft{i+1}" if g.is_repair else "FINAL"
            print(f"     {kind:<7} dsr={g.feats['distinct_sent_ratio']:.2f} "
                  f"own/100w={g.feats['own_per100w']:.1f} "
                  f"opp/100w={g.feats['opp_per100w']:.1f} "
                  f"util/100w={g.feats['util_per100w']:.1f}")

    # ---- think traces ----
    r1 = [t for t in finals if t.model == "deepseek-r1:14b"]
    print("\n" + "=" * 78)
    print(f"deepseek-r1 <think>: {sum(1 for t in r1 if t.think_chars > 0)}/{len(r1)} "
          f"turns emitted a trace (think_chars>0)")


if __name__ == "__main__":
    main()
