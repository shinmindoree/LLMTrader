import pandas as pd
for f in ['BTCUSDT_oi_5m','BTCUSDT_lsr_5m','BTCUSDT_taker_5m']:
    df = pd.read_parquet(f'data/perp_meta/{f}.parquet')
    df['ts']=pd.to_datetime(df['timestamp'],unit='ms',utc=True)
    print(f'=== {f} ===  rows={len(df):,}  range={df.ts.min()}..{df.ts.max()}')
    print(df.head(2).to_string()); print('...'); print(df.tail(2).to_string())
    cols = [c for c in df.columns if c not in ('ts','timestamp')]
    print('describe:'); print(df[cols].describe().T[['mean','std','min','max']])
    print()
