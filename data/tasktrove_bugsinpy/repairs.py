"""BugsInPy repairs: what we learned from running tasks, not from attempting builds.

patch.py alone converts BugsInPy to Harbor tasks; `patch.py --no-repairs` stops
there, so the validation pipeline can be evaluated on the plain conversion. These
repairs came from no-op, reference and review results, and are applied on top.
"""
import io
import tarfile


def scrapy_bug33_verifier_tests(tests: bytes, bug_id: int) -> tuple[bytes, list[str]]:
    """Make Bug 33's fixed-only helper absence a normal no-op assertion failure."""
    if bug_id != 33:
        return tests, []
    replacements = {
        "tests/test_pipeline_media.py": (
            "from scrapy.utils.log import failure_to_exc_info",
            "import scrapy.utils.log as scrapy_log\nfailure_to_exc_info = getattr("
            "scrapy_log, 'failure_to_exc_info', None)",
            "    def test_default_item_completed(self):\n",
            "    def test_default_item_completed(self):\n"
            "        self.assertTrue(callable(failure_to_exc_info), "
            "'Scrapy must provide failure_to_exc_info')\n",
        ),
        "tests/test_utils_log.py": (
            "from scrapy.utils.log import (failure_to_exc_info, TopLevelFormatter,",
            "import scrapy.utils.log as scrapy_log\nfailure_to_exc_info = getattr("
            "scrapy_log, 'failure_to_exc_info', None)\nfrom scrapy.utils.log import (TopLevelFormatter,",
            "    def test_failure(self):\n",
            "    def test_failure(self):\n"
            "        self.assertTrue(callable(failure_to_exc_info), "
            "'Scrapy must provide failure_to_exc_info')\n",
        ),
    }
    members = {}
    with tarfile.open(fileobj=io.BytesIO(tests), mode="r:gz") as archive:
        for member in archive:
            if member.isfile():
                members[member.name] = archive.extractfile(member).read()
    notes = []
    for path, (old_import, new_import, old_method, new_method) in replacements.items():
        content = members.get(path)
        if content is None:
            raise ValueError(f"Scrapy 33 verifier archive lacks {path}")
        source = content.decode("utf-8")
        if source.count(old_import) != 1 or source.count(old_method) != 1:
            raise ValueError(f"Scrapy 33 expected verifier code changed in {path}")
        source = source.replace(old_import, new_import, 1).replace(old_method, new_method, 1)
        members[path] = source.encode("utf-8")
        notes.append(f"{path}: check fixed-only helper inside test assertion")
    from data.tasktrove_bugsinpy.patch import compressed, task_tar
    return compressed(task_tar(members)), notes


def youtube_dl_verifier_tests(tests: bytes, bug_id: int) -> tuple[bytes, list[str]]:
    """Record targeted upstream-test adaptations without supplying production code."""
    helpers = {38: "urlencode_postdata", 39: "limit_length",
               40: "struct_unpack", 42: "fix_xml_ampersands"}
    if bug_id not in {*helpers, 14}:
        return tests, []
    members = {}
    with tarfile.open(fileobj=io.BytesIO(tests), mode="r:gz") as archive:
        for member in archive:
            if member.isfile():
                members[member.name] = archive.extractfile(member).read()
    path = "test/test_youtube_chapters.py" if bug_id == 14 else "test/test_utils.py"
    source = members[path].decode("utf-8")
    if bug_id in helpers:
        helper = helpers[bug_id]
        imported = f"    {helper},\n"
        method = f"    def test_{helper}(self):\n"
        if source.count(imported) != 1 or source.count(method) != 1:
            raise ValueError(f"youtube-dl {bug_id}: expected helper import/test changed")
        source = source.replace(imported, "", 1).replace(
            method, method + "        import youtube_dl.utils as utils\n"
            + f"        {helper} = getattr(utils, {helper!r}, None)\n"
            + f"        self.assertTrue(callable({helper}), 'Implement youtube_dl.utils.{helper}')\n", 1)
        notes = [f"{path}: resolve {helper} inside its test and assert callable before original assertions"]
    else:
        method = "    def test_youtube_chapters(self):\n"
        if source.count(method) != 1:
            raise ValueError("youtube-dl 14: expected chapter test changed")
        source = source.replace(method, method + '''        # Additional local regression: JSON chapters and description fallback.
        import json
        from test.helper import FakeYDL
        ie = YoutubeIE(FakeYDL())
        json_chapters = getattr(ie, '_extract_chapters_from_json', None)
        description_chapters = getattr(ie, '_extract_chapters_from_description', None)
        self.assertTrue(callable(json_chapters), 'Implement JSON chapter extraction')
        self.assertTrue(callable(description_chapters), 'Keep description chapter extraction')
        expected = [
            {'title': 'JSON intro', 'start_time': 0.0, 'end_time': 12.5},
            {'title': 'JSON ending', 'start_time': 12.5, 'end_time': 30},
        ]
        chapters = [{'chapterRenderer': {
            'timeRangeStartMillis': start, 'title': {'simpleText': title}}}
            for start, title in [(0, 'JSON intro'), (12500, 'JSON ending')]]
        response = chapters
        for key in reversed(['playerOverlays', 'playerOverlayRenderer',
                             'decoratedPlayerBarRenderer', 'decoratedPlayerBarRenderer',
                             'playerBar', 'chapteredPlayerBarRenderer', 'chapters']):
            response = {key: response}
        player = {'watch_next_response': json.dumps(response)}
        webpage = '"RELATED_PLAYER_ARGS": ' + json.dumps(player) + ',\\n'
        description = ('<a onclick="yt.www.watch.player.seekTo(0)">0:00</a> Intro'
                       '<br /><a onclick="yt.www.watch.player.seekTo(20)">0:20</a> Outro')
        fallback = [
            {'title': 'Intro', 'start_time': 0, 'end_time': 20},
            {'title': 'Outro', 'start_time': 20, 'end_time': 30},
        ]
        self.assertEqual(json_chapters(webpage, 'fixture', 30), expected)
        self.assertEqual(ie._extract_chapters(webpage, description, 'fixture', 30), expected)
        for missing in ['', '"RELATED_PLAYER_ARGS": {},\\n',
                        '"RELATED_PLAYER_ARGS": {"watch_next_response": "{}"},\\n']:
            self.assertEqual(ie._extract_chapters(missing, description, 'fixture', 30), fallback)

''', 1)
        notes = [f"{path}: add offline JSON chapter titles/times, JSON precedence and description fallback assertions; retain original cases"]
    members[path] = source.encode("utf-8")
    from data.tasktrove_bugsinpy.patch import compressed, task_tar
    return compressed(task_tar(members)), notes


