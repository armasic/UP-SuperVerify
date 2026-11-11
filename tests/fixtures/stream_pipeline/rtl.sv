module stream_pipeline #(
    parameter integer BUG = 0
) (
    input wire clk, rst_n,
    input wire in_valid,
    input wire [7:0] in_data,
    output wire in_ready,
    output reg out_valid,
    output reg [7:0] out_data,
    input wire out_ready
);
    assign in_ready = rst_n && (!out_valid || out_ready);
    always @(posedge clk) begin
        if (!rst_n) begin
            out_valid <= 0;
            out_data <= 0;
        end else if ((BUG != 0) || in_ready) begin
            // BUG deliberately overwrites a stalled slot. Normal operation
            // changes the slot only when it can accept a new transaction.
            out_valid <= in_valid;
            if (in_valid) out_data <= (in_data << 1) + in_data + 8'd7;
        end
    end
endmodule
