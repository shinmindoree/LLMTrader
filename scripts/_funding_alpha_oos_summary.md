# Funding contrarian — Train/Test OOS

- TRAIN: 2023-04-29 .. 2025-04-29 (24 months)
- TEST : 2025-04-29 .. 2026-04-29 (12 months)
- Grid: 2160 combos

## Train acceptance -> OOS survival

| Train tier | Combos pass | OOS test_ret > 0 | OOS test_ret median | OOS test_ret mean |
|---|---:|---:|---:|---:|
| T2 (PF>=1.1, trades>=20, DD<=30%) | 62 | 22 (35%) | -1.36% | -1.66% |
| T3 (T2 + >=60% +months) | 54 | 19 (35%) | -1.50% | -1.61% |
| T4 (T2 + >=70% +months) | 22 | 9 (41%) | -1.50% | -0.33% |

## Top 10 by TRAIN agg_ret (passing T2)

- pos=+5e-05 neg=-3e-05 hold=8h tp=0.006 sl=0.020 | TRAIN agg=+31.7% pf=1.10 dd=10.8% +M=15/24 trades=887 | TEST agg=-8.2% pf=0.94 dd=13.2% +M=5/12 trades=338
- pos=+7e-05 neg=-1e-04 hold=8h tp=0.006 sl=0.020 | TRAIN agg=+30.8% pf=1.12 dd=19.9% +M=16/24 trades=734 | TEST agg=+0.1% pf=1.01 dd=13.5% +M=6/12 trades=200
- pos=+7e-05 neg=-7e-05 hold=8h tp=0.006 sl=0.020 | TRAIN agg=+30.0% pf=1.12 dd=19.9% +M=16/24 trades=741 | TEST agg=-0.7% pf=1.00 dd=13.5% +M=6/12 trades=210
- pos=+5e-05 neg=-1e-04 hold=8h tp=0.006 sl=0.020 | TRAIN agg=+29.7% pf=1.10 dd=14.2% +M=15/24 trades=842 | TEST agg=-2.1% pf=0.99 dd=13.2% +M=6/12 trades=286
- pos=+3e-05 neg=-1e-05 hold=72h tp=0.006 sl=0.020 | TRAIN agg=+29.0% pf=1.34 dd=16.1% +M=16/24 trades=228 | TEST agg=-0.1% pf=1.01 dd=13.9% +M=6/12 trades=106
- pos=+3e-05 neg=-2e-05 hold=72h tp=0.006 sl=0.020 | TRAIN agg=+28.2% pf=1.33 dd=16.1% +M=16/24 trades=227 | TEST agg=+3.4% pf=1.09 dd=9.7% +M=6/12 trades=103
- pos=+3e-05 neg=-1e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+25.5% pf=1.47 dd=7.9% +M=19/24 trades=228 | TEST agg=-7.0% pf=0.82 dd=16.9% +M=5/12 trades=106
- pos=+2e-05 neg=-1e-05 hold=24h tp=0.006 sl=0.020 | TRAIN agg=+24.6% pf=1.12 dd=11.3% +M=14/24 trades=552 | TEST agg=-3.0% pf=0.98 dd=8.3% +M=7/12 trades=254
- pos=+1e-05 neg=-1e-04 hold=24h tp=0.006 sl=0.020 | TRAIN agg=+23.6% pf=1.11 dd=15.6% +M=14/24 trades=538 | TEST agg=-1.3% pf=1.00 dd=17.9% +M=7/12 trades=239
- pos=+1e-05 neg=-7e-05 hold=24h tp=0.006 sl=0.020 | TRAIN agg=+23.6% pf=1.11 dd=15.6% +M=14/24 trades=540 | TEST agg=-0.2% pf=1.01 dd=17.9% +M=7/12 trades=241

## Top 10 by TRAIN +months (passing T2)

