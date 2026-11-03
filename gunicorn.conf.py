bind='0.0.0.0:8000'
workers=2
worker_class='sync'
timeout=35
graceful_timeout=30
keepalive=2
max_requests=1000
max_requests_jitter=100
accesslog=None
errorlog='-'
capture_output=False
forwarded_allow_ips=''
def post_fork(server,worker):
    import os
    os.umask(0o077)
