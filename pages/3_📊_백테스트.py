"""백테스트 페이지."""

import asyncio
import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# src 디렉토리를 Python 경로에 추가
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from backtest.context import BacktestContext
from backtest.data_fetcher import fetch_all_klines
from backtest.engine import BacktestEngine
from backtest.risk import BacktestRiskManager
from binance.client import BinanceHTTPClient
from common.risk import RiskConfig
from settings import get_settings

st.set_page_config(page_title="백테스트", page_icon="📊", layout="wide")

st.title("📊 백테스트")
st.markdown("**과거 데이터를 사용하여 전략을 테스트합니다.**")

st.divider()

st.info("""
💡 **백테스트 기능**

과거 데이터를 사용하여 전략의 성과를 검증할 수 있습니다.
- 실제 주문이 발생하지 않습니다
- 과거 데이터를 기반으로 전략을 시뮬레이션합니다
- 수수료와 레버리지를 반영합니다
- 결과를 상세히 분석할 수 있습니다
""")

st.divider()

# 전략 파일 선택
st.subheader("1️⃣ 전략 선택")

strategy_files = list(Path("scripts/strategies").glob("*_strategy.py"))
strategy_files = [p for p in strategy_files if p.name != "generated_strategy.py"]

if not strategy_files:
    st.warning("전략 파일이 없습니다.")
    st.stop()

selected_file = st.selectbox(
    "전략 파일",
    options=strategy_files,
    format_func=lambda x: x.name,
)

st.divider()

# 설정
st.subheader("2️⃣ 거래 설정")

col1, col2 = st.columns(2)

with col1:
    symbol = st.text_input("심볼", value="BTCUSDT")
    leverage = st.number_input("레버리지", min_value=1, max_value=20, value=1, step=1)
    candle_interval = st.selectbox(
        "캔들 봉 간격",
        options=["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"],
        index=5,  # 기본값: 1h
    )
    max_position = st.slider("최대 포지션 크기 (%)", min_value=10, max_value=100, value=50, step=10) / 100

with col2:
    initial_balance = st.number_input("초기 자산 (USDT)", min_value=100.0, value=1000.0, step=100.0)
    commission = st.number_input("수수료율 (%)", min_value=0.0, max_value=1.0, value=0.04, step=0.01) / 100
    
    # 날짜 선택
    today = datetime.now()
    default_start = today - timedelta(days=30)
    start_date = st.date_input(
        "시작 날짜",
        value=default_start,
        max_value=today,
    )
    end_date = st.date_input(
        "종료 날짜",
        value=today,
        max_value=today,
    )

st.divider()

# StopLoss 설정
st.subheader("🛡️ StopLoss 설정")

stop_loss_value = st.number_input(
    "StopLoss (%)",
    min_value=0.1,
    max_value=50.0,
    value=5.0,
    step=0.1,
    format="%.1f",
    help="포지션 진입 시점 balance 대비 손실률",
)
stop_loss_pct = stop_loss_value / 100.0

st.divider()

# 백테스트 설정 요약
st.subheader("3️⃣ 백테스트 설정 요약")

summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)

with summary_col1:
    st.metric("심볼", symbol)
    st.metric("레버리지", f"{leverage}x")

with summary_col2:
    st.metric("캔들 간격", candle_interval)
    st.metric("최대 포지션", f"{max_position * 100:.0f}%")

with summary_col3:
    st.metric("초기 자산", f"${initial_balance:,.0f}")
    st.metric("수수료율", f"{commission * 100:.4f}%")

with summary_col4:
    days = (end_date - start_date).days
    st.metric("기간", f"{days}일")
    st.metric("시작일", start_date.strftime("%Y-%m-%d"))
    st.metric("StopLoss", f"{stop_loss_value:.1f}%")

st.divider()

# 백테스트 실행
st.subheader("4️⃣ 백테스트 실행")


