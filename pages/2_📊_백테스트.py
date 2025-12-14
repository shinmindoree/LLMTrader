"""백테스트 페이지."""

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
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

            # 캔들 차트 with 매매 시점, 이동평균선, RSI
            st.subheader("📊 캔들 차트 & 기술적 지표")

            if result.get("klines"):
                klines_df = pd.DataFrame(result["klines"])
                klines_df["datetime"] = pd.to_datetime(klines_df["timestamp"], unit="ms")

                # 이동평균선 계산
                ma_periods = [5, 10, 20, 60, 120]
                ma_colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7"]
                
                for period in ma_periods:
                    klines_df[f"MA{period}"] = klines_df["close"].rolling(window=period).mean()

                # RSI 계산 (14일 기본)
                def calculate_rsi(prices, period=14):
                    delta = prices.diff()
                    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
                    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
                    rs = gain / loss
                    rsi = 100 - (100 / (1 + rs))
                    return rsi

                klines_df["RSI"] = calculate_rsi(klines_df["close"], 14)

                # 서브플롯 생성 (캔들 차트 + RSI)
                fig = make_subplots(
                    rows=2, cols=1,
                    shared_xaxes=True,
                    vertical_spacing=0.03,
                    row_heights=[0.7, 0.3],
                    subplot_titles=("가격 & 이동평균선", "RSI (14)")
                )

                # 캔들 차트
                fig.add_trace(
                    go.Candlestick(
                        x=klines_df["datetime"],
                        open=klines_df["open"],
                        high=klines_df["high"],
                        low=klines_df["low"],
                        close=klines_df["close"],
                        name="Price",
                        increasing_line_color="#26A69A",
                        decreasing_line_color="#EF5350",
                    ),
                    row=1, col=1
                )

                # 이동평균선 추가
                for i, period in enumerate(ma_periods):
                    fig.add_trace(
                        go.Scatter(
                            x=klines_df["datetime"],
                            y=klines_df[f"MA{period}"],
                            mode="lines",
                            name=f"MA{period}",
                            line=dict(color=ma_colors[i], width=1.5),
                            hovertemplate=f"MA{period}: %{{y:,.2f}}<extra></extra>",
                        ),
                        row=1, col=1
                    )

                # RSI 차트
                fig.add_trace(
                    go.Scatter(
                        x=klines_df["datetime"],
                        y=klines_df["RSI"],
                        mode="lines",
                        name="RSI",
                        line=dict(color="#AB47BC", width=2),
                        hovertemplate="RSI: %{y:.1f}<extra></extra>",
                    ),
                    row=2, col=1
                )

                # RSI 과매수/과매도 라인
                fig.add_hline(y=70, line_dash="dash", line_color="red", line_width=1, row=2, col=1)
                fig.add_hline(y=30, line_dash="dash", line_color="green", line_width=1, row=2, col=1)
                fig.add_hline(y=50, line_dash="dot", line_color="gray", line_width=1, row=2, col=1)

                # RSI 과매수/과매도 영역 (음영)
                fig.add_hrect(y0=70, y1=100, fillcolor="red", opacity=0.1, line_width=0, row=2, col=1)
                fig.add_hrect(y0=0, y1=30, fillcolor="green", opacity=0.1, line_width=0, row=2, col=1)

                # 거래 시점 표시
                if result.get("trades"):
                    trades_df = pd.DataFrame(result["trades"])
                    
                    for _, trade in trades_df.iterrows():
                        entry_dt = pd.to_datetime(trade["entry_time"], unit="ms")
                        exit_dt = pd.to_datetime(trade["exit_time"], unit="ms")
                        
                        if trade["position_type"] == "LONG":
                            # 매수 진입 (초록 삼각형)
                            fig.add_trace(
                                go.Scatter(
                                    x=[entry_dt],
                                    y=[trade["entry_price"]],
                                    mode="markers",
                                    marker=dict(
                                        symbol="triangle-up",
                                        size=12,
                                        color="#00E676",
                                        line=dict(color="white", width=1),
                                    ),
                                    name="매수 진입",
                                    showlegend=False,
                                    hovertemplate=f"<b>매수 진입</b><br>가격: ${trade['entry_price']:,.2f}<br>수량: {trade['quantity']:.4f}<extra></extra>",
                                ),
                                row=1, col=1
                            )
                            # 매도 청산 (빨강 역삼각형)
                            fig.add_trace(
                                go.Scatter(
                                    x=[exit_dt],
                                    y=[trade["exit_price"]],
                                    mode="markers",
                                    marker=dict(
                                        symbol="triangle-down",
                                        size=12,
                                        color="#FF5252",
                                        line=dict(color="white", width=1),
                                    ),
                                    name="매도 청산",
                                    showlegend=False,
                                    hovertemplate=f"<b>매도 청산</b><br>가격: ${trade['exit_price']:,.2f}<br>손익: ${trade['pnl']:,.2f}<extra></extra>",
                                ),
                                row=1, col=1
                            )
                        else:  # SHORT
                            # 매도 진입 (빨강 역삼각형)
                            fig.add_trace(
                                go.Scatter(
                                    x=[entry_dt],
                                    y=[trade["entry_price"]],
                                    mode="markers",
                                    marker=dict(
                                        symbol="triangle-down",
                                        size=12,
                                        color="#FF5252",
                                        line=dict(color="white", width=1),
                                    ),
                                    name="매도 진입",
                                    showlegend=False,
                                    hovertemplate=f"<b>매도 진입</b><br>가격: ${trade['entry_price']:,.2f}<br>수량: {trade['quantity']:.4f}<extra></extra>",
                                ),
                                row=1, col=1
                            )
                            # 매수 청산 (초록 삼각형)
                            fig.add_trace(
                                go.Scatter(
                                    x=[exit_dt],
                                    y=[trade["exit_price"]],
                                    mode="markers",
                                    marker=dict(
                                        symbol="triangle-up",
                                        size=12,
                                        color="#00E676",
                                        line=dict(color="white", width=1),
                                    ),
                                    name="매수 청산",
                                    showlegend=False,
                                    hovertemplate=f"<b>매수 청산</b><br>가격: ${trade['exit_price']:,.2f}<br>손익: ${trade['pnl']:,.2f}<extra></extra>",
                                ),
                                row=1, col=1
                            )

                fig.update_layout(
                    hovermode="x unified",
                    height=800,
                    template="plotly_dark",
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=1.02,
                        xanchor="right",
                        x=1
                    ),
                    dragmode="zoom",  # 드래그로 줌 가능
                )

                # rangeslider 비활성화 (캔들 차트 기본 옵션)
                fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
                
                # Y축 설정 - autorange로 자동 스케일
                fig.update_yaxes(
                    title_text="가격 (USDT)",
                    autorange=True,
                    fixedrange=False,  # Y축 줌 허용
                    row=1, col=1
                )
                fig.update_yaxes(
                    title_text="RSI",
                    range=[0, 100],
                    fixedrange=False,
                    row=2, col=1
                )
                
                # X축 설정 - 줌 허용
                fig.update_xaxes(fixedrange=False, row=1, col=1)
                fig.update_xaxes(title_text="날짜", fixedrange=False, row=2, col=1)

                # 차트 출력 (스크롤 줌 활성화)
                st.plotly_chart(
                    fig,
                    use_container_width=True,
                    config={
                        "scrollZoom": True,  # 마우스 휠로 줌
                        "displayModeBar": True,
                        "modeBarButtonsToAdd": ["autoScale2d", "resetScale2d"],
                    }
                )

                # 이동평균선 범례 설명
                st.caption("📈 이동평균선: MA5(빨강), MA10(청록), MA20(파랑), MA60(초록), MA120(노랑) | 📉 RSI: 70↑ 과매수, 30↓ 과매도")

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
                        fill="tozeroy",
                    )
                )

                fig.add_hline(
                    y=initial_balance,
                    line_dash="dash",
                    line_color="gray",
                    annotation_text="Initial Balance",
                )

                fig.update_layout(
                    xaxis_title="날짜",
                    yaxis_title="자산 (USDT)",
                    hovermode="x unified",
                    height=400,
                    template="plotly_dark",
                    dragmode="zoom",
                )
                
                # 축 설정 - 자동 스케일 및 줌 허용
                fig.update_xaxes(fixedrange=False)
                fig.update_yaxes(autorange=True, fixedrange=False)

                st.plotly_chart(
                    fig,
                    use_container_width=True,
                    config={
                        "scrollZoom": True,
                        "displayModeBar": True,
                        "modeBarButtonsToAdd": ["autoScale2d", "resetScale2d"],
                    }
                )

            # 거래 내역 테이블
            st.subheader("📝 거래 내역")

            if result.get("trades"):
                trades_df = pd.DataFrame(result["trades"])
                
                # 타임스탬프를 날짜로 변환
                trades_df["진입 시간"] = pd.to_datetime(trades_df["entry_time"], unit="ms").dt.strftime("%Y-%m-%d %H:%M")
                trades_df["청산 시간"] = pd.to_datetime(trades_df["exit_time"], unit="ms").dt.strftime("%Y-%m-%d %H:%M")
                
                # 컬럼 이름 변경 및 정렬
                display_df = trades_df[[
                    "진입 시간",
                    "청산 시간",
                    "position_type",
                    "entry_price",
                    "exit_price",
                    "quantity",
                    "pnl",
                    "fee",
                ]].copy()
                
                display_df.columns = [
                    "진입 시간",
                    "청산 시간",
                    "포지션",
                    "진입 가격",
                    "청산 가격",
                    "수량",
                    "손익 (USDT)",
                    "수수료 (USDT)",
                ]
                
                # 수치 포맷 적용
                display_df["진입 가격"] = display_df["진입 가격"].apply(lambda x: f"${x:,.2f}")
                display_df["청산 가격"] = display_df["청산 가격"].apply(lambda x: f"${x:,.2f}")
                display_df["수량"] = display_df["수량"].apply(lambda x: f"{x:.4f}")
                display_df["손익 (USDT)"] = display_df["손익 (USDT)"].apply(lambda x: f"${x:,.2f}")
                display_df["수수료 (USDT)"] = display_df["수수료 (USDT)"].apply(lambda x: f"${x:,.2f}")
                
                # 테이블 표시
                st.dataframe(
                    display_df,
                    use_container_width=True,
                    hide_index=True,
                )
                
                # 거래 요약
                total_pnl = trades_df["pnl"].sum()
                total_fee = trades_df["fee"].sum()
                avg_pnl = trades_df["pnl"].mean()
                
                summary_col1, summary_col2, summary_col3 = st.columns(3)
                
                with summary_col1:
                    st.metric("총 손익", f"${total_pnl:,.2f}")
                
                with summary_col2:
                    st.metric("평균 손익", f"${avg_pnl:,.2f}")
                
                with summary_col3:
                    st.metric("총 수수료", f"${total_fee:,.2f}")
            else:
                st.info("거래 내역이 없습니다.")

            # 상세 결과
            with st.expander("📋 상세 결과"):
                st.json(result)

        except Exception as e:
            st.error(f"❌ 백테스트 실패: {e}")
            import traceback

            st.code(traceback.format_exc())

