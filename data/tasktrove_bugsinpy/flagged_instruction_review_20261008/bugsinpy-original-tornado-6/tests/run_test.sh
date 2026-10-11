#!/bin/bash
set +e
overall=0
rm -f /logs/verifier/unittest.jsonl
python3 /tests/run_unittest.py -q tornado.test.asyncio_test.LeakTest.test_ioloop_close_leak
status=$?
if [ "$status" -gt "$overall" ]; then overall=$status; fi
python3 /tests/run_unittest.py -q tornado.test.asyncio_test.LeakTest.test_asyncio_close_leak
status=$?
if [ "$status" -gt "$overall" ]; then overall=$status; fi
exit "$overall"