async def run_backtest_async() -> dict[str, Any]:
    """백테스트를 비동기로 실행."""
    settings = get_settings()
    
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key or "",
        api_secret=settings.binance.api_secret or "",
        base_url=settings.binance.base_url_backtest,
        timeout=60.0,
    )
    
    try:
        # 날짜를 타임스탬프로 변환
        start_ts = int(datetime.combine(start_date, datetime.min.time()).timestamp() * 1000)
        end_dt = datetime.combine(end_date, datetime.max.time().replace(microsecond=0))
        end_ts = int(end_dt.timestamp() * 1000)
        
        # 데이터 수집
        data_progress_bar = st.progress(0, text="📥 과거 데이터 수집 중...")
        klines = await fetch_all_klines(
            client=client,
            symbol=symbol,
            interval=candle_interval,
            start_ts=start_ts,
            end_ts=end_ts,
            progress_callback=lambda p: data_progress_bar.progress(p / 100, text=f"📥 과거 데이터 수집 중... {p:.1f}%"),
        )
        data_progress_bar.empty()
        
        if not klines:
            return {"error": "데이터가 없습니다.", "klines": []}
        
        # 리스크 관리자 생성
        risk_config = RiskConfig(
            max_leverage=float(leverage),
            max_position_size=max_position,
            max_order_size=max_position,
            stop_loss_pct=stop_loss_pct,
        )
        risk_manager = BacktestRiskManager(risk_config)
        
        # 백테스트 컨텍스트 생성
        ctx = BacktestContext(
            symbol=symbol,
            leverage=leverage,
            initial_balance=initial_balance,
            risk_manager=risk_manager,
            commission_rate=commission,
        )
        
        # 전략 로드
        spec = importlib.util.spec_from_file_location("custom_strategy", selected_file)
        if not spec or not spec.loader:
            return {"error": f"전략 파일을 로드할 수 없습니다: {selected_file}", "klines": klines}
        
        module = importlib.util.module_from_spec(spec)
        sys.modules["custom_strategy"] = module
        spec.loader.exec_module(module)
        
        # Strategy 클래스 찾기
        strategy_class = None
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and name.endswith("Strategy") and name != "Strategy":
                strategy_class = obj
                break
        
        if not strategy_class:
            return {"error": f"전략 클래스를 찾을 수 없습니다: {selected_file}", "klines": klines}
        
        # 전략 인스턴스 생성 (전략 파라미터는 전략 코드 내부 기본값 사용)
        try:
            strategy = strategy_class()
        except TypeError as e:
            return {"error": f"전략 인스턴스 생성 실패: {e}", "klines": klines}
        
        # 백테스트 엔진 생성 및 실행
        backtest_progress_bar = st.progress(0, text="🚀 백테스트 실행 중... 0%")
        engine = BacktestEngine(
            strategy, 
            ctx, 
            klines,
            progress_callback=lambda p: backtest_progress_bar.progress(
                p / 100, 
                text=f"🚀 백테스트 실행 중... {p:.1f}% ({len(klines)}개 캔들 처리 중)"
            ),
        )
        results = engine.run()
        backtest_progress_bar.empty()
        
        # klines 데이터를 결과에 포함
        results["klines"] = klines
        
        return results
    
    finally:
        await client.aclose()


# 백테스트 실행 버튼
if st.button("▶️ 백테스트 실행", type="primary", use_container_width=True, key="run_backtest_btn"):
    if start_date >= end_date:
        st.error("시작 날짜는 종료 날짜보다 이전이어야 합니다.")
    else:
        # 기존 결과 삭제 (메모리 해제)
        if "backtest_results" in st.session_state:
            del st.session_state.backtest_results
        
        # 백테스트 실행
        try:
            # Streamlit에서는 일반적으로 새 이벤트 루프가 없으므로 asyncio.run() 사용
            results = asyncio.run(run_backtest_async())
            # 결과를 session_state에 저장
            st.session_state.backtest_results = results
        except Exception as e:
            st.error(f"백테스트 실행 중 오류 발생: {e}")
            import traceback
            with st.expander("상세 오류 정보"):
                st.code(traceback.format_exc(), language="python")