def reviewed_test_commands(project: str, bug_id: int, fixed: str,
                           commands: list[str]) -> tuple[list[str], list[str]]:
    """Apply documented upstream selector corrections without changing tests."""
    if (project == "ansible" and bug_id == 16
            and fixed == "93d9d640380252084855885ad27873b4377898ec"):
        path = "test/units/module_utils/facts/hardware/test_linux_get_cpu_info.py"
        original = f"pytest {path}::test_get_cpu_info_missing_arch"
        if commands != [original]:
            raise ValueError("Ansible 16 upstream test command changed; review correction")
        # The upstream selector omits architecture, so both endpoints pass.
        # Keep that check and add the existing architecture-aware regression:
        # PowerPC processor/cpu fields otherwise double the reported CPU count.
        return [original + f" {path}::test_get_cpu_info"], [
            "ansible-16: retain upstream missing-architecture test and add upstream "
            "test_get_cpu_info to exercise the PowerPC CPU-count fix"]
    return list(commands), []


INSTRUCTION_REVIEW_EXCLUSIONS = {
    ("ansible", 12): ("reference_details", "The tests require environment variables to be read "
        "through one particular helper. A correct fix that reads them another way can fail."),
    ("black", 23): ("unrelated_changes", "The instruction asks for a fix to spacing around "
        "exec/eval, but the selected test checks different Python 2 formatting behavior. "
        "Fixing the reported issue is not enough to pass."),
    ("httpie", 4): ("unreliable_tests", "The test needs a successful response from the public "
        "httpbin.org service. A correct fix can fail when the network or service is unavailable."),
    ("luigi", 8): ("reference_details", "The test requires the database query to be written "
        "exactly like the reference fix. A different query that finds the same schema and "
        "table regardless of letter case can fail."),
    ("thefuck", 3): ("reference_details", "The test requires the exact command fish --version. "
        "A fix that safely gets the shell version another way can fail."),
    ("thefuck", 16): ("reference_details", "The tests require shell aliases to use a particular "
        "text layout and order of environment variables. An alias that works the same way "
        "but is written differently can fail."),
    ("tornado", 6): ("reference_details", "The test expects one closed event loop to remain "
        "in an internal tracking table. A fix that cleans up all closed loops immediately "
        "can fail even though it prevents the leak."),
}


def task_exclusion(project: str, bug_id: int) -> dict | None:
    if finding := INSTRUCTION_REVIEW_EXCLUSIONS.get((project, bug_id)):
        category, reason = finding
        task_id = f"bugsinpy-original-{project}-{bug_id}"
        evidence = ("https://github.com/MLI-lab/OT-next-data/tree/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/tasktrove_bugsinpy/"
                    f"flagged_instruction_review_20261008/{task_id}")
        return {"category": "flagged_by_instruction_loop",
                "reason": f"Flagged during instruction review and excluded after checking the tests. "
                          f"{reason} Evidence: {evidence}",
                "review_category": category, "evidence": evidence}
    if project == "youtube-dl" and bug_id == 40:
        return {"category": "environment_task_mismatch", "reason": (
            "Recorded Python 3.7 cannot exercise the older-Python struct Unicode-format "
            "compatibility bug. The selected test checks the helper interface, but an "
            "incomplete compatibility fix also passes. Excluded by review, not a NOP false positive.")}
    return None


def discarded_after_review(project: str, bug_id: int) -> str | None:
    """A converted task that validation showed cannot be graded, as its discard reason."""
    if (project, bug_id) == ("keras", 15):
        # Keras 15 fixes blank CSVLogger rows caused by Windows line-ending
        # translation. On Linux the unchanged source passes the selected test
        # (NOP reward 1, job 940231), so the task cannot be graded here.
        return "Discarded: Windows-only bug, unchanged source passes the test on Linux"
    return None
