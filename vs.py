"""Terminal side-by-side: NPU model vs NVIDIA/GPU model.

Type a prompt; it's sent to both processors concurrently and the two answers
are printed with timings so you can compare them directly.

  python vs.py
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from cascade.gpu_worker import make_gpu_worker
from cascade.npu_worker import NPUWorker

RULE = "=" * 72


def main() -> None:
    print("Loading (NPU compile ~20s)...")
    npu = NPUWorker()
    gpu = make_gpu_worker()
    gpu_ok = gpu.available()
    print(f"NPU: {npu.device} (qwen2.5-coder-1.5b) | "
          f"NVIDIA GPU: {'qwen2.5-coder:14b' if gpu_ok else 'unavailable'}")
    print("Enter a prompt (blank line or Ctrl-C to quit).")

    pool = ThreadPoolExecutor(max_workers=2)
    try:
        while True:
            q = input("\nprompt> ").strip()
            if not q:
                break
            fn = pool.submit(npu.draft, q, 512)
            fg = pool.submit(gpu.generate, q) if gpu_ok else None

            d = fn.result()
            print(f"\n{RULE}\n[NPU | {npu.device} | {d.latency_s:.2f}s]\n{RULE}")
            print(d.text.strip())

            print(f"\n{RULE}\n[NVIDIA GPU | ", end="")
            if fg is None:
                print("unavailable]")
            else:
                g = fg.result()
                print(f"{g.latency_s:.2f}s | {g.tokens_per_s:.0f} tok/s]\n{RULE}")
                print(g.text.strip())
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        pool.shutdown(wait=False)


if __name__ == "__main__":
    main()
