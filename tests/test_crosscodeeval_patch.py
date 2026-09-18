import io
import json
import subprocess
import sys
import tarfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data.crosscodeeval import patch as patcher


def test_prompt_preserves_code_fences_inside_source_comments():
    code = '"""Example:\n```python\nexample()\n```\n"""\nvalue = object.'
    instruction = (
        "## Context (Code to Complete)\n```python\n"
        + code
        + "\n```\n\n## Your Task\nWrite the continuation.\n"
        + "```bash\nwrite_solution example\n```\n"
    )
    assert patcher.prompt({"instruction.md": instruction.encode()}) == code


@pytest.mark.parametrize("language", ["csharp", "java", "python", "typescript"])
@pytest.mark.parametrize("match_count", [0, 1, 2])
def test_patched_oracle_writes_complete_reference(
    tmp_path, monkeypatch, capsys, language, match_count
):
    # Multiline source, shell metacharacters, and no final newline must survive.
    # Two statements: the benchmark's truncation would cut the reference itself,
    # so the verifier compares the full answer for this task.
    reference = b'Thing> values = "$HOME $(false) `false`";\n    return values;'
    if language == "python":
        reference = b'values = thing("$HOME $(false) `false`")\nreturn values'
    gold_path = "tests/expected.txt" if language == "java" else "tests/solution.txt"
    files = {
        "instruction.md": b"## Context (Code to Complete)\n```\nprefix\n```\n",
        gold_path: reference,
        "tests/test.sh": b"#!/bin/bash\nexit 0\n",
        "environment/Dockerfile": b"FROM python:3.10-slim\n",
        "solution/solve.sh": b"#!/bin/bash\nexit 1\n",
    }
    source = tmp_path / "input.parquet"
    output = tmp_path / "output.parquet"
    archive = tmp_path / "original.tar"
    table = pa.table(
        {
            "path": [f"crosscodeeval-{language}-0000"],
            "task_binary": [patcher.pack(files)],
            "other": ["preserved"],
        }
    )
    pq.write_table(table, source)
    record = {
        "prompt": "prefix" if match_count else "different prefix",
        "groundtruth": reference.decode(),
        "metadata": {"file": "target"},
        "crossfile_context": {
            "list": [{"filename": "sibling", "retrieved_chunk": "related code: Thing values thing"}]
        },
    }
    with tarfile.open(archive, "w") as tar:
        data = ((json.dumps(record) + "\n") * max(1, match_count)).encode()
        member = tarfile.TarInfo(f"{language}/line_completion_rg1_bm25.jsonl")
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "patcher",
            "--input",
            str(source),
            "--output",
            str(output),
            "--archive",
            str(archive),
        ],
    )
    patcher.main()
    report = json.loads(output.with_suffix(".report.json").read_text())
    assert report == json.loads(capsys.readouterr().out)
    task_id = f"crosscodeeval-{language}-0000"
    assert report["unmatched_task_ids"] == ([task_id] if match_count == 0 else [])
    assert report["ambiguous_task_ids"] == ([task_id] if match_count == 2 else [])
    assert report["oracle_upstream"] == int(match_count == 1)
    assert report["oracle_packaged_gold"] == int(match_count != 1)
    assert report["context_source"] == {"rg1_bm25": int(match_count == 1), "rg1_unixcoder_cosine_sim": 0, "rg1_openai_cosine_sim": 0}
    assert report["kept"] == 1 and report["dropped"] == 0
    result = pq.read_table(output)
    assert result.schema == table.schema
    assert result["path"].equals(table["path"])
    assert result["other"].equals(table["other"])
    blob = result["task_binary"][0].as_py()
    patched = patcher.unpack(blob)
    assert patched[gold_path] == reference
    assert patched["solution/solution_snippet.txt"] == reference
    assert patched["environment/Dockerfile"] == files["environment/Dockerfile"]
    if language == "python":
        assert patched["tests/prompt.txt"] == b"prefix"
    else:
        assert "tests/prompt.txt" not in patched
    assert patched["tests/cceval_verifier.py"] == patcher.VERIFIER.encode()
    assert all(
        reference not in text
        for name, text in patched.items()
        if name.startswith("setup_files/")
    )
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        assert tar.getmember("solution/solve.sh").mode & 0o111

    # Relocate container paths into tmp_path, then execute the generated shell.
    solution = tmp_path / "solution"
    solution.mkdir()
    (solution / "solution_snippet.txt").write_bytes(
        patched["solution/solution_snippet.txt"]
    )
    app = tmp_path / "app"
    script = patched["solution/solve.sh"].decode().replace("/solution/", f"{solution}/")
    script = script.replace("/app", str(app))
    subprocess.run(["bash", "-c", script], check=True)
    assert (app / "solution.txt").read_bytes() == reference
    tests = tmp_path / "tests"
    tests.mkdir()
    for name, data in patched.items():  # the verifier, gold and prompt files
        if name.startswith("tests/"):
            (tests / name[len("tests/"):]).write_bytes(data)
    logs = tmp_path / "logs"
    verifier = patched["tests/test.sh"].decode()
    for container, local in [("/app", app), ("/tests", tests), ("/logs", logs)]:
        verifier = verifier.replace(container, str(local))
    subprocess.run(["bash", "-c", verifier], check=True)
    assert (logs / "verifier/reward.txt").read_text().strip() == "1"
    rewards = json.loads((logs / "verifier/reward.json").read_text())
    assert rewards["reward"] == 1 and rewards["es"] == 100 and rewards["id_f1"] == 1
    (app / "solution.txt").write_bytes(b"deliberately wrong answer")
    assert subprocess.run(["bash", "-c", verifier]).returncode != 0
    assert (logs / "verifier/reward.txt").read_text().strip() == "0"
    assert json.loads((logs / "verifier/reward.json").read_text())["reward"] == 0


