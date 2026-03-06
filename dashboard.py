import gevent.monkey
gevent.monkey.patch_all()

print(">>> DATA: APEX Terminal starting up...")

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import threading
import time
import logging
from engine import TradeEngine
import os
from dotenv import load_dotenv

print(">>> DATA: Imports completed.")

# Cargar variables locales desde .env si existe
load_dotenv()

from logging.handlers import RotatingFileHandler

# Configuración básica con archivo y consola (Archivo desactivado en Render)
log_handlers = [logging.StreamHandler()]
if not os.environ.get("RENDER"):
    log_file = 'bot_activity.log'
    log_handlers.append(RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=2))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=log_handlers
)

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

# Credenciales (Cargadas desde .env o variables de entorno)
API_KEY = os.environ.get("BINANCE_API_KEY")
API_SECRET = os.environ.get("BINANCE_API_SECRET")

if not API_KEY or not API_SECRET:
    logging.warning("⚠️ API Keys no encontradas. El bot no podrá operar en REAL.")

engine = TradeEngine(API_KEY, API_SECRET, testnet=False)

def bot_loop():
    while True:
        try:
            engine.run_cycle()
            time.sleep(30)
        except Exception as e:
            logging.error(f"Loop error: {e}")
            time.sleep(5)

def stream_updates():
    """Background task to broadcast real-time prices via WebSocket."""
    while True:
        try:
            updates = {}
            for symbol in engine.symbols:
                ticker = engine.exchange.fetch_ticker(symbol)
                updates[symbol] = {
                    'price': ticker['last'],
                    'timestamp': int(time.time()),
                }
            
            socketio.emit('price_update', updates)
            # Sleep a bit to avoid hitting rate limits too hard, but keep it snappy
            time.sleep(2) 
        except Exception as e:
            logging.error(f"Streaming error: {e}")
            time.sleep(5)

@socketio.on('connect')
def handle_connect():
    logging.info("Cliente conectado via WebSocket")
    emit('status_msg', {'msg': 'Conectado al APEX Stream'})
    
    # Start background tasks if not already started
    if not hasattr(app, 'bot_threads_started'):
        app.bot_threads_started = True
        socketio.start_background_task(bot_loop)
        socketio.start_background_task(stream_updates)
        logging.info("Hilos de fondo iniciados via SocketIO")

@app.route('/health')
def health():
    return "OK", 200

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def status():
    tf = request.args.get('tf', '1h')
    # Devolver las stats filtradas por el timeframe solicitado
    filtered_stats = {}
    for symbol in engine.current_stats:
        if tf in engine.current_stats[symbol]:
            filtered_stats[symbol] = engine.current_stats[symbol][tf]
            
    return jsonify({
        'stats': filtered_stats,
        'symbols': engine.symbols,
        'bot_status': engine.get_bot_status()
    })

@app.route('/api/trading/history')
def api_get_trading_history_v2():
    try:
        # Get base history and stats from engine
        data = engine.get_trade_history()
        
        # Enrich active positions with current price for live PnL
        active_with_prices = {}
        # Use copy() to avoid threading issues during iteration
        active_positions_snapshot = engine.active_positions.copy()
        
        for symbol, pos in active_positions_snapshot.items():
            enriched = pos.copy()
            tf = engine.params.get('trading_timeframe', '1h')
            # Check current_stats threadsafety too
            current_stats_snapshot = engine.current_stats.get(symbol, {}).get(tf, {})
            
            enriched['tf'] = tf
            curr_price = current_stats_snapshot.get('price')
            enriched['current_price'] = curr_price
            if curr_price:
                # Use entry_price from the position item
                entry_price = enriched.get('entry_price', 0)
                if entry_price > 0:
                    enriched['pnl_pct'] = round((curr_price - entry_price) / entry_price * 100, 2)
            active_with_prices[symbol] = enriched

        # Merge enriched data into response
        data['active_positions'] = active_with_prices
        return jsonify(data)
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        logging.error(f"Error in /api/trading/history: {e}")
        with open("api_error.log", "w") as f:
            f.write(err_msg)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/trading/params', methods=['POST']) # Corrected endpoint name to match index.html
