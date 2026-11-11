# Bounded RTL fixture self-test report

The two intentionally defective fixtures were detected, and all four repaired
regression scenarios passed. A no-backpressure control also passed with the
pipeline defect enabled, demonstrating that the failing stimulus activates the
backpressure failure mechanism. These results validate the bounded fixtures;
they are not product acceptance or exhaustive protocol verification.

Executed 2026-11-11 UTC (2026-11-10 America/Los_Angeles), using VCS compiler and
runtime `O-2018.09-SP2_Full64`, SystemVerilog, `-full64`, `-timescale=1ns/1ps`, top
`tb`, and a 10 ns testbench clock. Stimuli are deterministic; no random seed is
used. Only the seven listed fixture inputs were staged, with SHA-256 verified
before each execution. Compile, simulation, command and status records were
captured separately. All seven final compilations returned 0 with no `Error-`
or `Warning-` messages. Raw logs, tool paths and remote session details remain
in ignored local run records; they are not included in the portable skill.

## Final-source regression

| Fixture | BUG | Case | Observed result | Checks | Simulated cycles | Runner exit |
| --- | ---: | --- | --- | ---: | ---: | ---: |
| register_control | 1 | directed | Expected FAIL: `CP-RC-10-event-clear-priority/read` | 35 | 12 | 1 |
| stream_pipeline | 1 | backpressure | Expected FAIL: `CP-SP-stall-data-hold` | 11 | 3 | 1 |
| stream_pipeline | 1 | nobackpressure | PASS, defect not activated | 205 | 35 | 0 |
| register_control | 0 | directed | PASS | 83 | 27 | 0 |
| stream_pipeline | 0 | nobackpressure | PASS | 205 | 35 | 0 |
| stream_pipeline | 0 | backpressure | PASS | 369 | 74 | 0 |
| stream_pipeline | 0 | reset | PASS | 164 | 30 | 0 |

Initial deliberately broken specimens were tested before adding the fixes and
failed at these same two checkpoints. Final-source `BUG=1` runs reproduced both
failures after repair; the default `BUG=0` selects the corrected branches.

This VCS runtime returned raw process status 0 even after `$fatal(1, ...)`.
The runner rejected both runs by the FAIL/Fatal signature and missing valid PASS
summary, returning 1. A zero raw simulation exit is therefore insufficient.
Every accepted run contains exactly one verified PASS summary, nonzero checks,
zero failures, and no mismatch/fatal/timeout signature. The runner preserves
nonzero compile/simulation status when the tool provides one.

## What the fixtures checked

The [controller specification](../fixtures/register_control/SPEC.md) defines
pre-edge configuration semantics, real enable/threshold effects, W1C, event-over-
clear priority, masked capture, unmasking retained status, reset and reserved
register behavior. Literal expected values are independent of the implementation.
The ordinary event and clear controls pass before the deliberate same-edge
priority defect fails.

The [pipeline specification](../fixtures/stream_pipeline/SPEC.md) defines an
8-bit transform, ordered conservation, one-entry ready/valid behavior, reset
flush and handshake-to-handshake latency. A transaction queue uses accepted
inputs and independent integer arithmetic; it never reads DUT internals. Data
checks use both the queue and output hold observations. A read-only oracle
review identified an idle-input stall gap; two such hold cycles were included
before the final-source regression. Drivers change inputs on falling edges;
transfer samples precede rising-edge NBA updates and hold/reset observations
follow them by 1 ns.

## Measured performance and reset

Only delivered transactions contribute to latency. Throughput uses the inclusive
edge span from the first to last output transfer, not the entire test including
reset and drain guard cycles.

| Repaired pipeline case | Accepted / delivered / reset-discarded | Output transfers / output-span cycles | Latency min–max (cycles) | Stall / hold / idle-input hold checks |
| --- | --- | --- | --- | --- |
| nobackpressure | 32 / 32 / 0 | 32 / 32 = 1.000 | 1–1 | 0 / 0 / 0 |
| backpressure | 40 / 40 / 0 | 40 / 69 ≈ 0.580 | 1–4 | 31 / 31 / 2 |
| reset | 25 / 24 / 1 | 24 / 24 = 1.000 after reset | 1–1 | 0 / 0 / 0 |

The no-backpressure case used continuous valid/ready. The backpressure case used
three ready-low cycles per seven-cycle stimulus period, then two idle-input
stall cycles before draining. The reset case discarded the one outstanding
pre-reset transaction and verified 24 new transactions. These are simulated
load-specific measurements. No synthesis, physical frequency, timing, area,
power, asynchronous reset or clock-domain crossing claim is made.

## Reproduction

Run on a VCS host from the repository root. Each output path must be new; the
script neither synchronizes sources nor deletes old outputs. Its command record
captures exact compiler arguments and simulation plusargs.

```bash
bash tests/fixtures/run_vcs.sh register_control 1 work/register-bug directed
bash tests/fixtures/run_vcs.sh stream_pipeline 1 work/stream-bug backpressure
bash tests/fixtures/run_vcs.sh stream_pipeline 1 work/stream-control nobackpressure
bash tests/fixtures/run_vcs.sh register_control 0 work/register-fixed directed
bash tests/fixtures/run_vcs.sh stream_pipeline 0 work/stream-full nobackpressure
bash tests/fixtures/run_vcs.sh stream_pipeline 0 work/stream-stalls backpressure
bash tests/fixtures/run_vcs.sh stream_pipeline 0 work/stream-reset reset
```

The first two commands must return 1 with their named failing checkpoints;
the remaining five must return 0 with the summaries above. A wrong case, invalid
BUG value or existing output directory is rejected before compiler execution.
Local validation also passed `bash -n` and these three rejected-input checks.

## Final source identity

Source manifest SHA-256:
`945cc0f5dc74e0510fdb4b634264b8390014e882792f4f4a13e90543ccb0a2a0`.
The manifest contains these seven lines, in the order shown, as lowercase hash,
two spaces, repository-relative path and newline. Retrieved evidence hashes
were checked for all 39 manifests/log/command/status/campaign files. Accepted
logs belong to fresh output directories and the final staged manifest.

| Input | SHA-256 |
| --- | --- |
| `tests/fixtures/register_control/SPEC.md` | `1d24973f887d988f7ee0cef6ab29e50f190855c22489e8e4e114f477779e4c62` |
| `tests/fixtures/register_control/rtl.sv` | `8fdc3013ee85db1baa50cf1cc99e2ca90d3836097ef43d9bc34a031422d5dd5a` |
| `tests/fixtures/register_control/tb.sv` | `ccec09580177c586b3ec1c5882da152206e6f5aa2aa5e08a2b65e4eb292ec46d` |
| `tests/fixtures/stream_pipeline/SPEC.md` | `99b75be6ca22682bb1ed7b8ed1941806e9929e86f81bb90e6b8b6e3ada7d3024` |
| `tests/fixtures/stream_pipeline/rtl.sv` | `ef3af8de773042c93454be312dec0051d95bb9e3bf4657a5295c445b1b52369a` |
| `tests/fixtures/stream_pipeline/tb.sv` | `22b730c3e6979e1e2ad0f4aa2634ea93d82d31e4826ad73c7bb01b3bc3d62202` |
| `tests/fixtures/run_vcs.sh` | `12ffbecd9d2c33ce9ca80ef9c1df1fae0e3066340faf62b106f31f3a6e60c441` |

The deterministic scenarios exercise the stated boundaries and the two known
failure mechanisms. They do not establish exhaustive input coverage, formal
proof, random-stress robustness or correctness of an external product. Source
or configuration changes require relevant re-execution before reusing results.
