"""Nonblocking in-process event broadcast."""
import asyncio
import logging
import os
import re
import traceback


class SecretRedactionFilter(logging.Filter):
    """Redact secrets before any handler formats a message or traceback."""

    def filter(self, record):
        message = record.getMessage()
        if record.exc_info:
            message += '\n' + ''.join(traceback.format_exception(*record.exc_info))
        record.msg = redact_secrets(message)
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


def redact_secrets(text):
    text = str(text)
    for name, value in os.environ.items():
        if not value or not ('KEY' in name.upper() or 'TOKEN' in name.upper()):
            continue
        # The inference key is always secret, even if unusually short.
        if name.upper() == 'VULTR_INFERENCE_API_KEY' or len(value) >= 32:
            text = text.replace(value, '[REDACTED]')
        for token in re.findall(r'[A-Za-z0-9_./+=-]{32,}', value):
            text = text.replace(token, '[REDACTED]')
    text = re.sub(r'(?i)\bbearer\s+[^\s,;\]\}\"\']+', 'Bearer [REDACTED]', text)
    # Header lines can contain non-Bearer credentials or custom schemes.
    return '\n'.join('[REDACTED AUTHORIZATION HEADER]' if re.search(r'(?i)authorization', line)
                     else line for line in text.split('\n'))


def pod_logger():
    logger = logging.getLogger('pod')
    if not any(isinstance(f, SecretRedactionFilter) for f in logger.filters):
        logger.addFilter(SecretRedactionFilter())
    if not any(getattr(h, '_pod_stderr', False) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler._pod_stderr = True
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    # Prevent a parent handler from rendering the original traceback a second time.
    logger.propagate = False
    return logger


def log_caught(ctx, stage, item_id, exc):
    pod_logger().exception('stage=%s item_id=%s exception', stage, item_id or '-')
    if os.getenv('POD_DEBUG_ERRORS') == '1' and ctx is not None:
        debug = getattr(ctx, '_errors_debug', None)
        if debug is None:
            debug = []
            ctx._errors_debug = debug
        debug.append(redact_secrets(f'{stage} item={item_id or "-"}: {type(exc).__name__}: {exc}'))


class Broadcaster:
    def __init__(self):
        self.subscribers = {}

    def subscribe(self, run_id):
        queue = asyncio.Queue(maxsize=256)
        self.subscribers.setdefault(run_id, set()).add(queue)
        return queue

    def unsubscribe(self, run_id, queue):
        subscribers = self.subscribers.get(run_id)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                self.subscribers.pop(run_id, None)

    def publish(self, event):
        for queue in tuple(self.subscribers.get(event['run_id'], ())):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
