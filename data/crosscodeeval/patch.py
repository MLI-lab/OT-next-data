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

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


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

PATCH_EXPLANATION = (
    "Issue: TaskTrove omitted the benchmark's cross-file context. Its C#, Python and "
    "TypeScript verifiers gave 0.25 reward for short code-like answers and could give "
    "full reward for prefix or leading-identifier matches without a correct completion. "
    "Java used a whole-answer diff that rejected different indentation. Fix: Replace this scoring "
    "with normalized exact match of the first statement, add a reference solution, "
    "and pin verifier dependencies. For uniquely matched upstream tasks, restore retrieved "
    "cross-file excerpts after removing target-file and answer-containing excerpts. "
    "Exclude tasks when the supplied code and known library names do not give the agent "
    "enough information to infer names required by the expected answer. Also exclude "
    "tasks whose scored answer contains no variable, function or other code names."
)
MISSING_NAMES_LABEL = 'Required code names cannot be inferred'
MISSING_NAMES_EXPLANATION = (
    "The expected answer uses names, such as methods or variables, that the agent "
    "cannot infer from the prompt, the available excerpts from other files, or the "
    "filter's list of known library names. None of the available sets of excerpts "
    "provides enough information. The agent would have to guess these names to match "
    "the expected answer."
)
NO_NAMES_LABEL = 'Expected completion contains no code names'
NO_NAMES_EXPLANATION = (
    "The part of the expected answer that is scored contains no variable, function, "
    "class or other code names (identifiers). These tasks were excluded by the "
    "patcher's rule requiring at least one such name in the scored completion."
)
CHANGE_FILE_LABELS = {name: 'context-and-verifier-repaired' for name in
                      ('instruction.md', 'tests/', 'solution/', 'environment/', 'setup_files/context/')}


def change_explanation(context_restored):
    return PATCH_EXPLANATION + ("" if context_restored else
        " Cross-file excerpts were not added for this task.")

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


# Versions installed when the images were built and oracle/no-answer were validated
# (python:3.10-slim, Python 3.10.21, 2026-09-29). Packages they pull in are not pinned.
PIP_PINS = {'pytest': '9.1.1', 'pytest-timeout': '2.4.0'}


def pin_pip_installs(files):
    """Pin the packages named in the Dockerfile's pip installs; an unknown one is an error."""
    name = 'environment/Dockerfile'
    if name not in files:
        return False
    def pin(line):
        words = line.group(0).split()
        for i, word in enumerate(words[3:], 3):
            if word.startswith('-') or '==' in word:
                continue
            if word not in PIP_PINS:
                raise ValueError(f'No pinned version for pip package {word!r}; add it to PIP_PINS')
            words[i] = f'{word}=={PIP_PINS[word]}'
        return ' '.join(words)
    before = files[name].decode('utf-8')
    after = re.sub(r'^RUN pip install [^\n]*$', pin, before, flags=re.M)
    files[name] = after.encode('utf-8')
    return after != before


def benchmark_verifier(files, language, prompt_text=None):
    """Install the scorer and return the new tests/test.sh."""
    files['tests/cceval_verifier.py'] = VERIFIER.encode('utf-8')
    if language == 'python' and prompt_text is not None:
        files['tests/prompt.txt'] = prompt_text.encode('utf-8')
    return (f'#!/bin/bash\nmkdir -p /logs/verifier\n'
            f'exec python3 /tests/cceval_verifier.py --language {language} --prediction /app/solution.txt'
            f' --reference /{gold_path(language)} --prompt /tests/prompt.txt --out /logs/verifier\n').encode()