cceval_verifier = patcher.load_verifier()


def _score(tmp_path, language, reference, prediction, prompt=None):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "gold").write_text(reference)
    (tmp_path / "pred").write_text(prediction)
    args = ["--language", language, "--prediction", str(tmp_path / "pred"),
            "--reference", str(tmp_path / "gold"), "--out", str(tmp_path / "out")]
    if prompt is not None:
        (tmp_path / "prompt").write_text(prompt)
        args += ["--prompt", str(tmp_path / "prompt")]
    rc = cceval_verifier.main(args)
    rewards = json.loads((tmp_path / "out/reward.json").read_text())
    assert (rc == 0) == (rewards["reward"] == 1)
    assert (tmp_path / "out/reward.txt").read_text().strip() == str(rewards["reward"])
    return rewards


def test_benchmark_scorer_rules(tmp_path):
    # Leading indentation, a trailing comment and text after the first ';'
    # do not matter (java-0638 in the diagnostic was rejected for indentation).
    r = _score(tmp_path / "java", "java", "return values;",
               "    return values; // done\nSystem.exit(1);\n")
    assert r == {"reward": 1, "es": 100, "id_em": 1, "id_precision": 1, "id_recall": 1, "id_f1": 1}
    # A leading bracket is not a statement by itself: the statement runs on to
    # the next `;`/`{`/`}` (the benchmark skipped index 0 and kept everything).
    assert _score(tmp_path / "cs0", "csharp", "} as Thing;", "} as Thing;\nfoo();")["reward"] == 1
    assert _score(tmp_path / "cs1", "csharp", "} as Thing;", "\n} as Thing;\nfoo();")["reward"] == 1
    assert _score(tmp_path / "cs2", "csharp", "});", "});\nfoo();")["reward"] == 1
    assert _score(tmp_path / "cs3", "csharp", "}", "}\nfoo();")["reward"] == 0
    # A near miss keeps the soft metrics: one of two identifiers matches.
    r = _score(tmp_path / "ts", "typescript", "return this.mqttConfig.load();",
               "return this.config.load();")
    assert r["reward"] == 0 and r["id_em"] == 0 and r["es"] == 89
    assert r["id_precision"] == 0.5 and r["id_recall"] == 0.5 and r["id_f1"] == 0.5
    # A reference the benchmark's truncation would cut is truncated too, so
    # only the first statement of either side counts (typescript golds such as
    # `foo(x, { a: 1 });` are cut at the first bracket).
    assert _score(tmp_path / "two0", "java", "a();\nb();", "a();\nb();\n")["reward"] == 1
    assert _score(tmp_path / "two1", "java", "a();\nb();", "a();\nb();\nc();\n")["reward"] == 1
    assert _score(tmp_path / "two2", "java", "a();\nb();", "a();")["reward"] == 1
    assert _score(tmp_path / "two3", "java", "a();\nb();", "b();")["reward"] == 0
    # An inline `{ ... }` group is part of the statement: the whole call is graded.
    assert _score(tmp_path / "ts2", "typescript", "foo(x, { a: 1 });", "foo(x, {")["reward"] == 0
    assert _score(tmp_path / "ts3", "typescript", "foo(x, { a: 1 });", "foo(x, { a: 1 });\nbar();")["reward"] == 1
    assert _score(tmp_path / "ts4", "typescript", "const { a, b } = f();", "const { a, b } = f();\nnext();")["reward"] == 1
    assert _score(tmp_path / "ts5", "typescript", "if (x) {", "if (x) {\n  y();\n}")["reward"] == 1
    # csharp/java keep cutting at the first `{`, so a one-line auto-property
    # still matches a reference whose `{` ends the line.
    assert _score(tmp_path / "cs_prop", "csharp", "MessageType MsgType {\n get; set; }", "MessageType MsgType { get; set; }")["reward"] == 1
    assert json.loads((tmp_path / "two0/out/metrics.json").read_text())["target_truncated"] is True
    assert json.loads((tmp_path / "java/out/metrics.json").read_text())["target_truncated"] is False
    # A missing answer file scores 0 on every metric instead of crashing.
    (tmp_path / "missing").mkdir()
    (tmp_path / "missing/gold").write_text("x = 1")
    assert cceval_verifier.main(["--language", "python", "--prediction", str(tmp_path / "missing/none"),
                                 "--reference", str(tmp_path / "missing/gold"), "--out", str(tmp_path / "missing/out")]) == 1
    assert json.loads((tmp_path / "missing/out/reward.json").read_text())["es"] == 0


