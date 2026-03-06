import multiprocessing
import os

# Gunicorn configuration for Render
bind = "0.0.0.0:" + os.environ.get("PORT", "5001")
workers = 1  # Keep it to 1 to maintain a single TradeEngine instance/thread
worker_class = 'gevent'
timeout = 120
keepalive = 5

# Logging
errorlog = "-"
accesslog = "-"
loglevel = "info"
