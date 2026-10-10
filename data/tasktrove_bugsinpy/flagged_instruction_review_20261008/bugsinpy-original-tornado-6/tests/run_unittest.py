import json
import sys
import traceback
import unittest
from pathlib import Path

class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed = []
        self.infrastructure_errors = []

    def startTest(self, test):
        self.executed.append({"id": test.id(), "class": type(test).__name__})
        super().startTest(test)

    def addError(self, test, err):
        frames = traceback.extract_tb(err[2])
        if (type(test).__name__ in {"_FailedTest", "_ErrorHolder"}
                or any(frame.name in {"setUp", "tearDown", "setUpClass", "tearDownClass",
                                      "setUpModule", "tearDownModule"} for frame in frames)):
            self.infrastructure_errors.append(test.id())
        super().addError(test, err)

class RecordingRunner(unittest.TextTestRunner):
    resultclass = RecordingResult

try:
    program = unittest.main(module=None, argv=["unittest", *sys.argv[1:]],
                            testRunner=RecordingRunner, exit=False)
except Exception:
    traceback.print_exc()
    raise SystemExit(2)
result = program.result
invalid = (not result.testsRun or bool(result.skipped) or bool(result.expectedFailures)
           or bool(result.unexpectedSuccesses) or bool(result.infrastructure_errors)
           or any(t["class"] == "_FailedTest" for t in result.executed))
record = {"arguments": sys.argv[1:], "tests_run": result.testsRun,
          "executed": result.executed, "skipped": [(t.id(), reason) for t, reason in result.skipped],
          "failures": [(t.id(), trace) for t, trace in result.failures],
          "errors": [(t.id(), trace) for t, trace in result.errors],
          "infrastructure_errors": result.infrastructure_errors,
          "expected_failures": len(result.expectedFailures),
          "successful": result.wasSuccessful(), "invalid": invalid}
with Path('/logs/verifier/unittest.jsonl').open('a') as stream:
    stream.write(json.dumps(record) + "\n")
raise SystemExit(2 if invalid else (0 if result.wasSuccessful() else 1))
