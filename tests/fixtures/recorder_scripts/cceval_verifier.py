#!/usr/bin/env python3
"""Score one CrossCodeEval task with the benchmark's own metric code.

Port of amazon-science/cceval 40c68d2b (scripts/eval_metric.py,
scripts/eval_utils.py, scripts/keywords). Plumbing differences: keyword files
inlined, nltk's word tokenizer is re.findall(r"\\w+"), fuzz.ratio is its
difflib path, and python statement boundaries use ast.parse instead of
tree-sitter.

Reward = exact match of the first statement: truncate, remove comments, strip
each line, drop empty lines, compare the line lists. Edit similarity and
identifier EM/precision/recall/F1 are reported next to it.

Deviations from the benchmark, each applied to prediction and reference alike:
- the reference is truncated too (the benchmark truncates only the prediction,
  so a reference with a second statement could never match);
- csharp/java/typescript: ';', '{', '}' and '//' inside string or template
  literals do not end a statement or start a comment; '#' is a comment only in
  python;
- a bracket with only whitespace before it does not end the statement (the
  benchmark's `if end_idx` skipped index 0);
- typescript: a '{ ... }' group closed on the same line belongs to the statement;
- a final line that is only '{' is ignored.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import keyword
import re
import sys
from pathlib import Path

CCEVAL_COMMIT = "40c68d2b7ca2a8eae95d901ac80e6a540a84a53d"

# --- scripts/keywords/*.txt (keywordlist.py: python uses keyword.kwlist) ---
KEYWORDS = {
    "java": """abstract assert boolean break byte case catch char class continue
default do double else enum extends final finally float for if implements
import instanceof int interface long native new package private protected
public return short static strictfp super switch synchronized this throw
throws transient try void volatile while var const goto""",
    "csharp": """abstract as base bool break byte case catch char checked class
const continue decimal default delegate do double else enum event explicit
extern finally fixed float for foreach goto if implicit in int interface
internal is lock long namespace new null object operator out override params
private protected public readonly ref return sbyte sealed short sizeof
stackalloc static string struct switch this throw try typeof uint ulong
unchecked unsafe ushort using virtual void volatile while""",
    "typescript": """break case catch class const continue debugger default delete
