#!/usr/bin/env python3
"""Patch TaskTrove CrossCodeEval tasks: retrieval context, benchmark verifier, oracle.

For each task in the input parquet:
1. Match it to the pinned upstream record (amazon-science/cceval 40c68d2b) by
   prompt and groundtruth. Unmatched or ambiguous rows keep their files and only
   get steps 4-5.
2. Keep the task only if every identifier of its graded reference is knowable.
   A name is knowable when it occurs in what the agent sees (instruction,
   context excerpts or their file names) as a whole word or by all its
   camelCase/snake_case parts, or is a literal, a python standard-library name,
   a name in KNOWN_NAMES, or a name used in API position by tasks from
   COMMON_REPOS other repositories. Context comes from the first retrieval
   (BM25, UniXcoder, OpenAI; before-cursor only) that makes every name
   knowable; tasks with no such retrieval, or with no identifier at all, are
   dropped.
3. Write that retrieval's excerpts to setup_files/context/, minus empty ones,
   ones from the target file and ones containing the reference.
4. Replace tests/test.sh with the benchmark scorer (VERIFIER, written as
   tests/cceval_verifier.py; python tasks also get tests/prompt.txt) and remove
   the legacy grading bullet from instruction.md.
5. Add an oracle (solution/solve.sh) that writes the reference.

The report (<output>.report.json) lists kept, dropped and unmatched task IDs,
the retrieval used per task and the unknowable names per tried retrieval.
Beside the output it writes review/dropped.jsonl (every dropped task with the
reason and the names that were not inferable) and review/sample.md (40 of them
with their code, to read): the filter and its audit are one script, so they
cannot disagree.
The standard-library list comes from the running interpreter (python_version
in the report).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import types
from pathlib import Path, PurePosixPath

import pyarrow as pa
import pyarrow.parquet as pq

CONTEXT_NOTE = ("\n\nAdditional read-only cross-file context is available under:\n"
                "  /setup_files/context/\n\n"
                "These files are retrieved excerpts from related repository files.\n"
                "They may be incomplete and may begin or end in the middle of a file.\n"
                "Inspect them when deciding the missing continuation.\n")

# Written into every task as tests/cceval_verifier.py (python3 standard library only).
VERIFIER = r'''#!/usr/bin/env python3
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
'''


# The pinned upstream: which TaskTrove files this patch reads, at which revision,
# and the name each repaired set is published under. --all fetches and patches
# every one of them, so "run the patcher on the pinned upstream" is one command.
UPSTREAM_REPO = 'open-thoughts/TaskTrove'
UPSTREAM_REVISION = '96567362fa3c41208e0954317c53767023b420eb'
SOURCES = {
    'csharp': ('deprecated/laion__exp_rpt_crosscodeeval-csharp-v4', 'laion__exp_rpt_crosscodeeval-csharp-v5'),
    'java': ('laion__exp_rpt_crosscodeeval-java-v3', 'laion__exp_rpt_crosscodeeval-java-v4'),
    'python': ('deprecated/laion__exp_rpt_crosscodeeval-python-v2', 'laion__exp_rpt_crosscodeeval-python-v3'),
    'typescript': ('deprecated/laion__exp_rpt_crosscodeeval-typescript-v2', 'laion__exp_rpt_crosscodeeval-typescript-v3'),
}

RETRIEVERS = ('rg1_bm25', 'rg1_unixcoder_cosine_sim', 'rg1_openai_cosine_sim')
COMMON_REPOS = 3
LITERALS = frozenset('true false null undefined None True False this self super NaN Infinity void'.split())
VERBS = frozenset("""get set is has to from load build create make add remove update find read
write parse init new on handle compute check fetch put with use apply run do try can should
validate convert render register resolve format open close start stop enable disable reset
clear delete save send receive emit ensure as into for by of at""".split())
# Known without context: keywords missing from the benchmark's keyword lists,
# language globals, standard and dominant framework APIs (from a review of
# names that alone blocked dropped tasks).
KNOWN_NAMES = {
    'csharp': frozenset("""where var async await dynamic partial yield nameof
        System Console Convert Math String Guid DateTime TimeSpan Enumerable Dictionary List HashSet
        Exception ArgumentNullException ArgumentException InvalidOperationException NotImplementedException
        Environment Path File Directory Regex Encoding CancellationToken Task IEnumerable Func Action
        StringComparer StringComparison StringBuilder Nullable Tuple KeyValuePair
        Rigidbody Collider Collision AudioClip AudioSource Material Transform GameObject Vector2 Vector3
        Quaternion MonoBehaviour Debug Instantiate Destroy Time Mathf Input Camera Color Animator Physics
        Renderer SpriteRenderer Texture2D Sprite Coroutine WaitForSeconds Resources Random
        HarmonyPatch HarmonyPrefix HarmonyPostfix HarmonyBefore HarmonyAfter HarmonyPriority
        __instance __result __state""".split()),
    'java': frozenset("""record sealed permits yield
        System Integer Long Double Float Boolean Character Byte Short String Math Collections Arrays Objects
        Optional List Map Set HashMap ArrayList LinkedList HashSet TreeMap Collectors Stream Thread Runnable
        Exception RuntimeException IllegalArgumentException IllegalStateException NullPointerException
        StringBuilder Files Paths Path UUID Duration Instant LocalDate LocalDateTime TimeUnit Logger Random
        singleton singletonList emptyList emptyMap unmodifiableList containsValue relativize drawOval
        currentTimeMillis nanoTime printStackTrace parseInt valueOf toString equals hashCode format
        requireNonNull assertEquals assertTrue assertFalse assertNotNull assertNull
        getAndIncrement getDeclaredConstructor""".split()),
    'python': frozenset("""assertEqual assertEquals assertTrue assertFalse assertIn assertIsNone assertRaises
        asarray zeros ones reshape flat ndarray iloc loc DataFrame Series
        requires_grad_ no_grad tensor cuda device vmap jit grad""".split()),
    'typescript': frozenset("""unknown keyof never readonly infer satisfies declare override namespace as is asserts
        type of get set
        JSON Math Object Array Promise Number String Boolean Date Map Set WeakMap Symbol Error TypeError RegExp
        console window document navigator globalThis process Buffer setTimeout setInterval clearTimeout
        fetch Response Request Headers URL URLSearchParams Iterable Iterator IterableIterator
        Partial Record Pick Omit Required Readonly ReturnType Parameters Awaited NonNullable
        Uint8Array ArrayBuffer TextEncoder TextDecoder byteLength GPUBufferUsage GPUDevice GPUBuffer mappedAtCreation
        closest querySelector querySelectorAll addEventListener getBoundingClientRect
        lstatSync statSync readFileSync writeFileSync existsSync readdirSync mkdirSync
        join resolve dirname basename tap""".split()),
}
# API position: after '.', 'new ' or '::', or before '(' or '<' (not prose in comments/strings).
API_USE = re.compile(r'(?:(?<=\.)|(?<=\bnew )|(?<=::))[A-Za-z_][A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_]*(?=\s*[(<])')


def gold_path(language):
    return 'tests/expected.txt' if language == 'java' else 'tests/solution.txt'


def load_verifier():
    module = types.ModuleType('cceval_verifier')
    exec(compile(VERIFIER, 'cceval_verifier.py', 'exec'), module.__dict__)
    return module


_verifier = None


def verifier_module():
    global _verifier
    if _verifier is None:
        _verifier = load_verifier()
    return _verifier


def key(lang, prompt_text, gold):
    return hashlib.sha256(json.dumps([lang, prompt_text, gold], ensure_ascii=False).encode()).hexdigest()


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def unpack(blob):
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as t:
        return {m.name.removeprefix("./"): t.extractfile(m).read() for m in t if m.isfile()}


def pack(files):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith('.sh') else 0o644
            t.addfile(info, io.BytesIO(data))
    return b.getvalue()


def prompt(files):
    """The code to complete. Source comments can contain Markdown fences, so the
    fence before "## Your Task" ends the block, not the first one."""
    s = files.get('instruction.md', b'').decode('utf8', 'replace')
    m = re.search(r"## Context \(Code to Complete\)\s*\n```[^\n]*\n(.*?)\n```[ \t]*(?=\n+## Your Task(?:\n|$)|\s*\Z)", s, re.S)
    return m.group(1) if m else None


def correct_grading_instruction(instruction):
    """Drop the legacy grading bullet and its partial-credit promises."""
    before, separator, task = instruction.rpartition("\n## Your Task")
    if not separator:
        return instruction
    task = re.sub(r"\s+It\s+also accepts[^\n]*(?:\n(?![ \t]*\n)[^\n]*)*", "", task)
    task = re.sub(r"\n- The verifier byte-compares your fragment to a reference[^\n]*(?:\n(?![ \t]*\n)[^\n]*)*", "", task)
    return before + separator + task


def add_oracle(files, language, original=None):
    """solution/solve.sh copies the reference (upstream groundtruth when matched)."""
    reference = files[gold_path(language)]
    source = 'packaged_gold'
    if original is not None:
        reference = original['groundtruth'].encode('utf-8')
        if reference != files[gold_path(language)]:
            raise ValueError('Upstream groundtruth differs from the verifier reference')
        source = 'upstream'
    if not reference.strip():
        raise ValueError(f'Empty oracle reference: {gold_path(language)}')
    files['solution/solution_snippet.txt'] = reference
    files['solution/solve.sh'] = b'#!/bin/bash\nset -euo pipefail\nmkdir -p /app\ncp /solution/solution_snippet.txt /app/solution.txt\n'
    return source


def benchmark_verifier(files, language, prompt_text=None):
    """Install the scorer and return the new tests/test.sh."""
    files['tests/cceval_verifier.py'] = VERIFIER.encode('utf-8')
    if language == 'python' and prompt_text is not None:
        files['tests/prompt.txt'] = prompt_text.encode('utf-8')
    return (f'#!/bin/bash\nmkdir -p /logs/verifier\n'
            f'exec python3 /tests/cceval_verifier.py --language {language} --prediction /app/solution.txt'
            f' --reference /{gold_path(language)} --prompt /tests/prompt.txt --out /logs/verifier\n').encode()


def whole_word(ident, text):
    return re.search(r'(?<![A-Za-z0-9_])' + re.escape(ident) + r'(?![A-Za-z0-9_])', text) is not None


def parts_of(ident):
    parts = [p for p in verifier_module().split_identifier_into_parts(ident) if p and p != '_']
    if len(parts) > 1 and parts[0].lower() in VERBS:
        parts = parts[1:]
    return [p.lower() for p in parts if len(p) >= 2 and not p.isdigit()]


_stdlib_names = None


def python_stdlib_names():
    """Attribute names of builtins and of every importable stdlib module and its classes."""
    global _stdlib_names
    if _stdlib_names is None:
        import builtins, importlib, inspect, warnings
        names = set(dir(builtins))
        skip = {'antigravity', 'this', 'idlelib', 'turtledemo', 'tkinter.tix', '__main__'}
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for mod in sorted(sys.stdlib_module_names - skip):
                try:
                    m = importlib.import_module(mod)
                except Exception:
                    continue
                names.update(dir(m))
                for _, cls in inspect.getmembers(m, inspect.isclass):
                    names.update(dir(cls))
        _stdlib_names = frozenset(n for n in names if not n.startswith('_'))
    return _stdlib_names


def classify(ident, text, is_common=lambda ident: False, stdlib=frozenset(), known=frozenset()):
    """'word' | 'parts' | 'trivial' | 'literal' | 'stdlib' | 'known' | 'common' | 'missing'"""
    if whole_word(ident, text):
        return 'word'
    parts = parts_of(ident)
    if not parts:
        return 'trivial'
    if all(p in text.lower() for p in parts):
        return 'parts'
    if ident in LITERALS:
        return 'literal'
    if ident in stdlib:
        return 'stdlib'
    if ident in known:
        return 'known'
    return 'common' if is_common(ident) else 'missing'


def reference_identifiers(gold, lang, prompt_text=''):
    """Identifiers of the graded reference, computed exactly as the verifier does."""
    v = verifier_module()
    target = v.remove_comments(v.postprocess_code_lines(prompt_text, gold, None, lang), lang)
    return sorted(set(v.extract_identifiers(target, lang)))


def reference_verdicts(gold, lang, text, is_common=lambda ident: False, stdlib=frozenset(), prompt_text=''):
    known = KNOWN_NAMES.get(lang, frozenset())
    return {i: classify(i, text, is_common, stdlib, known) for i in reference_identifiers(gold, lang, prompt_text)}


def recoverable(verdicts):
    return 'missing' not in verdicts.values()


def context_chunks(record, gold):
    """Retrieved excerpts minus empty ones, target-file ones and ones containing the reference."""
    target = PurePosixPath(record.get('metadata', {}).get('file', '')).as_posix()
    counts = dict.fromkeys(('context_chunks', 'kept_chunks', 'dropped_empty', 'dropped_target', 'dropped_gold'), 0)
    kept = []
    for c in (record.get('crossfile_context') or {}).get('list', []):
        text = c.get('retrieved_chunk', '')
        name = PurePosixPath(c.get('filename', '')).as_posix()
        counts['context_chunks'] += 1
        if not text.strip():
            counts['dropped_empty'] += 1
        elif name == target:
            counts['dropped_target'] += 1
        elif gold.strip() and (gold.strip() in text or norm(gold) in norm(text)):
            counts['dropped_gold'] += 1
        else:
            kept.append((name, text))
            counts['kept_chunks'] += 1
    return kept, counts


def load_original(archive, retrievers=RETRIEVERS):
    """{retriever: {key: [records]}} from the pinned upstream archive."""
    idx = {r: {} for r in retrievers}
    with tarfile.open(archive, 'r:*') as t:
        for m in t:
            name = PurePosixPath(m.name).name
            retriever = name[len('line_completion_'):-len('.jsonl')]
            if not (m.isfile() and name.startswith('line_completion_') and name.endswith('.jsonl') and retriever in idx):
                continue
            lang = PurePosixPath(m.name).parts[0]
            for line in t.extractfile(m):
                r = json.loads(line)
                idx[retriever].setdefault(key(lang, r['prompt'], r['groundtruth']), []).append(r)
    return idx


def choose_context(files, lang, prompt_text, gold, k, matches, originals, is_common, stdlib):
    """First retrieval whose excerpts make every reference name knowable, plus the
    unknowable names per tried retrieval (None: no unique record there)."""
    instruction = files['instruction.md'].decode('utf8', 'replace')
    missing = {}
    for retriever in RETRIEVERS:
        candidates = matches if retriever == 'rg1_bm25' else originals[retriever].get(k, [])
        if len(candidates) != 1:
            missing[retriever] = None
            continue
        kept, counts = context_chunks(candidates[0], gold)
        visible = instruction + '\n' + '\n'.join(n for n, _ in kept) + '\n' + '\n'.join(t for _, t in kept)
        verdicts = reference_verdicts(gold, lang, visible, is_common, stdlib, prompt_text)
        if recoverable(verdicts):
            return (retriever, kept, counts, verdicts), missing
        missing[retriever] = [i for i, v in verdicts.items() if v == 'missing']
    return None, missing


def graded_reference(gold, lang, prompt_text=''):
    """The part of the reference the verifier actually compares."""
    return verifier_module().postprocess_code_lines(prompt_text, gold, None, lang)


def write_review(out, records, sample=40, seed=20260916):
    """Why each dropped task was dropped, plus a sample of them to read.

    Only the dropped ones are written out: the kept tasks are the output parquet,
    and their verdicts are counted in the report.
    """
    out.mkdir(parents=True, exist_ok=True)
    dropped = [r for r in records if not r['kept']]
    with open(out / 'dropped.jsonl', 'w') as f:
        for r in dropped:
            f.write(json.dumps(r) + '\n')
    import random
    picked = random.Random(seed).sample(dropped, min(sample, len(dropped)))
    md = ['# Dropped tasks, sampled for review', '',
          f'{len(dropped)} of {len(records)} tasks were dropped; {len(picked)} shown.', '']
    for r in picked:
        md += [f"## {r['task']} ({r['language']}) - {r['reason']}", '',
               '```', r['graded_reference'].strip(), '```', '',
               f"unknowable names: {', '.join(r['missing_names']) or '(none)'}", '']
    (out / 'sample.md').write_text('\n'.join(md))
    return len(dropped)


def fetch_upstream(dest):
    """The pinned upstream parquets, downloaded once into <dest>/<source name>/."""
    from huggingface_hub import hf_hub_download
    out = {}
    for lang, (src, published) in SOURCES.items():
        path = Path(hf_hub_download(UPSTREAM_REPO, f'{src}/tasks.parquet', repo_type='dataset',
                                    revision=UPSTREAM_REVISION, local_dir=dest))
        out[lang] = (path, published)
        print(f'{src}/tasks.parquet -> {path}', file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--input', help='one upstream parquet')
    ap.add_argument('--output', help='where the repaired parquet goes')
    ap.add_argument('--all', action='store_true',
                    help=f'fetch and patch every source of {UPSTREAM_REPO} at {UPSTREAM_REVISION[:8]}')
    ap.add_argument('--upstream', type=Path, default=Path(os.environ.get('PILOT_ROOT', '.')) / 'upstream',
                    help='where --all downloads the pinned parquets')
    ap.add_argument('--outdir', type=Path, default=Path(os.environ.get('PILOT_ROOT', '.')) / 'patched',
                    help='where --all writes the repaired parquets')
    ap.add_argument('--archive', required=True)
    a = ap.parse_args()
    if a.all:
        originals = load_original(a.archive)
        for lang, (src, published) in fetch_upstream(a.upstream).items():
            out = a.outdir / published / 'tasks.parquet'
            print(f'\n=== {lang}: {src} -> {out}', file=sys.stderr)
            patch_parquet(src, out, originals)
        return
    if not (a.input and a.output):
        raise SystemExit('pass --all, or both --input and --output')
    patch_parquet(Path(a.input), Path(a.output), load_original(a.archive))


def patch_parquet(input_path, output_path, originals):
    table = pq.read_table(input_path)
    stats = dict.fromkeys(('rows', 'unique', 'unmatched', 'ambiguous', 'kept', 'dropped', 'dropped_no_identifiers',
                           'oracle_upstream', 'oracle_packaged_gold', 'context_chunks', 'kept_chunks',
                           'dropped_empty', 'dropped_target', 'dropped_gold'), 0)
    stats.update(unmatched_task_ids=[], ambiguous_task_ids=[], dropped_task_ids=[], dropped_no_identifiers_task_ids=[],
                 context_source={r: 0 for r in RETRIEVERS}, context_source_task_ids={r: [] for r in RETRIEVERS},
                 missing_by_retriever={}, identifier_verdicts={}, common_repos=COMMON_REPOS,
                 python_version=sys.version.split()[0])

    # Pass 1: match rows; per repository, the names used in API position in its BM25 view.
    prepared, repos_with = [], {}
    for row in table.to_pylist():
        files = unpack(row['task_binary'])
        lang = re.search(r'crosscodeeval-(\w+)-', row['path']).group(1)
        p = prompt(files)
        gold = files.get('tests/solution.txt', files.get('tests/expected.txt', b'')).decode('utf8', 'replace')
        k = key(lang, p, gold) if p is not None else None
        matches = originals['rg1_bm25'].get(k, []) if k else []
        match = matches[0] if len(matches) == 1 else None
        repo = match['metadata'].get('repository', row['path']) if match else row['path']
        text = files['instruction.md'].decode('utf8', 'replace')
        if match:
            text += '\n' + '\n'.join(t for _, t in context_chunks(match, gold)[0])
        for name in set(API_USE.findall(text)):
            repos_with.setdefault(name, set()).add(repo)
        prepared.append((row, files, lang, p, gold, k, matches, match, repo))
    stdlib = python_stdlib_names() if any(x[2] == 'python' for x in prepared) else frozenset()

    review_dir = output_path.parent / 'review'
    rows = {n: [] for n in table.column_names}
    review = []

    def record(row, lang, gold, p, kept, reason, retriever=None, verdicts=None, missing=None):
        review.append({'task': row['path'], 'language': lang, 'kept': kept, 'reason': reason,
                       'retriever': retriever, 'verdicts': verdicts or {},
                       'missing_names': sorted({n for names in (missing or {}).values()
                                                for n in (names or [])}),
                       'graded_reference': graded_reference(gold, lang, p or '')})

    for row, files, lang, p, gold, k, matches, match, repo in prepared:
        stats['rows'] += 1
        if not reference_identifiers(gold, lang, p or ''):
            stats['dropped_no_identifiers'] += 1
            stats['dropped_no_identifiers_task_ids'].append(row['path'])
            record(row, lang, gold, p, False, 'no identifier in the graded reference')
            continue
        if match is None:
            stats['unmatched' if not matches else 'ambiguous'] += 1
            stats['unmatched_task_ids' if not matches else 'ambiguous_task_ids'].append(row['path'])
        else:
            stats['unique'] += 1
            is_common = lambda ident, repo=repo: len(repos_with.get(ident, set()) - {repo}) >= COMMON_REPOS
            chosen, missing = choose_context(files, lang, p or '', gold, k, matches, originals, is_common, stdlib)
            if missing:
                stats['missing_by_retriever'][row['path']] = missing
            if chosen is None:
                stats['dropped'] += 1
                stats['dropped_task_ids'].append(row['path'])
                record(row, lang, gold, p, False, 'no retrieval makes every name knowable', missing=missing)
                continue
            retriever, kept, counts, verdicts = chosen
            record(row, lang, gold, p, True, 'kept', retriever, verdicts, missing)
            stats['context_source'][retriever] += 1
            stats['context_source_task_ids'][retriever].append(row['path'])
            for n, c in counts.items():
                stats[n] += c
            for v in verdicts.values():
                stats['identifier_verdicts'][v] = stats['identifier_verdicts'].get(v, 0) + 1
            for i, (name, text) in enumerate(kept):
                files[f'setup_files/context/{i:03d}_{name.replace("/", "__") or f"context_{i}.txt"}'] = text.encode()
            instruction = files['instruction.md'].decode('utf8', 'replace')
            if kept and '/setup_files/context/' not in instruction:
                files['instruction.md'] = (instruction.rstrip() + CONTEXT_NOTE).encode()
        if match is None:
            record(row, lang, gold, p, True, 'kept without context (no unique upstream match)')
        stats['kept'] += 1
        if 'tests/test.sh' in files:
            files['tests/test.sh'] = benchmark_verifier(files, lang, p)
        files['instruction.md'] = correct_grading_instruction(files['instruction.md'].decode('utf-8')).encode('utf-8')
        stats[f'oracle_{add_oracle(files, lang, match)}'] += 1
        row['task_binary'] = pack(files)
        for n in rows:
            rows[n].append(row[n])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(rows, schema=table.schema), output_path)
    report = json.dumps(stats, indent=2)
    output_path.with_suffix('.report.json').write_text(report + '\n')
    print(report)
    n = write_review(review_dir, review)
    # stdout stays the report as JSON, so it can be piped; notes go to stderr.
    print(f'review: {n} dropped tasks in {review_dir}/dropped.jsonl, sample in {review_dir}/sample.md',
          file=sys.stderr)


if __name__ == '__main__':
    main()
