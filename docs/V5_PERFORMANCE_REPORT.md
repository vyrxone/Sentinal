# V5 Performance Report

**These are development-VM numbers (Linux, 2 logical CPUs, Qt offscreen/software rendering, Python 3.13). No Celeron J3355
measurement exists. Do not read them as target-PC results.** Reproduce with `python scripts/benchmark_v5.py` (and
`scripts/benchmark_ai.py`); run both on the target PC to get real numbers.

| Measurement | Result (dev VM) |
|---|---|
| Startup: controller + main window shown | 665 ms (V4's 77→90 ms figure measured construction only, not comparable) |
| Idle RSS after startup | 96 MiB |
| Idle CPU over 5 s, AI off | 1.4 % of one core-equivalent (offscreen, no real wallpaper painting) |
| Page switch (27 switches) | median 9 ms, worst 30 ms |
| Threads after 15 panel open/close cycles | 1 → 1 |
| RSS after 15 open/close cycles | 98 → 277 MiB (closed windows are not freed — see V5_KNOWN_LIMITATIONS.md; the shipped app opens one panel) |
| AI request overhead (in-memory provider: queue + worker + router + validation) | median 29 ms, p95 45 ms (includes the benchmark's 1 ms UI polling) |
| After 200 AI requests | RSS 277 → 279 MiB, threads 1 → 1, job queue 0 |
| AI disabled | no threads, no queue, no requests (tested: `test_ai_disabled_is_completely_idle…`) |
| Wallpaper FPS | not measured in V5 (no rendering change); V2 measured 6–15 ms/frame animated region at 1080p on this VM |

Low-end controls: *Lightweight mode* (Settings → Animation) caps 15 fps, lowest effects, ≤ 6 particles, no pulses, reduces
under load and samples at most every 4 s, without altering your saved settings. Reduced motion, Silent Guardian preset and the
existing load reduction remain. AI work always runs on one background worker with a queue of at most 2; nothing runs from
animation timers or per event; closing/shutdown cancels the request and frees the worker (tested).
Not measured: AI real-network latency, TLS handshake cost on Windows, memory on 4 GB under sustained load.
