`timescale 1ns/1ps
`ifndef FIXTURE_BUG
`define FIXTURE_BUG 0
`endif
module tb;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst_n = 0, wr_en = 0, event_valid = 0;
    reg [1:0] wr_addr = 0, rd_addr = 0;
    reg [7:0] wr_data = 0, event_value = 0;
    wire [7:0] rd_data;
    wire irq, action_pulse;
    integer checks = 0, cycles = 0;
    string case_name;
    register_control #(.BUG(`FIXTURE_BUG)) dut (.*);

    task automatic require(input bit condition, input string checkpoint);
        checks = checks + 1;
        if (!condition) begin
            $display("CHECK_FAIL checkpoint=%s cycle=%0d", checkpoint, cycles);
            $display("FIXTURE_SUMMARY fixture=register_control bug=%0d case=%s result=FAIL checks=%0d failures=1 cycles=%0d", `FIXTURE_BUG, case_name, checks, cycles);
            $fatal(1, "independent specification check failed");
        end
    endtask

    task automatic step(
        input string checkpoint,
        input bit reset_n, write_en,
        input reg [1:0] write_addr,
        input reg [7:0] write_data,
        input bit ev,
        input reg [7:0] value,
        input reg [1:0] read_addr,
        input reg [7:0] expected_read,
        input bit expected_irq, expected_action
    );
        @(negedge clk);
        rst_n = reset_n; wr_en = write_en; wr_addr = write_addr;
        wr_data = write_data; event_valid = ev; event_value = value;
        rd_addr = read_addr;
        @(posedge clk);
        cycles = cycles + 1;
        #1;
        require(rd_data === expected_read, {checkpoint, "/read"});
        require(irq === expected_irq, {checkpoint, "/irq"});
        require(action_pulse === expected_action, {checkpoint, "/action"});
        $display("CHECK_PASS checkpoint=%s", checkpoint);
    endtask

    initial begin
        if (!$value$plusargs("CASE=%s", case_name)) case_name = "directed";
        require(case_name == "directed", "CP-RC-case-known");
        // Arguments: id, reset, write, addr, data, event, value, read, want, irq, action.
        step("CP-RC-01-reset-config", 0,1,0,8'hff,1,8'hff,0,0,0,0);
        step("CP-RC-01-reset-status", 0,0,0,0,0,0,1,0,0,0);
        step("CP-RC-01-reset-mask", 0,0,0,0,0,0,2,0,0,0);
        step("CP-RC-02-disabled", 1,0,0,0,1,8'hff,1,0,0,0);
        step("CP-RC-03-config-readback", 1,1,0,8'd21,0,0,0,8'd21,0,0);
        step("CP-RC-04-below-threshold", 1,0,0,0,1,8'd9,1,0,0,0);
        step("CP-RC-05-equal-threshold", 1,0,0,0,1,8'd10,1,1,1,1);
        step("CP-RC-06-sticky-and-pulse", 1,0,0,0,0,0,1,1,1,0);
        step("CP-RC-07-w1c-zero", 1,1,1,0,0,0,1,1,1,0);
        step("CP-RC-08-w1c-clear", 1,1,1,1,0,0,1,0,0,0);
        step("CP-RC-09-clear-below-threshold", 1,1,1,1,1,9,1,0,0,0);
        step("CP-RC-10-event-clear-priority", 1,1,1,1,1,10,1,1,1,1);
        step("CP-RC-11-mask-irq", 1,1,2,8'hff,0,0,2,1,0,0);
        step("CP-RC-12-clear-masked", 1,1,1,1,0,0,1,0,0,0);
        step("CP-RC-13-capture-masked", 1,0,0,0,1,8'hff,1,1,0,1);
        step("CP-RC-14-unmask-retained", 1,1,2,0,0,0,1,1,1,0);
        step("CP-RC-15-reserved-write", 1,1,3,8'hff,0,0,3,0,1,0);
        step("CP-RC-16-clear-reserved-bits", 1,1,1,8'hfe,0,0,1,1,1,0);
        step("CP-RC-17-clear-before-disable", 1,1,1,1,0,0,1,0,0,0);
        step("CP-RC-18-old-config-on-write", 1,1,0,8'd20,1,10,0,20,1,1);
        step("CP-RC-19-clear-disabled", 1,1,1,1,0,0,1,0,0,0);
        step("CP-RC-20-disable-function", 1,0,0,0,1,8'hff,1,0,0,0);
        step("CP-RC-21-enable-new-threshold", 1,1,0,8'd41,1,8'hff,0,41,0,0);
        step("CP-RC-22-new-threshold-reject", 1,0,0,0,1,19,1,0,0,0);
        step("CP-RC-23-new-threshold-accept", 1,0,0,0,1,20,1,1,1,1);
        step("CP-RC-24-reset-active", 0,1,0,8'hff,1,8'hff,1,0,0,0);
        step("CP-RC-25-reset-clears-config", 1,0,0,0,1,8'hff,0,0,0,0);
        require(checks > 0, "CP-RC-nonzero-checks");
        $display("FIXTURE_SUMMARY fixture=register_control bug=%0d case=%s result=PASS checks=%0d failures=0 cycles=%0d", `FIXTURE_BUG, case_name, checks, cycles);
        $finish;
    end
    initial begin
        #20000;
        $display("FIXTURE_SUMMARY fixture=register_control bug=%0d case=%s result=FAIL checks=%0d failures=1 reason=TIMEOUT", `FIXTURE_BUG, case_name, checks);
        $fatal(1, "fixture timeout");
    end
endmodule
