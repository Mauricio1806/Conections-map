# -*- coding: utf-8 -*-
"""
message_sanitizer.py
====================
Sanitizes LinkedIn message content for safe public output.
Strips HTML, removes emails, phone numbers, and truncates.
"""

import html as html_module
import re

_EMAIL_RE    = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', re.I)
_PHONE_RE    = re.compile(r'(\+?\d[\d\s\.\-\(\)]{7,}\d)', re.I)
_HTML_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE       = re.compile(r'\s+')


def strip_html(text: str) -> str:
    if not text:
        return ''
    text = _HTML_TAG_RE.sub(' ', text)
    text = html_module.unescape(text)
    return text


def sanitize_excerpt(text: str, max_len: int = 120) -> str:
    if not text:
        return ''
    text = strip_html(text)
    text = _EMAIL_RE.sub('[email]', text)
    text = _PHONE_RE.sub('[phone]', text)
    text = _WS_RE.sub(' ', text).strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip() + '…'
    return text


def is_safe_for_public(text: str) -> bool:
    """Return True if the text contains no PII patterns."""
    if _EMAIL_RE.search(text):
        return False
    cleaned = strip_html(text)
    if _EMAIL_RE.search(cleaned):
        return False
    return True
