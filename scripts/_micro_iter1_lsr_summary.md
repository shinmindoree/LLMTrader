# Iter1 — LSR contrarian (z-score thresholds)

Total combos: 648 (3 features × 4 wins × 5 k × 5 hold × 4 tp × 4 sl)
TRAIN: 2023-04-29..2025-04-29  TEST: 2025-04-29..2026-04-29

## Acceptance counts
- TRAIN T2 pass (PF>=1.1, trades>=20, DD<=30%, +M>=14/24): **202**
- OOS survivors (PF>=1.05, trades>=15, DD<=20%, +M>=6/12, ret>0): **15**

## Top 20 OOS survivors
- feature=lsr_top_pos mode=z_contra win=96 k=2.5 hold_h=48 tp=0.012 sl=0.02 | TRAIN agg=+44.9% pf=1.24 dd=15.3% +M=17/25 trades=269 | TEST agg=+31.4% pf=1.36 dd=11.3% +M=7/13 trades=140
- feature=lsr_top_pos mode=z_contra win=96 k=2.5 hold_h=24 tp=0.012 sl=0.02 | TRAIN agg=+37.2% pf=1.14 dd=13.8% +M=16/25 trades=418 | TEST agg=+29.4% pf=1.23 dd=15.4% +M=7/13 trades=214
- feature=lsr_acc mode=z_contra win=96 k=2.0 hold_h=24 tp=0.006 sl=0.02 | TRAIN agg=+48.0% pf=1.22 dd=13.8% +M=15/25 trades=536 | TEST agg=+25.2% pf=1.25 dd=6.7% +M=8/13 trades=269
- feature=lsr_top_pos mode=z_contra win=96 k=2.5 hold_h=48 tp=0.006 sl=0.02 | TRAIN agg=+50.8% pf=1.51 dd=8.1% +M=17/25 trades=269 | TEST agg=+23.4% pf=1.49 dd=9.3% +M=9/13 trades=140
- feature=lsr_acc mode=z_contra win=96 k=2.0 hold_h=24 tp=0.012 sl=0.02 | TRAIN agg=+90.6% pf=1.23 dd=8.8% +M=18/25 trades=536 | TEST agg=+14.0% pf=1.09 dd=7.1% +M=9/13 trades=269
- feature=lsr_top_acc mode=z_contra win=96 k=2.0 hold_h=48 tp=0.02 sl=0.012 | TRAIN agg=+33.5% pf=1.15 dd=10.6% +M=15/25 trades=308 | TEST agg=+12.0% pf=1.12 dd=9.3% +M=7/13 trades=159
- feature=lsr_top_pos mode=z_contra win=384 k=2.5 hold_h=8 tp=0.02 sl=0.02 | TRAIN agg=+26.4% pf=1.16 dd=11.4% +M=14/25 trades=290 | TEST agg=+11.4% pf=1.16 dd=7.9% +M=6/13 trades=135
- feature=lsr_top_pos mode=z_contra win=96 k=2.0 hold_h=48 tp=0.006 sl=0.02 | TRAIN agg=+44.9% pf=1.36 dd=6.1% +M=18/25 trades=313 | TEST agg=+7.5% pf=1.13 dd=8.5% +M=8/13 trades=160
- feature=lsr_acc mode=z_contra win=192 k=1.0 hold_h=48 tp=0.012 sl=0.02 | TRAIN agg=+96.0% pf=1.35 dd=7.3% +M=19/25 trades=349 | TEST agg=+6.7% pf=1.07 dd=10.4% +M=7/13 trades=177
- feature=lsr_top_pos mode=z_contra win=192 k=2.5 hold_h=24 tp=0.006 sl=0.02 | TRAIN agg=+11.0% pf=1.11 dd=13.4% +M=14/25 trades=277 | TEST agg=+6.4% pf=1.13 dd=2.8% +M=6/13 trades=139
- feature=lsr_acc mode=z_contra win=96 k=2.5 hold_h=24 tp=0.006 sl=0.02 | TRAIN agg=+47.1% pf=1.28 dd=14.9% +M=16/25 trades=416 | TEST agg=+6.2% pf=1.09 dd=9.7% +M=7/13 trades=199
- feature=lsr_top_pos mode=z_contra win=96 k=2.5 hold_h=24 tp=0.006 sl=0.02 | TRAIN agg=+35.2% pf=1.22 dd=14.9% +M=16/25 trades=418 | TEST agg=+5.3% pf=1.07 dd=17.0% +M=7/13 trades=214
- feature=lsr_top_pos mode=z_contra win=192 k=1.5 hold_h=48 tp=0.006 sl=0.02 | TRAIN agg=+40.8% pf=1.33 dd=6.8% +M=18/25 trades=315 | TEST agg=+4.4% pf=1.08 dd=10.5% +M=7/13 trades=164
- feature=lsr_top_pos mode=z_contra win=384 k=2.5 hold_h=24 tp=0.006 sl=0.02 | TRAIN agg=+21.3% pf=1.33 dd=9.3% +M=16/25 trades=181 | TEST agg=+4.2% pf=1.13 dd=7.0% +M=8/13 trades=88
- feature=lsr_top_pos mode=z_contra win=384 k=2.5 hold_h=8 tp=0.006 sl=0.02 | TRAIN agg=+13.1% pf=1.13 dd=10.7% +M=15/25 trades=290 | TEST agg=+2.0% pf=1.05 dd=13.1% +M=7/13 trades=135

