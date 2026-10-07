"""Compact PR tables with one archive reason per task."""
import html


def cell(value):
    return html.escape(str(value), quote=False).replace('|', '&#124;').replace('\n', '<br>')


def primary_label(labels, custom=None):
    if isinstance(custom, str) and custom.strip():
        return custom.strip()
    failures = sorted(set(label for label in labels if not label.startswith('warning:')))
    return failures[0] if failures else 'historical-unclassified'


def label_table(rows, heading):
    groups = {}
    for task, label, explanation in rows:
        group = groups.setdefault(label, {'tasks': set(), 'explanations': set()})
        group['tasks'].add(task)
        if explanation:
            group['explanations'].add(str(explanation))
    if not groups:
        return []
    lines = [f'| {heading} | Tasks | Explanation |', '| --- | ---: | --- |']
    for label, group in sorted(groups.items()):
        explanation = '<br>'.join(cell(text) for text in sorted(group['explanations']))
        lines.append(f'| {cell(label)} | {len(group["tasks"])} | {explanation} |')
    return lines
