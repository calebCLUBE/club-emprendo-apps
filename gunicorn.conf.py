"""Production Gunicorn defaults tuned for Render's memory-constrained instances."""

import os


# Threads preserve request concurrency without loading a second full copy of Django,
# pandas, and the admin/reporting modules into memory.
workers = int(os.getenv("WEB_CONCURRENCY", "1"))
worker_class = "gthread"
threads = int(os.getenv("GUNICORN_THREADS", "2"))

timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5

# Grading and other long-running admin jobs currently run in daemon threads inside
# this worker. Recycling the worker while one of those jobs is active kills the
# thread and leaves its database row stuck in "running". Keep recycling disabled
# by default until these jobs move to a separate durable worker process. Operators
# can still opt in explicitly, but must choose a value safely above the number of
# requests expected during the longest background job.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "0"))
max_requests_jitter = 0 if max_requests == 0 else 50
