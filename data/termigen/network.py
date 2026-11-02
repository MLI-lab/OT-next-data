"""Offline-first task policy; online exceptions require subsequent audit evidence."""
import re
import tomllib


def offline_config(config: bytes) -> bytes:
    text = config.decode()
    parsed = tomllib.loads(text)
    if parsed.get('environment', {}).get('allow_internet') is False:
        return config
    section = re.search(r'(?m)^\[environment\][ \t]*(?:#.*)?$', text)
    if section is None:
        text = text.rstrip() + '\n\n[environment]\nallow_internet = false\n'
    else:
        end = re.search(r'(?m)^\[', text[section.end():])
        stop = section.end() + end.start() if end else len(text)
        body = text[section.end():stop]
        if re.search(r'(?m)^allow_internet\s*=', body):
            body = re.sub(r'(?m)^(allow_internet\s*=\s*)(true|false)\b', r'\g<1>false', body)
        else:
            body = '\nallow_internet = false' + body
        text = text[:section.end()] + body + text[stop:]
    if tomllib.loads(text)['environment']['allow_internet'] is not False:
        raise ValueError('Termigen network policy could not be set offline')
    return text.encode()
