# Backlog (groomed)

Live, prioritized backlog. Ordering and zones follow
[PRIORITIZATION.md](PRIORITIZATION.md) — a 4×4 **Impact × Severity** matrix:
impact descending, then severity ascending (safest first); the `I1` column is
dropped, the `S4` row is parked + de-risked.

> **Last groomed: 2026-10-06 (later)** — new arc: **NH** (NPU as a helper node: spec
> extraction → DSL, GPU best-of-N selected by the gate, ledger-driven NPU drafting) and
> **CI-1/CI-2** housekeeping; KB-1/KB-2 updated. Earlier 2026-10-06: new arc: **SQ** (signal quality: diagnose the cap-everywhere
> finding, pin the gate to the task's language, a GPU sampling-temperature experiment, router
> JSON fallback, Ollama `num_ctx`), from a critique of an external RAG/quality analysis.
> Updated KB-1 (embedder off the GPU), KB-3 (repo-symbol corpus arm, new KB-4), JOB-6, and
> EXP-MR-1 (pin `num_ctx` + temperature across arms). Prior 2026-10-05: new arc: **JOB** (job-search daemon over a GPU / Gemini /
> Claude mesh; 18 tasks sized for a Sonnet agent, JOB-15 submit parked). Prior 2026-09-24: new arc: **KB** (RAG: book text in a local vector store,
> retrieved into the pipeline's prompts; product path). Prior 2026-09-19: new arcs: **EDGE-1** (supervisor launch, HIGH PRIORITY),
> **MD** (deprecate the edge MCP servers) and **EXP-MR** (local model refresh).
> Prior: 2026-06-26 (SR-1 shipped as #144).

## Current placement

```
 Severity ↓ \ Impact →   I1 Trivial   I2 Minor                     I3 Major                          I4 Critical
 S1 Safe                  ✗ (none)     MD-4 docs sweep ·            JOB-1 scaffold ·                  — (none)
                                          JOB-18 funnel report ·       DX-1 cap-everywhere diagnosis ·
                                          RT-1 router fallback probe · NH-0 NPU multi-model spike
                                          OL-1 pin Ollama num_ctx ·
                                          CI-1 actions Node 24 ·
                                          CI-2 no false-alarm cancels
 S2 Low                   ✗ (none)     MD-2 relocate shared →       KB-1 ingest · KB-3 experiment ·   ★ EDGE-1 supervisor
                                          MD-3 delete servers ·        KB-4 repo-symbol corpus ·         launch (acceptance left) ·
                                          JOB-9 Gemini 2nd opinion     EXP-MR-1 model refresh ·          JOB-2 ledger · JOB-3 backfill ·
                                                                       EXP-SP-1 GPU temperature ·        JOB-5 filter · JOB-12 fact gate
                                                                       DX-2 gate = task language ·
                                                                       NH-1 spec extractor ·
                                                                       NH-4 pair experiment ·
                                                                       JOB-4 scanner · JOB-6 embed ·
                                                                       JOB-8 calibrate · JOB-10 facts ·
                                                                       JOB-13 approval
 S3 Moderate              ✗ (none)     — (none)                     KB-2 retrieve step ·              JOB-14 pre-fill agent
                                                                       EXP-MR-2 MoE offload arm ·
                                                                       JOB-7 extractor · JOB-11 tailor ·
                                                                       NH-2 wire DSL · NH-3 best-of-N ·
                                                                       NH-5 ledger NPU drafting ·
                                                                       JOB-16 email · JOB-17 daemon
 S4 Severe (park)         ✗ (none)     ⏳ #5 PT-4 HOLD (re-probe)   — (none)                          ⏳ JOB-15 auto-submit
```

**Pick order (impact ↓, then severity ↑; dependencies may pull an item forward):**
1. **★ EDGE-1** `edge` = one-command supervisor launch (I4·S2) — 1a/1b/1c shipped; **only the
   idle-VRAM acceptance check remains**
2. **JOB arc, I4 cells** (user, 2026-10-05: the job search is the most time-sensitive work):
   JOB-1 (dependency, I3·S1) → JOB-2 → JOB-3 → JOB-5 → JOB-10 (dependency) → JOB-12 → JOB-14;
   the rest of the arc follows its own pick order, interleaved with KB/EXP-MR at equal cells
2a. **DX-1** diagnose cap-everywhere + `repair_rnds=0` (I3·S1) — read-only; **gates every
   experiment below** (if the repair loop isn't running, every baseline is confounded).
   Then **EXP-SP-1** GPU temperature A/B (I3·S2) and **DX-2** gate = task language (I3·S2)
2c. **NH arc** (user, 2026-10-06): **NH-0** NPU multi-model spike (I3·S1; ahead of KB-1 because
   it settles the embedder's device) → **NH-1** spec extractor (I3·S2) → **NH-2** wire DSL
   (I3·S3, needs DX-2) → **NH-3** best-of-N (I3·S3) → **NH-4** experiment (I3·S2, after
   EXP-SP-1) → **NH-5** ledger-driven NPU drafting (I3·S3); interleave with KB at equal cells
2b. **KB-1** ingest the book into a local knowledge base (I3·S2) — same cell as EXP-MR-1,
   ordered first because it is on the revenue path (user, 2026-09-24); book file in hand,
   blocked on dep approval
3. **KB-2** `retrieve` step in the budget chain (I3·S3) — depends on KB-1
3a. **KB-4** repo-symbol corpus (I3·S2) — after KB-1; must land before KB-3 so KB-3 can run
   its `symbols` arm
4. **KB-3** RAG-vs-control experiment (I3·S2) — depends on KB-2, so it runs after it
5. **EXP-MR-1** model refresh, 12 GB-resident candidates (I3·S2) — needs EDGE-1 (free VRAM,
   live substrate); can share KB-3's evidence-branch session
6. **EXP-MR-2** MoE partial-offload arm (I3·S3) — after EXP-MR-1's harness exists
7. **MD-4** docs sweep (I2·S1), **RT-1** router fallback probe (I2·S1), **OL-1** pin Ollama
   `num_ctx` (I2·S1; pulled forward — EXP-MR-1 precondition), **CI-1** + **CI-2** CI
   housekeeping (I2·S1; one small workflow PR)
8. **MD-2** relocate shared modules out of `mcp_servers/` (I2·S2), then **MD-3** delete the servers (I2·S2)

**Parked:** JOB-15 auto-submit (I4·S4) — de-risk steps in its block. #5 PT-4 (llama-cpp-python bump) — HOLD on AVX-512. **Next de-risk step
(new, 2026-09-19):** upstream is at **0.3.35** (2026-08-17); probe whether the cu12x
Windows wheel still requires AVX-512 (`--dry-run` install into a scratch venv + load one
GGUF). If not, PT-4 drops to S2 and re-enters at I2 — and it gains weight: 0.3.23 very
likely cannot load the 2026-Q3 architectures EXP-MR is evaluating.

**Standing precondition — substrate health check:** no task has been routed for ~85 days
(last `runs/cascade.rec` outcome was a LOSE), and Ollama was reinstalled 2026-09-19. Run
one trivial `mesh_solve_canvas.py --topology budget` end-to-end before starting EXP-MR.
(EDGE-1's launch-time table makes this automatic.)

**Shipped (for the record):** #1 PT-1, #2 PT-2, #3 PT-3 CLOSE, #4 gate-helper (#135),
#5 PT-4 HOLD, #6 OBS-1, #7 ts-verify-gate (#115), #8 difficulty-recal (#116),
#9 draft_gate-decompose (#119), #10 ts-shortcut (retired), #11 hook-scope (#133),
#12 obs-legibility (#117), #13 nonblock-hold (#134), #14 verify_func (#130),
**#VR-1 gate registry (#140), #VR-2 shell verifier (#141), #VR-3 JS verifier (#141),
#VR-4 wire call sites (#142), #VR-5 repair-prompt language field (#143)**,
**#SR-1 deterministic replay seed+params (#144)**.

---

## ★ EDGE-1 · `edge` = one-command supervisor launch  (I4 · S2) — HIGH PRIORITY

**Progress:** ✅ **1a** `cascade/health.py` probes + `plan_repairs` (#149). ✅ **1b** edge-cli
supervisor glue (steps 1–6 + step 8 flags): probes skip a dependant whose prereq is down,
`--only` for wait loops, `--json` carries `prereqs`; worker spawn is guarded by a live-process
check (reconnecting worker → `RECOVERED`, never a duplicate). Live-verified on this box:
worker killed → RESTARTED; redis stopped → redis RESTARTED + worker RECOVERED (1 node);
ollama stopped → RESTARTED; warm → all UP, no spawn; `-Check` starts nothing, exits 1 on a
critical DOWN. Cold start (Docker Desktop quit before `edge`) observed 2026-09-19: engine →
redis → worker → session came up in order within ~30 s and routes WIN (launch table not captured).
✅ **1c** step 7 / MD-1: edge-npu/gpu/verify no longer wired by default (`-Servers` = deprecated
opt-in with a warning); the appended prompt is now the pipeline-first CLAUDE.md policy with an
absolute `mesh_solve_canvas.py` command; `edge_summary.py` runs only for opted-in legacy servers.
**Remaining acceptance:** idle VRAM = worker only, checked from a fresh `edge` launch.

**Expectation (user, 2026-09-19):** `edge` is the single command. At startup it checks
that every dependency of the pipeline is up and **restarts any that are down**, then
launches the session.

**Reality after #147:** #147 only added the `edge.cmd` shim → `scripts/edge-cli.ps1`
with default flags. edge-cli has no health-check-and-repair step:

| Dependency | Today | Gap |
|---|---|---|
| venv extras | `uv sync --inexact` | ✅ |
| Docker Desktop | not touched; `-Canvas` only warns if `docker` is missing | never started |
| Redis broker (`edge-cascade-redis`) | `docker compose up -d redis` **only with `-Canvas`** | not checked by default (survives only via `restart: unless-stopped`) |
| Celery worker (npu,gpu,verify) | a new worker window **only with `-Canvas`**, no liveness check | never checked by default; a 2nd `-Canvas` launch spawns a **duplicate** worker |
| Ollama API (:11434) | not touched | never checked or started |
| Dashboard (:8789) | spawned if the port is free, else warn | ✅ roughly right |
| Legacy `edge-npu/gpu/verify` MCP servers | **wired by default**, readiness-probed by `edge_summary.py` | inverted: the *retired* path is the one that's health-checked |

**Why I4 (blocking):** `edge` is the entry point to the whole system, and today it
neither guarantees the Canvas pipeline is up nor steers the agent to it. Evidence: ~85
days with no routed task; the launched session's appended prompt mandates the retired
`edge-npu.route` flow (edge-cli.ps1:386-388); and the default `edge-gpu` server holds the
14b resident (**11.3 / 12.2 GB at idle**), so the Celery worker's first GPU task must load
a second copy into ~0.9 GB free → spill/OOM. The pipeline can't be relied on until the
launcher is fixed. **Why S2:** confined to the launcher plus a small, unit-testable Python
probe helper; every step is idempotent; revert = the previous script. Not S1 because it
starts external processes and waits on them (timeouts, Docker Desktop cold start).

**What — a supervisor pass before launching Claude, in dependency order:**

1. **Docker engine** — `docker info`; if down, start Docker Desktop and wait (bounded,
   ~90 s) for the engine.
2. **Redis** — `docker compose up -d redis`; wait for container health `healthy`.
3. **Ollama** — `GET :11434/api/version`; if down, start `ollama serve` (detached) and
   wait. Needed while it's the experiment backend and the llama_cpp fallback.
4. **Celery worker** — `celery -A cascade.celery_app inspect ping`; start the Slice-5
   worker (`_celery-worker.ps1 -Queues npu,gpu,verify`) **only if no node answers** →
   no duplicates. Wait for its pong.
5. **Dashboard** — existing :8789 logic.
6. **Status table** — one line per dependency: `UP` / `RESTARTED` / `FAILED` (+ how to
   fix). **Critical** deps (Docker, Redis, worker) failing → exit non-zero *before*
   launching Claude unless `-Force`; non-critical (dashboard, Ollama) → warn and continue.
7. **Pipeline-first session** (this is MD-1): stop wiring `edge-npu/gpu/verify` by
   default (keep `-Servers …` as a deprecated opt-in with a warning), and replace the
   appended "MANDATORY … edge-npu.route" prompt with the CLAUDE.md policy — one call to
   `mesh_solve_canvas.py --topology budget`, `capped->tier3` → Tier 3. Playwright stays.
8. **Flags:** `-Canvas` becomes default behaviour (kept as a no-op alias); add `-NoSupervise`
   for fast dev relaunches; `-Check` runs steps 1–6 as probe-only (report, never start).

**Design notes:** put the probe/decision logic in a covered Python module
(`scripts/edge_health.py` or `cascade/health.py`: pure `probe_*()` → status objects +
`plan_repairs(statuses)`), so it's under the 100% gate and testable with fakes; keep
edge-cli.ps1 as thin process-spawning glue. Route the Python helper through the pipeline
per the routing rule; the PowerShell glue is Tier 3.

**Acceptance (all live, on this box):**
- Cold start — Docker Desktop quit, Ollama stopped, no worker → `edge` brings all three
  up, prints `RESTARTED` for each, and launches.
- Warm start — everything up → all `UP`, **no** second worker (`inspect ping` = 1 node).
- Worker killed mid-session → next `edge` restarts exactly one.
- Idle VRAM after launch is the worker's footprint only (no `mcp_servers.gpu` process).
- A trivial `mesh_solve_canvas.py --topology budget` task WINs from the launched session.
- `edge -Check` reports without starting anything; CI green at 100%.

---

## JOB arc — job-search daemon: scan → score → tailor → approve → submit  (user, 2026-10-05)

**Why (user, 2026-10-05):** the job search has run as manual "rounds" (19 so far, logged in
`job-apply/CLAUDE.md`): Claude scans Greenhouse/Ashby boards, filters by fit, tailors a
resume + cover letter per job, and fills the form via Playwright. Every round re-derives the
filter and the dedupe by hand. The user wants a **daemon on a timer** that does the whole
flow, routed across a **custom inference mesh**: no model where code suffices, the local
GPU (Ollama 14b, $0) for volume work, Gemini (Google AI Studio) as a cheap second opinion,
the Claude API for quality-critical drafting, and Claude Code + Playwright MCP for the form.
It is also a second real workload for the mesh design, on the same quality-and-cost
metric ([[metric-priorities-quality-cost-over-latency]]).

**Non-negotiables (every JOB task inherits these; an agent that cannot honor one STOPS):**
1. **No PII in this repo.** Code lives here (public, MIT). Every personal artifact (profile
   answers, address, self-ID answers, resume sources, ledger DB,
   generated PDFs, screenshots, slug lists) lives under `JOB_AGENT_HOME`
   (= `C:/Users/danth/src/job-apply`, private, never committed). The repo ships only
   `profile.example.yaml` with fictional data. Tests use fictional fixtures.
2. **Never tick a legal box.** Arbitration agreements, application certifications,
   AI-use attestations, "I have not used AI" statements, email verification codes, and
   captchas are the user's. The agent detects them and **parks** the job; it never clicks them.
3. **A click is not a submission.** A job is `confirmed` only from a confirmation email
   (JOB-16), never from an exit code, a banner, or a "clicked" log
   ([[verify-outcomes-not-exit-codes]]).
4. **Human approval before any submit**, until JOB-15 is un-parked by evidence.
5. **Untrusted text is quarantined.** Job descriptions and form pages are attacker-controllable.
   Only the local tool-less model reads raw JD text (JOB-7); it emits schema-validated JSON,
   and only that JSON flows to stages that hold PII or tools.
6. **Exactly-once submit.** Every state change is a compare-and-swap on the ledger (JOB-2).
   Celery on Redis redelivers a task that outlives `visibility_timeout` (3600 s, see
   `cascade/celery_app.py`), so a long submit task re-run is assumed, and the CAS
   absorbs it.
7. **Facts are never invented.** Resume/letter text is built only from the fact bank
   (JOB-10) and gated by JOB-12. The claim boundaries in the user's profile ("can / cannot
   truthfully claim") are a hard deny-list.

**Where the code lives:** `projects/job-agent/` — its own uv project (Python 3.13, package
`jobagent`), like `projects/vinyl/`. It does **not** import `cascade` (that drags in
OpenVINO/torch); it copies nothing either: small fresh modules in the same style
(credit guard = the `cascade/credit_guard.py` pattern; SQLite ledger = the
`cascade/review_ledger.py` pattern). It reuses the box's **Redis** (separate Celery app
name + queue `jobs`) and **Ollama** (`:11434`). The existing Node tools in `job-apply/`
(`make-resume.js`, `make-cover.js`, `lib/helpers.js`) are called as subprocesses, not ported.

**Mesh routing (the design under test):**

| Stage | Volume | Tier | Task |
|---|---|---|---|
| scan boards | hundreds/day | no model | JOB-4 |
| hard filter + dedupe | hundreds | no model | JOB-5, JOB-2 |
| semantic pre-rank | hundreds | CPU embed (`nomic-embed-text`, `num_gpu: 0`) | JOB-6 |
| structured fit extraction (quarantine) | dozens | GPU 14b, JSON-only | JOB-7 |
| borderline second opinion | a few | Gemini Flash (anonymized input) | JOB-9 |
| tailor resume + letter | a few/day | Claude API (Sonnet) | JOB-11 |
| fact gate | same | no model | JOB-12 |
| pre-fill form | a few/day | Claude Code headless + Playwright MCP | JOB-14 |
| submit | approved only | same | JOB-15 (parked) |
| read confirmation email | a few/day | Gmail read-only + GPU 14b classify | JOB-16 |

**Job states (JOB-2 owns the transition table):**
`seen → filtered_out | candidate → scored → drafted → ready → approved → submitting →
submitted → confirmed | rejected | interview`; any state → `parked` (needs the user, with a
reason) or `failed` (with an error). Terminal: `filtered_out`, `confirmed`, `rejected`,
`interview`, `failed`. `parked` returns to its prior state only by a user action.

**User-owned decisions (block the named tasks; not agent work):**
- **D1 approval channel** (blocks JOB-13): Telegram bot (private chat) vs ntfy (topic name =
  the only secret on ntfy.sh). Either way the message carries company + title + fit score
  only, no PII.
- **D2 Gemini tier** (blocks JOB-9): AI Studio **free tier may use inputs for training**;
  JOB-9 sends only the public JD + an anonymized skills summary, but the user picks free vs paid.
- **D3 Gmail access** (blocks JOB-16): OAuth consent for `gmail.readonly` on a local desktop
  client; token stored under `JOB_AGENT_HOME`, never in the repo.
- **D4 daily spend ceiling** (blocks JOB-11/14): USD/day across Claude API + Gemini + headless
  Claude Code runs. Suggested starting point: a small fixed cap, raised on JOB-18 evidence.
- **D5 `git init` `JOB_AGENT_HOME`** (local only, no remote) so profile/fact-bank edits are
  reviewable. Recommended before JOB-3.

**Agent conventions for every JOB task (written for a cold-start Sonnet agent):**
- One task = one branch = one PR, based on `main`. Read this arc's non-negotiables and the
  task block; touch only the listed files; if a needed change falls outside them, stop and
  ask in the PR.
- Tests first. `uv run pytest` inside `projects/job-agent/`, 100% line + branch coverage
  gate (`fail_under = 100`), pytest-mock `mocker` (not monkeypatch), no network in unit tests
  (fakes at the HTTP / Ollama / subprocess / SQLite-path seams).
- Style: plain functions, dataclasses for data, `main()` is a thin launcher
  (`# pragma: no cover` on `main` + `__main__`), see [[python-main-is-a-launcher]].
- Every code PR gets `scripts/pr_review.py <PR#> --post`; never merge red
  (`gh pr checks <PR#>`). The user merges.
- Done = the task's **Acceptance** commands pass and are pasted into the PR body.

### JOB-1 · scaffold `projects/job-agent`  (I3 · S1)
**Depends:** none. **Files:** `projects/job-agent/{pyproject.toml, README.md,
profile.example.yaml, jobagent/__init__.py, jobagent/settings.py, tests/test_settings.py}`,
`.github/workflows/ci.yml` (new job `job-agent`).
**What:** uv project, Python 3.13, deps `pydantic`, `pyyaml`, `httpx`; dev `pytest`,
`pytest-cov`, `pytest-mock`. `settings.py`: `load_settings(env) -> Settings` reads
`JOB_AGENT_HOME` (required; clear error if unset), derives `ledger_path`, `profile_path`,
`out_dir`; `load_profile(path) -> Profile` (pydantic) validated against the shape of
`profile.example.yaml` (name, location, remote/hybrid prefs, max office days, standing
answers map, claim deny-list). README states the PII rule. Add `*.db`, `out/`, `.env` to the
project `.gitignore`.
**Acceptance:** `uv run pytest` green at 100%; CI job green; `profile.example.yaml` and all
fixtures hold fictional data only.
**Why I3 · S1:** unlocks every other task; additive, no runtime coupling.

### JOB-2 · ledger: SQLite state machine with CAS  (I4 · S2)
**Depends:** JOB-1. **Files:** `jobagent/ledger.py`, `tests/test_ledger.py`.
**What:** tables `jobs(key PK = "<ats>:<board>:<job_id>", company, title, url, ats,
posting_hash, state, prev_state, reason, fit, updated_at)`, `events(job_key, at, from, to,
note)`, `spend(at, job_key, stage, provider, model, usd)`. Functions: `connect(path)`,
`upsert_seen(conn, posting) -> bool` (new or changed hash), `transition(conn, key, frm, to,
note) -> bool` = `UPDATE ... WHERE key=? AND state=?` (CAS; returns False on lost race;
raises on a transition not in `ALLOWED`), `park(conn, key, reason)`, `record_spend(...)`,
`spend_today(conn) -> float`, `by_state(conn, state)`.
**Tests:** every allowed edge; every disallowed edge raises; two concurrent CAS → exactly
one wins (two connections on one tmp file); park/unpark restores `prev_state`.
**Acceptance:** 100% coverage; `ALLOWED` matches the state diagram in this arc verbatim.
**Why I4 · S2:** dedupe and exactly-once submit both rest on it; SQLite CAS is well understood.

### JOB-3 · backfill the ledger from the 19 manual rounds  (I4 · S2)
**Depends:** JOB-2, D5 recommended. **Files:** `jobagent/backfill.py`,
`tests/test_backfill.py`, `tests/fixtures/backfill_sample.md` (fictional rows).
**What:** parse the "Applications submitted — do not duplicate" tables in
`JOB_AGENT_HOME/CLAUDE.md` and the `submitted-<slug>.png` filenames into ledger rows in
state `submitted` (or `confirmed` where the row says email-confirmed; `parked` for
"parked for Dan"; `filtered_out` for "skipped"/"rejected on read"). Key by URL when present,
else `"manual:<slug>:<normalized title>"`. Idempotent. Print a summary: counts per state +
every row it could not parse.
**Acceptance:** run against the real file → zero unparsed rows OR each listed in the PR for
the user; a company the log marks submitted is in `submitted`/`confirmed`;
re-run adds 0 rows.
**Why I4 · S2:** without it the daemon re-applies to ~150 companies; parsing Markdown
tables is fiddly but low risk (read-only on the source).

### JOB-4 · board scanner (Python port of `tools/scan.js`)  (I3 · S2)
**Depends:** JOB-2. **Files:** `jobagent/boards.py`, `tests/test_boards.py`,
`tests/fixtures/{ashby,greenhouse}_board.json`.
**What:** `fetch_board(client, slug) -> list[Posting]` for Ashby
(`api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true`) then Greenhouse
(`boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`). **No Lever** (dead boards,
round 12). `strip_html()` as in `scan.js`. `scan(slugs) -> Iterator[Posting]` with bounded
concurrency (10). `posting_hash` = sha256 of title+location+text. Slugs from
`JOB_AGENT_HOME/slugs.txt`. CLI: `python -m jobagent scan` → `upsert_seen` for each.
**Acceptance:** fixtures normalize to the same fields `scan.js` emits; a live smoke
(`--limit 3`) on three known slugs writes rows (paste counts in PR).
**Why I3 · S2:** a straight port of working code.

### JOB-5 · hard filter: codify rounds 11–19 as pure predicates  (I4 · S2)
**Depends:** JOB-1 (tests run on old scan JSON; no ledger needed). **Files:**
`jobagent/filters.py`, `tests/test_filters.py`, `tests/fixtures/filter_golden.json`.
**What:** port the rules in `JOB_AGENT_HOME/tools/filt.py` + `filt_fit19.py` (title level,
not junior, Python primary, US, remote-OK or Bay/Sacramento hybrid ≤ profile's max office
days, reject 4–5 day / onsite, reject non-Greenhouse/Ashby). Each predicate returns
`(ok: bool, reason: str)`; `apply_filters(posting, profile) -> Verdict(ok, reasons)`.
All thresholds come from the profile, none hard-coded.
**Golden tests:** build `filter_golden.json` from the user's real decisions (round 16
shortlist = pass; its "Rejected on read" list = fail), **with company text replaced by
minimal fictional excerpts that preserve the triggering phrase**. Report agreement.
**Acceptance:** golden agreement ≥ 90%; every disagreement listed in the PR with the
predicate that caused it. **Why I4 · S2:** this is "fits me"; it is regexes over text the
round scripts already proved.

### JOB-6 · semantic pre-rank via local embeddings  (I3 · S2)
**Depends:** JOB-5; `ollama pull nomic-embed-text` (same precondition as KB-1, shared).
**Files:** `jobagent/embed.py`, `tests/test_embed.py`.
**What:** `embed(texts) -> list[list[float]]` via Ollama `/api/embed` pinned to CPU
(`options: {"num_gpu": 0}`, same rule as KB-1 — the daemon must not contend with the
worker's resident 14b); profile vector from
`JOB_AGENT_HOME/fit-summary.md` (a skills-only summary, no PII); `rank(candidates) ->
[(key, cosine)]`; store on the job row; only the top N (config, default 15/day) advance.
**Acceptance:** on the golden set, applied jobs' median rank beats rejected jobs' median
rank (report both). **Why I3 · S2:** additive, $0, one HTTP seam.

### JOB-7 · quarantined fit extractor (GPU 14b, JSON only)  (I3 · S3)
**Depends:** JOB-6. **Files:** `jobagent/extract.py`, `jobagent/prompts/extract.txt`,
`tests/test_extract.py`.
**What:** Ollama `/api/chat` with `format` = the JSON schema of `Extraction{seniority,
office_days: int|null, python_primary: bool, must_haves: list[str], red_flags: list[str],
legal_gates: list[str], fit: int 0-100, fit_reason: str ≤ 300 chars}`. Input = JD text
truncated to 6k chars + the skills-only summary. **No tools, no PII in the prompt.**
pydantic validation; one retry on invalid JSON; then `park(reason="extract_invalid")`.
Strings are length-capped and stripped of URLs before anything downstream sees them.
Spend row with `usd=0`.
**Acceptance:** unit tests with a fake Ollama; live run on 10 golden JDs → 10 valid
`Extraction`s (paste them). **Why S3:** prompt quality is an unknown; JOB-8 measures it.

### JOB-8 · calibrate the extractor (experiment)  (I3 · S2)
**Depends:** JOB-7. Protocol: `/experiment` (local evidence branch, `keep_awake`, never
merge the branch). **What:** run JOB-7 over the golden set ≥ 5 seeds each; report
agreement with the user's real decisions as a Beta posterior + 95% CI; per-threshold
precision/recall for `fit`; recommend the `fit` cutoff and the borderline band for JOB-9.
**Acceptance:** FINDINGS doc via a clean PR citing branch + sha. **Why I3 · S2:** $0, no
production change.

### JOB-9 · Gemini second opinion on the borderline band  (I2 · S2)
**Depends:** JOB-8 (sets the band), D2. **Files:** `jobagent/gemini.py`,
`jobagent/guard.py` (credit guard, `cascade/credit_guard.py` pattern), tests.
**What:** `google-genai` SDK; model id from config; input = the JOB-7 `Extraction` + public
JD excerpt + skills summary (no PII); output = same schema; final fit = mean. Guarded by
the daily ceiling (D4); a tripped guard skips (job keeps the 14b score), never blocks.
**Acceptance:** fake-client tests; spend rows recorded. **Why I2:** a tiebreaker; the
pipeline works without it.

### JOB-10 · fact bank  (I3 · S2)
**Depends:** JOB-1, D5. **Files:** `jobagent/facts.py`, tests; data file
`JOB_AGENT_HOME/facts.yaml` (**not** in the repo).
**What:** schema `Fact{id, employer, title, dates, kind: bullet|skill|metric|education,
text, numbers: list[str]}`. The agent drafts `facts.yaml` from `JOB_AGENT_HOME/resumes/base.md`
(+ the three source PDFs it names) with one fact per source bullet, and the claim deny-list
from the profile. **The user reviews `facts.yaml` before JOB-11 uses it** (park until
approved: `facts.yaml` header `reviewed: true`).
**Acceptance:** `load_facts()` validates; every bullet in `resumes/base.md` maps to ≥ 1 fact
id (report unmapped). **Why I3 · S2:** the ground truth the gate needs; low risk.

### JOB-12 · fact gate: deterministic verifier for resumes + letters  (I4 · S2)
**Depends:** JOB-10. (Built **before** JOB-11, which must pass it.) **Files:**
`jobagent/factgate.py`, tests.
**What:** input = a resume/letter Markdown where each bullet/sentence carries
`[f:<id>]` citations (stripped before rendering). Rejects when: a citation id is
unknown; a number, employer, title, or date range in the text is absent from the cited
facts; a deny-list claim appears; an uncited sentence contains a number or a proper noun
from a fixed list (employers, tools). Returns `GateResult(ok, violations[])`.
**Acceptance:** tests include one passing doc and one doc per violation type; 100%.
**Why I4 · S2:** this is the "never invent a fact" guarantee under the user's name;
deterministic code, no model.

### JOB-11 · tailor resume + cover letter (Claude API)  (I3 · S3)
**Depends:** JOB-12, D4. **Files:** `jobagent/tailor.py`, `jobagent/prompts/tailor.txt`,
tests.
**What:** Anthropic SDK, model `claude-sonnet-5-5` (config). Input = `Extraction` +
selected facts + the house rules (lead with professional experience; edge-cascade is a
supporting paragraph; AI use framed as quality first). Output = cited Markdown in the
`make-resume.js` / `make-cover.js` source format. Gate with JOB-12; on reject, one repair
call with the violations; then `park("factgate")`. On pass: strip citations, write
`JOB_AGENT_HOME/resumes/<slug>.md` + `cover-letters/<slug>.md`, run
`node make-resume.js <slug>` / `node make-cover.js <slug>` (subprocess, cwd =
`JOB_AGENT_HOME`), state → `drafted`. Spend row from the API usage.
**Acceptance:** fake-client tests for pass / repair-pass / park; one live run on a golden
job whose PDF the user reads (in the PR: the gate result, not the PDF).
**Why S3:** model output under the user's name; bounded by the gate.

### JOB-13 · digest + approval channel  (I3 · S2)
**Depends:** JOB-2, D1. **Files:** `jobagent/notify.py`, tests.
**What:** `send_digest(jobs)` = one message: index, company, title, fit, one-line reason
(no PII, no URLs to local files). `poll_replies()` parses `approve 1,3` / `skip 2` /
`park 4` → CAS transitions `ready → approved` etc. Unknown reply → help text.
**Acceptance:** fake-transport tests; a live round-trip with one fictional job.
**Why I3 · S2:** small; it is the human gate.

### JOB-14 · form pre-fill agent (headless Claude Code)  (I4 · S3)
**Depends:** JOB-2, JOB-11, D4. **Files:** `jobagent/prefill.py`,
`jobagent/prompts/prefill.md`, tests.
**What:** one job per invocation: `claude -p <prompt> --allowedTools <Playwright MCP
browser_* tools + Read>` with cwd = `JOB_AGENT_HOME` (so `job-apply/CLAUDE.md`'s
Ashby/Greenhouse playbooks load). The prompt carries the job URL, file paths, and the rule
"**stop before Submit**; read back every field; list every checkbox/attestation and do not
tick legal ones". The agent writes `out/<key>/prefill.json` (fields + read-back values +
`legal_gates[]` + screenshot path). `prefill.py` validates that JSON; any `legal_gates` or
read-back mismatch → `park`; else `drafted → ready`. Hard timeout 20 min (< the 3600 s
visibility timeout).
**Acceptance:** subprocess faked in unit tests; live: 3 real jobs pre-filled, screenshots
match read-backs (user checks), zero submits.
**Why I4 · S3:** the bulk of each round's manual effort; risky because forms lie
([[ashby-form-playbook]]), so it stops before Submit.

### JOB-15 · submit on approval  (I4 · S4) — ⏳ PARKED
**Why S4:** irreversible, under the user's name, on forms known to report set fields while
the underlying state is empty; past rounds recorded a mis-set screening answer and
falsely-reported successes. **De-risk steps (re-score after each):**
1. JOB-14 runs for ≥ 2 weeks with the user clicking Submit by hand; log read-back vs
   screenshot mismatches per ATS → Beta posterior of a clean pre-fill.
2. Submit path = re-open the form, re-verify every field against `prefill.json`, CAS
   `approved → submitting` **before** clicking (a redelivered task finds `submitting` and
   exits), click once, screenshot, `→ submitted`; never retry a click automatically.
3. Allow only ATS/form shapes whose clean-pre-fill posterior lower bound clears a bar the
   user sets. When steps 1–3 hold, S4 → S3 and JOB-15 enters at I4.

### JOB-16 · confirmation-email reader  (I3 · S3)
**Depends:** JOB-2, D3. **Files:** `jobagent/mail.py`, tests.
**What:** Gmail API `gmail.readonly`, query recent mail from ATS senders; match to
`submitted` jobs by company name + date window; GPU 14b classifies
`confirmation | rejection | interview | other` (JSON schema, same quarantine as JOB-7);
CAS transition. Unmatched mail → digest line, no state change.
**Acceptance:** fake-API tests; live: the last 20 known confirmations match.
**Why S3:** OAuth + fuzzy matching; read-only scope bounds the blast radius.

### JOB-17 · the daemon: Celery beat + worker on the shared Redis  (I3 · S3)
**Depends:** JOB-4, JOB-5, JOB-7, JOB-13 (later stages plug in as they ship).
**Files:** `jobagent/celery_app.py`, `jobagent/tasks.py`, `scripts/start-job-agent.ps1`
(in `projects/job-agent/`), tests (eager mode).
**What:** Celery app `jobagent`, queue `jobs`, Redis URL from env (same broker as
edge-cascade, different queue; never consumes `cascade` queues). Beat: `scan` every 6 h →
chain `filter → embed → extract` on new rows; `tailor` + `prefill` for the top N within the
daily spend ceiling; `digest` at 08:00 PT; `poll_replies` every 5 min; `mail` hourly.
Every task is idempotent (CAS) and `time_limit` < 3600 s. `keep_awake` while a chain runs
([[experiment-machine-sleep-state]]). The start script launches beat + one worker with
`--concurrency 1` (one browser at a time).
**Acceptance:** eager-mode tests for the chain; a live 24 h run produces a digest and
zero duplicate rows (paste `by_state` counts).
**Why S3:** long-running process on a laptop; bounded by the CAS and the spend guard.

### JOB-18 · funnel + spend report  (I2 · S1)
**Depends:** JOB-2. **Files:** `jobagent/report.py`, tests.
**What:** `python -m jobagent report` → counts per state, $ per stage per job, and
interview rate per source (Beta posterior + 95% CI), weekly.
**Acceptance:** fixture DB → expected table. **Why I2 · S1:** tells us whether the mesh
pays for itself; small.

**JOB pick order (impact ↓, severity ↑; dependencies pull forward):**
JOB-1 (dep) → JOB-2 → JOB-3 → JOB-5 → JOB-10 (dep) → JOB-12 → JOB-14 → JOB-4 → JOB-6 →
JOB-13 → JOB-7 → JOB-8 → JOB-11 → JOB-16 → JOB-17 → JOB-9 → JOB-18. **Parked:** JOB-15.
**First useful milestone:** after JOB-5 + JOB-4, `scan` + `filter` replace a manual
round's discovery step. **Second:** after JOB-14, a round = review the digest and click Submit.

---

## SQ arc — signal quality: is the pipeline measuring and sampling what we think?  (2026-10-06)

**Origin:** a critique (2026-10-06) of an external "why is local inference quality low"
analysis. Most of its root causes did not apply here: the 8 GB VRAM spill assumption (this box
has 12 GB with every 14b layer on the GPU, PT-1), Ollama `num_ctx=2048` (production GPU
is llama_cpp at `n_ctx=8192`, Slice 7), and missing ChatML on the NPU (`npu_worker._CHAT`
already wraps `<|im_start|>`/`<|im_end|>`, greedy decode). Its "100% deterministic"
grammar-constrained-shell and "9.5/10" claims had no evidence. What survived is below, plus
two open findings from 2026-09-19 that the critique pointed back to. **Order matters:** DX-1
first — every experiment's baseline is suspect until we know the repair loop runs.

### DX-1 · diagnose cap-everywhere + `repair_rnds=0`  (I3 · S1)
**Evidence (2026-09-19 sessions, in the open-threads log, not yet investigated):** every
route that session capped — git commands (which should WIN post-VR-4), `probe_all` with a
DSL, commit messages, PR bodies; one `budget` route took > 5 min; and a DSL-gated
`plan_repairs` route capped with **`repair_rnds=0` in 12.7 s**, i.e. the GPU repair round may
never have run. The tail of `runs/cascade.rec` is still `done: LOSE (-> capped->tier3)`.
**What (read-only first):** (1) tabulate `runs/cascade.rec` since 2026-09-19 by
outcome × task kind × `repair_rnds` × wall time; (2) for each cap with `repair_rnds=0`, trace
the chain step that ended it (`topologies_canvas` budget chain: route → draft → gate →
repair) — is it the skip-repair-on-degen path (PD-1 v2, by design), an unavailable GPU
(`available=False` → cap), or a bug; (3) for prose routes, confirm "no gate ⇒ always cap"
is by design and decide whether prose should be routed at all; (4) one live repro per
distinct cause. **Output:** a FINDINGS doc + one follow-up item per bug found (S-scored then).
**Why I3:** if the repair loop silently doesn't run, the local win rate, every A/B baseline
(EXP-SP-1, KB-3, EXP-MR) and the routing policy are all wrong. **Why S1:** analysis + repro,
no code change in this item.
**Acceptance:** every 2026-09-19+ cap is attributed to a named cause; the `repair_rnds=0`
case is explained (by-design or bug + follow-up item filed).

### DX-2 · gate on the task's language, not the output's  (I3 · S2) — after DX-1
**The gap:** `cascade/gate.py` picks the verifier from the **output's** fence
(`detect_language`), and unfenced output goes to `gate_any`, which passes if **any**
registered language verifies. Observed 2026-09-19: the NPU answered a git task by wrapping
git in a Python `subprocess` call → the Python gate passed it → a false NPU WIN.
**What:** thread the task's expected language (the router's `category`, or an explicit
`--lang` on `mesh_solve_canvas.py`) into `gate(text, dsl, expected=...)`; a fenced block in a
different language fails with `expr: "language-mismatch"` (the repair prompt's OUTPUT
CONTRACT from VR-5 already tells the model what to emit); `gate_any` only runs when no
expected language is known. Route the change through the pipeline.
**Why I3:** false WINs corrupt the win/lose ledger that every routing and experiment
decision reads. **Why S2:** one call-site signature in the hot path (VR-4 class) but
additive — `expected=None` is byte-identical to today; guarded by the parity batch.
**Acceptance:** the 2026-09-19 git-in-Python output fails the gate when the task is git;
`expected=None` parity unchanged; CI green at 100%.

### EXP-SP-1 · experiment: GPU sampling temperature 0.8 vs 0.2  (I3 · S2) — after DX-1
**Fact:** `cascade/config.py` defaults `CASCADE_GPU_TEMPERATURE=0.8` (top_p 0.95). SR-1 made
it explicit but only preserved Ollama's old default; no experiment chose it. 0.8 is high
for code. **But do not just flip it:** the bounded repair loop and the self-consistency
/ context-precision experiments depend on sample diversity — a near-greedy retry can
reproduce the same bug, so a lower temperature could raise first-pass and *lower*
post-repair pass rates.
**Hypothesis (stated before running):** at 0.2, first-pass functional pass rate rises;
post-repair pass rate is ≥ 0.8's; caps (handoffs) fall. **Arms:** 0.8 (control) · 0.2 ·
optional hybrid (0.2 first pass, 0.8 on repair rounds — needs a per-round knob, build only
if the two plain arms disagree on first-pass vs post-repair). **Subjects:**
`scripts/model_bench.py` functional subjects (dijkstra class) + parity A/B/C +
`git_model_bench.py` NL→git. **Trials:** ≥ 30 per (arm × subject), seeds recorded (SR-1),
llama_cpp backend (production). **Metrics:** first-pass rate, post-repair rate, cap rate
→ Beta posteriors, 95% CI, P(arm > control), paired conditionals
([[experiment-methods-bayesian-monte-carlo]]). **Decision gate:** change the default only if
P(arm > control) ≥ 0.95 on post-repair pass rate (the outcome that matters) **and** no
subject regresses. Protocol: `/experiment`.
**Why I3 · S2:** the cheapest likely quality lever on every GPU route; $0, evidence branch,
no production change until a separate flip PR. Its winning temperature then feeds EXP-MR-1.

### RT-1 · router JSON fallback rate + structured-output probe  (I2 · S1)
**Fact:** `npu_worker._route` regex-extracts `{...}` from the 1.5B's output and, on any parse
failure, **silently defaults to `difficulty=0.5, category="standard"`** — no record that it
fell back. (Distinct from #8 difficulty-recal, which addressed *parsed* over-rating.)
**What:** (1) measure: replay the router over the historical prompts in `runs/edge-npu.rec`
/ `cascade.rec` (greedy ⇒ deterministic) and count fallbacks; add a `route_parsed: bool`
field to the route record so it's visible going forward; (2) probe: does the installed
`openvino_genai` (2026.1) support structured / JSON-schema-constrained generation on the
NPU device (the analysis called it "GBNF", which is llama.cpp's grammar format, not
OpenVINO's)? Scratch-venv spike, no repo change.
**Why I2 · S1:** affects tier selection only when parsing fails — size unknown until
measured; measurement + a record field is additive.
**Follow-up (not yet scored): RT-2** constrain the router's output to the JSON schema —
score it only if RT-1 finds a material fallback rate **and** the probe finds support.

### OL-1 · pin `num_ctx` on the Ollama path  (I2 · S1)
**Fact:** `gpu_worker._generate` sends `num_predict`/`temperature`/`top_p`/`seed` but **no
`num_ctx`**, so the Ollama backend runs at whatever context Ollama chooses per model and
version. Production (llama_cpp, `n_ctx=8192`) is unaffected, but the Ollama path is the
fallback **and** the EXP-MR backend, where differing defaults per candidate would confound
the comparison.
**What:** pass `num_ctx` from one shared knob (reuse `CASCADE_LLAMA_N_CTX`, default 8192, so
both backends agree) and record it in the result dict (SR-1 identity fields).
**Why I2 · S1:** one option key on a non-default path; unit-tested at the HTTP seam.
**Acceptance:** the Ollama payload carries `num_ctx`; the `.rec` result shows it; CI green.

---

## NH arc — NPU as a helper node; GPU best-of-N selected by the gate  (user, 2026-10-06)

**Why (user, 2026-10-06):** raise local inference quality and use the hardware better: RAG
for the GPU, and move NPU load from coding inference to helper roles in the pipeline.
**Evidence that shaped the plan:** `runs/cascade.rec` (144 outcomes to 2026-10-06) = **NPU
WIN 43 (30%) · GPU WIN 46 (32%) · capped→tier3 55 (38%)**. The NPU wins about as often as the
GPU, mostly on short tasks, so pulling it off coding entirely would push cheap wins up a
tier. Some of those NPU WINs are false (git-wrapped-in-Python, DX-2). **Decision:** keep NPU
drafting only for task classes where the cleaned ledger proves it wins (NH-5), and give the
NPU helper jobs everywhere else.
**Why spec extraction + best-of-N are a pair:** most routes carry no DSL, so the gate is
syntax-only. That is where false WINs come from, and it is why sampling more candidates
cannot help: a syntax gate cannot tell a right candidate from a wrong one. NH-1/NH-2 give the
gate a functional check on more routes; NH-3 spends GPU calls on independent samples that
the functional gate can then select between.

**Target shape (each node behind a default-off flag until NH-4 measures it):**
```
route (NPU) ─┬─ task class with a proven NPU win rate (NH-5) ─► NPU draft ─► gate
             └─ everything else:
                spec-extract (NPU, NH-1/2) → retrieve (KB arc; NPU embed + rerank if NH-0 allows)
                → GPU sample, up to N, gate-selected (NH-3) → gate with extracted DSL (CPU)
                → Tier 3
```
**Ruled out (recorded so it isn't re-proposed):** NPU-drafts / GPU-verifies speculative
decoding. Syncing the two devices on every token cancels the gain. Same-GPU speculative
decoding (0.5b draft for the 14b) only buys speed, which is a tiebreaker here
([[metric-priorities-quality-cost-over-latency]]).
**Ordering:** after DX-1 (repair-loop baseline) and alongside DX-2 (gate language), which
NH-2 depends on.

### NH-0 · spike: how many models can the NPU hold at once?  (I3 · S1)
**Question:** can `openvino_genai` keep the 1.5B router/drafter **plus** a spec-extractor
prompt (same model, no new weights), an embedder (`bge-small-en-v1.5`, INT8 IR), and a small
cross-encoder reranker resident on the NPU together, and at what compile time, memory, and
per-call latency? **Fallback device:** the Arrow Lake **Xe iGPU** (OpenVINO `GPU` device —
check the device-name ordering against the RTX; OpenVINO only enumerates Intel GPUs). Then CPU.
**What:** scratch-venv script, no repo change: load each combination, time 50 calls each,
record `npu_compile_s`, resident memory, p50/p95 latency; run the existing router
concurrently to check for contention. **Output:** a short FINDINGS doc with a placement
table (model → device) that NH-1 and KB-1 consume.
**Why I3 · S1:** decides where every helper node runs; read-only spike.
**Acceptance:** the placement table exists with measured numbers; KB-1's embedder choice
(currently CPU-pinned `nomic-embed-text`) is confirmed or switched by it.

### NH-1 · spec extractor: prompt → DSL test cases  (I3 · S2) — after NH-0
**What:** `cascade/spec.py` — `extract_spec(prompt) -> Spec(fn_name, arity, cases)` via the
NPU model with a strict JSON output contract, then `dsl_from_cases(fn_name, cases)` (exists,
`cascade/verifier.py`). **Safety rule (the whole point):** extract **only what the prompt
states**: examples written in the prompt (`f(3) -> 6`), the stated name and signature.
Never invent cases. A prompt with no examples yields a signature-only check (the function
exists and accepts the stated arity), and a parse failure yields `None` (today's behaviour).
A wrong extracted test rejects correct code, which is worse than no test.
**Calibration (offline, $0):** run the extractor over the `scripts/model_bench.py` subjects
(they have ground-truth DSLs and reference solutions) plus the historical prompts in
`runs/cascade.rec`. Measure **false-reject rate = extracted DSL fails a known-correct
reference solution** (must be **0**) and coverage (share of prompts that get ≥ 1 case).
**Why S2:** a pure module + offline calibration; nothing in the chain calls it yet.
**Acceptance:** false-reject = 0 on every reference solution; coverage reported; fakes at
the NPU seam; CI green at 100%.

### NH-2 · wire extracted DSL into the budget chain  (I3 · S3) — after NH-1 + DX-2
**What:** a `_budget_spec` step after `_budget_route`: when the caller passed no `--dsl`, set
`env["dsl"] = extract_spec(...)`, tagged `dsl_source="extracted"` in the trace. A
caller-supplied DSL always wins. Flag `CASCADE_SPEC_EXTRACT = off | on`, **default off**,
no-op via the existing `_shortcut` pattern.
**Why I3:** turns syntax-only routes into functional ones, which cuts false WINs and makes
every later quality lever measurable. **Why S3:** a hot-path chain change (VR-4 / KB-2
class). An extracted test that is wrong would flip correct code to a cap; the NH-1
false-reject gate and the default-off flag bound that.
**Guard:** parity batch with the flag off; with it on, re-run NH-1's reference set through
the live chain.
**Acceptance:** with `off`, outcomes match today; with `on`, a prompt carrying examples shows
`dsl_source=extracted` and a functional gate result in the trace.

### NH-3 · GPU best-of-N, selected by the gate  (I3 · S3) — after NH-2
**What:** when a functional DSL exists (caller-supplied or extracted), the GPU tier may
spend its round budget on **independent fresh samples** (stop at the first that passes the
gate) instead of repair rounds that condition on the failed candidate. Mode flag
`CASCADE_GPU_STRATEGY = repair | resample | hybrid` (hybrid = one fresh resample then
repair), **default `repair`** (today). The GPU-call budget is the same `cap` in every mode, so
the comparison is at equal compute and the spend invariant is untouched. Prior art:
PD-1 v2 skip-repair (+22.8 pp) already showed that a fresh generate beats repairing a
poisoned draft.
**Why I3:** likely the strongest local quality lever: local samples are $0 and quality
outranks latency. **Why S3:** changes what the GPU tier does with its budget; bounded by the
unchanged cap, the default-off mode, and `over_cap_episodes = 0`.
**Acceptance:** each mode stays within `cap` GPU calls (tested); `repair` is byte-identical
to today; the trace records the mode and which sample won.

### NH-4 · experiment: does the pair raise the local win rate?  (I3 · S2) — after NH-3
**Hypothesis (stated before running):** at an equal GPU-call budget, `resample` with an
extracted DSL beats `repair` on post-gate functional pass rate, and the gain is concentrated
on prompts that state examples. The false-WIN rate (scored against the reference solutions)
falls with `CASCADE_SPEC_EXTRACT=on`.
**Arms:** extract {off, on} × strategy {repair, resample, hybrid}. Temperature fixed at
EXP-SP-1's winner (resample needs diverse samples, so EXP-SP-1 should report first).
**Subjects:** `model_bench.py` functional subjects + parity A/B/C + git subjects.
**Trials:** ≥ 30 per (arm × subject), seeds recorded (SR-1). **Metrics:** functional pass
rate against reference solutions (**not** the gate's own verdict, which is the thing that
changed), cap rate, GPU calls per resolved task → Beta posteriors, 95% CI,
P(arm > control), paired conditionals. **Decision gate:** flip a default only if
P(arm > control) ≥ 0.95 pooled and no subject regresses. Protocol: `/experiment`.
**Why I3 · S2:** measurement-only, $0, evidence branch.

### NH-5 · ledger-driven NPU drafting  (I3 · S3) — after DX-2 cleans the ledger
**What:** the router decides *whether* the NPU drafts from the measured NPU win rate per
task class (router `category` × difficulty band) in the cleaned `cascade.rec`, not from
the difficulty threshold alone. Classes below a floor (e.g. P(NPU win) < 0.3 by Beta
posterior) skip straight to the GPU path; the NPU's freed time goes to the helper nodes.
Re-estimated from the ledger on a schedule, never per call.
**Why I3:** this is the user's "move NPU load off coding" done with evidence: drafting stays
where it wins and stops where it only delays the GPU. **Why S3:** changes tier selection,
the same class as #8 difficulty-recal; measure cap rate and the NPU-win share before and
after on `cascade.rec`.
**Acceptance:** a per-class table of NPU win posteriors is committed with the change; the
skip decision is unit-tested against it; win/lose shape on the parity batch is no worse.

**Not yet scored (follow-ups from the 2026-10-06 brainstorm):**
- **NPU reranker in KB-2's retrieve step**: CPU search for the top 20, rerank to the top 3.
  Placement comes from NH-0. Usually a bigger precision gain than a better embedder.
- **Hybrid search** (BM25 on CPU + vectors): exact symbol names beat embeddings on code;
  pairs with KB-4.
- **Solved-history corpus**: the pipeline's own past WIN answers as few-shot examples.
  This is the KB arc's top predicted corpus, and it is not yet an item. Needs DX-2 first so
  false WINs don't seed it.
- **NPU context compression** of long retrieved passages, if the KB-2 token cap bites.
- **Parallel gate execution** for N candidates on CPU, if NH-3 drops its early-exit.

---

## CI housekeeping

### CI-1 · bump GitHub Actions off Node 20  (I2 · S1)
**Fact:** every CI run carries a deprecation warning: `actions/checkout@v4` and
`astral-sh/setup-uv@v5` target Node 20 and are being forced onto Node 24.
**What:** bump both in `.github/workflows/ci.yml` to their current majors (check each
action's releases for the Node 24 major; keep `enable-cache` + `python-version` inputs
compatible).
**Acceptance:** a CI run on the PR shows no Node 20 annotation and stays green.

### CI-2 · stop the false-alarm "cancelled" CI runs  (I2 · S1)
**Fact (2026-10-06, PR #156; same on #155):** a force-push to a PR branch fired two
`pull_request` runs milliseconds apart. `concurrency: cancel-in-progress` cancelled one, and
GitHub shows that as a **failure-level annotation** ("Canceling since a higher priority
waiting request … exists") on the job named `… + bandit (Windows)`. That reads like a bandit
error; it isn't (bandit: "No issues identified"), and re-running the cancelled job cleared
it. Separately, `on: push` with no branch filter runs CI twice for every PR-branch push
(once for `push`, once for `pull_request`).
**What:** (1) `on: push: branches: [main]` so PR branches run CI once, via `pull_request`;
(2) `cancel-in-progress: ${{ github.event_name != 'pull_request' }}` so superseded PR runs
finish (about 40 s each) instead of being cancelled into a red annotation. The check on the
new head SHA is unaffected.
**Why S1:** workflow-only; revert = the previous file.
**Acceptance:** a force-push to a PR yields no cancelled run; a push to main still runs CI.

## KB arc — RAG: local knowledge base + `retrieve` step  (product path)

**Why (user, 2026-09-24; corrected same day — the original prompt said "DAG", a typo for
"RAG"):** the revenue direction is *private local AI* — an appliance a regulated buyer
runs on their own hardware, whose core is **retrieval-augmented generation over documents
that never leave the box**. The book's text goes into a local vector store; before a
model drafts, the pipeline retrieves the most relevant passages, prepends them to the
prompt, and records which passages were used (an audit trail). Corpus #1 is *The
Pragmatic Programmer* (20th-anniversary ed., 100 numbered tips) — it improves our own
pipeline and dogfoods the RAG path; corpus #2 is a client's own documents, which is what
ships. The book's
text never leaves this machine (own copy, local index; **not** resellable).

**Hypothesis (formalized 2026-09-24, user; stated before any code):**
- **H1 (effectiveness):** conditioning the local tiers' prompts on retrieved passages
  relevant to the task raises the local functional pass rate at a fixed repair cap.
- **H2 (cost):** any H1 gain shows up as a lower `capped->tier3` handoff rate and fewer
  GPU repair rounds per resolved task — i.e. a cheaper tier per WIN. Retrieval overhead
  (one embed call + a vector search) is negligible against one GPU round; the real cost is
  prompt tokens on the 1.5B NPU's small context.
- **H0 (corpus):** *The Pragmatic Programmer* yields no measurable lift (P(lift > 0) < 0.95);
  the user's stated confidence is low. It is the **construction corpus** (build the RAG
  path on a real, well-structured text) and a **negative control** — if it shows lift, the
  measurement leaks noise. Once the path is sound, swap corpora.
- **Predicted corpus ordering (Tier 3's input):** the pipeline's own solved history
  (past WIN answers for similar tasks, as few-shot — matches the task distribution, $0,
  grows with every win) > the gate's output contracts + verifier rules > API / stdlib
  references for the libraries the tasks use > the book. **Design implication for KB-1:**
  the corpus abstraction must support incremental append, not only a one-shot book load.
- **KB-3 refinements (Tier 3's input):** a **placebo arm** (k random passages) to separate
  *relevant* context from *any extra* context; a **relevance floor** (skip injection when
  the top score is below a threshold) so retrieval cannot poison an easy task.
- **Primary metric:** handoff rate + repair rounds per resolved task, then pass rate;
  latency is a tie-breaker only ([[metric-priorities-quality-cost-over-latency]]).

**Substrate facts (2026-09-24):** no vector store or embedding library is installed; Ollama
0.34.3 holds only `qwen2.5-coder:14b` (no embedding model); the book is the publisher's
**PDF** (P3.0, 340 pp, DRM-free) at `C:/Users/danth/OneDrive/Documents/the-pragmatic-programmer-20th-anniversary-edition_P3.0.pdf`
(outside the repo, never committed); the budget chain threads one envelope
through Celery steps (`cascade/topologies_canvas.py`), so a new step slots in without
touching the gate or the repair cap.

**Choices (proposed; the KB-1 spike may overturn them):** embeddings = `nomic-embed-text`
via Ollama `/api/embed` **pinned to CPU** (`options: {"num_gpu": 0}`) — revised 2026-10-06:
since Slice 7 the worker holds the 14b in-process at ~10.5 GB (PT-1 VRAM delta 10,526 MB of
12,227), so an Ollama-resident embedder on the GPU eats the last ~1.2 GB of headroom the
KV cache needs at long context ([[llm-vram-cliff-12gb]]). One short query embed per route
is cheap on CPU; the one-time book ingest is a batch job. Fallbacks if CPU latency is bad:
a small ONNX/OpenVINO embedder (`bge-small-en-v1.5`) on CPU, or on the NPU *only if* a
second `openvino_genai` pipeline can share the NPU with the router/drafter (unverified;
**NH-0 measures this** and its placement table decides the device before KB-1 starts).
store = LanceDB
(embedded, file-based, pip-only, Apache-2.0, Windows wheels); chunking = the book's own
boundaries with `corpus`, `chapter`, `topic`, `tip`, `page`, `text` columns so citations
are exact.

**PDF spike (2026-09-24, read-only, ephemeral `pypdf`):** real text layer; a 106-entry
bookmark outline with all **53 Topics** and their start pages (chapters 1-9, Postface,
appendices, Index) -> chunk = one Topic's page range, sub-split at tip callouts; all
**100 tips** appear inline as `<title>Tip <n>` callouts (regex-extractable; Tip 15 = DRY on
p51, Tip 100 on p303); em-dashes and curly quotes extract as real U+2014 / U+2019 (the
console garbling was display-only, no fontTools needed); `lancedb 0.39.0` + `pyarrow 25`
resolve on the venv's **Python 3.13.12**; Ollama answers on :11434. Tip 1's first regex hit
is the preface's "box labeled Tip nn" -- anchor the callout regex to a line end.

### KB-1 · ingest: book → chunks → embeddings → LanceDB  (I3 · S2)
**Preconditions:** [x] book file (PDF, above); `ollama pull nomic-embed-text` and confirm a
CPU-pinned embed call leaves `nvidia-smi` unchanged while the worker holds the 14b; `uv add lancedb pypdf` (wheels verified on 3.13);
the standing substrate health check (one trivial `budget` route WINs — last route was
117 h ago).
**What:** `cascade/knowledge.py` — pure functions `chunk_book()`, `embed()`, `upsert()`,
`search(query, k, corpus)` — with `chunk_book()` driven by the PDF outline (Topic page
ranges) + the tip-callout regex — and `scripts/kb_ingest.py --corpus pragprog <file>` (thin
launcher, [[python-main-is-a-launcher]]). `corpus` is a parameter from day one: the second
corpus must need **zero code**. Route both through the pipeline; tests use fakes at the
Ollama and LanceDB seams, under the 100% gate.
**Why I3:** the first real new capability on the product path; nothing in the repo
retrieves today. **Why S2:** purely additive module + script; the dependency unknowns
(wheels, text layer, outline) were closed by the spike.
**Acceptance:** `kb_ingest` loads the book once and a re-run is idempotent; `embed()` passes
`num_gpu: 0` (unit-tested at the HTTP seam) and a live embed adds 0 MB VRAM;
`search("don't repeat yourself", k=3)` returns the DRY tip (Tip 15 in the
20th-anniversary numbering) in the top 3 with chapter + tip number; CI green at 100%.

### KB-2 · `retrieve` step in the budget chain (RAG)  (I3 · S3) — depends on KB-1
**What:** a new Celery task `_budget_retrieve` between `_budget_route` and `_budget_draft`:
`search(env["query"], k)` → `env["passages"] = [{corpus, tip, chapter, text, score}]`.
The NPU draft prompt and `feedback.build_repair_prompt()` prepend the passages when
present (the augmentation half of RAG); the trace gains `retrieved=[tip ids]` so the
dashboard shows the audit trail. Targeting flag `CASCADE_RAG_TARGET = off | npu | gpu |
both`, **default `off`** until KB-3 proves a gain; `off` makes the step a no-op via the
existing `_shortcut` pattern.
**Why I3:** this is the RAG path the product is built on, and it feeds the pipeline's own
win rate. **Why S3:** a hot-path chain change (same class as VR-4) plus prompt changes that
move the primary metric; blast radius confined by the default-off flag.
**Context cap (added 2026-10-06):** retrieved passages are truncated to a hard token budget
(`CASCADE_RAG_MAX_TOKENS`, default ~1,000) before prepending. The 14b slows sharply as
context grows ([[llm-vram-cliff-12gb]]), and NH-3 multiplies prompt cost by the sample count.
**Guard:** parity check (`scripts/parity_batch.py`, Case B within ±20%) with the flag `off`
before merge; the live `tests/test_canvas_live_behavior.py` probe.
**Acceptance:** with `off`, `runs/cascade.rec` outcomes match today's shape apart from the
new empty field; with `gpu`, a route's trace lists the retrieved tips; the repair cap is
untouched (`over_cap_episodes` = 0).

### KB-3 · experiment: does RAG raise the local win rate?  (I3 · S2) — after KB-2
**Hypothesis (stated before running):** `gpu` targeting helps the 14b repair round;
`npu` targeting *hurts* the 1.5B draft (tiny context — passages crowd out the task).
**Arms:** `off` (control) · `placebo` (k random passages) · `npu` · `gpu` · `both` — all on the
book corpus — plus **`gpu × symbols`** (corpus = KB-4's `cascade-symbols`; added 2026-10-06).
**Symbols hypothesis (stated before running):** the 14b knows Python but not this repo's
APIs, so injecting the signatures a task touches lifts tasks that call `cascade/` internals
(`plan_repairs`, `probe_all`-class) and is neutral on self-contained functions. This arm is
the first test of the KB arc's predicted ordering that API references beat the book. Report it per
subject class (repo-internal vs self-contained) — pooling would hide the effect. **Subjects:** reuse the
`scripts/model_bench.py` functional subjects, `parity_batch.py` A/B/C, and
`git_model_bench.py` NL→git (baseline 97%). **Trials:** ≥ 30 per (arm × subject), seeds
recorded (SR-1). **Metrics:** functional pass rate → Beta posterior, 95% CI,
P(arm > off), paired conditionals; `k` and passage length as a reported sensitivity.
**Decision gate:** flip the `CASCADE_RAG_TARGET` default only if P(arm > off) ≥ 0.95 pooled
**and** no subject regresses (git ≥ 97%). Otherwise record NULL / REVERT and keep `off` —
the retrieve step still ships for the product path. Protocol: `/experiment` (LOCAL evidence branch,
`keep_awake`, segregated telemetry, findings leave via a clean PR citing the sha).
**Why I3 · S2:** the primary metric, measured; $0, evidence branch, no production change.

### KB-4 · repo-symbol corpus via `ast`  (I3 · S2) — after KB-1, before KB-3
**Why (2026-10-06):** the 14b knows Python but not this repo's APIs; tasks that call
`cascade/` internals are where it invents names. The KB arc's predicted corpus ordering ranks
API references above the book; this is the cheapest real instance.
**What:** `scripts/kb_ingest.py --corpus cascade-symbols cascade/` with a second source
adapter, `chunk_python_symbols(path)`: stdlib `ast` (no Tree-sitter — Python only) → one
chunk per public function/class: `module.qualname(signature) -> return` + the docstring's
first paragraph, ~50-150 tokens each, columns `corpus, module, symbol, lineno, text`. Same
`embed`/`upsert`/`search` as KB-1 — the adapter is the only new code. Idempotent re-ingest
keyed by `(module, symbol)` so it can refresh on each merge.
**Why S2:** additive adapter + corpus; KB-1 already proved the store.
**Acceptance:** `search("restart a dead celery worker", corpus="cascade-symbols")` returns
`cascade.health.plan_repairs` in the top 3; re-ingest is a no-op; CI green at 100%.

---

## MD arc — deprecate the edge MCP servers completely

**Why (evidence, 2026-09-19):** the per-tier MCP servers (`edge-npu`, `edge-gpu`,
`edge-verify`, `edge-cloud` in `mcp_servers/`) are the *retired* topology — CLAUDE.md
says the Canvas pipeline (`cascade.mesh.solve` via `mesh_solve_canvas.py`) is the single
inference path. But they are still live, and it costs us three ways:

1. **Contradictory agent policy.** `scripts/edge-cli.ps1:386-388` appends a system prompt
   that says *"MANDATORY: … FIRST call edge-npu.route, then … edge-gpu.generate …"* —
   the opposite of CLAUDE.md. Every `edge`-launched session starts with both instructions.
2. **VRAM contention.** `edge-gpu` (`python -m mcp_servers.gpu`) holds the llama_cpp
   14b resident: measured **11.3 / 12.2 GB used at idle** (≈ PT-1's +10.5 GB). A Celery
   GPU worker, SDXL, or an Ollama model on the same card then spills (see
   [FINDINGS-llm-vram-capability.md](FINDINGS-llm-vram-capability.md)).
3. **Dead surface area.** ~950 LOC in `mcp_servers/`, the `mcp` extra, smoke/contract
   tests, edge-cli catalog + status-probe code, and doc references across ~25 files.

**The catch:** `mcp_servers/` is *not* only the servers. Two modules are shared by the
live pipeline and must move first:

| Module | Used by (outside the servers) |
|---|---|
| `mcp_servers/_rec.py` (`make_recorder`, `recorded`, `EXPERIMENT_PREFIX`, `make_experiment_recorder`) | `cascade/tasks.py`, `replay.py`, `scripts/image_server.py`, `scripts/pr_review.py`, `scripts/git_model_bench.py`, `scripts/cli_model_bench.py`, `scripts/warn_prompt_validation_v2.py`, tests |
| `mcp_servers/_funcverify_child.py` (functional-gate sandbox) | `cascade/tasks.py` (`-m mcp_servers._funcverify_child`), `scripts/model_bench.py`, `tests/test_funcverify_child.py` |
| `mcp_servers/_npu_worker_proc.py` | **server-only** (`npu.py`, `smoke_npu_mcp.py`) — delete with the servers |

`cascade/credit_guard.py` was already lifted out (used by `pr_review.py`) — it stays.
The **Playwright** MCP (#147) is not a cascade tier and is **out of scope** — it stays.

### MD-1 · stop wiring the edge servers  (I3 · S1) — ⤴ SUBSUMED by EDGE-1 step 7
Kept for the record; ships as part of EDGE-1.
**What:** In `scripts/edge-cli.ps1`: default `$Servers` → `@()`; replace the appended
"MANDATORY … edge-npu.route" prompt with the pipeline-first policy (one call to
`mesh_solve_canvas.py --topology budget`, cap → Tier 3); keep `-Servers edge-npu,…` as an
explicit opt-in that prints a deprecation warning for one release. Update
`scripts/edge_summary.py` so an empty tier list is a clean READY, not an error.
Local-only follow-up for the user: drop the `mcp__edge-*` allows from the untracked
`.claude/settings.local.json`.
**Why I3:** removes a live contradiction in every agent session's instructions and frees
~10.5 GB VRAM for the real GPU tier. **Why S1:** launcher-only change, no library code;
revert = one flag. **Acceptance:** `edge` launches with no `edge-*` servers; the appended
prompt names the Canvas pipeline only; `nvidia-smi` shows the 14b not resident at idle;
`edge -Check` green.

### MD-2 · relocate the shared modules into `cascade/`  (I2 · S2)
**What:** Move `_rec.py` → `cascade/recorder.py` and `_funcverify_child.py` →
`cascade/funcverify_child.py`; update every importer in the table above; leave thin
re-export shims in `mcp_servers/` for one slice so evidence branches and old scripts
keep running. **Why I2:** pure enabler for MD-3, no user-visible change. **Why S2:**
mechanical move, BUT `mcp_servers/*` is in `coverage.omit` today — landing these in
`cascade/` puts them under the **100% gate**, so any untested branch surfaces as a
failure (per [[coverage-holes-are-deficiencies]] that's a finding to fix, not to omit).
`test_funcverify_child.py` / `test_degen_recorder.py` already exercise most of it.
**Acceptance:** full suite green at 100%; `grep -r "mcp_servers\._rec\|_funcverify_child"`
hits only the shims.

### MD-3 · delete the servers  (I2 · S2) — depends on MD-2
**What:** Remove `mcp_servers/{npu,gpu,verify,cloud,_demo,_npu_worker_proc}.py`, the
shims, `scripts/smoke_npu_mcp.py`, `tests/test_edge_summary_contract.py`, the
`mcp_servers*` entries in `tests/test_smoke_imports.py`, the `mcp` extra in
`pyproject.toml` (+ its `coverage.omit` lines), the CI `--extra mcp`, and edge-cli's
server catalog / status-probe block. **Keep** the `mcp` *package* only if something else
still imports it (Playwright is npx, not Python — check before dropping).
**Why I2:** cleanup; the behaviour change already happened in MD-1. **Why S2:** broad
but deletion-only once MD-2 has cut the dependencies; guarded by CI + the live
`tests/test_canvas_live_behavior.py` probes. **Acceptance:** CI green; `edge` + a live
`budget` route work end-to-end; `mcp_servers/` gone.

### MD-4 · docs sweep  (I2 · S1)
**What:** CLAUDE.md, `.claude/skills/edge-cascade/SKILL.md`, README, RUNBOOK, and the
`pipeline_reminder.py` hook text: remove per-server instructions and mark
`ARCHITECTURE.md` historical. Fold in the known drift: three places still claim *"git/
shell output fails the Python gate → capped→tier3"*, which has been false since VR-2/VR-4
(git routes WIN). Leave `FINDINGS-*`, `PLAN-*` and `evidence/` untouched — they are
history. **Why I2 · S1:** accuracy of the instructions agents act on; text-only.

---

## EXP-MR arc — local model refresh (2026-Q3 open models)

**Why:** the production GPU code model, `qwen2.5-coder:14b`, dates from late 2024. A
2026-09-19 scan of open-weight releases in the prior 60 days found candidates that fit the
12 GB RTX 5070 Ti fully, plus MoE models with only ~3B active parameters that could run
with CPU offload (the box has **63.5 GB RAM**). Details and sources in the session
write-up; published numbers are sparse and mostly vendor/aggregator-reported, so **we
measure, we don't adopt on reputation.** Protocol: the `/experiment` skill (LOCAL
evidence branch, Bayesian MC, `keep_awake`, segregated telemetry, findings leave via a
clean PR citing the evidence sha).

### EXP-MR-1 · 12 GB-resident candidates vs the incumbent  (I3 · S2)
**Arms (all via the Ollama backend — llama-cpp-python 0.3.23 likely can't load the new
architectures; see PT-4):**

| Arm | Model | Q4 size | Notes |
|---|---|---|---|
| control | `qwen2.5-coder:14b` | ~9.0 GB | incumbent; dijkstra 3/3 in FINDINGS-llm-vram-capability |
| T1 | `ornith-1.5:9b` | 6.6 GB | 256K ctx; billed for agentic coding; **no published coding benchmarks** |
| T2 | `granite4.2:8b` | 5.3 GB | IBM, Apache-2.0, dense, FIM; no published coding benchmarks |

**Subjects:** reuse the existing harnesses rather than inventing tasks — (a) the
dijkstra-class functional subjects in `scripts/model_bench.py` (DSL-gated, killed
subprocess), (b) the three parity cases A/B/C from `scripts/parity_batch.py`, (c) NL→git
from `scripts/git_model_bench.py` (incumbent baseline **97%** per
FINDINGS-git-model-selection). Optional (d) one TS subject gated by `ts_verifier`.
**Trials:** ≥ 30 per (arm × subject); record seed + sampling params (SR-1).
**Metrics, in priority order** ([[metric-priorities-quality-cost-over-latency]]):
functional pass rate → Beta posterior, 95% CI, **P(treatment > control)**, paired
per-subject conditionals; cost is $0 for all arms; latency + peak VRAM (at 4k / 16k ctx)
as tiebreakers only.
**Decision gate:** SHIP a candidate as the production GPU model only if P(T > control)
≥ 0.95 on the pooled functional subjects **and** no subject regresses (git ≥ 97%, parity
A/B/C tiers unchanged). The model flip is a separate follow-up PR (`cascade/config.py`
default + FINDINGS doc). Otherwise record REVERT/NULL and keep the 14b.
**Preconditions:** MD-1 landed (or the edge-gpu server stopped) so the card isn't at
11.3 GB idle; substrate health check passed; `ollama pull` both candidates; **OL-1 landed**
— the Ollama path sends no `num_ctx`, so without it each arm runs at whatever context
Ollama picks for that model, and that difference confounds the comparison (added 2026-10-06).
**Hold sampling constant across arms:** one temperature / top_p for all arms, recorded
per SR-1; if EXP-SP-1 has reported, use its winning temperature, else the current 0.8 and
say so in the methodology.
**Why I3:** the model choice drives every GPU route's quality — the primary metric.
**Why S2:** measurement-only on a LOCAL evidence branch, $0, no production change until
the separate flip PR. Known unknowns: chat-template / thinking-mode quirks in new models
(Ollama templates handle most; note any manual `/no_think` style switches in the
methodology), and the harnesses importing `mcp_servers._rec` (fine before MD-2; update
paths if MD-2 lands first).

### EXP-MR-2 · MoE partial-offload arm  (I3 · S3) — after EXP-MR-1
**Arm:** `laguna-xs-2.1` (Poolside; 33B total / **3B active**; 20 GB Q4; OpenMDW-1.1;
**SWE-bench Verified 70.9%**, per its Ollama page). Optional second: `nemotron-3.5-lightning`
(NVIDIA; 30B / 3B active; 25 GB Q4). Neither fits 12 GB — run with Ollama's automatic
GPU/CPU split, same subjects + gate as EXP-MR-1, and add wall time per task as a
reported (not decisive) column.
**Why I3:** a quality ceiling far above the 14b class if the offload is tolerable.
**Why S3:** real unknowns — offload throughput on this laptop, whether the new
architectures run correctly in Ollama 0.34.2 on Windows, and the VRAM-cliff behaviour
we measured for dense models may or may not transfer to 3B-active MoE. Stays a
measurement; a production flip would additionally need the GPU backend decision (Ollama
vs llama_cpp → PT-4).

### Follow-up (not yet scored): NPU tier refresh
`granite4.2:3b` (2.2 GB) as a replacement for the 1.5B NPU router/drafter needs an
OpenVINO conversion + NPU-compile spike first, and OpenVINO is 2026.1 vs upstream
2026.4. Score it after that spike tells us whether it compiles for the NPU at all.

---

## ✅ SR-1 · deterministic replay — seed, sampling params, model identity  (I2 · S2) — SHIPPED (#144)

**What:** Make all stochastic generation inputs explicit and logged in the
per-tier `.rec` records so a session can be audited or re-run identically.
Four categories of change per generation path:

1. **Per-call seed** — generate a random `seed` per call, pass it to the model,
   include it in the result dict → captured automatically by the existing
   `@recorded` decorator.
2. **Explicit sampling params** — make temperature and top_p explicit in
   `cascade/config.py` (`CASCADE_GPU_TEMPERATURE`, `CASCADE_GPU_TOP_P`,
   `CASCADE_NPU_TEMPERATURE`) and pass them through each worker instead of
   inheriting backend defaults.
3. **Model identity** — capture once at worker construction, bind into each
   result dict. "Name alone" doesn't pin the weights:

   | Tier | Identity fields to add |
   |------|------------------------|
   | NPU | `model_dir` (CONFIG path), `openvino_genai` version via `importlib.metadata` |
   | GPU Ollama | model **digest** from `/api/tags` response (already fetched by `_available()`), Ollama server version from `/api/version` |
   | GPU llama_cpp | GGUF blob **SHA** (already encoded in the blob path as `sha256-<digest>`), `llama_cpp` version via `importlib.metadata` |

4. **Surface in records** — all of the above appear in the `result` JSON blob
   of every `route`, `draft`, and `generate` record in `edge-npu.rec` /
   `edge-gpu.rec`.

**Design note:** identity fields are cheap to capture at worker init (not
per-call) and then bound into the worker closure, so every result carries
them with zero extra I/O on the hot path.

Files: `cascade/config.py`, `cascade/npu_worker.py`, `cascade/gpu_worker.py`,
`cascade/llama_worker.py`, `cascade/tasks.py`. No changes to `logfmt.py`,
`_rec.py`, or `replay.py` — the grammar and recorder are already correct;
the new fields fall through automatically. Full design in
`C:\Users\danth\.claude\plans\robust-juggling-graham.md`.

**Backend seed support:** OpenVINO GenAI `GenerationConfig.rng_seed`; Ollama
`options.seed`; llama-cpp-python `create_chat_completion(seed=...)`. Cloud
(Anthropic API) excluded — no reproducible seed API.

**Determinism caveat:** seeding makes replay *high-fidelity*, not guaranteed
bit-identical. CUDA non-determinism (cuBLAS, TF32 rounding), driver/version
differences, and NUMA-order variation mean exact bit-reproducibility is not
promised. The goal is the same weights + same seed ≈ same output, good enough
for debugging and experiment auditing.

**Why I2:** observability improvement; doesn't fix a failure mode or unblock
anything. Useful for debugging, experiment reproducibility, and post-hoc audit.
**Why S2:** purely additive — new fields in result dicts, explicit params in
model calls, identity fields bound at worker init. No structural hot-path
changes; config additions follow the established env-var override pattern.

---

## ✅ Verifier Registry arc (VR-1–VR-5)

Design doc: [DESIGN-verifier-registry.md](DESIGN-verifier-registry.md)

**Root cause:** 23/97 routed outcomes capped (24%) wasting 19 min of GPU wall time.
Log analysis (2026-06-01): NPU gate pass rate only 47%; git commands guaranteed LOSE
since the Python AST gate rejects them; `_gate` dispatch lives in `coverage.omit` so
regressions in language dispatch are invisible to the 100% gate.

### ✅ VR-1 · language registry `cascade/gate.py`  (I3 · S2)

**What:** New covered module `cascade/gate.py`: `LanguageVerifier` protocol,
`_REGISTRY`, `_LANG_MAP`, `register()`, `detect_language()`, `gate()`, `gate_any()`.
Self-registers Python and TypeScript at import. Comprehensive unit tests in
`tests/test_gate.py`. **No wiring yet** (topologies_canvas.py still calls its own
`_gate`); this slice is purely additive.

**Why I3:** Foundation for the entire arc; moves dispatch from coverage.omit into the
100% gate. **Why S2:** Additive new module; does not change any production call site.

### VR-2 · shell/git verifier  (I3 · S2)

**What:** `cascade/shell_verifier.py` — `verify_git()` (structural: fence extract +
`git <verb>` regex) and `verify_shell()` (`bash -n` stdin, fail-soft if bash absent).
Registered in `gate.py`. Tests in `tests/test_shell_verifier.py`.

**Why I3:** Directly fixes the git-always-caps problem. Every git route is currently a
guaranteed LOSE; after VR-2+VR-4 it becomes a WIN. **Why S2:** Structural regex + one
`bash -n` subprocess, same fail-soft pattern as `ts_verifier.py`.

### VR-4 · wire call sites  (I3 · S3)  — depends on VR-1+VR-2

**What:** Replace `_gate` body in `cascade/topologies_canvas.py` with a 1-line
delegation to `cascade.gate.gate()`. Replace `tasks.verify_syntax(text)` in
`cascade/wiring.py` with `cascade.gate.gate(text, dsl=None)`.

**Why I3:** Makes VR-1/VR-2/VR-3 real on production routes.
**Why S3:** Touches the hot-path Canvas gate step AND the in-process pipe gate.
Guard with parity check (`scripts/parity_batch.py --backend llama_cpp`, Case B ≤ 37.3s
±20%) and live git route test before merging.

### VR-3 · JS verifier  (I2 · S2)  — can fanout with VR-2

**What:** `cascade/js_verifier.py` — `verify_js()` via
`node --check --input-type=commonjs` stdin (no new npm deps; same node as ts_verifier).
Registered in `gate.py`. Tests in `tests/test_js_verifier.py`.

**Why I2:** New capability, no JS routes in current `cascade.rec`; lower immediate
impact than git. **Why S2:** Same subprocess pattern as ts_verifier, fail-soft on
node unavailable.

### ✅ VR-5 · repair prompt `language` field  (I2 · S2)  — SHIPPED (#143)

**What:** Surface the `language` key from the richer failure dicts in
`cascade/feedback.py:build_repair_prompt()`. The repair instruction can then say
"your **git** command doesn't start with `git <verb>`" rather than "your code has
a syntax error."

**Why I2:** Improves GPU repair-round success rate on non-Python artifacts.
**Why S2:** Additive read of an existing dict key; `CheckFailure` dataclass unchanged.

---

## Self-healing arc #7–#11 (from the 2026-05-30 routing-log analysis)

**Evidence base** — 24 routed outcomes this session (`runs/cascade.rec`,
session-scoped by a 55-min ts gap): **18W / 6L (75%)**; final_tier `npu 2 · gpu
16 · capped→tier3 6`; **11/24 (46%) skipped the NPU draft** (difficulty ≥ 0.7);
routing wall-time 13.1 min, of which the **6 caps consumed ~5.5 min (42%) for
zero usable output**. Language split: **TypeScript 0/3 won (0%)** vs
Python/algo **18/21 (86%)**.

### ✅ #7 · ts-verify-gate — a TS backend for the deterministic gate  (I3 · S2) — SHIPPED (PR #115)
**Done:** `cascade/ts_verifier.py` + `dashboard/scripts/ts-syntax-check.mjs`
(single-file `ts.transpileModule` syntax check, parity with the Python AST gate)
+ `_gate` language dispatch. Live-verified: a TS task that capped 100% before now
wins @ npu; Python parity intact. Original analysis retained below.

**The gap:** all **3 TS routes capped (100%)**. The locals *draft* TS fine; the
`edge-verify` gate is Python-only ([[edge-verify-ts-gap]]), so every TS draft
fails gating and burns both GPU repair rounds before `capped→tier3`. **The gate,
not the model, is the wall.** Wire `tsc + eslint + vitest` as a verify backend
keyed on `.ts`/language so TS drafts can actually PASS.
**Why I3:** turns a structurally-0%-win lane winnable AND reclaims ~100s/session
of guaranteed-cap GPU time; it's the linchpin of the whole arc. **Why S2:**
additive — a new verify backend behind the existing gate interface; the Python
gate path is untouched, fully reversible. Highest-leverage pick.

### #8 · difficulty-recal — the NPU router over-rates short prompts  (I3 · S3)
**The gap:** **11/24** routes scored ≥ 0.7 and skipped the NPU draft straight to
GPU — but several were trivial single functions (e.g. "Write a single Python
function `snapshot()`" scored **0.85**). Of those eleven 0.85s: 9 GPU wins, 2
caps — and a chunk of the GPU wins were cheap enough the NPU likely could have
taken them. Over-rating pushes work up a tier ($ + latency) needlessly. Length-
correct or recalibrate the `≥0.7 ⇒ skip-draft` threshold.
**Why I3:** affects every route's tier selection (cost/latency lever). **Why
S3:** touches the router's difficulty signal — mis-calibration risks drafting a
genuinely-hard task at NPU (a wasted round); guarded by the win/lose metric +
parity tests, measure before/after on `cascade.rec`.

### #9 · draft_gate-decompose — split the overloaded gate node  (I3 · S3)
**The gap:** `mesh.balanced._draft_gate` is ONE node doing **three** jobs behind
**two** verifiers: it (a) *verifies* via `_gate()` (syntax **or** functional
engine), (b) *resolves* on PASS (`final_tier="npu"`, `resolved`), and (c)
*escalate-routes* on FAIL (carry `failures` → GPU). The `verify` queue also runs
`_done`, so the dashboard's single node conflates gating with final logging.
Decompose into distinct chain nodes: **verify** (pure pass/fail) → **resolve**
(finalize npu win) | **escalate** (carry to GPU).
**Why I3:** the live ring (#6) gives per-node meaning only if a node is one
responsibility; this also decouples verification from routing policy so either
can change/reuse independently. **Why S3:** a refactor of the hot-path Canvas
chain — the cap invariant + the pipe-parity contract
([FINDINGS-canvas-phase1.md]) must hold across the split; eager-test the new
composition before it lands.

### ~~#10 · ts-shortcut~~ — RETIRED (superseded by #7)  (I2 · S2)
**Dropped:** #7 shipped, so the gate now wins TS instead of guaranteed-capping
it — the stopgap has no remaining purpose. Kept for the record only.

(original) — hand TS straight to Tier 3 until #7 lands:
**The gap (stopgap):** while the gate can't certify TS (#7), a TS task is a
*guaranteed* cap — ~35s of draft + 2 GPU rounds for nothing. Detect TS and route
it directly to `capped→tier3` (still logged), skipping the futile draft/repair.
**Why I2:** saves ~35s/TS route but is a workaround, and goes **dead the moment
#7 lands** — schedule behind #7, drop if #7 ships first. **Why S2:** a small
routing branch on the language signal, reversible.

### #11 · hook-scope — session-scope the advisory scoreboard  (I2 · S1)
**The gap:** `pipeline_reminder.py` reports **all-time** metrics ("37 routed,
20W/8L"), so a strong session (this one: 18W/6L, 75%) is diluted by history and
W+L ≠ total (9 older records lack a `done:` trace line). Show *this session's*
W/L alongside all-time (borrow the dashboard's `START_FROM_EOF` session-coupling).
**Why I2:** observability nicety, sharpens the nudge. **Why S1:** additive to an
advisory hook that already degrades to silence on any error — cannot break prompt
submission. Quick surgical win.

---

## ✅ #6 — live cascade-activity tool (OBS-1)  (I3 · S2) — **SHIPPED 2026-05-30**

**Done** (PRs #109–#113): `cascade/flower_activity.py` (Flower-backed probe) +
`cascade_top` debug view + `sample_occupancy` + the event-receiver push producer
(`cascade/live_receiver.py` / `scripts/cascade_live_receiver.py`) + the
dashboard spinning ring on its own `cascade-spin` live region (event-driven push,
no polling) + the `tool=status` probe filter + `docs/DESIGN-observability-lanes.md`.
The original design notes are retained below for history.

**The gap (found this session):** the dashboard can't show *which node is
currently spinning* because `.rec` records are written at task **completion**,
not start. While `gpu_solve` actually grinds (~60s on a hard task), zero records
exist, so the node only blips "hot" for a couple seconds *after* each generate
finishes — there is no in-progress signal in the `.rec` stream. Confirmed live:
a red-black-tree solve ran `gpu_solve` ~60s with the node dark the whole time.

**The data already exists natively — no Flower needed.** Celery's
`app.control.inspect().active()` returns the currently-executing task per worker
in real time (verified this session: `{'celery@Alienware': []}` idle; it lists
the running task during a solve). `task_track_started=True` is already set in
`cascade/celery_app.py`. A ~60-line module over `inspect.active()` is more
decoupled and reusable than wrapping Flower's HTTP API; Flower stays an optional
heavyweight UI, not a dependency.

**Design — a DECOUPLED tool, not a dashboard feature** (user directive: one
source of truth usable by the UI, debugging, AND experimentation):

`cascade/live_activity.py` — thin, read-only probe (no broker writes; safe to
call from anywhere):

```python
@dataclass(frozen=True)
class ActiveTask:
    task_name: str   # "mesh.balanced._gpu_solve"
    node: str        # "gpu_solve"  (mapped chain-node id)
    tier: str        # "gpu"
    task_id: str
    worker: str
    runtime_s: float

def snapshot(timeout=2.0) -> list[ActiveTask]   # running tasks, all workers
def active_nodes(snap) -> set[str]              # {"gpu_solve"}
NODE_BY_TASK: dict[str, str]                    # celery task name -> node id
```

Three thin consumers on top (each just imports or fetches the tool):
1. **Debugging** — `scripts/cascade_top.py`: a live `top`-style view of active
   cascade tasks (~2 Hz refresh).
2. **Experimentation** — experiments `import snapshot()` / `active_nodes()` to
   measure tier occupancy + timings.
3. **UI** — a tiny JSON endpoint `GET /active` → `snapshot()`; the Node
   dashboard polls it and `dashboard/src/flow.ts` renders a **spinning ring** on
   the active node (distinct from the post-completion "hot" blip added this
   session). Transport choice **A (HTTP/JSON)** so the same endpoint also serves
   curl-debugging + experiments.

**Build order:** module + debug CLI first (the decoupled core, immediately
useful for debug/experiments), then the JSON endpoint + the UI spinning ring.

**Route-first:** from-scratch build → through the pipeline (route → GPU draft
for the self-contained snapshot/mapping logic → Tier-3 for the Celery-integration
glue), per the route-every-coding-task rule.

**Acceptance:**
- `snapshot()` returns the running task during a live solve; its mapped node
  matches the chain step actually executing.
- `cascade_top.py` shows `gpu_solve` lit for the *whole* GPU phase, not a blip.
- Dashboard spinning ring tracks the active node live (`gpu_solve` spins the
  full ~60s of a hard solve).

**Why I3·S2:** Major — one reusable observability source unblocks the live
"spinning node" UI *plus* debugging *plus* experiment instrumentation
(multi-consumer payoff). Not I4 (the cascade runs fine without it). S2 —
additive, read-only `inspect.active()`, touches neither the hot path nor the
repair/self-heal loop, needs no worker config change (`-E` not required),
fully reversible.

---

## #1–#3 — llama_cpp performance-tuning sub-arc  (I3 · Major)

**Why I3:** this is the linchpin that unblocks **Slice 7** (flip the GPU backend
default `ollama → llama_cpp` and drop the Ollama daemon dependency) — the payoff
of the whole Phase-2 direct-loading arc (collapse the HTTP hop, per-model VRAM
control). Not `I4`: the cascade works today on Ollama and latency is a tiebreaker
metric, not a top one ([PRIORITIZATION.md]/metric-priorities). Not `I2`: it gates
a whole planned slice + the direct-loading thesis.

**The gap (Slice 2 / PR #95,
[FINDINGS-celery-phase2-parity.md](FINDINGS-celery-phase2-parity.md)):** functional
parity holds, but `llama_cpp` is **3.4× slower** than Ollama on GPU-heavy cases at
the current config (Case B: 110.4s vs 32.1s; Case C: 136.0s vs 40.5s). Same GGUF
blob is loaded on both backends, so the gap is **configuration**, not the model.

**Measurement harness (existing):** `scripts/parity_batch.py --backend
<ollama|llama_cpp>` writes `runs/parity-canvas-<backend>.json` with per-case wall
times. Re-run Case B/C after each tuning change; the bar is the Slice-2 criterion
(**within ±20% of Ollama's steady-state**). Needs the live GPU + CUDA
`llama-cpp-python` wheel — not a CI/eager change.

Current config in [cascade/llama_worker.py](../cascade/llama_worker.py)
(`make_llama_worker`): `n_gpu_layers=-1`, `n_ctx=8192`, `verbose=False`, no
`flash_attn`/`n_batch` tuning; `_generate` issues a fresh `create_chat_completion`
per call (re-prefills the system prompt every time).

Decomposed levers, in priority order:

### ✅ #1 · PT-1 — confirm full GPU offload  (I3 · S1) — **DONE 2026-05-31**
**Result: PASS.** VRAM delta +10,526 MB = 123% of 8,571 MB GGUF. All layers on GPU.
Breakdown: 8,571 MB weights + 1,955 MB KV cache + overhead at n_ctx=8192. GPU offload
is NOT the cause of the 3.4× gap. Also surfaced: `n_ctx_train=32768` vs our `n_ctx=8192`;
Ollama likely sizes context dynamically to the prompt (much smaller for typical queries),
so our fixed `n_ctx=8192` over-allocates the KV cache. **PT-2 is the next lever.**

Diagnostic tool: `scripts/pt1_gpu_offload_check.py` (install llama-cpp-python first;
see pyproject.toml `llama-cpp` extra setup notes and the `--no-build` flag workaround).

### ✅ #2 · PT-2 — context + attention config sweep  (I3 · S2) — **DONE 2026-05-31**
**Result: PASS — decision gate met.** 4-config sweep on RTX 5070 Ti Laptop, 2026-05-31:

| Config | Case B | Case C | vs Ollama B |
|--------|--------|--------|-------------|
| 8192, no flash | 42.1s | 23.3s | 31% slower |
| 4096, no flash | 40.9s | 25.7s | 27% slower |
| 4096, flash=True | 38.9s | 25.1s | 21% slower |
| **8192, flash=True** | **37.3s** | **25.6s** | **16% ✓** |

`n_ctx=8192` with `flash_attn=True` is within ±20% on Case B; Case C is faster than
Ollama (skip-repair caps without 2 full repair rounds). `flash_attn=True` is now the
production default in `make_llama_worker`. **Slice 7 unblocked.**

### #3 · PT-3 — KV-cache / system-prompt prefix reuse  (I2 · S3, downgraded from I3)
`_generate` re-prefills the `_SYSTEM` prompt on every call; Ollama's daemon keeps
the session/KV warm. Reuse the prefix KV across calls in the resident worker
(prompt caching). **Reclassified I3→I2·S3 on 2026-05-31**: was I3 because it
blocked Slice 7; the PT-2 gate passed without it (16% vs Ollama, within the bar)
and Slice 7 shipped. Now a pure perf optimization — useful, but gates nothing.
Higher severity: touches the call pattern and risks chat-template / correctness
drift if prefix caching is mishandled — guarded by the parity gate (`parity_batch.py`).

---

## #4 — extract `_pick_first_verified` gating into a covered helper  (I2 · S2)
Reviewer idea from PR #103. The low_latency chord's gating *decision*
(cheapest-first / unavailable-skip / double-miss-cap) is product logic living in the
coverage-omitted celery substrate (`cascade/topologies_canvas.py`). It's
behaviorally tested by the 7 eager cases but not under the 100% gate. Extract the
pure decision into a non-omitted helper so the gate covers it. Minor impact (logic
*is* tested), small safe refactor.

## #5 — llama-cpp-python version check  (I2 · S2)
On `0.3.23`; check upstream for perf changes since the cu124 wheel was cut and bump
if it helps. Minor/maintenance; a bump can shift the CUDA wheel + runtime-DLL setup
([FINDINGS-celery-phase2-parity.md] side-finding), so measure before keeping.

## #14 — verify_func refactor  (I2 · S2)

**The gap:** `verify_functional` / the `dsl=` parameter is a useful concept (run the
generated code against assertion test-cases, not just check syntax) but almost never
used in practice because the interface is awkward: callers must hand-craft a raw Python
assertion string, pass it as an opaque `dsl` kwarg, and there's no helper to build or
validate that string before it hits the sandboxed subprocess. As a result `verify_func`
is always 0 in the dashboard — the functional gate exists but is effectively dead code
for real dev tasks.

**Refactor candidates (any subset):**
- A `dsl_from_cases(fn_name, cases)` helper that builds the assertion string from
  structured `(args, expected)` pairs — makes it trivial to attach test cases to a
  `mesh.solve` call without writing raw Python.
- Expose `dsl=` more prominently in `mesh_solve_canvas.py --dsl` CLI flag (it exists
  but is buried; add an example to the help text and the CLAUDE.md routing rule).
- Rename / alias `verify_functional` → `verify_dsl` in `tasks.py` for discoverability
  (the current name implies "functional testing" in the abstract sense, not "run the DSL
  assertions").
- Structured failure output: currently `failures` is a list of raw dicts; a dataclass
  or typed dict would let the repair prompt builder surface better error context.

**Why I2:** the functional gate is genuinely useful for experiment harnesses and
hard-to-gate tasks (parser/interpreter subjects), but it's off the hot path for normal
dev routing — impact is bounded to experiment quality, not session throughput.
**Why S2:** the subprocess sandbox (`_funcverify_child`) is already the isolation
boundary; the refactor touches Python API surface only, not the sandbox itself.

---

## ✅ Slice 7 — SHIPPED 2026-05-31 (PR #127)

Default flipped `ollama → llama_cpp`. PT-2 gate passed (B=37.3s = 16% slower than
Ollama, within ±20% bar). `flash_attn=True` now the production default.
`llama_worker.make_llama_worker` returns an unavailable worker (not a crash) when
the GGUF can't be loaded — required for CI where Ollama isn't installed.
Ollama still works via `CASCADE_GPU_BACKEND=ollama`.

## User-owned (needs the hardware, not agent engineering work)

- **low_latency vs balanced wall-time comparison** — fill the TBD table in
  [FINDINGS-canvas-phase2-low-latency.md](FINDINGS-canvas-phase2-low-latency.md) by
  running `scripts/mesh_solve_canvas.py --topology {balanced,low_latency}` on the
  NPU + RTX + Redis box, then settle the Phase-0 decision gate for low_latency.
