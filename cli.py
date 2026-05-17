"""Edge cascade CLI.

Usage:
  python cli.py "write a python function to ..."      # one-shot, local-only
  python cli.py --cloud "design a distributed ..."    # allow the PAID cloud tier
  python cli.py                                        # interactive REPL

The cloud (Tier-3) tier is PAID and OFF by default. Enable it explicitly with
--cloud (or CASCADE_ENABLE_CLOUD=1). Without it the cascade returns the best
local answer instead of escalating off-box.

Startup compiles the Tier-1 model (NPU probe -> iGPU fallback), so the first
prompt is slow; subsequent prompts reuse the loaded pipeline.
"""
from __future__ import annotations

import argparse

from cascade.orchestrator import Orchestrator, processor


def main() -> None:
    ap = argparse.ArgumentParser(description="3-tier edge inference cascade")
    ap.add_argument(
        "--cloud", action="store_true",
        help="enable the PAID Anthropic cloud tier (off by default)",
    )
    ap.add_argument("query", nargs="*", help="prompt; omit for interactive mode")
    args = ap.parse_args()

    print("Loading cascade (Tier-1 compile + NPU probe)...")
    orch = Orchestrator(enable_cloud=args.cloud)
    print(f"Ready. Tier-1: {processor(orch.npu.device)} | "
          f"Tier-2: NVIDIA RTX 5070 Ti | "
          f"Tier-3: {orch.cloud.status()}")
    print(f"Log: {orch.log_path}")
    print(f"  tail -f \"{orch.log_path}\"                        (Git Bash)")
    print(f"  Get-Content -Wait -Tail 20 \"{orch.log_path}\"     (PowerShell)")

    if args.query:
        orch.run(" ".join(args.query))  # output is teed to console + log
        return

    print("Interactive mode — empty line or Ctrl-C to exit.")
    try:
        while True:
            q = input("\n> ").strip()
            if not q:
                break
            orch.run(q)  # output is teed to console + log
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == "__main__":
    main()
