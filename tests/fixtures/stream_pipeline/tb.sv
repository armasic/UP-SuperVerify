`timescale 1ns/1ps
`ifndef FIXTURE_BUG
`define FIXTURE_BUG 0
`endif
module tb;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst_n = 0, in_valid = 0, out_ready = 0;
    reg [7:0] in_data = 0;
    wire in_ready, out_valid;
    wire [7:0] out_data;
    stream_pipeline #(.BUG(`FIXTURE_BUG)) dut (.*);

    // This transaction oracle has no access to DUT internals or transform code.
    integer expected_data [0:511];
    integer accepted_at [0:511];
    integer head = 0, tail = 0, pending = 0;
    integer checks = 0, cycles = 0, accepted = 0, received = 0;
    integer stalls = 0, holds = 0, idle_holds = 0, replacements = 0, reset_drops = 0;
    integer latency_min = 999999, latency_max = 0;
    integer first_output = 0, last_output = 0;
    bit last_accepted = 0;
    string case_name;

    task automatic require(input bit condition, input string checkpoint);
        checks = checks + 1;
        if (!condition) begin
            $display("CHECK_FAIL checkpoint=%s cycle=%0d", checkpoint, cycles);
            $display("FIXTURE_SUMMARY fixture=stream_pipeline bug=%0d case=%s result=FAIL checks=%0d failures=1 cycles=%0d accepted=%0d received=%0d", `FIXTURE_BUG, case_name, checks, cycles, accepted, received);
            $fatal(1, "independent specification check failed");
        end
    endtask

    task automatic tick(input bit reset_n, valid_in,
                        input reg [7:0] data_in, input bit ready_out);
        bit take_in, take_out, was_stalled;
        reg [7:0] held_data;
        integer latency;
        @(negedge clk);
        rst_n = reset_n; in_valid = valid_in;
        in_data = data_in; out_ready = ready_out;
        @(posedge clk);
        cycles = cycles + 1;
        last_accepted = 0;
        was_stalled = 0;
        // Pre-NBA sample is exactly the transfer edge specified in SPEC.md.
        if (!rst_n) begin
            require(in_ready === 1'b0, "CP-SP-reset-ready");
            reset_drops = reset_drops + pending;
            head = 0; tail = 0; pending = 0;
        end else begin
            require(in_ready === ((pending == 0) || out_ready), "CP-SP-input-ready");
            require(out_valid === (pending != 0), "CP-SP-output-occupancy");
            if (pending != 0)
                require(out_data === expected_data[head][7:0], "CP-SP-data-order");
            take_in = in_valid && in_ready;
            take_out = out_valid && out_ready;
            was_stalled = out_valid && !out_ready;
            held_data = out_data;
            if (was_stalled) stalls = stalls + 1;
            if (take_in && take_out) replacements = replacements + 1;
            if (take_out) begin
                latency = cycles - accepted_at[head];
                require(latency >= 1, "CP-SP-no-fallthrough");
                if (case_name != "backpressure")
                    require(latency == 1, "CP-SP-one-cycle-latency");
                if (latency < latency_min) latency_min = latency;
                if (latency > latency_max) latency_max = latency;
                head = head + 1; pending = pending - 1;
                received = received + 1;
                if (first_output == 0) first_output = cycles;
                last_output = cycles;
            end
            if (take_in) begin
                require(tail < 512, "CP-SP-oracle-capacity");
                expected_data[tail] = (3 * int'(in_data) + 7) % 256;
                accepted_at[tail] = cycles;
                tail = tail + 1; pending = pending + 1;
                accepted = accepted + 1; last_accepted = 1;
            end
        end
        #1;
        if (!rst_n) begin
            require(out_valid === 1'b0, "CP-SP-reset-flush");
        end else if (was_stalled) begin
            holds = holds + 1;
            if (!in_valid) idle_holds = idle_holds + 1;
            require(out_valid === 1'b1, "CP-SP-stall-valid-hold");
            require(out_data === held_data, "CP-SP-stall-data-hold");
        end
    endtask

    integer item, phase;
    initial begin
        if (!$value$plusargs("CASE=%s", case_name)) case_name = "nobackpressure";
        require(case_name == "nobackpressure" || case_name == "backpressure" || case_name == "reset", "CP-SP-case-known");
        tick(0, 0, 0, 0);
        if (case_name == "nobackpressure") begin
            for (item = 0; item < 32; item = item + 1)
                tick(1, 1, (item * 17 + 3) % 256, 1);
            tick(1, 0, 0, 1);
            tick(1, 0, 0, 1);
            require(accepted == 32 && received == 32, "CP-SP-full-load-counts");
            require(last_output - first_output + 1 == 32, "CP-SP-one-output-per-cycle");
            require(replacements == 31, "CP-SP-full-load-replacement");
        end else if (case_name == "backpressure") begin
            item = 0; phase = 0;
            while (item < 40) begin
                // Reoffer precisely the same data until the handshake succeeds.
                tick(1, 1, (item * 29 + 11) % 256, (phase % 7 >= 3));
                if (last_accepted) item = item + 1;
                phase = phase + 1;
            end
            // The final accepted output must also survive an idle source.
            tick(1, 0, 0, 0);
            tick(1, 0, 0, 0);
            tick(1, 0, 0, 1);
            tick(1, 0, 0, 1);
            require(accepted == 40 && received == 40, "CP-SP-backpressure-counts");
            require(idle_holds == 2, "CP-SP-idle-input-hold-activated");
            require(stalls > 0 && holds > 0 && replacements > 0, "CP-SP-backpressure-activated");
            require(latency_max > 1, "CP-SP-backpressure-latency-observed");
        end else begin
            tick(1, 1, 8'hd3, 0);
            require(pending == 1, "CP-SP-reset-has-pending");
            tick(0, 1, 8'hc7, 1);
            tick(1, 0, 0, 1);
            for (item = 0; item < 24; item = item + 1)
                tick(1, 1, (item * 13 + 5) % 256, 1);
            tick(1, 0, 0, 1);
            tick(1, 0, 0, 1);
            require(reset_drops == 1, "CP-SP-reset-discards-exactly-pending");
            require(accepted == 25 && received == 24, "CP-SP-reset-counts");
        end
        require(pending == 0, "CP-SP-drained");
        require(accepted == received + reset_drops, "CP-SP-conservation");
        require(checks > 0 && received > 0, "CP-SP-active-checks");
        $display("FIXTURE_METRICS fixture=stream_pipeline case=%s clock_period_ns=10 accepted=%0d received=%0d reset_drops=%0d stalls=%0d holds=%0d idle_holds=%0d replacements=%0d latency_min_cycles=%0d latency_max_cycles=%0d throughput_outputs=%0d throughput_span_cycles=%0d", case_name, accepted, received, reset_drops, stalls, holds, idle_holds, replacements, latency_min, latency_max, received, last_output-first_output+1);
        $display("FIXTURE_SUMMARY fixture=stream_pipeline bug=%0d case=%s result=PASS checks=%0d failures=0 cycles=%0d", `FIXTURE_BUG, case_name, checks, cycles);
        $finish;
    end
    initial begin
        #20000;
        $display("FIXTURE_SUMMARY fixture=stream_pipeline bug=%0d case=%s result=FAIL checks=%0d failures=1 reason=TIMEOUT", `FIXTURE_BUG, case_name, checks);
        $fatal(1, "fixture timeout");
    end
endmodule
