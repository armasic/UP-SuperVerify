# Register controller fixture, specification v1

This small, synchronous controller is a verification self-test, not a product IP.
All inputs are sampled on a rising clock edge. `rst_n=0` synchronously clears
configuration, status, interrupt mask and `action_pulse`; reset wins over writes
and events. Read data is combinational and is independent of the write address.

| Address | Read / write behavior |
| --- | --- |
| 0 | RW configuration: bit 0 enables event processing; bits 7:1 are the unsigned threshold (0–127). Reset 0. |
| 1 | Bit 0 is sticky event status. Writing 1 clears it; writing 0 preserves it. Other bits read zero and have no effect. |
| 2 | Bit 0 is RW IRQ mask, reset 0. Other bits read zero and are ignored. |
| 3 | Reserved: reads zero, writes have no effect. |

An event qualifies when `event_valid`, the **pre-edge** enable bit, and
`event_value >= pre-edge threshold` are all true. Each qualifying event produces
`action_pulse=1` for the following cycle and sets status. A configuration write
on the same edge affects the next event only. Otherwise `action_pulse=0`.
Status remains set until W1C or reset. A qualifying event and W1C on the same
edge **set** status: event wins. `irq = status && !mask`. Mask affects only IRQ;
it never cancels capture or the action pulse. Threshold changes and disable
must affect real event processing, not merely register readback.

The default (`BUG=0`) implements this contract. `BUG=1` deliberately makes W1C
win over a simultaneous event, losing the new event. The same oracle must fail
at `CP-RC-10-event-clear-priority`, after ordinary capture and W1C controls pass.

The test drives inputs on falling edges, observes registered outputs 1 ns after
the next rising edge, and compares against literal expectations derived above.
Its checkpoints cover reset (including reset with simultaneous activity), RW
readback and functional enable/threshold effects, exact threshold equality,
W1C-zero preservation, W1C clearing, event/clear priority, masked capture,
unmasking retained status, same-edge configuration semantics and reserved bits.

Run `bash tests/fixtures/run_vcs.sh register_control 0 OUTPUT_DIR directed` from
the repository root on a VCS host. `OUTPUT_DIR` must not exist. Use `1` for the
known defect. The fixed summary reports nonzero checks or the test fails.
This fixture has no product throughput or physical-frequency acceptance claim.
