// Fictional two receiver controller used only as a data-contract example.
module example(input logic select_b, input logic event_i, output logic a, b);
  assign a = event_i & ~select_b;
  assign b = event_i & select_b;
endmodule
