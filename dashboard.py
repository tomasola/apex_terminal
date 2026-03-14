import sys
import traceback
import secrets
from functools import wraps
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

import gevent.monkey
gevent.monkey.patch_all()

print(">>> DATA: APEX Terminal starting up... (forced flush)", flush=True)

try:
    print(">>> Importing flask...", flush=True)
    from flask import Flask, render_template, jsonify, request, session, redirect, url_for, send_from_directory
    
    print(">>> Importing flask_socketio...", flush=True)
    from flask_socketio import SocketIO, emit
    
    print(">>> Importing threading...", flush=True)
    import threading
    
    print(">>> Importing time...", flush=True)
    import time
    
    print(">>> Importing logging...", flush=True)
    import logging
    
    print(">>> Importing engine...", flush=True)
    from engine import TradeEngine
    
    print(">>> Importing os...", flush=True)
    import os
    
    print(">>> Importing dotenv...", flush=True)
    from dotenv import load_dotenv
    
    print(">>> Importing RotatingFileHandler...", flush=True)
    from logging.handlers import RotatingFileHandler
    
    print(">>> DATA: Imports completed.", flush=True)
except BaseException as e:
    print(f">>> CRITICAL IMPORT ERROR: {e}", file=sys.stderr, flush=True)
    traceback.print_exc()
    raise

# Cargar variables locales desde .env si existe
load_dotenv()

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

APP_PASSWORD = os.environ.get("APP_PASSWORD", "101010")
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(16))

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            if request.path.startswith('/api/'):
                return jsonify({"status": "error", "message": "No autenticado"}), 401
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

# Binance Credentials
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")

# OKX Credentials
OKX_API_KEY = os.environ.get("OKX_API_KEY")
OKX_API_SECRET = os.environ.get("OKX_API_SECRET")
OKX_PASSPHRASE = os.environ.get("OKX_PASSPHRASE")

engines = {}
if BINANCE_API_KEY and BINANCE_API_SECRET:
    engines['binance'] = TradeEngine(BINANCE_API_KEY, BINANCE_API_SECRET, exchange_id='binance', testnet=False)
if OKX_API_KEY and OKX_API_SECRET:
    engines['okx'] = TradeEngine(OKX_API_KEY, OKX_API_SECRET, exchange_id='okx', testnet=False, passphrase=OKX_PASSPHRASE)

if not engines:
    logging.warning("⚠️ No se encontraron API Keys válidas. El bot operará en modo limitado.")

# Fallback engine for endpoints that don't specify one
def get_engine(engine_id=None):
    if not engine_id:
        return next(iter(engines.values())) if engines else None
    return engines.get(engine_id)

# Track last errors for diagnostics
last_errors = []

def bot_loop():
    while True:
        try:
            for eng_id, engine in engines.items():
                try:
                    engine.run_cycle()
                except Exception as e:
                    logging.error(f"Error in {eng_id} cycle: {e}")
            time.sleep(30)
        except Exception as e:
            err_msg = f"Loop error: {e}"
            logging.error(err_msg)
            last_errors.append(err_msg)
            if len(last_errors) > 20: last_errors.pop(0)
            time.sleep(5)

def stream_updates():
    """Background task to broadcast real-time prices via WebSocket."""
    while True:
        try:
            full_updates = {}
            for eng_id, engine in engines.items():
                updates = {}
                for symbol in engine.symbols:
                    try:
                        ticker = engine.exchange.fetch_ticker(symbol)
                        updates[symbol] = {
                            'price': ticker['last'],
                            'timestamp': int(time.time()),
                        }
                    except: continue
                full_updates[eng_id] = updates
            
            if full_updates:
                socketio.emit('price_update', full_updates)
            time.sleep(2) 
        except Exception as e:
            err_msg = f"Streaming error: {e}"
            logging.error(err_msg)
            last_errors.append(err_msg)
            if len(last_errors) > 20: last_errors.pop(0)
            time.sleep(5)

@app.route('/api/diagnostics')
@login_required
def diagnostics():
    engine = get_engine(request.args.get('engine_id'))
    if not engine: return jsonify({"status": "error", "message": "No engine found"}), 404
    return jsonify({
        "status": "online",
        "last_errors": last_errors,
        "symbols": list(engine.history.keys()),
        "bot_trading_mode": engine.trading_mode
    })

@socketio.on('connect')
def handle_connect():
    logging.info("Cliente conectado via WebSocket")
    emit('status_msg', {'msg': 'Conectado al APEX Stream'})
    
    # Hilos de fondo ya están iniciados en __main__
    
