# Pass@k concurrency audit, 2026-10-14

Live queue inspection around 12:13 to 12:20 Europe/Berlin. Counts are remaining
Slurm array elements, not dataset tasks or attempts.

| Cluster / workload | Array | Running | Pending | Requested trials per job | CPUs per job | Live array cap |
| --- | --- | --- | --- | --- | --- | --- |
| Helma Set A pass@16 Instruct | 1012398 | 0 | 22 | 14 | 32, including 4 serving | 24 |
| Helma Set A pass@16 Thinking | 1012399 | 0 | 48 | 14 | 32, including 4 serving | 24 |

Both arrays request one H200 per job and are pending for Priority. All 70
pending requests were checked and request concurrency 14. The serving CPU
reservation leaves 28 CPUs for trials. Two existing resources.json records
beside pending Thinking requests report effective concurrency 14, CPU ceiling
28, and one reserved CPU per trial. These are earlier execution records, not
proof that pending reruns have started. Other pending requests had no such
record; their effective concurrency will be calculated from their task payloads
at startup. No live GPU utilization measurement was possible on these jobs.
The live throttle is 24 per array, although the original submit lines say 12.

Evidence: `squeue -u y500bb12 -h -r -o '%i|%T|%j'`, `scontrol show job
1012398_235`, `scontrol show job 1012399_69`, and all pending requests indexed
by the two `batches-*.json` files under
`/home/hpc/y500bb/y500bb12/pass16-recovery-20261012/resubmission-20261014/`.
The frozen `code/hpc/validation_worker.py` computes and records the CPU cap.
CPU-only validation arrays and Coral/CodeEvolve smoke jobs are excluded.

CLAIX and LRZ returned empty user queues. The CLAIX SWE-Lego pass@8 full plan
was previously prepared with concurrency five, 24 CPUs, 120 GiB and array cap
32, but no active submission was observed. Its controller status file still
says waiting_for_pilot with a 2026-10-12 timestamp; that stale record does not
establish current controller health or pilot success.

Capella and Alpha were unreachable because the Mac VPN reverse tunnel at
127.0.0.1:22231 refused connections. Current run counts and effective
concurrency there remain unverified. The last documented Scale SWE pass@8
arrays were 4407591 (Instruct) and 4407592 (Thinking), each originally 1,400
shards, concurrency one, 12 CPUs and one H100 per shard. Last recorded array
caps were 64 and 220. A remote controller was prepared to replace pending
shards with concurrency-three jobs (14 CPUs, two serving CPUs) after throughput
pilots passed. Do not assume either that the transition succeeded or that the
old configuration remains current. Reconnect and inspect acceleration-controller
status, coverage records, live queues and resources.json before changing jobs.
See [cluster access](cluster-access.md) for exact run paths and tunnel setup.

Recommendation: Helma already requests parallel trials; its current delay is
queueing, not observed single-trial execution. For Scale SWE, verify whether
the existing concurrency-three transition succeeded before launching more work.
Use CPU-budgeted concurrency and measured throughput for future submissions.
