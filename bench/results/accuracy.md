| Algorithm | Peak in any window / limit, boundary burst | Peak / limit, random bursts | Admitted vs exact log, random bursts | Redis bytes per key (100 requests) |
| --- | ---: | ---: | ---: | ---: |
| fixed_window | 2.00 | 1.95 | 1.20 | 104 |
| sliding_log | 1.00 | 1.00 | 1.00 | 5,192 |
| sliding_counter | 1.00 | 1.55 | 1.03 | 120 |
| token_bucket | 1.00 | 1.95 | 1.27 | 136 |
| leaky_bucket | 1.00 | 1.95 | 1.27 | 136 |