def test_python_statement_truncation_uses_prompt(tmp_path):
    # The first prefix that completes the prompt into a valid module and is
    # followed by a newline ends the statement; the prompt alone decides that.
    r = _score(tmp_path / "a", "python", "bar)", "bar)\nprint(1)\n", prompt="def f():\n    return foo(")
    assert r["reward"] == 1
    assert json.loads((tmp_path / "a/out/metrics.json").read_text())["pred"] == "bar)"
    # Without the prompt the same answer is cut after 'bar' is no longer valid.
    r = _score(tmp_path / "b", "python", "bar)", "bar)\nprint(1)\n")
    assert r["reward"] == 0
    # A multi-line statement stays whole; a second statement is dropped.
    r = _score(tmp_path / "c", "python", "x = foo(\n    1)", "x = foo(\n    1)\ny = 2\n", prompt="")
    assert r["reward"] == 1
    # A two-statement reference is compared in full instead.
    r = _score(tmp_path / "d", "python", "x = 1\ny = 2", "x = 1\ny = 2\n", prompt="")
    assert r["reward"] == 1


def test_oracle_rejects_mismatched_upstream_reference():
    with pytest.raises(ValueError, match="differs"):
        patcher.add_oracle(
            {"tests/solution.txt": b"correct"}, "python", {"groundtruth": "wrong"}
        )


@pytest.mark.parametrize("reference", [b"", b" \n\t"])
def test_oracle_rejects_empty_reference(reference):
    with pytest.raises(ValueError, match="Empty"):
        patcher.add_oracle({"tests/solution.txt": reference}, "python")


def test_oracle_rejects_missing_reference():
    with pytest.raises(KeyError, match="tests/solution.txt"):
        patcher.add_oracle({}, "python")


