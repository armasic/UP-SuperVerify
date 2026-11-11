module register_control #(
    parameter integer BUG = 0
) (
    input wire clk, rst_n,
    input wire wr_en,
    input wire [1:0] wr_addr, rd_addr,
    input wire [7:0] wr_data,
    input wire event_valid,
    input wire [7:0] event_value,
    output reg [7:0] rd_data,
    output wire irq,
    output reg action_pulse
);
    reg [7:0] config_q;
    reg status_q, mask_q;
    wire qualifying_event = event_valid && config_q[0] &&
                            (event_value >= {1'b0, config_q[7:1]});
    wire clear_status = wr_en && (wr_addr == 2'd1) && wr_data[0];
    assign irq = status_q && !mask_q;
    always @* begin
        case (rd_addr)
            2'd0: rd_data = config_q;
            2'd1: rd_data = {7'd0, status_q};
            2'd2: rd_data = {7'd0, mask_q};
            default: rd_data = 8'd0;
        endcase
    end
    always @(posedge clk) begin
        if (!rst_n) begin
            config_q <= 0;
            status_q <= 0;
            mask_q <= 0;
            action_pulse <= 0;
        end else begin
            if (wr_en && wr_addr == 2'd0) config_q <= wr_data;
            if (wr_en && wr_addr == 2'd2) mask_q <= wr_data[0];
            action_pulse <= qualifying_event;
            if (BUG != 0) begin
                // Known defect: W1C discards a simultaneous new event.
                if (clear_status) status_q <= 0;
                else if (qualifying_event) status_q <= 1;
            end else begin
                // Specification priority: capture wins over W1C.
                if (qualifying_event) status_q <= 1;
                else if (clear_status) status_q <= 0;
            end
        end
    end
endmodule