@app.route('/health')
def health():
    return "OK", 200

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password == APP_PASSWORD:
            session['authenticated'] = True
            session.permanent = True
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        else:
            return render_template('login.html', error=True)
    
    if session.get('authenticated'):
        return redirect(url_for('index'))
        
    return render_template('login.html', error=False)

@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/sw.js')
def serve_sw():
    return send_from_directory('static', 'sw.js')

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/icon-192.png')
def serve_icon():
    return send_from_directory('static', 'icon-192.png')

@app.route('/api/status')
@login_required
def status():
    eng_id = request.args.get('engine_id')
    engine = get_engine(eng_id)
    if not engine: return jsonify({"status": "error", "message": "Engine not found"}), 404
    
    tf = request.args.get('tf', '1h')
    filtered_stats = {}
    for symbol in engine.current_stats:
        if tf in engine.current_stats[symbol]:
            filtered_stats[symbol] = engine.current_stats[symbol][tf]
            
    return jsonify({
        'stats': filtered_stats,
        'symbols': engine.symbols,
        'bot_status': engine.get_bot_status(),
        'exchange': engine.exchange_id
    })

@app.route('/api/trading/history')
@login_required
def api_get_trading_history_v2():
    try:
        engine = get_engine(request.args.get('engine_id'))
        if not engine: return jsonify({"status": "error", "message": "Engine not found"}), 404
        
        data = engine.get_trade_history()
        active_with_prices = {}
        active_positions_snapshot = engine.active_positions.copy()
        
        for symbol, pos in active_positions_snapshot.items():
            enriched = pos.copy()
            tf = engine.params.get('trading_timeframe', '1h')
            current_stats_snapshot = engine.current_stats.get(symbol, {}).get(tf, {})
            
            enriched['tf'] = tf
            curr_price = current_stats_snapshot.get('price')
            enriched['current_price'] = curr_price
            if curr_price:
                entry_price = enriched.get('entry_price', 0)
                if entry_price > 0:
                    enriched['pnl_pct'] = round((curr_price - entry_price) / entry_price * 100, 2)
            active_with_prices[symbol] = enriched

        data['active_positions'] = active_with_prices
        return jsonify(data)
    except Exception as e:
        logging.error(f"Error in /api/trading/history: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/trading/params', methods=['POST'])
@login_required
def update_params():
    try:
        data = request.json
        engine = get_engine(data.get('engine_id'))
        if not engine: return jsonify({"status": "error", "message": "Engine not found"}), 404
        
        engine.params['rsi_fast'] = int(data.get('rsi_fast', engine.params['rsi_fast']))
        engine.params['rsi_slow'] = int(data.get('rsi_slow', engine.params['rsi_slow']))
        engine.params['stoch_rsi_len'] = int(data.get('stoch_rsi_len', engine.params['stoch_rsi_len']))
        engine.params['stoch_k_period'] = int(data.get('stoch_k_period', engine.params['stoch_k_period']))
        engine.params['stoch_smooth_k'] = int(data.get('stoch_smooth_k', engine.params['stoch_smooth_k']))
        engine.params['st_factor'] = float(data.get('st_factor', 3.0))
        engine.params['investment_amount'] = float(data.get('investment_amount', 100.0))
        engine.params['trading_timeframe'] = data.get('trading_timeframe', '1h')
        engine.params['stop_loss_pct'] = float(data.get('stop_loss_pct', 5.0))
        engine.params['take_profit_pct'] = float(data.get('take_profit_pct', 0.0))
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
        engine.params['stoch_offset'] = float(data.get('stoch_offset', 30))
        engine.params['pullback_pct_4'] = float(data.get('pullback_pct_4', 0.0))
        
        logging.info(f"PARAMS UPDATED for {engine.exchange_id}: {engine.params}")
        engine.run_cycle()
        return jsonify({"status": "success", "params": engine.params})
    except Exception as e:
        logging.error(f"Params update error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/trading/mode', methods=['POST'])
@login_required
def set_trading_mode():
    data = request.json
    engine = get_engine(data.get('engine_id'))
    if not engine: return jsonify({"status": "error", "message": "Engine not found"}), 404
    
    mode = data.get('mode')
    if mode in ['OFF', 'SIM', 'REAL']:
        engine.trading_mode = mode
        logging.info(f"Trading mode for {engine.exchange_id} set to: {mode}")
        return jsonify({"status": "success", "mode": mode})
    return jsonify({"status": "error", "message": "Invalid mode"}), 400

@app.route('/api/trading/manual', methods=['POST'])
@login_required
def manual_trade():
    try:
        data = request.json
        engine = get_engine(data.get('engine_id'))
        if not engine: return jsonify({"status": "error", "message": "Engine not found"}), 404
        
        action = data.get('action') 
        symbol = data.get('symbol')
        
        if engine.trading_mode == "OFF":
            return jsonify({"status": "error", "message": "Trading APAGADO. Cambia a SIM o REAL."}), 400

        ticker = engine.exchange.fetch_ticker(symbol)
        price = ticker['last']

        if action == "BUY":
            success, message = engine.open_position(symbol, price)
            return jsonify({"status": "success" if success else "error", "message": message})
        elif action == "SELL":
            success, message = engine.close_position(symbol, price)
            return jsonify({"status": "success" if success else "error", "message": message})

        return jsonify({"status": "error", "message": "Acción no válida"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/api/trading/balance')
def get_trading_balance():
    engine = get_engine(request.args.get('engine_id'))
    if not engine: return jsonify({"balance": 0, "leverage": 1, "currency": "N/A"})
    res = engine.get_balance()
    return jsonify({
        "balance": res['total'],
        "leverage": res['leverage'],
        "currency": res['currency']
    })

@app.route('/api/trading/panic', methods=['POST'])
def panic_button():
    engine = get_engine(request.json.get('engine_id'))
    if engine:
        engine.close_all_positions()
    return jsonify({"message": "PÁNICO ACTIVADO: Todas las posiciones cerradas"})

@app.route('/api/trading/auto_symbols', methods=['POST'])
def update_auto_symbols():
    data = request.json
    engine = get_engine(data.get('engine_id'))
    if not engine: return jsonify({"status": "error"}), 404
    
    symbols = data.get('symbols', [])
    engine.auto_symbols = [s for s in symbols if s in engine.symbols]
    return jsonify({"status": "success", "auto_symbols": engine.auto_symbols})

@app.route('/api/watchlist')
def get_watchlist():
    engine = get_engine(request.args.get('engine_id'))
    if not engine: return jsonify({"watchlist": []})
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
    engine = get_engine(request.json.get('engine_id'))
    if engine:
        engine.clear_trade_history()
    return jsonify({"status": "success", "message": "Historial borrado"})

@app.route('/api/history/delete/<int:index>', methods=['POST'])
def delete_trade_record(index):
    engine = get_engine(request.json.get('engine_id'))
    if engine and engine.delete_trade(index):
        return jsonify({"status": "success", "message": f"Trade {index} deleted"})
    return jsonify({"status": "error", "message": "Error al borrar registro"}), 400

@app.route('/api/backtest')
def run_backtest():
    engine = get_engine(request.args.get('engine_id'))
    if not engine: return jsonify({"error": "No engine"}), 404
    symbol = request.args.get('symbol')
    tf = request.args.get('tf', '1h')
    return jsonify(engine.run_backtest(symbol, tf))

@app.route('/api/history/<path:symbol>')
def history(symbol):
    engine = get_engine(request.args.get('engine_id'))
    if not engine: return jsonify({"error": "No engine"}), 404
    
    tf = request.args.get('tf', '1h')
    if symbol in engine.history and tf in engine.history[symbol]:
        h = engine.history[symbol][tf].copy()
        h['bot_status'] = engine.get_bot_status()
        if symbol in engine.current_stats and tf in engine.current_stats[symbol]:
            stats = engine.current_stats[symbol][tf]
            h['sentiment'] = stats.get('sentiment', 50)
            h['confluence'] = stats.get('confluence')
        
        return jsonify(h)
    return jsonify({"error": "Not found"}), 404

# Iniciar hilos al cargar si estamos en Render (Desactivado para dar prioridad al arranque)
# Se iniciarán automáticamente al conectar el primer SocketIO client

if __name__ == '__main__':
    try:
        print(">>> DATA: Initiating background threads...", flush=True)
        # Hilo del bot (Análisis y ejecución profunda)
        t = threading.Thread(target=bot_loop, daemon=True)
        t.start()
        
        # Hilo de streaming (Precios en tiempo real)
        s = threading.Thread(target=stream_updates, daemon=True)
        s.start()
        
        # Render/Local binding
        port = int(os.environ.get("PORT", 5003))
        print(f">>> DATA: Attempting to bind SocketIO to 0.0.0.0:{port}...", flush=True)
        socketio.run(app, host='0.0.0.0', port=port, debug=False)
    except Exception as e:
        print(f">>> CRITICAL STARTUP ERROR: {e}", file=sys.stderr, flush=True)
        traceback.print_exc()
        raise