@pytest.mark.parametrize("promise", [
    " It\n  also accepts a leading-identifier match, and gives partial credit (0.25) for any non-empty\n  `python`-shaped fragment under ~400 chars.",
    " It also accepts:\n    * a leading-identifier match, and\n    * a non-empty partial credit.\n  So writing something is strictly better than writing nothing.",
])
def test_removes_only_legacy_grading_promises(promise):
    code = '# partial credit (0.25) in a source comment\nvalue = object.'
    prefix = "## Context (Code to Complete)\n```python\n" + code + "\n```\n\n## Your Task\n- The verifier byte-compares your fragment after whitespace normalisation."
    suffix = "\n\n### Worked example\nKEEP THIS\n"
    original = prefix + promise + suffix
    corrected = patcher.correct_grading_instruction(original)
    assert corrected == prefix + suffix
    assert patcher.prompt({"instruction.md": corrected.encode()}) == code
    assert patcher.correct_grading_instruction(corrected) == corrected


@pytest.mark.parametrize("sentence", [
    "- The verifier byte-compares your fragment to a reference after whitespace normalisation.",
    "- The verifier byte-compares your fragment to a reference fragment after\n  whitespace normalisation (CRLF -> LF, trailing whitespace stripped,\n  leading and trailing blank lines stripped).",
])
def test_removes_legacy_grading_sentence(sentence):
    original = "## Context (Code to Complete)\n```java\nx.\n```\n\n## Your Task\n- Output ONLY the fragment.\n" + sentence + "\n\n### Worked example\nKEEP\n"
    corrected = patcher.correct_grading_instruction(original)
    assert corrected == "## Context (Code to Complete)\n```java\nx.\n```\n\n## Your Task\n- Output ONLY the fragment.\n\n### Worked example\nKEEP\n"
    assert patcher.correct_grading_instruction(corrected) == corrected


def test_reference_recoverability_verdicts():
    text = "class Course {\n  public List<Student> Students;\n  var cfg = getMqttConfig();\n}"
    verdicts = patcher.reference_verdicts("Student> Roster = mqttConfig.load(this.x, false);", "csharp", text,
                                          is_common=lambda ident: ident == "load")
    # `this` is a C# keyword and never counts as an identifier.
    assert verdicts == {"Student": "word", "Roster": "missing", "mqttConfig": "parts", "load": "common",
                        "x": "trivial", "false": "literal"}
    assert not patcher.recoverable(verdicts)
    assert patcher.recoverable({k: v for k, v in verdicts.items() if k != "Roster"})
    stdlib = patcher.python_stdlib_names()
    assert "iterdir" in stdlib and "nametowidget" in stdlib and "decompose_fori_loop" not in stdlib
    assert patcher.reference_verdicts("sorted(d.iterdir())", "python", "d = task_dir", stdlib=stdlib) == {
        "d": "word", "iterdir": "stdlib", "sorted": "stdlib"}


def _archive_with(path, chunks_by_retriever, reference, language="java"):
    with tarfile.open(path, "w") as tar:
        for retriever, chunk in chunks_by_retriever.items():
            record = {"prompt": "prefix", "groundtruth": reference, "metadata": {"file": "target", "repository": "r1"},
                      "crossfile_context": {"list": [{"filename": "sibling", "retrieved_chunk": chunk}]}}
            data = (json.dumps(record) + "\n").encode()
            member = tarfile.TarInfo(f"{language}/line_completion_{retriever}.jsonl")
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))


