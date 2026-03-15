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
    def __init__(self, api_key, api_secret, exchange_id='binance', testnet=True, passphrase=None):
        self.exchange_id = exchange_id
        
        exchange_class = getattr(ccxt, exchange_id)
        
        # Base config
        config = {
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'adjustForTimeDifference': True
            }
        }
        
        if passphrase:
            config['password'] = passphrase
        
        # Exchange specific defaults
        if exchange_id == 'binance':
            config['options']['defaultType'] = 'spot'
        elif exchange_id == 'okx':
            config['options']['defaultType'] = 'swap' # Perpetuals
            
        self.exchange = exchange_class(config)
        
        if testnet:
            self.exchange.set_sandbox_mode(True)
            
        # Default Symbols per exchange
        if exchange_id == 'binance':
            self.symbols = [
                'BTC/USDC', 'ETH/USDC', 'SOL/USDC', 'BNB/USDC', 'XRP/USDC', 
                'ADA/USDC', 'DOT/USDC', 'POL/USDC', 'LINK/USDC',
                'UNI/USDC', 'LTC/USDC', 'BCH/USDC', 'SUI/USDC',
                'HBAR/USDC', 'XLM/USDC'
            ]
        elif exchange_id == 'okx':
            # OKX Futures often use USDT or USDC margin. For consistency, let's use USDT based swaps.
            self.symbols = [
                'BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT', 'BNB/USDT:USDT', 
                'XRP/USDT:USDT', 'ADA/USDT:USDT', 'DOT/USDT:USDT', 'MATIC/USDT:USDT',
                'LINK/USDT:USDT', 'UNI/USDT:USDT', 'LTC/USDT:USDT', 'AVAX/USDT:USDT'
            ]
        else:
            self.symbols = []
        self.auto_symbols = [s for s in self.symbols if s != 'BNB/USDC'] # Default: all enabled except BNB
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
            'stoch_k_period': 10,
            'stoch_smooth_k': 2,
            'st_factor': 10.0,
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
            # Strategy 4 Parameters (Opciones sugeridas por el usuario)
            'rsi_fast_4': 5,
            'rsi_slow_4': 14,
            'rsi_offset': 10.0,
            'st_len_4': 14,
            'st_factor_4': 3.0,
            'stoch_offset': 10.0,
            'pullback_pct_4': 1.0,
            'leverage': 1
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
            # Some symbols might need conversion if we use them in multiple contexts
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            logging.error(f"Error fetching OHLCV for {symbol} on {self.exchange_id} ({timeframe}): {e}")
            return None

    def set_leverage(self, symbol, leverage):
        if self.exchange_id != 'okx':
            return # Only for futures
        try:
            self.exchange.set_leverage(leverage, symbol)
            logging.info(f"Leverage set to {leverage}x for {symbol} on {self.exchange_id}")
            return True
        except Exception as e:
            logging.error(f"Error setting leverage: {e}")
            return False

    def analyze(self, symbol, timeframe='1h', skip_trading=False):
        df = self.fetch_ohlcv(symbol, timeframe)
        if df is None or len(df) < 50:
            return
            
        # Get Strategy Signal using modular logic
        signal, indicators_data = self.get_strategy_signal(df, self.params['active_strategy'], symbol)
        
        # Execute Trade Logic (only on the selected trading timeframe)
        if timeframe == self.params.get('trading_timeframe', '1h') and not skip_trading:
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
                v = float(val) if not pd.isna(val) else 0.0
                c = '#ff0000' if v > 0 else '#00ff00' # Red if > 0 (Sell zone) else Lime (Buy zone)
                rsi_data.append({'time': int(timestamps[i]), 'value': v, 'color': c})
            
        stoch_k_data = []
        trend_up = []
        trend_dn = []

        if stoch_k is not None and st_dir is not None and st_trend is not None:
            for i in range(len(stoch_k)):
                k_val = float(stoch_k.iloc[i]) if not pd.isna(stoch_k.iloc[i]) else 0.0
                t_val = float(st_trend.iloc[i]) if not pd.isna(st_trend.iloc[i]) else 0.0
                d_val = st_dir.iloc[i]
                
                # K is a single continuous solid line in Pine Script
                stoch_k_data.append({'time': int(timestamps[i]), 'value': k_val})

                # Split trend based on direction (-1 = UP, 1 = DOWN)
                if d_val == -1:
                    trend_up.append({'time': int(timestamps[i]), 'value': t_val})
                else:
                    trend_dn.append({'time': int(timestamps[i]), 'value': t_val})
        signal_markers = []
        if st_signals is not None:
            for i in range(len(st_signals)):
                if st_signals.iloc[i] != "":
                    signal_markers.append({
                        'time': int(timestamps[i]),
                        'position': 'belowBar' if st_signals.iloc[i] == 'BUY' else 'aboveBar',
                        'color': '#26a69a' if st_signals.iloc[i] == 'BUY' else '#ef5350', # Better Teal/Red
                        'shape': 'arrowUp' if st_signals.iloc[i] == 'BUY' else 'arrowDown',
                        'text': st_signals.iloc[i],
                        'size': 6 # Increased size for better visibility
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
        
        # Strategy 4 Primed Contribution
        conf = indicators_data.get('confluence')
        if conf:
            if conf['buy'].get('primed'): sentiment += 25
            if conf['sell'].get('primed'): sentiment -= 25

        
        # Clamp sentiment
        sentiment = max(0, min(100, sentiment))

        # Prepare Raw Markers for Indicator charts (less strict)
        raw_markers = []
        if isinstance(st_signals, pd.Series):
             # Extract raw trend flips from supertrend dir if possible
             # For now, let's just use the st_signals from the strategy (already contains some signals)
             # But if we want more, we can use Supertrend flips.
             # Actually, let's just use the same markers for now but ensure they are available for subcharts.
             pass

        hist = self.history[symbol][timeframe]
        hist['candles'] = candles
        hist['rsi_div'] = rsi_data
        hist['stoch_k'] = stoch_k_data
        hist['trend_up'] = trend_up
        hist['trend_dn'] = trend_dn
        hist['signals'] = signal_markers # These are the strict confluence markers
        hist['raw_signals'] = signal_markers # Legacy or for future use
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

    def run_cycle(self, skip_trading=False):
        for s in self.symbols:
            for tf in self.timeframes:
                try:
                    self.analyze(s, tf, skip_trading=skip_trading)
                except Exception as e:
                    logging.error(f"Analysis error for {s} ({tf}): {e}")

        # Check Stop Loss & Take Profit for all active positions
        if not skip_trading:
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
            'entry_timestamp': pos.get('entry_timestamp', 0),
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
        # Calculate total performance summary
        total_pnl = sum([t['pnl_pct'] for t in self.trade_history])
        wins = [t for t in self.trade_history if t.get('pnl_pct', 0) > 0]
        win_rate = (len(wins) / len(self.trade_history) * 100) if self.trade_history else 0
        
        # Calculate Total PnL in USDC (replacing old Daily PnL logic)
        total_pnl_usdc = sum([t.get('pnl_val', 0) for t in self.trade_history])

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
            'daily_pnl': round(total_pnl_usdc, 2), # Still using key 'daily_pnl' for frontend compatibility
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
        tf = self.params.get('trading_timeframe', '1h')
        status = f"MODO: {self.trading_mode} | ESTRAT: {self.params.get('active_strategy', 1)} | TF: {tf}"
        
        if self.active_positions:
            symbols = ", ".join(self.active_positions.keys())
            status += f" | ABIERTO: {symbols} (Esperando VENTA)"
        else:
            status += " | ESCANEANDO (Esperando COMPRA)"
            
        return status

    def get_balance(self):
        try:
            balance = self.exchange.fetch_balance()
            total = 0.0
            currency = 'USDC'
            
            if self.exchange_id == 'binance':
                usdc_spot = balance.get('USDC', {}).get('total', 0.0)
                usdc_earn = balance.get('LDUSDC', {}).get('total', 0.0)
                total = usdc_spot + usdc_earn
                if total == 0:
                    total = balance.get('USDT', {}).get('total', 0.0)
                    currency = 'USDT'
            elif self.exchange_id == 'okx':
                # OKX Swap balance is usually under 'total' if it's a unified account
                total = balance.get('info', {}).get('data', [{}])[0].get('totalEq', 0.0)
                if not total:
                    # Fallback for standard CCXT balance format
                    total = balance.get('USDT', {}).get('total', 0.0)
                    currency = 'USDT'
                else:
                    total = float(total)
                    currency = 'USDT (Eq)'

            return {
                'total': round(total, 2),
                'currency': currency,
                'leverage': self.params.get('leverage', 1)
            }
        except Exception as e:
            logging.error(f"Error fetching balance from {self.exchange_id}: {e}")
            return {'total': 0.0, 'currency': 'N/A', 'leverage': 1}

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
            sig, _ = self.get_strategy_signal(chunk, self.params['active_strategy'], symbol)
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

    def get_strategy_signal(self, df, strategy_id, symbol="UNKNOWN"):
        """Modular logic to get signals based on strategy ID"""
        signal = "HOLD"
        data = {}

        if strategy_id == 1: # Fixed Dynamic RSI & STOCH (Strategy 4 Logic Fixed)
            # FIXED Parameters based on user image
            rsi_fast = 5
            rsi_slow = 14
            rsi_off = 14.0
            stoch_off = 30.0
            pullback_factor = 0.10 # 10%
            
            # Indicators
            rsi_div = calculate_rsi_divergence(df, rsi_fast, rsi_slow)
            stoch_k, st_trend, st_dir, _ = calculate_stochastic_supertrend(df, 14, 14, 3, 3.0)
            
            conf_signals = pd.Series("", index=df.index)
            is_primed_buy = False
            is_primed_sell = False
            peak_stoch = 0.0
            peak_rsi = 0.0
            last_side = ""
            
            stoch_buy_thr = 50 - stoch_off
            stoch_sell_thr = 50 + stoch_off
            
            for i in range(1, len(df)):
                sk = stoch_k.iloc[i]
                rd = rsi_div.iloc[i]
                d_sk = sk - 50
                d_rd = rd
                
                if d_sk < -stoch_off and d_rd < -rsi_off:
                    if not is_primed_buy:
                        is_primed_buy = True
                        peak_stoch = d_sk
                        peak_rsi = d_rd
                    else:
                        peak_stoch = min(peak_stoch, d_sk)
                        peak_rsi = min(peak_rsi, d_rd)
                    is_primed_sell = False
                elif d_sk > stoch_off and d_rd > rsi_off:
                    if not is_primed_sell:
                        is_primed_sell = True
                        peak_stoch = d_sk
                        peak_rsi = d_rd
                    else:
                        peak_stoch = max(peak_stoch, d_sk)
                        peak_rsi = max(peak_rsi, d_rd)
                    is_primed_buy = False
                
                if is_primed_buy:
                    thr_sk = peak_stoch * (1 - pullback_factor)
                    thr_rd = peak_rsi * (1 - pullback_factor)
                    if d_sk >= thr_sk or d_rd >= thr_rd:
                        if last_side != "BUY":
                            conf_signals.iloc[i] = "BUY"
                            last_side = "BUY"
                        is_primed_buy = False
                elif is_primed_sell:
                    thr_sk = peak_stoch * (1 - pullback_factor)
                    thr_rd = peak_rsi * (1 - pullback_factor)
                    if d_sk <= thr_sk or d_rd <= thr_rd:
                        if last_side != "SELL":
                            conf_signals.iloc[i] = "SELL"
                            last_side = "SELL"
                        is_primed_sell = False

            signal = conf_signals.iloc[-1] if not conf_signals.empty else "HOLD"
            if signal == "": signal = "HOLD"
            
            lk = stoch_k.iloc[-1] - 50
            ld = rsi_div.iloc[-1]
            confluence = {
                'buy': { 'primed': bool(is_primed_buy), 'stoch_ok': bool(lk < -stoch_off), 'rsi_ok': bool(ld < -rsi_off) },
                'sell': { 'primed': bool(is_primed_sell), 'stoch_ok': bool(lk > stoch_off), 'rsi_ok': bool(ld > rsi_off) }
            }
                
            data = {
                'rsi_div': rsi_div,
                'stoch_rsi': stoch_k,
                'st_trend': st_trend,
                'signals': conf_signals,
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
            rsi_off = float(self.params.get('rsi_offset', 10)) # User example: 10
            stoch_off = float(self.params.get('stoch_offset', 10)) # User example: 10
            pullback_val = float(self.params.get('pullback_pct_4', 0.0))
            pullback_factor = pullback_val / 100.0 # User: 15 = 15% (0.15)
            
            # Indicators
            rsi_div = calculate_rsi_divergence(df, rsi_fast, rsi_slow)
            # Use standard SuperTrend for background visuals
            stoch_k, st_trend, st_dir, _ = calculate_stochastic_supertrend(df, 14, 14, 3, 3.0)
            
            conf_signals = pd.Series("", index=df.index)
            
            # State for history processing
            is_primed_buy = False
            is_primed_sell = False
            peak_stoch = 0.0
            peak_rsi = 0.0
            last_side = ""
            
            # Correct threshold display for UI
            stoch_buy_thr = 50 - stoch_off
            stoch_sell_thr = 50 + stoch_off
            
            for i in range(1, len(df)):
                sk = stoch_k.iloc[i]
                rd = rsi_div.iloc[i]
                
                # Centered values
                d_sk = sk - 50
                d_rd = rd
                
                # --- PRIMING (Both must be outside range) ---
                # BUY Zone: Both below range (e.g. < -10)
                if d_sk < -stoch_off and d_rd < -rsi_off:
                    if not is_primed_buy:
                        is_primed_buy = True
                        peak_stoch = d_sk
                        peak_rsi = d_rd
                        logging.info(f"[{self.exchange_id}] {symbol} - ESTRATEGIA 4: PRIMADO PARA COMPRA. Esperando retroceso...")
                    else:
                        peak_stoch = min(peak_stoch, d_sk)
                        peak_rsi = min(peak_rsi, d_rd)
                    is_primed_sell = False # Mutually exclusive
                
                # SELL Zone: Both above range (e.g. > 10)
                elif d_sk > stoch_off and d_rd > rsi_off:
                    if not is_primed_sell:
                        is_primed_sell = True
                        peak_stoch = d_sk
                        peak_rsi = d_rd
                        logging.info(f"[{self.exchange_id}] {symbol} - ESTRATEGIA 4: PRIMADO PARA VENTA. Esperando retroceso...")
                    else:
                        peak_stoch = max(peak_stoch, d_sk)
                        peak_rsi = max(peak_rsi, d_rd)
                    is_primed_buy = False
                
                # --- TRIGGERING (Pullback achieved in EITHER primed indicator) ---
                if is_primed_buy:
                    # Valley pullback: val increases towards 0
                    # if peak was -15, and factor is 0.1, trigger is -13.5
                    thr_sk = peak_stoch * (1 - pullback_factor)
                    thr_rd = peak_rsi * (1 - pullback_factor)
                    if d_sk >= thr_sk or d_rd >= thr_rd:
                        if last_side != "BUY":
                            conf_signals.iloc[i] = "BUY"
                            logging.info(f"[{self.exchange_id}] {symbol} - ESTRATEGIA 4: SEÑAL DE COMPRA DISPARADA POR RETROCESO.")
                            last_side = "BUY"
                        is_primed_buy = False
                        peak_stoch = 0.0
                        peak_rsi = 0.0
                
                elif is_primed_sell:
                    # Peak pullback: val decreases towards 0
                    # if peak was 15, and factor is 0.1, trigger is 13.5
                    thr_sk = peak_stoch * (1 - pullback_factor)
                    thr_rd = peak_rsi * (1 - pullback_factor)
                    if d_sk <= thr_sk or d_rd <= thr_rd:
                        if last_side != "SELL":
                            conf_signals.iloc[i] = "SELL"
                            logging.info(f"[{self.exchange_id}] {symbol} - ESTRATEGIA 4: SEÑAL DE VENTA DISPARADA POR RETROCESO.")
                            last_side = "SELL"
                        is_primed_sell = False
                        peak_stoch = 0.0
                        peak_rsi = 0.0
            
            # Latest Signal
            signal = conf_signals.iloc[-1] if not conf_signals.empty else "HOLD"
            if signal == "": signal = "HOLD"
            
            # Real-time Diagnostics
            lk = stoch_k.iloc[-1] - 50
            ld = rsi_div.iloc[-1]
            confluence = {
                'buy': {
                    'primed': bool(is_primed_buy),
                    'stoch_ok': bool(lk < -stoch_off),
                    'rsi_ok': bool(ld < -rsi_off)
                },
                'sell': {
                    'primed': bool(is_primed_sell),
                    'stoch_ok': bool(lk > stoch_off),
                    'rsi_ok': bool(ld > rsi_off)
                },
                'params': {
                    'stoch_buy_thr': round(stoch_buy_thr, 1),
                    'stoch_sell_thr': round(stoch_sell_thr, 1),
                    'rsi_off': round(rsi_off, 2),
                    'pullback_val': round(pullback_val, 2)
                }
            }
                
            data = {
                'rsi_div': rsi_div,
                'stoch_rsi': stoch_k,
                'st_trend': st_trend,
                'signals': conf_signals,
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
