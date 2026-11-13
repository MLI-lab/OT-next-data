"""Canonical per-criterion judge cells, with compatibility for older result files."""
import json


def record(reviews):
    statuses = [r['status'] for r in reviews]
    status = ('not_run' if statuses and all(s == 'not_run' for s in statuses) else
              'fail' if 'fail' in statuses else
              'pass' if statuses and all(s in ('pass', 'warning', 'not_applicable') for s in statuses)
              else 'incomplete')
    result = {'status': status, 'details': '\n\n'.join(r.get('details') or '' for r in reviews)}
    if len(reviews) != 1 or statuses[0] != status:
        result['reviews'] = reviews
    return result


def reviews(value):
    return value.get('reviews') if 'reviews' in value else [
        {'status': value['status'], 'details': value.get('details') or ''}]


def decode(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if not isinstance(value, dict) or not value:
        return None
    old = value.get('verdicts', value.get('status'))
    if isinstance(old, (dict, list)):
        texts = dict(value.get('details') or {})
        return {model: record([{'status': s, 'details': (texts.get(model) or [])[i]
                               if i < len(texts.get(model) or []) else ''}
                              for i, s in enumerate(statuses)])
                for model, statuses in dict(old).items()} or None
    if all(isinstance(v, dict) and isinstance(v.get('status'), str) and 'details' in v
           for v in value.values()):
        return value
    return None