@pytest.mark.parametrize("chunks, expected_source", [
    ({"rg1_bm25": "Student", "rg1_unixcoder_cosine_sim": "Student only", "rg1_openai_cosine_sim": "Student Roster"}, "rg1_openai_cosine_sim"),
    ({"rg1_bm25": "Student", "rg1_unixcoder_cosine_sim": "Student Roster here", "rg1_openai_cosine_sim": "Student Roster"}, "rg1_unixcoder_cosine_sim"),
    ({"rg1_bm25": "Student", "rg1_unixcoder_cosine_sim": "Student", "rg1_openai_cosine_sim": "nothing"}, None),
])
def test_context_falls_back_to_other_retrievers_or_drops_task(tmp_path, monkeypatch, capsys, chunks, expected_source):
    # Retrievers are tried in order; the first whose excerpts name every
    # reference identifier supplies the context, else the task is dropped.
    reference = "Student> Roster {"
    files = {"instruction.md": b"## Context (Code to Complete)\n```\nprefix\n```\n",
             "tests/expected.txt": reference.encode(), "tests/test.sh": b"#!/bin/bash\nexit 0\n"}
    source, output, archive = tmp_path / "in.parquet", tmp_path / "out.parquet", tmp_path / "orig.tar"
    pq.write_table(pa.table({"path": ["crosscodeeval-java-0001"], "task_binary": [patcher.pack(files)]}), source)
    _archive_with(archive, chunks, reference)
    monkeypatch.setattr(sys, "argv", ["patcher", "--input", str(source), "--output", str(output), "--archive", str(archive)])
    patcher.main()
    report = json.loads(capsys.readouterr().out)
    result = pq.read_table(output)
    if expected_source is None:
        assert report["dropped"] == 1 and report["dropped_task_ids"] == ["crosscodeeval-java-0001"] and result.num_rows == 0
        return
    assert report["dropped"] == 0 and report["context_source"][expected_source] == 1 and result.num_rows == 1
    patched = patcher.unpack(result["task_binary"][0].as_py())
    assert patched["setup_files/context/000_sibling"] == chunks[expected_source].encode()
    assert report["identifier_verdicts"] == {"word": 2}


def test_recoverability_checks_only_the_graded_first_statement():
    # Only the first statement is graded, so names after it are not required.
    assert patcher.reference_identifiers("foo(x);\nbar(unknownName);", "typescript") == ["foo", "x"]
    # A typescript { ... } group closed on the same line is part of the statement.
    assert patcher.reference_identifiers("foo(x, { a: known });", "typescript") == ["a", "foo", "known", "x"]
    assert patcher.reference_identifiers("a.b();\nunknown();", "java") == ["a", "b"]
    assert patcher.reference_identifiers("load(CKPT)\nunknown()", "python", "model = M.") == ["CKPT", "load"]
    # Same pipeline as the verifier: truncate, remove comments, extract; so a
    # trailing comment adds no identifiers and the verifier's target_ids match.
    ref, prompt_text = "foo(x); // uses secretName", ""
    assert patcher.reference_identifiers(ref, "java") == ["foo", "x"]
    _, detail = patcher.verifier_module().score(prompt_text, ref, ref, "java")
    assert sorted(set(detail["target_ids"])) == patcher.reference_identifiers(ref, "java")


def test_string_aware_truncation_and_comments(tmp_path):
    # Template literal, URL and private field: the benchmark's raw scan would cut
    # at `${`, delete from `//` and drop the `#` line; here they are graded whole.
    assert _score(tmp_path / "tpl", "typescript", "const k = `dev:${dto.level}`;", "const k = `dev:${dto.level}`;\nnext();")["reward"] == 1
    assert _score(tmp_path / "tpl2", "typescript", "const k = `dev:${dto.level}`;", "const k = `dev:${other}`;")["reward"] == 0
    assert _score(tmp_path / "url", "java", 'get("https://x.io/a");', 'get("https://x.io/a"); // c\nb();')["reward"] == 1
    assert _score(tmp_path / "url2", "java", 'get("https://x.io/a");', 'get("https://x.io/b");')["reward"] == 0
    assert _score(tmp_path / "priv", "typescript", "#items: string[] = [];", "#items: string[] = []; // note")["reward"] == 1
    # Python keeps the benchmark's '#' comment rule.
    assert _score(tmp_path / "py", "python", "x = 1", "x = 1  # done", "y = 2\n")["reward"] == 1
    assert patcher.reference_identifiers('get("https://x.io/a");', "java") == ["get"]