- pos=+3e-05 neg=-1e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+25.5% pf=1.47 dd=7.9% +M=19/24 trades=228 | TEST agg=-7.0% pf=0.82 dd=16.9% +M=5/12 trades=106
- pos=+3e-05 neg=-2e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+19.1% pf=1.34 dd=7.9% +M=19/24 trades=227 | TEST agg=-3.5% pf=0.91 dd=11.0% +M=6/12 trades=103
- pos=+3e-05 neg=-7e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+18.6% pf=1.34 dd=7.9% +M=19/24 trades=219 | TEST agg=+5.9% pf=1.25 dd=3.8% +M=7/12 trades=95
- pos=+3e-05 neg=-1e-05 hold=72h tp=0.004 sl=0.012 | TRAIN agg=+5.8% pf=1.10 dd=10.8% +M=19/24 trades=228 | TEST agg=-4.8% pf=0.86 dd=14.0% +M=7/12 trades=106
- pos=+3e-05 neg=-1e-04 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+17.0% pf=1.31 dd=7.9% +M=18/24 trades=215 | TEST agg=+5.1% pf=1.22 dd=3.8% +M=6/12 trades=93
- pos=+7e-05 neg=-7e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+16.3% pf=1.36 dd=7.9% +M=18/24 trades=187 | TEST agg=+0.7% pf=1.05 dd=8.2% +M=7/12 trades=68
- pos=+3e-05 neg=-3e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+15.5% pf=1.27 dd=7.9% +M=18/24 trades=225 | TEST agg=-1.5% pf=0.97 dd=9.8% +M=7/12 trades=102
- pos=+5e-05 neg=-5e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+13.8% pf=1.25 dd=7.9% +M=18/24 trades=214 | TEST agg=-10.3% pf=0.71 dd=11.2% +M=6/12 trades=89
- pos=+7e-05 neg=-5e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+12.8% pf=1.26 dd=7.9% +M=18/24 trades=192 | TEST agg=-4.0% pf=0.86 dd=8.4% +M=7/12 trades=75
- pos=+3e-05 neg=-5e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+12.0% pf=1.21 dd=7.9% +M=18/24 trades=223 | TEST agg=-2.8% pf=0.92 dd=7.9% +M=6/12 trades=98

## All T4 train combos (their OOS test result)

