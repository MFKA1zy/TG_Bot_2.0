"""Redact after formatting so exception tracebacks receive the same protection."""
import logging
import re


class SecretFormatter(logging.Formatter):
    def __init__(self, *args, secrets=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.secrets = sorted({str(value) for value in secrets if value}, key=len, reverse=True)

    def format(self, record):
        text = super().format(record)
        for value in self.secrets:
            text = text.replace(value, '[REDACTED]')
        text = re.sub(r'(?<!\d)\d{5,}:[A-Za-z0-9_-]{20,}\b', '[REDACTED]', text)
        return re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)