do else export extends finally for function if import in instanceof new
return super switch this throw try typeof var void while with yield enum
implements interface let package private protected public static""",
}


def get_language_keywords(language):
    language = language.lower()
    if language == "python":
        return frozenset(k for k in keyword.kwlist if k != "True" and k != "False")
    return frozenset(KEYWORDS[language].split())


# --- scripts/eval_utils.py ---
IDENTIFIER_REGEX = re.compile("[_a-zA-Z][_a-zA-Z0-9]*")
REGEX_TEXT = ("(?<=[a-z0-9])(?=[A-Z])|"
              "(?<=[A-Z0-9])(?=[A-Z][a-z])|"
              "(?<=[0-9])(?=[a-zA-Z])|"
              "(?<=[A-Za-z])(?=[0-9])|"
              "(?<=[@$.'\"])(?=[a-zA-Z0-9])|"
              "(?<=[a-zA-Z0-9])(?=[@$.'\"])|"
              "_|\\s+")
string_pattern = r'"([^"\\]*(\\.[^"\\]*)*)"|\'([^\'\\]*(\\.[^\'\\]*)*)\''

SPLIT_REGEX = re.compile(REGEX_TEXT)


def code_tokenize(text):
    """nltk RegexpTokenizer(r'\\w+').tokenize"""
    return re.findall(r"\w+", text)


def fuzz_ratio(s1, s2):
    """fuzzywuzzy.fuzz.ratio without python-Levenshtein (the benchmark's install)."""
    if s1 is None or s2 is None:
        return 0
    if s1 == s2:
        return 100
    return int(round(100 * difflib.SequenceMatcher(None, s1, s2).ratio()))


def cal_edit_sim(references, hypotheses):
    total = len(references)
    edit_sim = 0.0
    for pred, gt in zip(hypotheses, references):
        pred = pred.strip()
        gt = gt.strip()
        edit_sim += fuzz_ratio(pred, gt)
    return edit_sim / total


def split_identifier_into_parts(identifier):
    """
    Split a single identifier into parts on snake_case and camelCase
    """
    identifier_parts = list(s for s in SPLIT_REGEX.split(identifier) if len(s) > 0)

    if len(identifier_parts) == 0:
        return [identifier]
    if "_" in identifier:  # We consider "_" as part of identifier and add it back in between each semantic part
        # if snake_case, we only split identifiers based on "_", ignore the mixed camelCase or other special symbols
        # this helps us avoid splitting identifiers like "get_2d_array" into ["get", "2", "d", "array"]
        # also avoid many other corner cases
        identifier_parts = identifier.split("_")
        tmp = [identifier_parts[0]]
        for i in identifier_parts[1:]:
            tmp.append("_")
            tmp.append(i)
        identifier_parts = tmp

    return identifier_parts


def is_identifier(token, lang=None):
    return True if IDENTIFIER_REGEX.match(token) \
                   and (lang is None or token not in get_language_keywords(lang)) \
        else False


def extract_identifiers(source_code, lang):
    # the main idea is to remove String from a source code
    # then, tokenize the code to get all words and match with identifier regular expression
    # check if it is a language specific keyword, it not, then it is an identifier
    source_code_without_strings = re.sub(string_pattern, '', source_code)
    _ids = [t for t in code_tokenize(source_code_without_strings) if is_identifier(t, lang)]
    return _ids


def code_chars(s):
    """(index, char) outside string and template literals; ${...} parts are code."""
    i, n, state = 0, len(s), []
    while i < n:
        c = s[i]
        top = state[-1] if state else None
        if top in ('"', "'", '`'):
            if c == '\\':
                i += 2
                continue
            if c == top:
                state.pop()
            elif top == '`' and s.startswith('${', i):
                state.append('${')
                i += 2
                continue
            i += 1
            continue
        if c in ('"', "'", '`'):
            state.append(c)
        elif top == '${' and c == '}':
            state.pop()
        else:
            yield i, c
        i += 1


def get_bracket_lang_statement(completion, inline_groups=False):
    chars = list(code_chars(completion))
    end_idx = None
    k = 0
    while k < len(chars):
        i, c = chars[k]
        if c in [";", "}", "{"]:
            # A leading bracket (`} as Thread;`, `});`) is not a statement by itself.
            if not completion[:i].strip():
                k += 1
                continue
            if c == "{" and inline_groups:
                # A group closed on the same line (object literal, destructuring)
                # belongs to the statement; only a `{` left open starts a block.
                depth, j, closed = 0, k, None
                while j < len(chars):
                    if chars[j][1] == "{":
                        depth += 1
                    elif chars[j][1] == "}":
                        depth -= 1
                        if depth == 0:
                            closed = j
                            break
                    j += 1
                if closed is not None and "\n" not in completion[i:chars[closed][0]]:
                    k = closed + 1
                    continue
            end_idx = i
            break
        k += 1
    return completion[:end_idx + 1] if end_idx is not None else completion


def remove_comments(code, lang=None):
    """'#' comments for python, '//' comments outside strings otherwise."""
    if lang == "python":
        return re.sub(r'#.*', '', code)
    out, last = [], 0
    for i, c in code_chars(code):
        if c == '/' and code.startswith('//', i):
            j = code.find('\n', i)
            j = len(code) if j < 0 else j
            out.append(code[last:i])
            last = j
    out.append(code[last:])
    return ''.join(out)


def is_parse_valid(parser, code):
    """Benchmark: tree-sitter parse tree without ERROR nodes. Here: ast.parse."""
    try:
        ast.parse(code)
        return True
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False


def get_python_one_statement(prompt, completion, parser):
    for i in range(len(completion)):
        code = prompt + completion[:i + 1]
        if not is_parse_valid(parser, code):
            continue
        if completion[i + 1] == "\n":
            return completion[:i + 1].rstrip()

    return completion


def postprocess_code_lines(prompt, completion, parser, lang):
    try:
        if lang in ["java", "csharp", "typescript"]:
            return get_bracket_lang_statement(completion, inline_groups=(lang == "typescript"))
        elif lang == "python":
            return get_python_one_statement(prompt, completion, parser)
    except Exception as e:
        return completion


# --- scripts/eval_metric.py ---
def compute_id_match(pred_ids, target_ids):
    pred_ids = list(set(pred_ids))
    target_ids = list(set(target_ids))
    tp = 0
    fp = 0
    fn = 0
    for pid in pred_ids:
        if pid in target_ids:
            tp += 1
        else:
            fp += 1
    for tid in target_ids:
        if tid not in pred_ids:
            fn += 1
    return tp, fp, fn


def normalized_lines(code):
    return [l.strip() for l in code.split("\n") if l.strip()]


def score(prompt, pred, groundtruth, lang):
    """process_examples plus the per-sample block of compute_metric_stmt."""
    prediction = remove_comments(postprocess_code_lines(prompt, pred, None, lang), lang)
    target = remove_comments(postprocess_code_lines(prompt, groundtruth, None, lang), lang)
    target_truncated = normalized_lines(target) != normalized_lines(remove_comments(groundtruth, lang))

    pred_lines = normalized_lines(prediction)
    gt_lines = normalized_lines(target)
    # A trailing lone "{" (C# method headers) carries no information.
    if len(pred_lines) > 1 and pred_lines[-1] == "{":
        pred_lines = pred_lines[:-1]
    if len(gt_lines) > 1 and gt_lines[-1] == "{":
        gt_lines = gt_lines[:-1]
    em_label = int(pred_lines == gt_lines)

    pred_ids = extract_identifiers(prediction, lang)
    target_ids = extract_identifiers(target, lang)

    identifier_em = int(pred_ids == target_ids)
    es = cal_edit_sim([target], [prediction])
    id_tp, id_fp, id_fn = compute_id_match(pred_ids, target_ids)
    return {
        "em": em_label,
        "es": es,
        "id_em": identifier_em,
        "id_precision": id_tp / (id_tp + id_fp) if (id_tp + id_fp) != 0 else 0,
        "id_recall": id_tp / (id_tp + id_fn) if (id_tp + id_fn) != 0 else 0,
        "id_f1": 2 * id_tp / (2 * id_tp + id_fp + id_fn) if (2 * id_tp + id_fp + id_fn) != 0 else 0,
    }, {"pred": prediction, "target": target, "pred_ids": pred_ids, "target_ids": target_ids,
        "pred_lines": pred_lines, "gt_lines": gt_lines, "target_truncated": target_truncated}


# --- plumbing ---
def read_text(path):
    return Path(path).read_bytes().decode("utf-8", "replace")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--language", required=True, choices=["csharp", "java", "python", "typescript"])
    ap.add_argument("--prediction", required=True, help="the agent's answer file")
    ap.add_argument("--reference", required=True, help="the task's groundtruth file")
    ap.add_argument("--prompt", default=None, help="code before the cursor (python truncation)")
    ap.add_argument("--out", required=True, help="verifier output directory")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    groundtruth = read_text(a.reference)
    missing = not Path(a.prediction).is_file()
    pred = "" if missing else read_text(a.prediction)
    prompt = read_text(a.prompt) if a.prompt and Path(a.prompt).is_file() else ""
    metrics, detail = score(prompt, pred, groundtruth, a.language)
    rewards = {"reward": metrics["em"], "es": metrics["es"], "id_em": metrics["id_em"],
               "id_precision": metrics["id_precision"], "id_recall": metrics["id_recall"], "id_f1": metrics["id_f1"]}
    (out / "reward.json").write_text(json.dumps(rewards) + "\n")
    (out / "reward.txt").write_text(f"{metrics['em']}\n")
    detail.update(language=a.language, cceval_commit=CCEVAL_COMMIT, prediction_missing=missing,
                  prompt_chars=len(prompt))
    (out / "metrics.json").write_text(json.dumps(detail, indent=2) + "\n")
    lines = [f"{'PASS' if metrics['em'] else 'FAIL'}[cceval-em] es={metrics['es']} id_f1={metrics['id_f1']:.3f}"]
    if missing:
        lines.append(f"no prediction file: {a.prediction}")
    if detail["target_truncated"]:
        lines.append("reference truncated to its first statement (the benchmark would not truncate it)")
    if not metrics["em"]:
        lines += list(difflib.unified_diff(detail["gt_lines"], detail["pred_lines"], "reference", "prediction", lineterm=""))
    (out / "test_output.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if metrics["em"] else 1


if __name__ == "__main__":
    sys.exit(main())
