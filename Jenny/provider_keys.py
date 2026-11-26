"""Discover optional numbered API keys without a fixed maximum."""

import os
import re


def configured_keys(base, environment=None):
    """Return (setting name, value) pairs in configured fallback order."""
    if environment is None:
        environment = os.environ
    pattern = re.compile(re.escape(base) + r"_([1-9][0-9]*)")
    numbered = []
    for name in environment:
        match = pattern.fullmatch(name)
        if match:
            suffix = match[1]
            numbered.append((len(suffix), suffix, name))
    setting_names = [base, *(item[2] for item in sorted(numbered)), base + "_BACKUP"]
    seen = set()
    keys = []
    for name in setting_names:
        value = (environment.get(name) or "").strip()
        if value and value not in seen:
            seen.add(value)
            keys.append((name, value))
    return tuple(keys)