# session_state에 결과가 있으면 표시
if "backtest_results" in st.session_state:
    results = st.session_state.backtest_results
    
    if "error" in results:
        st.error(f"오류: {results['error']}")
    else:
        st.success("백테스트가 완료되었습니다!")
        
        st.divider()
        
        # 결과 표시
        st.subheader("5️⃣ 백테스트 결과")
        
        # 거래 통계 계산
        trades = results.get("trades", [])
        profitable_trades = []
        losing_trades = []
        total_profit = 0.0
        total_loss = 0.0
        max_profit = 0.0
        max_loss = 0.0
        stoploss_exit_count = 0  # StopLoss로 인한 청산 횟수
        
        # 연속 손실/이익 추적
        max_consecutive_losses = 0
        max_consecutive_wins = 0
        current_consecutive_losses = 0
        current_consecutive_wins = 0
        
        for trade in trades:
            pnl = trade.get("pnl")
            reason = trade.get("reason", "")
            exit_reason = trade.get("exit_reason")
            
            # StopLoss로 인한 청산인지 확인 (pnl이 있는 exit 거래만 카운트)
            if pnl is not None and (exit_reason == "STOP_LOSS" or "StopLoss" in reason):
                stoploss_exit_count += 1
            
            if pnl is not None:
                if pnl > 0:
                    profitable_trades.append(pnl)
                    total_profit += pnl
                    max_profit = max(max_profit, pnl)
                    # 연속 이익 추적
                    current_consecutive_wins += 1
                    current_consecutive_losses = 0
                    max_consecutive_wins = max(max_consecutive_wins, current_consecutive_wins)
                elif pnl < 0:
                    losing_trades.append(pnl)
                    total_loss += abs(pnl)
                    max_loss = min(max_loss, pnl)  # max_loss는 음수값
                    # 연속 손실 추적
                    current_consecutive_losses += 1
                    current_consecutive_wins = 0
                    max_consecutive_losses = max(max_consecutive_losses, current_consecutive_losses)
                else:
                    # pnl이 0인 경우 연속 카운트 리셋
                    current_consecutive_losses = 0
                    current_consecutive_wins = 0
        
        total_trades_with_pnl = len(profitable_trades) + len(losing_trades)
        win_rate = (len(profitable_trades) / total_trades_with_pnl * 100) if total_trades_with_pnl > 0 else 0.0
        profit_factor = (total_profit / total_loss) if total_loss > 0 else (float('inf') if total_profit > 0 else 0.0)
        
        # 주요 지표
        result_col1, result_col2, result_col3, result_col4 = st.columns(4)
        
        with result_col1:
            delta = results.get("total_return_pct", 0)
            st.metric(
                "수익률",
                f"{delta:.2f}%",
                delta=f"{delta:.2f}%",
            )
            st.metric("초기 자산", f"${results.get('initial_balance', 0):,.2f}")
        
        with result_col2:
            final_balance = results.get("final_balance", 0)
            net_profit = results.get("net_profit", 0)
            st.metric("최종 자산", f"${final_balance:,.2f}")
            st.metric("순손익(수수료 포함)", f"{net_profit:,.2f}", delta=f"{net_profit:,.2f}")
        
        with result_col3:
            total_trades = results.get("total_trades", 0)
            total_commission = results.get("total_commission", 0)
            st.metric("총 거래 횟수", total_trades)
            st.metric("총 수수료", f"${total_commission:,.2f}")
        
        with result_col4:
            if total_trades > 0:
                avg_profit_per_trade = net_profit / total_trades
                st.metric("거래당 평균 수익", f"${avg_profit_per_trade:,.2f}")
            else:
                st.metric("거래당 평균 수익", "$0.00")
        
        st.divider()
        
        # 추가 통계 지표
        st.subheader("📊 거래 통계")
        stats_col1, stats_col2, stats_col3, stats_col4 = st.columns(4)
        
        with stats_col1:
            st.metric("승률", f"{win_rate:.1f}%")
            st.caption(f"수익 거래: {len(profitable_trades)}건 / 손실 거래: {len(losing_trades)}건")
        
        with stats_col2:
            if profit_factor == float('inf'):
                st.metric("손익비", "∞")
            else:
                st.metric("손익비", f"{profit_factor:.2f}")
            st.caption(f"총 수익: ${total_profit:,.2f} / 총 손실: ${total_loss:,.2f}")
        
        with stats_col3:
            if max_profit > 0:
                st.metric("최대 수익", f"${max_profit:,.2f}")
            else:
                st.metric("최대 수익", "$0.00")
            st.caption("개별 거래 중 최대 수익")
        
        with stats_col4:
            if max_loss < 0:
                st.metric("최대 손실", f"${max_loss:,.2f}")
            else:
                st.metric("최대 손실", "$0.00")
            st.caption("개별 거래 중 최대 손실")
        
        # 연속 거래 통계 및 StopLoss 통계
        stats_col5, stats_col6, stats_col7 = st.columns(3)
        
        with stats_col5:
            st.metric("최대 연속 손실", f"{max_consecutive_losses}회")
            st.caption("연속으로 손실이 발생한 최대 횟수")
        
        with stats_col6:
            st.metric("최대 연속 이익", f"{max_consecutive_wins}회")
            st.caption("연속으로 수익이 발생한 최대 횟수")
        
        with stats_col7:
            st.metric("StopLoss 청산 횟수", f"{stoploss_exit_count}회")
            stoploss_pct = (stoploss_exit_count / total_trades_with_pnl * 100) if total_trades_with_pnl > 0 else 0.0
            st.caption(f"전체 거래 대비 {stoploss_pct:.1f}%")
        
        st.divider()
        
        # 가격 데이터 표시
        klines = results.get("klines", [])
        if klines:
            st.subheader("📈 가격 데이터")
            
            # 가격 데이터를 DataFrame으로 변환
            price_data = []
            for kline in klines:
                open_time = int(kline[0])
                close_time = int(kline[6])
                open_price = float(kline[1])
                high_price = float(kline[2])
                low_price = float(kline[3])
                close_price = float(kline[4])
                volume = float(kline[5])
                
                price_data.append({
                    "시작 시간": datetime.fromtimestamp(open_time / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                    "종료 시간": datetime.fromtimestamp(close_time / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                    "시가": f"${open_price:,.2f}",
                    "고가": f"${high_price:,.2f}",
                    "저가": f"${low_price:,.2f}",
                    "종가": f"${close_price:,.2f}",
                    "거래량": f"{volume:,.4f}",
                })
            
            df = pd.DataFrame(price_data)
            
            # 페이지네이션 설정
            # 페이지 상태 초기화
            if "price_data_page" not in st.session_state:
                st.session_state.price_data_page = 1
            if "price_data_items_per_page" not in st.session_state:
                st.session_state.price_data_items_per_page = 100
            
            items_per_page = st.selectbox(
                "페이지당 항목 수",
                options=[50, 100, 200, 500, 1000],
                index=[50, 100, 200, 500, 1000].index(st.session_state.price_data_items_per_page) if st.session_state.price_data_items_per_page in [50, 100, 200, 500, 1000] else 1,
                key="items_per_page"
            )
            
            # 페이지당 항목 수가 변경되면 session_state 업데이트 및 페이지 재계산
            if items_per_page != st.session_state.price_data_items_per_page:
                st.session_state.price_data_items_per_page = items_per_page
                # 페이지당 항목 수가 변경되면 1페이지로 리셋
                st.session_state.price_data_page = 1
            
            total_items = len(df)
            total_pages = (total_items + items_per_page - 1) // items_per_page
            
            # 페이지당 항목 수가 변경되면 현재 페이지를 유효한 범위로 조정
            if st.session_state.price_data_page > total_pages:
                st.session_state.price_data_page = max(1, total_pages)
            
            # 페이지 선택 UI
            if total_pages > 1:
                col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
                
                with col1:
                    st.caption(f"총 {total_items:,}개 캔들 | 페이지 {st.session_state.price_data_page}/{total_pages}")
                
                with col2:
                    prev_disabled = st.session_state.price_data_page <= 1
                    if st.button("◀ 이전", key="prev_page", disabled=prev_disabled):
                        if not prev_disabled:
                            st.session_state.price_data_page = max(1, st.session_state.price_data_page - 1)
                
                with col3:
                    page = st.number_input(
                        "페이지",
                        min_value=1,
                        max_value=total_pages,
                        value=st.session_state.price_data_page,
                        step=1,
                        key="page_input",
                        label_visibility="collapsed"
                    )
                    if page != st.session_state.price_data_page:
                        st.session_state.price_data_page = page
                
                with col4:
                    next_disabled = st.session_state.price_data_page >= total_pages
                    if st.button("다음 ▶", key="next_page", disabled=next_disabled):
                        if not next_disabled:
                            st.session_state.price_data_page = min(total_pages, st.session_state.price_data_page + 1)
                
                # 페이지 범위 계산
                start_idx = (st.session_state.price_data_page - 1) * items_per_page
                end_idx = min(start_idx + items_per_page, total_items)
                
                st.caption(f"표시 중: {start_idx + 1:,} ~ {end_idx:,}번째 캔들")
                
                # 해당 페이지의 데이터만 표시
                df_page = df.iloc[start_idx:end_idx]
            else:
                df_page = df
                st.caption(f"총 {total_items:,}개 캔들")
            
            # 데이터프레임 표시
            st.dataframe(
                df_page,
                use_container_width=True,
                hide_index=True,
                height=400,
            )
            
            # CSV 다운로드 버튼
            csv = df.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                label="📥 가격 데이터 CSV 다운로드",
                data=csv,
                file_name=f"price_data_{symbol}_{candle_interval}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
                use_container_width=True,
            )
            
            st.divider()
        
        # 상세 결과
        with st.expander("📋 상세 결과 (JSON)"):
            # klines는 너무 크므로 JSON에서 제외
            results_for_json = {k: v for k, v in results.items() if k != "klines"}
            st.json(results_for_json)
        
        # 거래 내역
        trades = results.get("trades", [])
        if trades:
            st.divider()
            st.subheader("📊 거래 분석")
            include_fee = st.checkbox("수수료 적용", value=True, help="체크 해제 시 수수료를 제외한 순수 손익을 표시합니다")
            
            # 거래별 손익 및 자산 변동 데이터 구성
            initial_balance = results.get("initial_balance", 0)
            equity_data = []
            cumulative_pnl = 0.0
            cumulative_commission = 0.0
            current_balance = initial_balance
            
            # 초기 상태 (첫 거래 이전)
            first_trade = next((t for t in trades if t.get("timestamp")), None)
            if first_trade and first_trade.get("timestamp"):
                first_timestamp = first_trade.get("timestamp")
                dt_first = datetime.fromtimestamp(first_timestamp / 1000)
                equity_data.append({
                    "시점": dt_first.strftime("%Y-%m-%d %H:%M:%S"),
                    "타임스탬프": first_timestamp,
                    "누적손익": 0.0,
                    "자산": initial_balance,
                    "순손익": 0.0,
                })
            
            # 거래별로 자산 변동 추적
            # 거래 내역은 시간순으로 정렬되어 있다고 가정
            for trade in trades:
                timestamp = trade.get("timestamp", 0)
                if not timestamp:
                    continue
                
                pnl = trade.get("pnl")
                commission = trade.get("commission", 0.0) or 0.0
                effective_commission = commission if include_fee else 0.0
                side = trade.get("side", "")
                
                # 거래 처리
                if side == "BUY":
                    if pnl is not None and pnl != 0:
                        # BUY (숏 청산): pnl - commission
                        current_balance += (pnl - effective_commission)
                        cumulative_pnl += pnl
                    else:
                        # BUY (롱 진입/추가): 수수료만 차감
                        current_balance -= effective_commission
                elif side == "SELL":
                    if pnl is not None and pnl != 0:
                        # SELL (롱 청산): pnl - commission
                        current_balance += (pnl - effective_commission)
                        cumulative_pnl += pnl
                    else:
                        # SELL (숏 진입): 수수료만 차감
                        current_balance -= effective_commission
                
                cumulative_commission += effective_commission
                
                dt = datetime.fromtimestamp(timestamp / 1000)
                equity_data.append({
                    "시점": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "타임스탬프": timestamp,
                    "누적손익": cumulative_pnl,
                    "자산": current_balance,
                    "순손익": current_balance - initial_balance,
                })
            
            # 차트 데이터 준비 - 거래가 발생한 시점만 추출
            # 거래별 손익 및 해당 시점의 자산 데이터 추출 (pnl이 있는 거래만)
            trade_chart_data = []
            
            current_balance_for_chart = initial_balance
            
            for trade in trades:
                timestamp = trade.get("timestamp", 0)
                if not timestamp:
                    continue
                
                pnl = trade.get("pnl")
                commission = trade.get("commission", 0.0) or 0.0
                effective_commission = commission if include_fee else 0.0
                side = trade.get("side", "")
                
                # 거래 처리하여 자산 업데이트
                if side == "BUY":
                    if pnl is not None and pnl != 0:
                        # BUY (숏 청산): pnl - commission
                        current_balance_for_chart += (pnl - effective_commission)
                    else:
                        # BUY (롱 진입/추가): 수수료만 차감
                        current_balance_for_chart -= effective_commission
                elif side == "SELL":
                    if pnl is not None and pnl != 0:
                        # SELL (롱 청산): pnl - commission
                        current_balance_for_chart += (pnl - effective_commission)
                    else:
                        # SELL (숏 진입): 수수료만 차감
                        current_balance_for_chart -= effective_commission
                
                # pnl이 있는 거래만 차트 데이터에 추가
                if pnl is not None and pnl != 0:
                    dt = datetime.fromtimestamp(timestamp / 1000)
                    trade_chart_data.append({
                        "datetime": pd.to_datetime(timestamp, unit="ms"),
                        "pnl": pnl,
                        "equity": current_balance_for_chart,
                    })
            
            df_trade_chart = pd.DataFrame(trade_chart_data)
            
            if len(df_trade_chart) > 0:
                # 시간순 정렬
                df_trade_chart = df_trade_chart.sort_values("datetime")
                
                # 거래 순서 인덱스 추가 (1부터 시작)
                df_trade_chart["trade_index"] = range(1, len(df_trade_chart) + 1)
                
                # 이중 Y축 차트 생성
                fig = make_subplots(specs=[[{"secondary_y": True}]])
                
                # 거래별 손익 바 차트 (왼쪽 Y축) - 거래 순서로 표시
                colors = ["#2ecc71" if pnl > 0 else "#e74c3c" for pnl in df_trade_chart["pnl"]]
                
                # 호버 템플릿에 날짜 정보 포함
                hover_texts = []
                for idx, row in df_trade_chart.iterrows():
                    dt_str = row["datetime"].strftime("%Y-%m-%d %H:%M:%S")
                    hover_texts.append(f"거래 #{row['trade_index']}<br>{dt_str}<br>손익: ${row['pnl']:,.2f}")
                
                fig.add_trace(
                    go.Bar(
                        x=df_trade_chart["trade_index"],
                        y=df_trade_chart["pnl"],
                        name="거래별 손익",
                        marker_color=colors,
                        hovertemplate="%{customdata}<extra></extra>",
                        customdata=hover_texts,
                        width=0.6,  # 일정한 너비로 설정
                    ),
                    secondary_y=False,
                )
                
                # 자산 변동 선형 차트 (오른쪽 Y축) - 거래 순서로 표시
                equity_hover_texts = []
                for idx, row in df_trade_chart.iterrows():
                    dt_str = row["datetime"].strftime("%Y-%m-%d %H:%M:%S")
                    equity_hover_texts.append(f"거래 #{row['trade_index']}<br>{dt_str}<br>자산: ${row['equity']:,.2f}")
                
                fig.add_trace(
                    go.Scatter(
                        x=df_trade_chart["trade_index"],
                        y=df_trade_chart["equity"],
                        name="자산",
                        mode="lines+markers",
                        line=dict(color="#3498db", width=2),
                        marker=dict(size=6),
                        hovertemplate="%{customdata}<extra></extra>",
                        customdata=equity_hover_texts,
                    ),
                    secondary_y=True,
                )
                
                # 축 레이블 설정
                fig.update_xaxes(title_text="거래 순서")
                fig.update_yaxes(title_text="거래별 손익 (USDT)", secondary_y=False)
                fig.update_yaxes(title_text="자산 (USDT)", secondary_y=True)
                
                # 레이아웃 설정
                fig.update_layout(
                    title="자산 변동 및 거래별 손익",
                    height=500,
                    hovermode="x unified",
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=1.02,
                        xanchor="right",
                        x=1,
                    ),
                    xaxis=dict(
                        type="linear",  # 선형 타입으로 설정하여 일정한 간격 유지
                        dtick=1,  # 거래마다 눈금 표시
                    ),
                )
                
                st.plotly_chart(fig, use_container_width=True)
                
                # 요약 정보
                initial_equity = initial_balance
                final_equity = df_trade_chart["equity"].iloc[-1] if len(df_trade_chart) > 0 else initial_balance
                total_pnl = cumulative_pnl
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("초기 자산", f"${initial_equity:,.2f}")
                with col2:
                    st.metric("최종 자산", f"${final_equity:,.2f}", delta=f"${final_equity - initial_equity:+,.2f}")
                with col3:
                    fee_label = f"수수료: ${cumulative_commission:,.2f}" if include_fee else "수수료 미적용"
                    st.metric("총 손익", f"${total_pnl:+,.2f}", fee_label)
            
            st.divider()
            
            with st.expander(f"📋 거래 내역 ({len(trades)}건)"):
                # 거래 내역을 테이블로 표시
                trade_data = []
                previous_balance = initial_balance
                
                for i, trade in enumerate(trades, 1):
                    # 타임스탬프를 초 단위까지 표시
                    timestamp = trade.get("timestamp", 0)
                    if timestamp:
                        dt = datetime.fromtimestamp(timestamp / 1000)
                        time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        time_str = "-"
                    
                    side = trade.get("side", "")
                    pnl = trade.get("pnl")
                    position_size_usdt = trade.get("position_size_usdt")
                    entry_price = trade.get("entry_price")
                    balance_after = trade.get("balance_after")
                    
                    # Exit 거래(pnl이 있는 거래)인지 확인
                    is_exit = pnl is not None and pnl != 0
                    
                    # 자산 변동 계산 (exit 거래인 경우만)
                    balance_change = None
                    if is_exit and balance_after is not None:
                        balance_change = balance_after - previous_balance
                        previous_balance = balance_after
                    elif balance_after is not None:
                        previous_balance = balance_after
                    
                    trade_data.append({
                        "#": i,
                        "시점": time_str,
                        "구분": side,
                        "수량": f"{trade.get('quantity', 0):.6f}",
                        "체결가": f"${trade.get('price', 0):,.2f}",
                        "포지션 크기 (USDT)": f"${position_size_usdt:,.2f}" if position_size_usdt else "-",
                        "평균 진입가": f"${entry_price:,.2f}" if entry_price else "-",
                        "손익": f"${pnl:,.2f}" if pnl is not None else "-",
                        "수수료": f"${trade.get('commission', 0):,.4f}",
                        "자산 변동": f"${balance_change:+,.2f}" if balance_change is not None else "-",
                        "거래 후 자산": f"${balance_after:,.2f}" if balance_after is not None else "-",
                        "사유": trade.get("reason", ""),
                    })
                
                st.dataframe(trade_data, use_container_width=True, hide_index=True)
        
        # 결과 다운로드
        st.divider()
        st.subheader("💾 결과 다운로드")
        
        # klines를 제외한 결과를 JSON으로 변환
        results_for_json = {k: v for k, v in results.items() if k != "klines"}
        results_json = json.dumps(results_for_json, indent=2, ensure_ascii=False, default=str)
        st.download_button(
            label="📥 JSON 파일로 다운로드",
            data=results_json,
            file_name=f"backtest_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json",
            use_container_width=True,
        )
        
        # 새로 실행 버튼
        if st.button("🔄 새로 실행", use_container_width=True, key="rerun_backtest"):
            # session_state 초기화
            if "backtest_results" in st.session_state:
                del st.session_state.backtest_results
            if "price_data_page" in st.session_state:
                del st.session_state.price_data_page
            if "price_data_items_per_page" in st.session_state:
                del st.session_state.price_data_items_per_page
            st.rerun()