def test_api_position_names():
    text = "// they said it is going to fetch\nconn.getInputStream(); new BufferedInputStream(x); List<Item> a; Foo::bar"
    assert set(patcher.API_USE.findall(text)) == {"getInputStream", "BufferedInputStream", "List", "bar"}


def test_reference_without_identifiers_is_dropped(tmp_path, monkeypatch, capsys):
    files = {"instruction.md": b"## Context (Code to Complete)\n```\nprefix\n```\n",
             "tests/solution.txt": b"const {", "tests/test.sh": b"#!/bin/bash\nexit 0\n"}
    source, output, archive = tmp_path / "in.parquet", tmp_path / "out.parquet", tmp_path / "orig.tar"
    pq.write_table(pa.table({"path": ["crosscodeeval-typescript-0002"], "task_binary": [patcher.pack(files)]}), source)
    _archive_with(archive, {"rg1_bm25": "anything"}, "const {", language="typescript")
    monkeypatch.setattr(sys, "argv", ["patcher", "--input", str(source), "--output", str(output), "--archive", str(archive)])
    patcher.main()
    report = json.loads(capsys.readouterr().out)
    assert report["dropped_no_identifiers"] == 1 and report["dropped_no_identifiers_task_ids"] == ["crosscodeeval-typescript-0002"]
    assert report["kept"] == 0 and pq.read_table(output).num_rows == 0


def test_excerpt_file_names_count_as_visible(tmp_path, monkeypatch, capsys):
    # The agent lists setup_files/context/, so a class named like an excerpt
    # file (GeneralSettings.cs) is inferable even if the excerpt text lacks it.
    reference = "GeneralSettings settings)"
    files = {"instruction.md": b"## Context (Code to Complete)\n```\nvoid Run(\n```\n",
             "tests/solution.txt": reference.encode(), "tests/test.sh": b"#!/bin/bash\nexit 0\n"}
    source, output, archive = tmp_path / "in.parquet", tmp_path / "out.parquet", tmp_path / "orig.tar"
    pq.write_table(pa.table({"path": ["crosscodeeval-csharp-0009"], "task_binary": [patcher.pack(files)]}), source)
    with tarfile.open(archive, "w") as tar:
        record = {"prompt": "void Run(", "groundtruth": reference, "metadata": {"file": "target", "repository": "r1"},
                  "crossfile_context": {"list": [{"filename": "src/GeneralSettings.cs", "retrieved_chunk": "public int settings = 1;"}]}}
        data = (json.dumps(record) + "\n").encode()
        member = tarfile.TarInfo("csharp/line_completion_rg1_bm25.jsonl"); member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    monkeypatch.setattr(sys, "argv", ["patcher", "--input", str(source), "--output", str(output), "--archive", str(archive)])
    patcher.main()
    report = json.loads(capsys.readouterr().out)
    assert report["kept"] == 1 and report["context_source"]["rg1_bm25"] == 1


def test_curated_known_names():
    v = patcher.reference_verdicts("where T : class, IUserMod", "csharp", "nothing here")
    assert v["where"] == "known" and v["IUserMod"] == "missing"
    v = patcher.reference_verdicts("JSON.stringify(payload)", "typescript", "nothing here")
    assert v == {"JSON": "known", "stringify": "missing", "payload": "missing"}
    assert patcher.reference_verdicts("Collections.singleton(x)", "java", "x = 1") == {
        "Collections": "known", "singleton": "known", "x": "word"}


def test_trailing_lone_brace_line_is_optional(tmp_path):
    gold = "Mandalore __instance, StateInfo __state)\n        {"
    assert _score(tmp_path / "b1", "csharp", gold, "Mandalore __instance, StateInfo __state)")["reward"] == 1
    assert _score(tmp_path / "b2", "csharp", gold, "Mandalore __instance, StateInfo __state)\n{\n")["reward"] == 1
    assert _score(tmp_path / "b3", "csharp", gold, "Mandalore __instance)")["reward"] == 0
    # A reference that is only "{" keeps it (nothing else to compare).
    assert _score(tmp_path / "b4", "typescript", "{", "{")["reward"] == 1
