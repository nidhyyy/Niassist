bind = '127.0.0.1:8000'
workers = 2
worker_class = 'gthread'
threads = 4
timeout = 150
graceful_timeout = 35
keepalive = 5
accesslog = '-'
errorlog = '-'
loglevel = 'info'
# Do not record cookies, authorization, request bodies, or query strings.
access_log_format = '%(h)s %(m)s %(U)s %(s)s %(L)s'
max_requests = 1000
max_requests_jitter = 100