def update_params():
    try:
        data = request.json
        # Map frontend names to engine names
        engine.params['rsi_fast'] = int(data.get('rsi_fast', engine.params['rsi_fast']))
        engine.params['rsi_slow'] = int(data.get('rsi_slow', engine.params['rsi_slow']))
        engine.params['stoch_rsi_len'] = int(data.get('stoch_rsi_len', engine.params['stoch_rsi_len']))
        engine.params['stoch_k_period'] = int(data.get('stoch_k_period', engine.params['stoch_k_period']))
        engine.params['stoch_smooth_k'] = int(data.get('stoch_smooth_k', engine.params['stoch_smooth_k']))
        engine.params['st_factor'] = float(data.get('st_factor', 3.0))
        engine.params['investment_amount'] = float(data.get('investment_amount', 100.0))
        engine.params['trading_timeframe'] = data.get('trading_timeframe', '1h')
        engine.params['stop_loss_pct'] = float(data.get('stop_loss_pct', 5.0))
        engine.params['trailing_stop'] = bool(data.get('trailing_stop', False))
        engine.params['active_strategy'] = int(data.get('active_strategy', 1))
        engine.params['ema_fast'] = int(data.get('ema_fast', 9))
        engine.params['ema_slow'] = int(data.get('ema_slow', 21))
        engine.params['macd_fast'] = int(data.get('macd_fast', 12))
        engine.params['macd_slow'] = int(data.get('macd_slow', 26))
        engine.params['macd_signal'] = int(data.get('macd_signal', 9))
        engine.params['adx_period'] = int(data.get('adx_period', 14))
        engine.params['adx_threshold'] = int(data.get('adx_threshold', 25))
        
        # Strategy 4 Specific
        engine.params['rsi_fast_4'] = int(data.get('rsi_fast_4', 5))
        engine.params['rsi_slow_4'] = int(data.get('rsi_slow_4', 14))
        engine.params['rsi_offset'] = float(data.get('rsi_offset', 0))
        engine.params['st_len_4'] = int(data.get('st_len_4', 14))
        engine.params['st_factor_4'] = float(data.get('st_factor_4', 3.0))
        engine.params['stoch_offset'] = float(data.get('stoch_offset', 30))
        
        logging.info(f"PARAMS UPDATED: {engine.params}")
        # Force a cycle to apply changes immediately
        engine.run_cycle()
        return jsonify({"status": "success", "params": engine.params})
    except Exception as e:
        logging.error(f"Params update error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/trading/mode', methods=['POST'])
def set_trading_mode():
    data = request.json
    mode = data.get('mode')
    if mode in ['OFF', 'SIM', 'REAL']:
        engine.trading_mode = mode
        logging.info(f"Trading mode set to: {mode}")
        return jsonify({"status": "success", "mode": mode})
    return jsonify({"status": "error", "message": "Invalid mode"}), 400

@app.route('/api/trading/manual', methods=['POST'])
def manual_trade():
    try:
        data = request.json
        action = data.get('action') # BUY or SELL
        symbol = data.get('symbol', 'BTC/USDC')
        
        if engine.trading_mode == "OFF":
            return jsonify({"status": "error", "message": "Trading APAGADO. Cambia a SIM o REAL."}), 400

        ticker = engine.exchange.fetch_ticker(symbol)
        price = ticker['last']

        if action == "BUY":
            success, message = engine.open_position(symbol, price)
            if success:
                return jsonify({"status": "success", "message": message})
            else:
                return jsonify({"status": "error", "message": message}), 500
        
        elif action == "SELL":
            success, message = engine.close_position(symbol, price)
            if success:
                return jsonify({"status": "success", "message": message})
            else:
                return jsonify({"status": "error", "message": message}), 500

        return jsonify({"status": "error", "message": "Acción no válida"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/api/trading/balance')
def get_trading_balance():
    return jsonify({"balance": engine.get_balance()})

@app.route('/api/trading/panic', methods=['POST'])
def panic_button():
    engine.close_all_positions()
    return jsonify({"message": "PÁNICO ACTIVADO: Todas las posiciones cerradas"})

@app.route('/api/watchlist')
def get_watchlist():
    return jsonify({"watchlist": engine.get_watchlist()})

@app.route('/api/logs')
def get_logs():
    try:
        if os.path.exists('bot_activity.log'):
            with open('bot_activity.log', 'r') as f:
                lines = f.readlines()
                # Return last 200 lines
                return jsonify({"logs": lines[-200:]})
        return jsonify({"logs": ["No se encontró el archivo de log."]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/history/clear', methods=['POST'])
def clear_history():
    engine.clear_trade_history()
    return jsonify({"status": "success", "message": "Historial borrado"})

@app.route('/api/history/delete/<int:index>', methods=['POST'])
def delete_trade_record(index):
    if engine.delete_trade(index):
        return jsonify({"status": "success", "message": f"Trade {index} deleted"})
    return jsonify({"status": "error", "message": "Invalid index"}), 400

@app.route('/api/backtest')
def run_backtest():
    symbol = request.args.get('symbol', 'BTC/USDC')
    tf = request.args.get('tf', '1h')
    return jsonify(engine.run_backtest(symbol, tf))

@app.route('/api/history/<path:symbol>')
def history(symbol):
    tf = request.args.get('tf', '1h')
    if symbol in engine.history and tf in engine.history[symbol]:
        h = engine.history[symbol][tf].copy()
        # Add global status and sentiment
        h['bot_status'] = engine.get_bot_status()
        if symbol in engine.current_stats and tf in engine.current_stats[symbol]:
            stats = engine.current_stats[symbol][tf]
            h['sentiment'] = stats.get('sentiment', 50)
            h['confluence'] = stats.get('confluence')
        
        logging.info(f"Serving history for {symbol} ({tf}): {len(h.get('candles', []))} candles, {len(h.get('signals', []))} signals")
        return jsonify(h)
    return jsonify({"error": "Not found"}), 404

# Iniciar hilos al cargar si estamos en Render (Desactivado para dar prioridad al arranque)
# Se iniciarán automáticamente al conectar el primer SocketIO client

if __name__ == '__main__':
    # Hilo del bot (Análisis y ejecución profunda)
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()
    
    # Hilo de streaming (Precios en tiempo real)
    s = threading.Thread(target=stream_updates, daemon=True)
    s.start()
    
    # Render/Local binding
    port = int(os.environ.get("PORT", 5002))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
