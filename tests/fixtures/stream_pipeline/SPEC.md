# Ready/valid pipeline fixture, specification v1

This bounded self-test is an unsigned 8-bit, one-entry elastic transform stage.
An input transfer occurs on a rising edge with `in_valid && in_ready`; an output
transfer occurs with `out_valid && out_ready`. Each accepted input `x` produces
exactly one output `(3*x+7) mod 256`, in acceptance order, unless reset discards
that pending transaction. The source holds its input valid/data until accepted.

- Synchronous active-low reset clears output valid and the pending transaction.
  Reset wins over all transfers; input ready is low while reset is asserted.
- Outside reset, input ready is true exactly when the entry is empty or the
  receiver is ready. Simultaneous output consumption/input acceptance is legal.
- Output valid and data remain unchanged across an edge whose pre-edge output
  is valid and receiver is not ready. This also holds when a new input is offered.
- With continuous valid and ready, the stage accepts and emits one item per
  cycle after filling. An input accepted at edge `k` is transferred out at edge
  `k+1`: **one-cycle handshake-to-handshake latency**, not zero-cycle fallthrough.
  Backpressure may increase latency, but may never cause loss or reordering.

The test uses a queue of expected transactions built only from accepted inputs,
integer arithmetic (RTL uses shift/add), and acceptance-edge timestamps. It
checks occupancy/ready/valid, data/order, each stalled output's stability,
minimum latency and reset flushing. No DUT internals or DUT functions are read.
Inputs change at falling edges; transfers are sampled before rising-edge NBA
updates; registered hold/reset checks run 1 ns later. A 10 ns simulation clock
sets the test time scale only; no physical timing analysis is performed.

| Case | Stimulus and acceptance |
| --- | --- |
| `nobackpressure` | 32 consecutive inputs with ready high; 32 ordered outputs, all latency 1 cycle, 32 outputs in a 32-cycle output span. |
| `backpressure` | 40 inputs, data held until accepted; deterministic ready-low bursts interspersed with ready-high cycles, followed by two stalled cycles with input idle; positive stall/hold/replacement counts; all 40 outputs drained in order. |
| `reset` | Queue one item while ready low, reset with activity, confirm exactly one pending item discarded, then send/drain 24 fresh items with ready high; no old output may escape. |

`BUG=0` is the default repaired implementation. `BUG=1` updates the output slot
while its old output is stalled, allowing a merely offered input to overwrite
an accepted one. The backpressure test must fail the independent output-hold
checkpoint. The no-backpressure case is a control where this bug is not activated.

Summary throughput is observed output transfers divided by the inclusive edge
span from first to last output transfer; the backpressure span includes stalls.
Latency is output-transfer edge minus input-acceptance edge, over delivered
transactions. Reset-discarded items are counted separately. These are bounded
simulation measurements under the case's specified load, not universal targets
or a claim that a synthesized circuit meets 100 MHz.

Run `bash tests/fixtures/run_vcs.sh stream_pipeline 0 OUTPUT_DIR CASE` from the
repository root on a VCS host. `OUTPUT_DIR` must not exist. `CASE` is one of the
three rows above; unknown names fail. Run `1 ... backpressure` for defect detection.
