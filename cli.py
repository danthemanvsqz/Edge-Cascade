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

from cascade import topologies
from cascade.orchestrator import cascade_session, processor


def main() -> None:
    ap = argparse.ArgumentParser(description="3-tier edge inference cascade")
    ap.add_argument(
        "--cloud", action="store_true",
        help="enable the PAID Anthropic cloud tier (off by default)",
    )
    ap.add_argument(
        "--topology", default=topologies.DEFAULT_TOPOLOGY,
        choices=sorted(topologies.TOPOLOGIES),
        help=f"mesh topology (default: {topologies.DEFAULT_TOPOLOGY})",
    )
    ap.add_argument("query", nargs="*", help="prompt; omit for interactive mode")
    args = ap.parse_args()

    print("Loading cascade (Tier-1 compile + NPU probe)...")
    with cascade_session(enable_cloud=args.cloud) as cs:
        print(f"Ready. Tier-1: {processor(cs.tier1_device)} | "
              f"Tier-2: NVIDIA RTX 5070 Ti | "
              f"Tier-3: {cs.cloud_status}")
        print(f"Log: {cs.log_path}")
        print(f"  tail -f \"{cs.log_path}\"                        (Git Bash)")
        print(f"  Get-Content -Wait -Tail 20 \"{cs.log_path}\"     (PowerShell)")

        if args.query:
            cs.run(" ".join(args.query), args.topology)  # teed to console + log
            return

        print("Interactive mode — empty line or Ctrl-C to exit.")
        try:
            while True:
                q = input("\n> ").strip()
                if not q:
                    break
                cs.run(q, args.topology)  # output is teed to console + log
        except (KeyboardInterrupt, EOFError):
            print()


if __name__ == "__main__":
    main()