## Top 20 TRAIN passes (any OOS)
- feature=lsr_top_acc mode=z_contra win=192 k=1.5 hold_h=8 tp=0.012 sl=0.02 | TRAIN agg=+304.3% pf=1.32 dd=17.9% +M=19/25 trades=998 | TEST agg=-60.5% pf=0.72 dd=60.5% +M=1/13 trades=509
- feature=lsr_top_acc mode=z_contra win=192 k=1.0 hold_h=8 tp=0.012 sl=0.02 | TRAIN agg=+303.1% pf=1.22 dd=28.0% +M=19/25 trades=1468 | TEST agg=-61.0% pf=0.79 dd=61.0% +M=1/13 trades=762
- feature=lsr_top_acc mode=z_contra win=384 k=1.5 hold_h=8 tp=0.012 sl=0.02 | TRAIN agg=+281.8% pf=1.37 dd=5.5% +M=19/25 trades=834 | TEST agg=-51.8% pf=0.74 dd=51.8% +M=2/13 trades=433
- feature=lsr_top_acc mode=z_contra win=192 k=1.0 hold_h=8 tp=0.02 sl=0.02 | TRAIN agg=+252.8% pf=1.18 dd=25.6% +M=16/25 trades=1468 | TEST agg=-53.3% pf=0.85 dd=53.3% +M=3/13 trades=762
- feature=lsr_top_acc mode=z_contra win=96 k=2.0 hold_h=8 tp=0.02 sl=0.02 | TRAIN agg=+251.7% pf=1.31 dd=12.5% +M=17/25 trades=829 | TEST agg=-37.2% pf=0.83 dd=37.2% +M=3/13 trades=412
- feature=lsr_acc mode=z_contra win=384 k=1.0 hold_h=24 tp=0.012 sl=0.02 | TRAIN agg=+238.1% pf=1.45 dd=12.8% +M=20/25 trades=554 | TEST agg=-13.1% pf=0.95 dd=20.9% +M=5/13 trades=302
- feature=lsr_top_acc mode=z_contra win=96 k=2.0 hold_h=8 tp=0.012 sl=0.02 | TRAIN agg=+230.5% pf=1.34 dd=12.6% +M=18/25 trades=829 | TEST agg=-21.3% pf=0.90 dd=23.7% +M=6/13 trades=412
- feature=lsr_acc mode=z_contra win=192 k=1.5 hold_h=8 tp=0.012 sl=0.02 | TRAIN agg=+223.4% pf=1.25 dd=16.1% +M=18/25 trades=1065 | TEST agg=-55.4% pf=0.76 dd=55.9% +M=1/13 trades=551
- feature=lsr_top_acc mode=z_contra win=96 k=1.5 hold_h=8 tp=0.012 sl=0.02 | TRAIN agg=+221.8% pf=1.21 dd=28.2% +M=19/25 trades=1275 | TEST agg=-48.6% pf=0.83 dd=50.2% +M=4/13 trades=666
- feature=lsr_top_acc mode=z_contra win=96 k=1.5 hold_h=8 tp=0.02 sl=0.02 | TRAIN agg=+197.4% pf=1.18 dd=27.7% +M=17/25 trades=1275 | TEST agg=-61.5% pf=0.78 dd=62.7% +M=4/13 trades=666
- feature=lsr_top_acc mode=z_contra win=384 k=1.0 hold_h=8 tp=0.012 sl=0.02 | TRAIN agg=+196.7% pf=1.19 dd=27.0% +M=20/25 trades=1304 | TEST agg=-58.7% pf=0.79 dd=58.7% +M=2/13 trades=703
- feature=lsr_acc mode=z_contra win=192 k=1.5 hold_h=8 tp=0.02 sl=0.02 | TRAIN agg=+189.0% pf=1.20 dd=16.5% +M=19/25 trades=1065 | TEST agg=-52.7% pf=0.80 dd=57.7% +M=4/13 trades=551
- feature=lsr_acc mode=z_contra win=192 k=2.5 hold_h=8 tp=0.02 sl=0.02 | TRAIN agg=+187.4% pf=1.64 dd=6.9% +M=18/25 trades=357 | TEST agg=-34.2% pf=0.68 dd=38.0% +M=2/13 trades=181
- feature=lsr_top_acc mode=z_contra win=192 k=1.5 hold_h=8 tp=0.02 sl=0.02 | TRAIN agg=+183.7% pf=1.21 dd=18.6% +M=15/25 trades=998 | TEST agg=-61.5% pf=0.74 dd=61.9% +M=2/13 trades=509
- feature=lsr_top_acc mode=z_contra win=384 k=1.5 hold_h=8 tp=0.02 sl=0.02 | TRAIN agg=+179.6% pf=1.24 dd=11.8% +M=18/25 trades=834 | TEST agg=-57.7% pf=0.72 dd=57.9% +M=2/13 trades=433
- feature=lsr_acc mode=z_contra win=384 k=1.0 hold_h=8 tp=0.012 sl=0.02 | TRAIN agg=+164.9% pf=1.16 dd=26.9% +M=18/25 trades=1380 | TEST agg=-43.4% pf=0.87 dd=47.0% +M=3/13 trades=726
- feature=lsr_top_acc mode=z_contra win=192 k=1.0 hold_h=8 tp=0.006 sl=0.02 | TRAIN agg=+157.2% pf=1.21 dd=26.3% +M=20/25 trades=1468 | TEST agg=-44.6% pf=0.82 dd=44.6% +M=3/13 trades=762
- feature=lsr_acc mode=z_contra win=96 k=2.0 hold_h=8 tp=0.012 sl=0.02 | TRAIN agg=+149.5% pf=1.25 dd=13.0% +M=19/25 trades=854 | TEST agg=-21.2% pf=0.91 dd=25.6% +M=6/13 trades=439
- feature=lsr_acc mode=z_contra win=384 k=1.5 hold_h=8 tp=0.012 sl=0.02 | TRAIN agg=+149.4% pf=1.23 dd=8.3% +M=17/25 trades=882 | TEST agg=-43.5% pf=0.79 dd=43.5% +M=3/13 trades=448
- feature=lsr_top_acc mode=z_contra win=192 k=1.5 hold_h=24 tp=0.012 sl=0.02 | TRAIN agg=+145.6% pf=1.36 dd=12.5% +M=18/25 trades=491 | TEST agg=-34.7% pf=0.80 dd=39.2% +M=4/13 trades=256