def context_file_name(name):
    """Repository paths become one file name: '/' -> '__'. Other characters stay as
    they are; the static checks work on a renamed copy when a name needs it."""
    return name.replace('/', '__')


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
    ap.add_argument('--upstream', type=Path, default=Path(os.environ.get('OT_WORKSPACE', '.')) / 'upstream',
                    help='where --all downloads the pinned parquets')
    ap.add_argument('--outdir', type=Path, default=Path(os.environ.get('OT_WORKSPACE', '.')) / 'patched',
                    help='where --all writes the repaired parquets')
    ap.add_argument('--archive', help='the upstream cceval archive; not needed with --pin-only')
    ap.add_argument('--pin-only', action='store_true',
                    help='only pin the pip installs of an already patched parquet (--input, --output)')
    a = ap.parse_args()
    if a.pin_only:
        if not (a.input and a.output):
            raise SystemExit('--pin-only needs --input and --output')
        print(json.dumps(pin_parquet(Path(a.input), Path(a.output))))
        return
    if not a.archive:
        raise SystemExit('--archive is required')
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


def pin_parquet(input_path, output_path):
    """Pin the pip installs of every task; all other files keep their bytes."""
    parent_manifest = input_path.with_suffix('.manifest.json')
    provenance = json.loads(parent_manifest.read_text()) if parent_manifest.exists() else None
    if provenance:
        original = provenance.get('original_parquet')
        if not original:
            raise ValueError('pin-only reporting needs a manifest with original_parquet; rerun the full patcher')
        with Path(original['path']).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != original['sha256']:
                raise ValueError('original Parquet changed since the full patch run')
        for artifact in (output_path.with_suffix('.archive.parquet'), output_path.with_suffix('.manifest.json')):
            if artifact.exists():
                raise ValueError(f'Use a new output location; reporting artifact exists: {artifact}')
    table = pq.read_table(input_path)
    rows = {n: [] for n in table.column_names}
    pinned = 0
    for row in table.to_pylist():
        files = unpack(row['task_binary'])
        if pin_pip_installs(files):
            pinned += 1
            row['task_binary'] = pack(files)
        for n in rows:
            rows[n].append(row[n])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(rows, schema=table.schema), output_path)
    if provenance:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from data.utils.patch_reporting import write_patch_report
        write_patch_report(original['path'], output_path, patcher=__file__, source=provenance['source'],
            dropped={task['task_id']: {'category': task['labels'][0], 'reason': task['reason']}
                     for task in provenance['tasks'] if task['action'] == 'dropped'},
            file_labels=CHANGE_FILE_LABELS,
            change_reasons={task['task_id']: task.get('reason') or change_explanation(
                any(name.startswith('setup_files/context/') for name in task['changed_files']))
                for task in provenance['tasks'] if task['action'] == 'changed'},
            patches=[*provenance.get('patches', []), {'operation': 'pin-only', 'pins': PIP_PINS}])
    return {'input': str(input_path), 'input_sha256': hashlib.sha256(input_path.read_bytes()).hexdigest(),
            'output': str(output_path), 'output_sha256': hashlib.sha256(output_path.read_bytes()).hexdigest(),
            'tasks': table.num_rows, 'pinned': pinned, 'pins': PIP_PINS}


