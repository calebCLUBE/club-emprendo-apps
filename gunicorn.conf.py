"""Production Gunicorn defaults tuned for Render's memory-constrained instances."""

import os


# Threads preserve request concurrency without loading a second full copy of Django,
# pandas, and the admin/reporting modules into memory.
workers = int(os.getenv("WEB_CONCURRENCY", "1"))
worker_class = "gthread"
threads = int(os.getenv("GUNICORN_THREADS", "4"))

timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5

# Periodically recycle the worker to cap growth from report generation and large CSVs.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "500"))
max_requests_jitter = 50
