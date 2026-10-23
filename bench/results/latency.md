| Backend | Algorithm | p50 (µs) | p95 (µs) | p99 (µs) | Throughput, 1 coroutine (checks/s) | Throughput, 64 coroutines (checks/s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| memory | fixed_window | 5.2 | 11.5 | 22.6 | 149,038 | 168,037 |
| memory | sliding_log | 5.1 | 7.7 | 21.0 | 161,252 | 120,225 |
| memory | sliding_counter | 5.9 | 12.6 | 23.5 | 136,565 | 148,178 |
| memory | token_bucket | 4.9 | 7.0 | 13.8 | 180,385 | 167,996 |
| memory | leaky_bucket | 4.8 | 6.8 | 20.1 | 178,039 | 164,489 |
| redis | fixed_window | 145.9 | 224.4 | 416.2 | 6,162 | 7,800 |
| redis | sliding_log | 169.9 | 316.5 | 525.9 | 5,158 | 8,659 |
| redis | sliding_counter | 151.1 | 249.7 | 432.7 | 5,921 | 9,212 |
| redis | token_bucket | 142.4 | 207.5 | 342.9 | 6,404 | 9,727 |
| redis | leaky_bucket | 145.0 | 241.1 | 451.1 | 6,133 | 9,029 |

CPU busy during the 64-coroutine run (% of one core):

| Backend | Algorithm | Python process | Redis server |
| --- | --- | ---: | ---: |
| memory | fixed_window | 100% | n/a |
| memory | sliding_log | 96% | n/a |
| memory | sliding_counter | 100% | n/a |
| memory | token_bucket | 99% | n/a |
| memory | leaky_bucket | 93% | n/a |
| redis | fixed_window | 97% | 28% |
| redis | sliding_log | 100% | 51% |
| redis | sliding_counter | 100% | 32% |
| redis | token_bucket | 99% | 32% |
| redis | leaky_bucket | 99% | 30% |