def patch_parquet(input_path, output_path, originals):
    for artifact in (output_path.with_suffix('.archive.parquet'), output_path.with_suffix('.manifest.json')):
        if artifact.exists():
            raise ValueError(f'Use a new output location; reporting artifact exists: {artifact}')
    table = pq.read_table(input_path)
    stats = dict.fromkeys(('rows', 'unique', 'unmatched', 'ambiguous', 'kept', 'dropped', 'dropped_no_identifiers',
                           'oracle_upstream', 'oracle_packaged_gold', 'context_chunks', 'kept_chunks',
                           'dropped_empty', 'dropped_target', 'dropped_gold', 'pinned_pip_installs'), 0)
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
            record(row, lang, gold, p, False, NO_NAMES_EXPLANATION)
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
                names = sorted({name for values in missing.values() for name in (values or [])})
                record(row, lang, gold, p, False,
                       MISSING_NAMES_EXPLANATION + ' Names missing from one or more sets of excerpts: '
                       + ', '.join(names), missing=missing)
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
                files[f'setup_files/context/{i:03d}_{context_file_name(name) or f"context_{i}.txt"}'] = text.encode()
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
        stats['pinned_pip_installs'] += pin_pip_installs(files)
        row['task_binary'] = pack(files)
        for n in rows:
            rows[n].append(row[n])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(rows, schema=table.schema), output_path)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from data.utils.patch_reporting import write_patch_report
    write_patch_report(input_path, output_path, patcher=__file__,
        file_labels=CHANGE_FILE_LABELS,
        change_reasons={entry['task']: change_explanation(entry['retriever'] is not None)
                        for entry in review if entry['kept']},
        source={'dataset': UPSTREAM_REPO, 'revision': UPSTREAM_REVISION,
                'url': f'https://huggingface.co/datasets/{UPSTREAM_REPO}/tree/{UPSTREAM_REVISION}'},
        dropped={entry['task']: {
            'category': (NO_NAMES_LABEL if entry['task'] in stats['dropped_no_identifiers_task_ids']
                         else MISSING_NAMES_LABEL),
            'reason': entry['reason']} for entry in review if not entry['kept']},
        patches=[{'patcher': 'data/crosscodeeval/patch.py',
                  'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}])
    report = json.dumps(stats, indent=2)
    output_path.with_suffix('.report.json').write_text(report + '\n')
    print(report)
    n = write_review(review_dir, review)
    # stdout stays the report as JSON, so it can be piped; notes go to stderr.
    print(f'review: {n} dropped tasks in {review_dir}/dropped.jsonl, sample in {review_dir}/sample.md',
          file=sys.stderr)


# Prepare
"""Download the published repair at its immutable revision, then apply pip pins."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from huggingface_hub import hf_hub_download
import pyarrow as pa
import pyarrow.parquet as pq

REVISION = '12df4483fe99c79ccbb4c923d76ab5a2b042e64a'
prepare_SOURCES = {'csharp-v5': 1353, 'java-v4': 1895, 'python-v3': 432, 'typescript-v3': 3030}


def prepare_main(root):
    root = root.resolve()
    os.environ.setdefault('HF_HOME', str(root / 'cache/huggingface'))
    records, sample = [], []
    for family, count in prepare_SOURCES.items():
        source = f'laion__exp_rpt_crosscodeeval-{family}/tasks.parquet'
        original = hf_hub_download('open-thoughts/TaskTrove', source,
                                   repo_type='dataset', revision=REVISION,
                                   local_dir=root / 'published', cache_dir=root / 'cache/huggingface/hub')
        output = root / 'parquets-pinned' / f'{family}.parquet'
        record = pin_parquet(Path(original), output)
        if record['tasks'] != count:
            raise ValueError(f'Unexpected coverage: {record}')
        records.append(record)
        table = pq.read_table(output)
        sample.append(table.slice(0, 3 if family.startswith(('csharp', 'java')) else 2))
        print(json.dumps(record), flush=True)
    provenance = {'dataset': 'open-thoughts/TaskTrove', 'revision': REVISION, 'files': records}
    (root / 'parquets-pinned/source.json').write_text(json.dumps(provenance, indent=2) + '\n')
    (root / 'sample').mkdir(exist_ok=True)
    pq.write_table(pa.concat_tables(sample), root / 'sample/tasks.parquet')


def prepare_cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    prepare_main(parser.parse_args().root)


# Warmup
"""Warm distinct CrossCodeEval environments and benchmark cached container I/O.

Uses the real Harbor bridge and stages 3 -> 5 -> 4, without publishing or
changing production caches. Inputs are already patched/materialized tasks;
cross-file excerpts are supplied in setup_files, not downloaded Git checkouts.
Explicit paths also allow use outside ZIH. Run only inside a CPU allocation.
"""

import argparse
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def fingerprint(directory):
    """Same file-name/content digest as the patched bridge (not just Dockerfile)."""
    digest = hashlib.sha256()
    for path in sorted(directory.rglob('*')):
        if path.is_file():
            name, content = str(path.relative_to(directory)).encode(), path.read_bytes()
            digest.update(len(name).to_bytes(4, 'big')); digest.update(name)
            digest.update(len(content).to_bytes(4, 'big')); digest.update(content)
    return digest.hexdigest()[:12]


def inventory(tasks):
    groups, languages = {}, {}
    for task in sorted(tasks.iterdir()):
        if not (task / 'task.toml').is_file():
            continue
        if not task.name.startswith('crosscodeeval-'):
            raise ValueError(f'Expected patched CrossCodeEval tasks: {task}')
        if not (task / 'environment/Dockerfile').is_file():
            raise ValueError(f'Missing Dockerfile: {task}')
        key = fingerprint(task / 'environment')
        groups.setdefault(key, []).append(task)
        languages.setdefault(task.name.rsplit('-', 1)[0], task)
    if not groups:
        raise ValueError('No materialized CrossCodeEval tasks found')
    representatives = sorted(set(languages.values()) | {v[0] for v in groups.values()})
    return groups, representatives


def cache_files(cache, keys):
    """Resolve symlinks; copy SIF and the adjacent deferred build companions."""
    result = {}
    for key in keys:
        matches = sorted(cache.glob(f'*-{key}.sif'))
        if not matches:
            continue
        source = matches[0].resolve(strict=True)
        result[f'build_warmup-{key}.sif'] = source
        for suffix in ('.deferred.json', '.overlay.img'):
            companion = source.with_suffix(suffix)
            if companion.exists():
                result[f'build_warmup-{key}{suffix}'] = companion
    return result


def copy_cache(files, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name, source in files.items():
        # Preserve sparse overlays; dereference shared-cache symlinks.
        subprocess.run(['cp', '--sparse=always', '--reflink=auto', '--',
                        str(source), str(destination / name)], check=True)


@contextmanager
def bridge(cache, scratch, out):
    from hpc.validation_worker import free_port, wait_ready
    import harbor
    scratch.mkdir(parents=True)
    out.mkdir(parents=True, exist_ok=True)
    previous = os.environ.copy()
    processes, logs = [], []
    for key in ('APPTAINER_BIND', 'APPTAINER_BINDPATH', 'SINGULARITY_BIND', 'SINGULARITY_BINDPATH'):
        os.environ.pop(key, None)
    os.environ.update(TMPDIR=str(scratch), APPTAINER_TMPDIR=str(scratch / 'build'),
        APPTAINER_NO_MOUNT='hostfs,bind-paths,cwd', APPTAINER_BINDPATH='/etc/resolv.conf:/etc/resolv.conf:ro',
        BRIDGE_USE_FAKEROOT='1', BRIDGE_INSTANCE_REUSE='0', BRIDGE_START_CONCURRENCY='1',
        BRIDGE_START_INTERVAL='0.25', OT_NET_ISOLATION='0', HARBOR_SIF_CACHE=str(cache),
        OT_NETWORK_STATUS_PATH=str(out / 'network.json'))
    (scratch / 'build').mkdir()
    port = free_port(40000 + os.getpid() % 10000)
    url = f'http://127.0.0.1:{port}'
    os.environ['APPTAINER_BRIDGE_URL'] = url
    launcher = ROOT / 'validation/stages/service_entrypoint.py'
    server = Path(harbor.__file__).parent / 'environments/apptainer/server.py'
    try:
        commands = [
            ('server', [str(server), '--host', '127.0.0.1', '--port', str(port)]),
            ('worker', [str(ROOT / 'harbor_patches/bridge_worker.py'), '--bridge-url', url,
                        '--sif-cache', str(cache), '--staging-base', str(scratch / 'instances'), '--num-workers', '2'])]
        for name, command in commands:
            log = (out / f'{name}.log').open('w'); logs.append(log)
            processes.append(subprocess.Popen([sys.executable, '-u', str(launcher), *command],
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True))
            wait_ready(url + '/status', processes, timeout=600, workers=name == 'worker')
        yield url
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.wait()
        for log in logs:
            log.close()
        os.environ.clear(); os.environ.update(previous)


async def measure(task, out, cache, url, journal, label, build_only=False):
    from validation.stages import harbor as runtime
    from validation.stages.runner import parser
    from validation.stages.container_reuse import ReuseScope
    from harbor.trial.trial import Trial
    args = parser().parse_args([str(task), '--backend', 'apptainer', '--submit', 'never',
                               '--trial-cpus', '1', '--trial-memory-mb', '2048'])
    args.environment_kwargs = dict(bridge_url=url, sif_cache=str(cache))
    # Instrument the actual harness copy, not a stand-in filesystem benchmark.
    upload = Trial._upload_setup_files
    upload_times = []
    async def timed_upload(self):
        started = time.monotonic()
        try:
            return await upload(self)
        finally:
            upload_times.append(time.monotonic() - started)
    Trial._upload_setup_files = timed_upload
    try:
        async with ReuseScope() as scope:
            for stage in ([3] if build_only else [3, 5, 4]):
                stage_out = out / f'stage-{stage}'
                stage_out.mkdir(parents=True)
                if stage != 3:
                    await scope.clear_logs()
                upload_times.clear()
                started = time.monotonic()
                if stage == 3:
                    result = await runtime.build_task(task, stage_out, args)
                else:
                    config = runtime.job_config(task, stage_out / 'jobs', args,
                                                'nop' if stage == 5 else 'oracle')
                    job_dir = await runtime.execute_job(config)
                    result = runtime.assess_trials(runtime.trial_results(job_dir), 1, 0 if stage == 5 else 1)
                record = dict(layout=label, task=task.name, stage=stage,
                              seconds=time.monotonic() - started, setup_upload_seconds=list(upload_times),
                              result=result, starts=scope.starts, reuses=scope.reuses)
                journal.write(json.dumps(record) + '\n'); journal.flush(); os.fsync(journal.fileno())
                print(json.dumps(record), flush=True)
                if result['status'] not in ('passed', 'completed'):
                    raise RuntimeError(f'{label} {task.name} stage {stage} failed; see durable outcomes.jsonl')
            cleanup_started = time.monotonic()
        journal.write(json.dumps(dict(layout=label, task=task.name, phase='container_cleanup',
                                      seconds=time.monotonic() - cleanup_started)) + '\n')
        journal.flush(); os.fsync(journal.fileno())
    finally:
        Trial._upload_setup_files = upload


def warmup_main():
    ap = argparse.ArgumentParser(description='Warm distinct CrossCodeEval environments and benchmark cached container I/O.\n\nUses the real Harbor bridge and stages 3 -> 5 -> 4, without publishing or\nchanging production caches. Inputs are already patched/materialized tasks;\ncross-file excerpts are supplied in setup_files, not downloaded Git checkouts.\nExplicit paths also allow use outside ZIH. Run only inside a CPU allocation.')
    ap.add_argument('--tasks', type=Path, required=True)
    ap.add_argument('--cache', type=Path, required=True, help='existing source image cache (read only)')
    ap.add_argument('--out', type=Path, required=True, help='new durable run directory')
    ap.add_argument('--local-root', type=Path, required=True, help='private scratch selected by cluster mapping')
    ap.add_argument('--max-local-gib', type=float, default=8)
    ap.add_argument('--repeats', type=int, default=2)
    ap.add_argument('--warmup-only', action='store_true')
    args = ap.parse_args()
    if not os.environ.get('SLURM_JOB_ID'):
        ap.error('Run in a Slurm CPU allocation')
    if args.repeats < 1 or args.max_local_gib <= 0:
        ap.error('repeats and max-local-gib must be positive')
    args.out = args.out.resolve(); args.local_root = args.local_root.resolve()
    if args.out.is_relative_to(Path.home()) or args.local_root.is_relative_to(Path.home()):
        ap.error('Data and scratch must be outside home')
    args.out.mkdir(parents=True, exist_ok=False)
    groups, representatives = inventory(args.tasks)
    save(args.out / 'inventory.json', dict(groups={k: [p.name for p in v] for k, v in groups.items()},
         representatives=[p.name for p in representatives], source=str(args.tasks.resolve()),
         cluster=os.environ.get('SLURM_CLUSTER_NAME'), partition=os.environ.get('SLURM_JOB_PARTITION'),
         local_root=str(args.local_root), note='Cached-image starts, not cold builds; OS page cache is not flushed.'))
    cache = args.out / 'images'
    started = time.monotonic()
    copy_cache(cache_files(args.cache, groups), cache)
    save(args.out / 'cache-seed.json', dict(seconds=time.monotonic() - started))
    with (args.out / 'outcomes.jsonl').open('x') as journal:
        with bridge(cache, args.out / 'warmup-scratch', args.out / 'warmup-services') as url:
            for key, tasks in groups.items():
                asyncio.run(measure(tasks[0], args.out / 'warmup' / key, cache, url, journal, 'warmup', True))
        if args.warmup_only:
            return
        files = cache_files(cache, groups)
        if len([n for n in files if n.endswith('.sif')]) != len(groups):
            raise RuntimeError('Warmup did not cache every distinct environment')
        task_bytes = sum(p.stat().st_size for task in representatives for p in task.rglob('*') if p.is_file())
        # Conservative apparent size (including sparse overlays) plus writable
        # instance/build headroom. Only one task is live in this initial pilot.
        needed = sum(p.stat().st_size for p in files.values()) + task_bytes + 4 * 2**30
        args.local_root.mkdir(parents=True, exist_ok=True)
        mount = subprocess.check_output(['findmnt', '-T', str(args.local_root), '-no', 'FSTYPE'], text=True).strip()
        if mount == 'tmpfs':
            memory_mb = int(os.environ.get('SLURM_MEM_PER_NODE', '0'))
            if not memory_mb:
                memory_mb = int(os.environ.get('SLURM_MEM_PER_CPU', '0')) * int(os.environ.get('SLURM_CPUS_PER_TASK', '1'))
            if args.max_local_gib * 1024 > memory_mb / 4:
                raise RuntimeError('RAM scratch budget must fit within one quarter of allocated job memory')
        if needed > args.max_local_gib * 2**30 or needed > shutil.disk_usage(args.local_root).free:
            raise RuntimeError(f'Local pilot needs at least {needed} bytes; exceeds budget/free space')
        local = args.local_root / f'cce-warmup-{os.getpid()}'
        local.mkdir()
        try:
            started = time.monotonic()
            copy_cache(files, local / 'images')
            image_seconds = time.monotonic() - started
            started = time.monotonic()
            for task in representatives:
                shutil.copytree(task, local / 'tasks' / task.name)
            save(args.out / 'staging.json', dict(image_seconds=image_seconds,
                 task_seconds=time.monotonic() - started, estimated_peak_bytes=needed, filesystem=mount))
            layouts = [('horse', cache, args.out / 'scratch', False),
                       ('local-images', local / 'images', args.out / 'scratch', False),
                       ('local-work', cache, local / 'scratch', True),
                       ('local-both', local / 'images', local / 'scratch', True)]
            for repeat in range(args.repeats):
                # Reverse order on alternate repetitions to expose warm-cache/order effects.
                for name, images, scratch, local_tasks in (layouts if repeat % 2 == 0 else layouts[::-1]):
                    label = f'{repeat}-{name}'
                    output = args.out / label
                    with bridge(images, scratch / label, output / 'services') as url:
                        for task in representatives:
                            source = local / 'tasks' / task.name if local_tasks else task
                            asyncio.run(measure(source, output / task.name, images, url, journal, label))
            save(args.out / 'complete.json', dict(status='passed', repeats=args.repeats,
                 tasks=len(representatives), layouts=len(layouts)))
        finally:
            shutil.rmtree(local)


def warmup_cli():
    warmup_main()


# Rewards
#!/usr/bin/env python3
"""How to grade a CrossCodeEval answer locally, and which wrong answers are worth
trying. tests/test_dataset_reward_plugins.py imports this module as
`data.<dataset>.patch`; register new reward contracts in that test.
"""
import contextlib
import importlib.util
import io
import json
import re
import tempfile
from pathlib import Path

# Where a trial's answer has to be for the task's tests/test.sh to grade it.
ANSWER_PATH = '/app/solution.txt'


def language(task: Path) -> str:
    return re.search(r'-(\w+)-\d+$', task.name).group(1)


def reference(task: Path) -> str:
    """The graded reference solution, as shipped in the task."""
    lang = language(task)
    return (task / 'tests' / ('expected.txt' if lang == 'java' else 'solution.txt')).read_text()


def grade(task: Path, answer: str) -> float:
    """Reward for `answer`, using this task's own verifier, in this process."""
    lang = language(task)
    spec = importlib.util.spec_from_file_location('v', task / 'tests/cceval_verifier.py')
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    gold_file = task / 'tests' / ('expected.txt' if lang == 'java' else 'solution.txt')
    prompt = task / 'tests/prompt.txt'
    with tempfile.TemporaryDirectory() as d:
        pred = Path(d) / 'solution.txt'
        pred.write_text(answer)
        args = ['--language', lang, '--prediction', str(pred), '--reference', str(gold_file),
                '--out', d + '/out']
        if prompt.exists():
            args += ['--prompt', str(prompt)]
        with contextlib.redirect_stdout(io.StringIO()):
            v.main(args)
        return json.loads((Path(d) / 'out/reward.json').read_text())['reward']


def extra_variants(task: Path, gold: str) -> dict[str, tuple[str, float]]:
    """{label: (answer, expected reward)} beyond the generic reference/empty pair.

    Each of these caught a real verifier defect:
      re-indented reference   whitespace must not matter (Java's old diff failed this)
      lone } ; {              what the retired TaskTrove verifier paid partial credit for
      return null;            the most common trivial first statement
      one identifier renamed  catches a verifier rewarding a prefix or first-token match
    """
    spec = importlib.util.spec_from_file_location('v', task / 'tests/cceval_verifier.py')
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    ids = v.extract_identifiers(gold, language(task))
    near = gold.replace(ids[0], ids[0] + 'Wrong', 1) if ids else gold + ' wrong'
    reindented = '\n'.join('    ' + l.strip() for l in gold.splitlines()) + '\n'
    return {'reference re-indented': (reindented, 1),
            'garbage call': ('__PILOT_WRONG_ANSWER__();', 0),
            'lone }': ('}', 0), 'lone ;': (';', 0), 'lone {': ('{', 0),
            'return null;': ('return null;', 0),
            'one identifier renamed': (near, 0)}


def rewards_cli():
    import argparse
    parser = argparse.ArgumentParser(description="Grade one task with its own verifier")
    parser.add_argument("task", type=Path)
    task = parser.parse_args().task
    gold = reference(task)
    for label, (answer, want) in {"reference": (gold, 1), "empty answer": ("", 0), **extra_variants(task, gold)}.items():
        print(label, grade(task, answer), "expected", want)


if __name__ == '__main__':
    from data.utils.cli import dispatch
    dispatch({'prepare': prepare_cli, 'warmup': warmup_cli, 'rewards': rewards_cli})
    main()