- pos=+3e-05 neg=-1e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+25.5% pf=1.47 dd=7.9% +M=19/24 trades=228 | TEST agg=-7.0% pf=0.82 dd=16.9% +M=5/12 trades=106
- pos=+3e-05 neg=-2e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+19.1% pf=1.34 dd=7.9% +M=19/24 trades=227 | TEST agg=-3.5% pf=0.91 dd=11.0% +M=6/12 trades=103
- pos=+3e-05 neg=-7e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+18.6% pf=1.34 dd=7.9% +M=19/24 trades=219 | TEST agg=+5.9% pf=1.25 dd=3.8% +M=7/12 trades=95
- pos=+7e-05 neg=-7e-05 hold=72h tp=0.006 sl=0.020 | TRAIN agg=+17.6% pf=1.25 dd=16.0% +M=17/24 trades=187 | TEST agg=+4.6% pf=1.19 dd=7.3% +M=6/12 trades=68
- pos=+3e-05 neg=-1e-04 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+17.0% pf=1.31 dd=7.9% +M=18/24 trades=215 | TEST agg=+5.1% pf=1.22 dd=3.8% +M=6/12 trades=93
- pos=+7e-05 neg=-7e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+16.3% pf=1.36 dd=7.9% +M=18/24 trades=187 | TEST agg=+0.7% pf=1.05 dd=8.2% +M=7/12 trades=68
- pos=+3e-05 neg=-3e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+15.5% pf=1.27 dd=7.9% +M=18/24 trades=225 | TEST agg=-1.5% pf=0.97 dd=9.8% +M=7/12 trades=102
- pos=+5e-05 neg=-1e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+14.1% pf=1.25 dd=7.9% +M=17/24 trades=222 | TEST agg=-8.4% pf=0.77 dd=11.6% +M=5/12 trades=95
- pos=+5e-05 neg=-5e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+13.8% pf=1.25 dd=7.9% +M=18/24 trades=214 | TEST agg=-10.3% pf=0.71 dd=11.2% +M=6/12 trades=89
- pos=+7e-05 neg=-5e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+12.8% pf=1.26 dd=7.9% +M=18/24 trades=192 | TEST agg=-4.0% pf=0.86 dd=8.4% +M=7/12 trades=75
- pos=+3e-05 neg=-5e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+12.0% pf=1.21 dd=7.9% +M=18/24 trades=223 | TEST agg=-2.8% pf=0.92 dd=7.9% +M=6/12 trades=98
- pos=+7e-05 neg=-1e-04 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+11.9% pf=1.26 dd=8.9% +M=17/24 trades=183 | TEST agg=-0.7% pf=0.98 dd=8.2% +M=7/12 trades=64
- pos=+5e-05 neg=-7e-05 hold=72h tp=0.004 sl=0.020 | TRAIN agg=+11.8% pf=1.22 dd=7.9% +M=17/24 trades=209 | TEST agg=-5.6% pf=0.82 dd=8.3% +M=5/12 trades=83
- pos=+3e-05 neg=-7e-05 hold=72h tp=0.006 sl=0.012 | TRAIN agg=+11.5% pf=1.15 dd=10.7% +M=17/24 trades=219 | TEST agg=+8.8% pf=1.29 dd=4.7% +M=8/12 trades=95
- pos=+3e-05 neg=-7e-05 hold=72h tp=0.004 sl=0.012 | TRAIN agg=+11.0% pf=1.21 dd=5.4% +M=18/24 trades=219 | TEST agg=+9.2% pf=1.46 dd=3.4% +M=9/12 trades=95
- pos=+3e-05 neg=-1e-04 hold=72h tp=0.004 sl=0.012 | TRAIN agg=+9.5% pf=1.18 dd=5.4% +M=17/24 trades=215 | TEST agg=+8.4% pf=1.42 dd=3.4% +M=9/12 trades=93
- pos=+3e-05 neg=-3e-05 hold=72h tp=0.006 sl=0.012 | TRAIN agg=+9.2% pf=1.12 dd=10.2% +M=17/24 trades=225 | TEST agg=-3.8% pf=0.92 dd=10.6% +M=7/12 trades=102
- pos=+3e-05 neg=-5e-05 hold=72h tp=0.004 sl=0.012 | TRAIN agg=+9.1% pf=1.17 dd=5.4% +M=17/24 trades=223 | TEST agg=+5.2% pf=1.22 dd=4.5% +M=8/12 trades=98
- pos=+3e-05 neg=-1e-05 hold=72h tp=0.006 sl=0.012 | TRAIN agg=+9.1% pf=1.12 dd=14.9% +M=17/24 trades=228 | TEST agg=-1.6% pf=0.97 dd=13.6% +M=7/12 trades=106
- pos=+3e-05 neg=-2e-05 hold=72h tp=0.006 sl=0.012 | TRAIN agg=+8.5% pf=1.11 dd=13.4% +M=18/24 trades=227 | TEST agg=+0.3% pf=1.02 dd=10.6% +M=7/12 trades=103
- pos=+3e-05 neg=-3e-05 hold=72h tp=0.004 sl=0.012 | TRAIN agg=+8.1% pf=1.15 dd=5.4% +M=18/24 trades=225 | TEST agg=-1.5% pf=0.96 dd=7.2% +M=7/12 trades=102
- pos=+3e-05 neg=-1e-05 hold=72h tp=0.004 sl=0.012 | TRAIN agg=+5.8% pf=1.10 dd=10.8% +M=19/24 trades=228 | TEST agg=-4.8% pf=0.86 dd=14.0% +M=7/12 trades=106