st.divider()

# 추가 안내
st.subheader("📚 추가 정보")

with st.expander("백테스트 가정사항"):
    st.markdown("""
    ### 백테스트 시 가정
    
    1. **체결 가격**
       - 모든 주문은 시장가로 즉시 체결된다고 가정합니다
       - 실제 슬리피지는 고려하지 않습니다
    
    2. **수수료**
       - 기본값은 0.04% (taker 수수료)입니다
       - 필요시 수정할 수 있습니다
    
    3. **데이터**
       - 바이낸스 API에서 제공하는 과거 캔들 데이터를 사용합니다
       - 최대 1500개씩 나누어 요청하여 전체 기간을 수집합니다
    
    4. **지표 계산**
       - RSI, SMA, EMA 등 지표는 닫힌 봉 기준으로 계산됩니다
       - 라이브 트레이딩과 동일한 방식으로 계산됩니다
    
    5. **포지션 관리**
       - 평균 진입가를 사용하여 포지션을 관리합니다
       - 레버리지가 적용된 명목가치 기준으로 리스크를 관리합니다
    """)

with st.expander("백테스트 결과 해석"):
    st.markdown("""
    ### 주요 지표 설명
    
    1. **수익률 (Total Return)**
       - 초기 자산 대비 최종 자산의 변동률
       - 백분율로 표시됩니다
    
    2. **순손익 (Net Profit)**
       - 초기 자산과 최종 자산의 차이
       - 수수료를 반영한 실제 손익입니다
    
    3. **총 거래 횟수**
       - 청산(SELL) 거래의 횟수
       - 진입과 청산이 한 쌍으로 카운트됩니다
    
    4. **총 수수료**
       - 모든 거래에서 발생한 수수료 합계
       - 백테스트 기간 동안의 총 비용입니다
    
    5. **거래당 평균 수익**
       - 순손익을 거래 횟수로 나눈 값
       - 전략의 거래 효율성을 나타냅니다
    """)

with st.expander("자주 묻는 질문"):
    st.markdown("""
    ### Q: 백테스트와 라이브 트레이딩의 차이는?
    A: 백테스트는 과거 데이터를 기반으로 시뮬레이션하는 반면, 라이브 트레이딩은 실제 주문을 실행합니다.
    
    ### Q: 백테스트 결과가 좋으면 라이브도 좋을까요?
    A: 백테스트는 과거 데이터를 기반으로 하므로 미래 수익을 보장하지 않습니다. 과최적화에 주의하세요.
    
    ### Q: 얼마나 많은 데이터를 사용할 수 있나요?
    A: 바이낸스 API는 최대 1500개씩 요청할 수 있으므로, 필요한 만큼 자동으로 여러 번 요청합니다.
    
    ### Q: 백테스트 속도는?
    A: 데이터 수집 시간과 캔들 개수에 비례합니다. 긴 기간(예: 1년)은 몇 분이 걸릴 수 있습니다.
    """)
