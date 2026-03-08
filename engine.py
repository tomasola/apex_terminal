import ccxt
import pandas as pd
import logging
import time
import numpy as np
import json
import os
import requests
import datetime
from indicators import calculate_rsi_divergence, calculate_stochastic_supertrend, calculate_macd, calculate_adx, calculate_ema, calculate_rsi, calculate_bollinger_bands
class TradeEngine:
    def __init__(self, api_key, api_secret, testnet=True):
        self.exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'spot',
                'adjustForTimeDifference': True
            }
        })
        if testnet:
            self.exchange.set_sandbox_mode(True)
            
        self.symbols = [
            'BTC/USDC', 'ETH/USDC', 'SOL/USDC', 'BNB/USDC', 'XRP/USDC', 
            'ADA/USDC', 'DOT/USDC', 'POL/USDC', 'LINK/USDC',
            'UNI/USDC', 'LTC/USDC', 'BCH/USDC'
        ]
        self.auto_symbols = self.symbols[:] # Initially all enabled
        self.timeframes = ['1m', '5m', '15m', '1h', '1d']
        
        # Trading State
        self.trading_mode = "OFF" # OFF, SIM, REAL
        self.trade_history = []
        self.active_positions = {} # {symbol: {side, entry_price, amount, timestamp}}
        self.history_file = "trade_history.json"
        self.load_history()
        
        self.history = {
            s: {
                tf: {
                    'candles': [], 
                    'rsi_div': [], 
                    'stoch_rsi': [], 
                    'st_trend': [], 
                    'signals': [],
                    'sentiment': 50 # Default sentiment
                } for tf in self.timeframes
            } for s in self.symbols
        }
        self.current_stats = {s: {tf: {} for tf in self.timeframes} for s in self.symbols}
        
        # Default Indicator Parameters
        self.params = {
            'rsi_fast': 5,
            'rsi_slow': 14,
            'stoch_rsi_len': 14,
            'stoch_k_period': 14,
            'stoch_smooth_k': 3,
            'st_factor': 3.0,
            'investment_amount': 20.0,
            'trading_timeframe': '1h',
            'stop_loss_pct': 5.0,
            'trailing_stop': False,
            'active_strategy': 1,
            'ema_fast': 9,
            'ema_slow': 21,
            'macd_fast': 12,
            'macd_slow': 26,
            'macd_signal': 9,
            'adx_period': 14,
            'adx_threshold': 25,
            # Strategy 4 Parameters
            'rsi_fast_4': 5,
            'rsi_slow_4': 14,
            'rsi_offset': 0,
            'st_len_4': 14,
            'st_factor_4': 3.0,
            'stoch_offset': 30
        }

    def send_telegram(self, msg):
        token = os.environ.get('TELEGRAM_BOT_TOKEN')
        chat_id = os.environ.get('TELEGRAM_CHAT_ID')
        if token and chat_id:
            try:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                # Send quickly
                requests.post(url, json={'chat_id': chat_id, 'text': msg}, timeout=3)
            except Exception as e:
                logging.error(f"Telegram err: {e}")

    def fetch_ohlcv(self, symbol, timeframe='1h', limit=250):
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            logging.error(f"Error fetching OHLCV for {symbol} ({timeframe}): {e}")
            return None

    def analyze(self, symbol, timeframe='1h'):
        df = self.fetch_ohlcv(symbol, timeframe)
        if df is None or len(df) < 50:
            return
            
        # Get Strategy Signal using modular logic
        signal, indicators_data = self.get_strategy_signal(df, self.params['active_strategy'])
        
        # Execute Trade Logic (only on the selected trading timeframe)
        if timeframe == self.params.get('trading_timeframe', '1h'):
            self.execute_trade_logic(symbol, signal, df['close'].iloc[-1])

        # Standard indicators for sentiment and stats (Independent of strategy)
        df['rsi'] = calculate_rsi(df['close'], period=14)
        df['ema_fast'] = calculate_ema(df['close'], length=self.params['ema_fast'])
        df['ema_slow'] = calculate_ema(df['close'], length=self.params['ema_slow'])
        
        # Calculate Bollinger Bands
        upper_bb, sma_bb, lower_bb = calculate_bollinger_bands(df['close'], period=20, std_dev=2)
        df['upper_bb'] = upper_bb
        df['sma_bb'] = sma_bb
        df['lower_bb'] = lower_bb

        rsi_div = indicators_data.get('rsi_div')
        stoch_k = indicators_data.get('stoch_rsi')
        st_trend = indicators_data.get('st_trend', pd.Series(0.0, index=df.index))
        st_signals = indicators_data.get('signals', pd.Series("", index=df.index))
        st_dir = indicators_data.get('st_dir', pd.Series(0, index=df.index))
        
        # BULLETPROOF TIMESTAMP CONVERSION
        timestamps = df.index.values.astype('datetime64[s]').astype(np.int64).tolist()
        
        candles = []
        for i in range(len(df)):
            # Handle NaNs in BB returning None for json serialization
            ubb = float(df['upper_bb'].iloc[i]) if not pd.isna(df['upper_bb'].iloc[i]) else None
            mbb = float(df['sma_bb'].iloc[i]) if not pd.isna(df['sma_bb'].iloc[i]) else None
            lbb = float(df['lower_bb'].iloc[i]) if not pd.isna(df['lower_bb'].iloc[i]) else None
            
            # EMA Handle NaNs
            ef = float(df['ema_fast'].iloc[i]) if not pd.isna(df['ema_fast'].iloc[i]) else None
            es = float(df['ema_slow'].iloc[i]) if not pd.isna(df['ema_slow'].iloc[i]) else None
            
            candles.append({
                'time': int(timestamps[i]),
                'open': float(df['open'].iloc[i]),
                'high': float(df['high'].iloc[i]),
                'low': float(df['low'].iloc[i]),
                'close': float(df['close'].iloc[i]),
                'volume': float(df['volume'].iloc[i]),
                'upper_bb': ubb,
                'sma_bb': mbb,
                'lower_bb': lbb,
                'ema_fast': ef,
                'ema_slow': es
            })
            
        rsi_data = []
        if rsi_div is not None:
            for i in range(len(rsi_div)):
                val = rsi_div.iloc[i]
                rsi_data.append({'time': int(timestamps[i]), 'value': float(val) if not pd.isna(val) else 0.0})
            
        stoch_data = []
        trend_data = []
        if stoch_k is not None:
            for i in range(len(stoch_k)):
                k_val = stoch_k.iloc[i]
                stoch_data.append({'time': int(timestamps[i]), 'value': float(k_val) if not pd.isna(k_val) else 0.0})
        
        if st_trend is not None:
            for i in range(len(st_trend)):
                t_val = st_trend.iloc[i]
                trend_data.append({'time': int(timestamps[i]), 'value': float(t_val) if not pd.isna(t_val) else 0.0})
            
        signal_markers = []
        if st_signals is not None:
            for i in range(len(st_signals)):
                if st_signals.iloc[i] != "":
                    signal_markers.append({
                        'time': int(timestamps[i]),
                        'position': 'belowBar' if st_signals.iloc[i] == 'BUY' else 'aboveBar',
                        'color': '#14c79a' if st_signals.iloc[i] == 'BUY' else '#e42c69',
                        'shape': 'arrowUp' if st_signals.iloc[i] == 'BUY' else 'arrowDown',
                        'text': st_signals.iloc[i],
                        'size': 2
                    })
        
        # Sentiment Scoring (0 to 100)
        sentiment = 50 # Start neutral
        
        # RSI Contribution
        if not df['rsi'].empty and df['rsi'].iloc[-1] < 30: sentiment += 15
        if not df['rsi'].empty and df['rsi'].iloc[-1] > 70: sentiment -= 15
        
        # Stoch Contribution
        if stoch_k is not None and not stoch_k.empty and stoch_k.iloc[-1] < 20: sentiment += 15
        if stoch_k is not None and not stoch_k.empty and stoch_k.iloc[-1] > 80: sentiment -= 15
        
        # Supertrend Contribution
        if signal_markers and signal_markers[-1]['text'] == 'BUY': sentiment += 20
        if signal_markers and signal_markers[-1]['text'] == 'SELL': sentiment -= 20
        
        # EMA Strategy Contribution (Opt 2)
        ema_buy = False
        ema_sell = False
        if not df['ema_fast'].empty and not df['ema_slow'].empty:
            if df['ema_fast'].iloc[-1] > df['ema_slow'].iloc[-1] and df['ema_fast'].iloc[-2] <= df['ema_slow'].iloc[-2]:
                ema_buy = True
            elif df['ema_fast'].iloc[-1] < df['ema_slow'].iloc[-1] and df['ema_fast'].iloc[-2] >= df['ema_slow'].iloc[-2]:
                ema_sell = True

        if ema_buy: sentiment += 25
        if ema_sell: sentiment -= 25
        
        # Clamp sentiment
        sentiment = max(0, min(100, sentiment))

        hist = self.history[symbol][timeframe]
        hist['candles'] = candles
        hist['rsi_div'] = rsi_data
        hist['stoch_rsi'] = stoch_data
        hist['st_trend'] = trend_data
        hist['signals'] = signal_markers
        hist['sentiment'] = sentiment # Add sentiment to history
        
        # Get latest values Safely for Stats
        curr_rsi_div = rsi_div.iloc[-1] if rsi_div is not None and not rsi_div.empty else 0.0
        curr_stoch = stoch_k.iloc[-1] if stoch_k is not None and not stoch_k.empty else 50.0
        curr_st_dir = st_dir.iloc[-1] if st_dir is not None and not st_dir.empty else 0

        self.current_stats[symbol][timeframe] = {
            'price': float(df['close'].iloc[-1]),
            'rsi_div': float(curr_rsi_div),
            'stoch_k': float(curr_stoch),
            'direction': int(curr_st_dir),
            'signal': signal,
            'timeframe': timeframe,
            'sentiment': sentiment,
            'confluence': indicators_data.get('confluence')
        }

    def run_cycle(self):
        for s in self.symbols:
            for tf in self.timeframes:
                try:
                    self.analyze(s, tf)
                except Exception as e:
                    logging.error(f"Analysis error for {s} ({tf}): {e}")

        # Check Stop Loss & Take Profit for all active positions
        self.check_risk_management()


    def execute_trade_logic(self, symbol, signal, price):
        if self.trading_mode == "OFF":
            return
            
        if symbol not in self.auto_symbols:
            return

        # Check for open position
        has_pos = symbol in self.active_positions
        
        # BUY Logic
        if signal == "BUY" and not has_pos:
            self.open_position(symbol, price)
        
        # SELL Logic
        elif signal == "SELL" and has_pos:
            self.close_position(symbol, price)

    def open_position(self, symbol, price):
        amount = self.params.get('investment_amount', 100.0)
        qty = amount / price
        
        pos = {
            'symbol': symbol,
            'side': 'BUY',
            'entry_price': price,
            'qty': qty,
            'entry_time': time.strftime('%H:%M:%S'),
            'entry_timestamp': int(time.time()),
            'mode': self.trading_mode
        }
        
        if self.trading_mode == "REAL":
            try:
                # Actual Binance Order
                order = self.exchange.create_market_buy_order(symbol, qty)
                logging.info(f"REAL BUY ORDER PLACED: {symbol} @ {price}")
                pos['order_id'] = order['id']
                
                # OCO Logic
                sl_pct = float(self.params.get('stop_loss_pct', 5.0))
                tp_pct = float(self.params.get('take_profit_pct', 0.0))
                
                if sl_pct > 0 and tp_pct > 0:
                    try:
                        stop_price = price * (1 - (sl_pct / 100))
                        limit_price = price * (1 + (tp_pct / 100))
                        
                        oco_params = {
                            'stopPrice': self.exchange.price_to_precision(symbol, stop_price),
                            'stopLimitPrice': self.exchange.price_to_precision(symbol, stop_price),
                            'stopLimitTimeInForce': 'GTC'
                        }
                        # Place OCO (Limit Maker for TP, Stop Limit for SL)
                        oco_order = self.exchange.create_order(
                            symbol, 'limit', 'sell', qty, 
                            self.exchange.price_to_precision(symbol, limit_price), 
                            oco_params
                        )
                        logging.info(f"REAL OCO PLACED: {symbol} TP @ {limit_price}, SL @ {stop_price}")
                        pos['oco_id'] = oco_order['id'] # Track OCO
                    except Exception as eco:
                        logging.error(f"Failed to place OCO for {symbol}: {eco}")

            except Exception as e:
                err_msg = f"FALLO COMPRA REAL para {symbol}: {e}"
                logging.error(err_msg)
                return False, err_msg

        self.active_positions[symbol] = pos
        self.save_history()
        msg = f"🟢 Posición ABIERTA [{self.trading_mode}]: {symbol} @ {price}"
        logging.info(msg)
        self.send_telegram(msg)
        return True, msg

    def close_position(self, symbol, price):
        if symbol not in self.active_positions:
            return False, f"No hay posición activa para {symbol}"
            
        pos = self.active_positions.pop(symbol)
        entry_price = pos['entry_price']
        qty = pos['qty']
        
        # Calculate PnL
        pnl_val = (price - entry_price) * qty
        pnl_pct = (price - entry_price) / entry_price * 100
        
        trade = {
            'symbol': symbol,
            'mode': pos['mode'],
            'entry_price': entry_price,
            'exit_price': price,
            'pnl_val': round(pnl_val, 2),
            'pnl_pct': round(pnl_pct, 2),
            'entry_time': pos['entry_time'],
            'exit_time': time.strftime('%H:%M:%S'),
            'exit_timestamp': int(time.time()), # Added for daily PnL calculation
            'status': 'WIN' if pnl_pct > 0 else 'LOSS'
        }
        
        if self.trading_mode == "REAL":
            try:
                # Cancel OCO if it exists before selling manually
                if 'oco_id' in pos:
                    try:
                        self.exchange.cancel_order(pos['oco_id'], symbol)
                        logging.info(f"Cancelled dangling OCO {pos['oco_id']} for {symbol}")
                    except Exception as eco:
                        logging.warning(f"Could not cancel OCO {pos['oco_id']} for {symbol}: {eco}")

                # Actual Binance Order
                self.exchange.create_market_sell_order(symbol, qty)
                logging.info(f"REAL SELL ORDER PLACED: {symbol} @ {price}")
            except Exception as e:
                err_msg = f"FALLO VENTA REAL para {symbol}: {e}"
                logging.error(err_msg)
                # Note: We still popped the position, but it failed on exchange.
                return False, err_msg
        
        self.trade_history.append(trade)
        self.save_history()
        status_icon = "🔵" if pnl_pct > 0 else "🔴"
        msg = f"{status_icon} Posición CERRADA [{pos['mode']}]: {symbol} PnL: {trade['pnl_pct']}%"
        logging.info(msg)
        self.send_telegram(msg)
        return True, msg

    def get_trade_history(self):
        # Calculate total performance
        total_pnl = sum([t['pnl_pct'] for t in self.trade_history])
        wins = [t for t in self.trade_history if t.get('pnl_pct', 0) > 0]
        win_rate = (len(wins) / len(self.trade_history) * 100) if self.trade_history else 0
        
        # Calculate Daily PnL (approximate for last 24h)
        now_ts = time.time()
        daily_trades = [t for t in self.trade_history if now_ts - t.get('exit_timestamp', 0) < 86400]
        daily_pnl = sum([t.get('pnl_pct', 0) for t in daily_trades])

        # Prepare profit curve data
        balance = 1000.0 # Virtual starting balance for curve
        profit_curve = [{'time': int(time.time() - 86400 * 30), 'value': balance}]
        cumulative_pnl = 0
        
        # Sort history by exit time for the curve
        sorted_hist = sorted(self.trade_history, key=lambda x: x.get('exit_timestamp', 0))
        for t in sorted_hist:
            cumulative_pnl += t.get('pnl_pct', 0) / 100 * balance # Assuming pnl_pct is percentage of initial investment
            profit_curve.append({
                'time': t.get('exit_timestamp', int(time.time())),
                'value': round(balance + cumulative_pnl, 2)
            })

        return {
            'history': self.trade_history[::-1], # Newest first
            'total_pnl': round(total_pnl, 2),
            'daily_pnl': round(daily_pnl, 2),
            'win_rate': round(win_rate, 1),
            'profit_curve': profit_curve,
            'trades_count': len(self.trade_history),
            'active_count': len(self.active_positions),
            'active_positions': self.active_positions
        }

    def get_bot_status(self):
        """Returns a human-readable string of what the bot is currently doing."""
        if self.trading_mode == "OFF":
            return "ESPERA: Trading APAGADO"
            
        status = f"MODO: {self.trading_mode} | ESTRAT: {self.params.get('active_strategy', 1)}"
        
        if self.active_positions:
            symbols = ", ".join(self.active_positions.keys())
            status += f" | ABIERTO: {symbols} (Esperando VENTA)"
        else:
            status += " | ESCANEANDO (Esperando COMPRA)"
            
        return status

    def get_balance(self):
        try:
            balance = self.exchange.fetch_balance()
            # Total = Spot (free) + Earn (Flexible Savings usually starts with LD)
            usdc_spot = balance.get('USDC', {}).get('free', 0.0)
            usdc_earn = balance.get('LDUSDC', {}).get('total', 0.0)
            
            total = usdc_spot + usdc_earn
            
            # Fallback to USDT if USDC is completely 0 (for diagnostics)
            if total == 0:
                usdt_spot = balance.get('USDT', {}).get('free', 0.0)
                if usdt_spot > 0:
                    logging.info(f"USDC is 0, but found {usdt_spot} USDT. User might need to swap.")
                    return round(usdt_spot, 2)
            
            return round(total, 2)
        except Exception as e:
            logging.error(f"Error fetching balance from Binance: {e}")
            return 0.0

    def check_risk_management(self):
        sl_threshold = float(self.params.get('stop_loss_pct', 5.0))
        tp_threshold = float(self.params.get('take_profit_pct', 0.0))
        
        if sl_threshold <= 0 and tp_threshold <= 0:
            return

        symbols_to_close = []
        for symbol, pos in self.active_positions.items():
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                current_price = ticker['last']
                entry_price = pos['entry_price']
                
                # Calculate current PnL %
                pnl_pct = (current_price - entry_price) / entry_price * 100
                
                # TAKE PROFIT LOGIC
                if tp_threshold > 0 and pnl_pct >= tp_threshold:
                    logging.warning(f"TAKE PROFIT HIT for {symbol}: {pnl_pct:.2f}% (Target: {tp_threshold}%)")
                    symbols_to_close.append((symbol, current_price, 'WIN (TP)'))
                    continue

                # TRAILING STOP LOGIC
                is_trailing = self.params.get('trailing_stop', False)
                if is_trailing and sl_threshold > 0 and pnl_pct > 1.0: # Start trailing after 1% profit
                    highest_seen = pos.get('highest_price', entry_price)
                    if current_price > highest_seen:
                        pos['highest_price'] = current_price
                        logging.info(f"Trailing SL moved up for {symbol} to -{sl_threshold}% from new peak {current_price}")
                    
                    # Effective SL based on highest price
                    trail_pnl_pct = (current_price - pos['highest_price']) / pos['highest_price'] * 100
                    if trail_pnl_pct <= -sl_threshold:
                        logging.warning(f"TRAILING STOP HIT for {symbol}: {trail_pnl_pct:.2f}% (Peak: {pos['highest_price']})")
                        symbols_to_close.append((symbol, current_price, 'LOSS (TRAIL)'))
                        continue

                # STANDARD STOP LOSS LOGIC
                if sl_threshold > 0 and pnl_pct <= -sl_threshold:
                    logging.warning(f"STOP LOSS HIT for {symbol}: {pnl_pct:.2f}% (Limit: -{sl_threshold}%)")
                    symbols_to_close.append((symbol, current_price, 'LOSS (STOP)'))
                    
            except Exception as e:
                logging.error(f"Error checking TP/SL for {symbol}: {e}")

        for symbol, price, reason in symbols_to_close:
            self.close_position(symbol, price)
            # Add a tag to the last trade to indicate it was a TP/SL
            if self.trade_history:
                self.trade_history[-1]['status'] = reason
                self.save_history()

    def get_watchlist(self):
        symbols = self.symbols
        watchlist = []
        try:
            tickers = self.exchange.fetch_tickers(symbols)
            for s in symbols:
                if s in tickers:
                    t = tickers[s]
                    watchlist.append({
                        'symbol': s,
                        'price': t['last'],
                        'change': round(t.get('percentage', 0), 2),
                        'is_auto': s in self.auto_symbols
                    })
        except Exception as e:
            logging.error(f"Watchlist error: {e}")
        return watchlist

    def run_backtest(self, symbol, tf='1h'):
        """Simulate strategy on historical data"""
        logging.info(f"Running REAL backtest for {symbol} ({tf})")
        df = self.fetch_ohlcv(symbol, tf, limit=500)
        if df is None or len(df) < 50: return {"error": "Insufficient data"}
        
        # Strategy loop
        balance = 1000.0
        initial_balance = balance
        pos = None
        trades = 0
        wins = 0
        
        # We start from index 50 to have enough history for indicators
        for i in range(50, len(df)):
            chunk = df.iloc[:i+1] # Grow data
            sig, _ = self.get_strategy_signal(chunk, self.params['active_strategy'])
            price = df['close'].iloc[i]
            
            if sig == "BUY" and pos is None:
                pos = price
            elif sig == "SELL" and pos is not None:
                pnl = (price - pos) / pos
                balance += balance * pnl
                trades += 1
                if pnl > 0: wins += 1
                pos = None
        
        net_profit = (balance - initial_balance) / initial_balance * 100
        return {
            "symbol": symbol,
            "period": f"Last {len(df)} candles",
            "net_profit": f"{net_profit:.2f}%",
            "trades": trades,
            "win_rate": f"{(wins/trades*100) if trades > 0 else 0:.1f}%",
            "final_balance": f"${balance:.2f}"
        }

    def close_all_positions(self):
        """Emergency Liquidate everything"""
        symbols = list(self.active_positions.keys())
        logging.info(f"PANIC BUTTON: Closing {len(symbols)} positions")
        for s in symbols:
            # We need current price, try to fetch it or use last stored
            try:
                ticker = self.exchange.fetch_ticker(s)
                price = ticker['last']
                self.close_position(s, price)
                # Mark as panic
                if self.trade_history:
                    self.trade_history[-1]['status'] = 'MANUAL (PANIC)'
            except Exception as e:
                logging.error(f"Panic close fail for {s}: {e}")
        self.save_history()

    def clear_trade_history(self):
        """Remove all trade records"""
        self.trade_history = []
        self.save_history()
        logging.info("Trade history cleared.")

    def delete_trade(self, index):
        """Remove a specific trade record by index"""
        if 0 <= index < len(self.trade_history):
            deleted = self.trade_history.pop(index)
            self.save_history()
            logging.info(f"Deleted trade: {deleted}")
            return True
        return False

    def get_strategy_signal(self, df, strategy_id):
        """Modular logic to get signals based on strategy ID"""
        signal = "HOLD"
        data = {}
        
        if strategy_id == 1: # RSI Div + Stoch Supertrend
            rsi_div = calculate_rsi_divergence(df, self.params['rsi_fast'], self.params['rsi_slow'])
            stoch_k, st_trend, st_dir, st_signals = calculate_stochastic_supertrend(df, self.params['stoch_rsi_len'], self.params['stoch_k_period'], self.params['stoch_smooth_k'], self.params['st_factor'])
            
            latest_rsi_div = rsi_div.iloc[-1]
            latest_st_signal = st_signals.iloc[-1]
            if latest_st_signal == "BUY" and latest_rsi_div > 0: signal = "BUY"
            elif latest_st_signal == "SELL" and latest_rsi_div < 0: signal = "SELL"
            
            # Real-time Confluence Diagnostic for Strategy 1
            confluence = {
                'buy': {
                    'trend_ok': bool(latest_st_signal == "BUY"),
                    'stoch_ok': True, # Not used in S1
                    'rsi_ok': bool(latest_rsi_div > 0)
                },
                'sell': {
                    'trend_ok': bool(latest_st_signal == "SELL"),
                    'stoch_ok': True, # Not used in S1
                    'rsi_ok': bool(latest_rsi_div < 0)
                }
            }

            # Create synchronized signals for visualization
            sync_signals = pd.Series("", index=df.index)
            for i in range(len(df)):
                if st_signals.iloc[i] == "BUY" and rsi_div.iloc[i] > 0:
                    sync_signals.iloc[i] = "BUY"
                elif st_signals.iloc[i] == "SELL" and rsi_div.iloc[i] < 0:
                    sync_signals.iloc[i] = "SELL"

            data = {
                'rsi_div': rsi_div,
                'stoch_rsi': stoch_k,
                'st_trend': st_trend,
                'signals': sync_signals,
                'confluence': confluence
            }
            
        elif strategy_id == 2: # EMA Cross
            ema_f = calculate_ema(df['close'], self.params['ema_fast'])
            ema_s = calculate_ema(df['close'], self.params['ema_slow'])
            
            sync_signals = pd.Series("", index=df.index)
            for i in range(1, len(df)):
                if ema_f.iloc[i] > ema_s.iloc[i] and ema_f.iloc[i-1] <= ema_s.iloc[i-1]:
                    sync_signals.iloc[i] = "BUY"
                elif ema_f.iloc[i] < ema_s.iloc[i] and ema_f.iloc[i-1] >= ema_s.iloc[i-1]:
                    sync_signals.iloc[i] = "SELL"
            
            signal = sync_signals.iloc[-1] if sync_signals.iloc[-1] != "" else "HOLD"
            
            data = {
                'ema_f': ema_f,
                'ema_s': ema_s,
                'signals': sync_signals
            }

        elif strategy_id == 3: # MACD + ADX
            macd, signal_line, hist = calculate_macd(df['close'], self.params['macd_fast'], self.params['macd_slow'], self.params['macd_signal'])
            adx = calculate_adx(df, self.params['adx_period'])
            
            sync_signals = pd.Series("", index=df.index)
            for i in range(1, len(df)):
                # Buy: MACD Cross UP + ADX > Threshold
                if macd.iloc[i] > signal_line.iloc[i] and macd.iloc[i-1] <= signal_line.iloc[i-1] and adx.iloc[i] > float(self.params['adx_threshold']):
                    sync_signals.iloc[i] = "BUY"
                # Sell: MACD Cross DOWN
                elif macd.iloc[i] < signal_line.iloc[i] and macd.iloc[i-1] >= signal_line.iloc[i-1]:
                    sync_signals.iloc[i] = "SELL"
            
            signal = sync_signals.iloc[-1] if sync_signals.iloc[-1] != "" else "HOLD"
                
            data = {
                'hist': hist,
                'macd': macd,
                'signal': signal_line,
                'adx': adx,
                'signals': sync_signals
            }

        elif strategy_id == 4: # Dynamic RSI + Stoch (Quad-Filter Confluence)
            # Parameters
            rsi_fast = int(self.params.get('rsi_fast_4', 5))
            rsi_slow = int(self.params.get('rsi_slow_4', 14))
            rsi_off = float(self.params.get('rsi_offset', 0))
            st_len = int(self.params.get('st_len_4', 14))
            st_fact = float(self.params.get('st_factor_4', 3.0))
            stoch_off = float(self.params.get('stoch_offset', 30))
            
            # Indicators
            rsi_div = calculate_rsi_divergence(df, rsi_fast, rsi_slow)
            stoch_k, st_trend, st_dir, st_signals = calculate_stochastic_supertrend(df, st_len, 14, 3, st_fact)
            
            # Thresholds
            stoch_buy_thr = 50 - stoch_off
            stoch_sell_thr = 50 + stoch_off
            
            # Initialize confluence signals series
            conf_signals = pd.Series("", index=df.index)
            
            # 4-Filter Confluence check for historical data
            # Logic: We find the first candle in each trend segment that satisfies ALL filters.
            
            last_signal_dir = 0 # 0: None, -1: Last was Buy, 1: Last was Sell
            current_trend_id = 0
            # Track if we already gave a signal for the current Supertrend segment
            signaled_in_current_trend = False
            
            for i in range(1, len(df)):
                s_dir = st_dir.iloc[i]
                prev_s_dir = st_dir.iloc[i-1]
                
                # Reset signal tracker on trend flip
                if s_dir != prev_s_dir:
                    signaled_in_current_trend = False
                
                if signaled_in_current_trend:
                    continue
                    
                s_k = stoch_k.iloc[i]
                r_div = rsi_div.iloc[i]
                
                # BUY CONDITION: Trend is UP (-1) AND Stoch K < BuyThr AND RSI Div > Offset
                if s_dir == -1 and s_k < stoch_buy_thr and r_div > rsi_off:
                    conf_signals.iloc[i] = "BUY"
                    signaled_in_current_trend = True
                
                # SELL CONDITION: Trend is DOWN (1) AND Stoch K > SellThr AND RSI Div < -Offset
                elif s_dir == 1 and s_k > stoch_sell_thr and r_div < -rsi_off:
                    conf_signals.iloc[i] = "SELL"
                    signaled_in_current_trend = True
            
            # Latest Signal for execution
            signal = conf_signals.iloc[-1]
            
            # Real-time Confluence Diagnostic
            s_dir = st_dir.iloc[-1]
            s_k = stoch_k.iloc[-1]
            r_div = rsi_div.iloc[-1]
            
            confluence = {
                'buy': {
                    'trend_ok': bool(s_dir == -1),
                    'stoch_ok': bool(s_k < stoch_buy_thr),
                    'rsi_ok': bool(r_div > rsi_off)
                },
                'sell': {
                    'trend_ok': bool(s_dir == 1),
                    'stoch_ok': bool(s_k > stoch_sell_thr),
                    'rsi_ok': bool(r_div < -rsi_off)
                },
                'params': {
                    'stoch_buy_thr': round(stoch_buy_thr, 1),
                    'stoch_sell_thr': round(stoch_sell_thr, 1),
                    'rsi_off': round(rsi_off, 2)
                }
            }
                
            data = {
                'rsi_div': rsi_div,
                'stoch_rsi': stoch_k,
                'st_trend': st_trend,
                'signals': conf_signals, # Selective confluence markers
                'confluence': confluence
            }
            
        return signal, data

    def load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r') as f:
                    data = json.load(f)
                    self.trade_history = data.get('trades', [])
                    self.active_positions = data.get('active', {})
                logging.info(f"Loaded {len(self.trade_history)} trades from history.")
            except Exception as e:
                logging.error(f"Error loading history: {e}")

    def save_history(self):
        try:
            with open(self.history_file, 'w') as f:
                json.dump({
                    'trades': self.trade_history,
                    'active': self.active_positions
                }, f, indent=4)
        except Exception as e:
            logging.error(f"Error saving history: {e}")
