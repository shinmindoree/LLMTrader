"""백테스트 페이지."""

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from llmtrader.backtest.data_loader import HistoricalDataLoader
from llmtrader.backtest.engine import BacktestEngine
from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.settings import get_settings

st.set_page_config(page_title="백테스트", page_icon="📊", layout="wide")

st.title("📊 백테스트")
st.markdown("과거 데이터로 전략 성능을 검증합니다.")

st.divider()

# 전략 파일 선택
st.subheader("1️⃣ 전략 선택")

# 전략 파일 목록
strategy_files = list(Path(".").glob("*_strategy.py"))
strategy_files += list(Path("src/llmtrader/strategy/examples").glob("*.py"))

if not strategy_files:
    st.warning("전략 파일이 없습니다. 전략 생성 페이지에서 먼저 생성해주세요.")
    st.stop()

selected_file = st.selectbox(
    "전략 파일",
    options=strategy_files,
    format_func=lambda x: x.name,
)

# 백테스트 설정
st.subheader("2️⃣ 백테스트 설정")

col1, col2, col3 = st.columns(3)

with col1:
    symbol = st.text_input("심볼", value="BTCUSDT")
    interval = st.selectbox("캔들 간격", options=["1m", "5m", "15m", "1h", "4h", "1d"], index=3)

with col2:
    days = st.number_input("백테스트 기간 (일)", min_value=1, max_value=365, value=7)
    initial_balance = st.number_input("초기 자금 (USDT)", min_value=100.0, value=10000.0, step=100.0)

with col3:
    maker_fee = st.number_input("메이커 수수료 (%)", min_value=0.0, max_value=1.0, value=0.02, step=0.01) / 100
    taker_fee = st.number_input("테이커 수수료 (%)", min_value=0.0, max_value=1.0, value=0.04, step=0.01) / 100

st.divider()

# 백테스트 실행
if st.button("🚀 백테스트 실행", type="primary", use_container_width=True):
    with st.spinner("백테스트를 실행중입니다..."):
        async def run_backtest():
            # 전략 로드
            spec = importlib.util.spec_from_file_location("custom_strategy", selected_file)
            if not spec or not spec.loader:
                raise ValueError("전략 파일을 로드할 수 없습니다")

            module = importlib.util.module_from_spec(spec)
            sys.modules["custom_strategy"] = module
            spec.loader.exec_module(module)

            strategy_class = None
            for name in dir(module):
                obj = getattr(module, name)
                if isinstance(obj, type) and name.endswith("Strategy") and name != "Strategy":
                    strategy_class = obj
                    break

            if not strategy_class:
                raise ValueError("전략 클래스를 찾을 수 없습니다")

            strategy = strategy_class()

            # 데이터 로드
            settings = get_settings()
            client = BinanceHTTPClient(
                api_key=settings.binance.api_key,
                api_secret=settings.binance.api_secret,
                base_url=settings.binance.base_url,
            )

            loader = HistoricalDataLoader(client)
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(days=days)

            klines = await loader.load_klines(symbol, interval, start_time, end_time)

            # 백테스트 실행
            engine = BacktestEngine(
                strategy=strategy,
                initial_balance=initial_balance,
                maker_fee=maker_fee,
                taker_fee=taker_fee,
                slippage=0.0001,
            )

            result = engine.run(klines)

            await client.aclose()

            return result, engine.equity_curve, strategy_class.__name__

        try:
            result, equity_curve, strategy_name = asyncio.run(run_backtest())

            st.divider()
            st.success(f"✅ 백테스트 완료: {strategy_name}")

            # 결과 표시
            st.subheader("📈 성과 지표")

            metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)

            with metric_col1:
                st.metric(
                    "총 수익률",
                    f"{result['total_return_pct']:.2f}%",
                    delta=f"${result['final_equity'] - result['initial_balance']:.2f}",
                )

            with metric_col2:
                st.metric("최대 낙폭 (MDD)", f"{result['max_drawdown_pct']:.2f}%")

            with metric_col3:
                st.metric("샤프 비율", f"{result.get('sharpe_ratio', 0):.2f}")

            with metric_col4:
                win_rate = result.get("win_rate_pct", 0)
                st.metric("승률", f"{win_rate:.1f}%")

            # 거래 통계
            st.subheader("💼 거래 통계")

            trade_col1, trade_col2, trade_col3, trade_col4 = st.columns(4)

            with trade_col1:
                st.metric("총 거래", result.get("num_trades", 0))

            with trade_col2:
                st.metric("승리", result.get("num_wins", 0))

            with trade_col3:
                st.metric("패배", result.get("num_losses", 0))

            with trade_col4:
                st.metric("캔들 수", result["num_bars"])

            # 에쿼티 커브 차트
            st.subheader("📉 에쿼티 커브")

            if equity_curve:
                df = pd.DataFrame(equity_curve)
                df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")

                fig = go.Figure()

                fig.add_trace(
                    go.Scatter(
                        x=df["datetime"],
                        y=df["total_equity"],
                        mode="lines",
                        name="Total Equity",
                        line=dict(color="blue", width=2),
                    )
                )

                fig.add_hline(
                    y=initial_balance,
                    line_dash="dash",
                    line_color="gray",
                    annotation_text="Initial Balance",
                )

                fig.update_layout(
                    xaxis_title="Date",
                    yaxis_title="Equity (USDT)",
                    hovermode="x unified",
                    height=400,
                )

                st.plotly_chart(fig, use_container_width=True)

            # 상세 결과
            with st.expander("📋 상세 결과"):
                st.json(result)

        except Exception as e:
            st.error(f"❌ 백테스트 실패: {e}")
            import traceback

            st.code(traceback.format_exc())

