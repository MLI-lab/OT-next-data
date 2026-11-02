"""Historical post-validation MiMo repairs, omitted by patch.py --no-repairs.

These reproduce the published v1 payloads, including the old Git cleanup text.
The audit baseline must not run that cleanup: it measures the unmodified image.
Image pinning, tmux build compatibility and incomplete image packages stay in patch.py.
"""
import hashlib
import json
from pathlib import Path

# Installed copies in the 2026-10-16 images that still differ from the repository (checked
# 2026-10-17, job 1039963, ~/autonomous-20261017/mimo-residue/probe-1039963.out: every differing
# file's blob was looked up in the repository's history with git log --all --find-object).
# Kept after the same check: format-code-task-000407 (all 3 differing airflow files are older
# repository versions), format-code-task-002070 (node_modules/nopt 7.2.1 differs only in
# package.json and the bin/ tester; lib/ equals the repository), format-code-task-002517 (no
# /testbed; the Go module cache holds the go-streams v0.12.0 release the test module requires;
# the requested S3 connector, NewS3Source, exists nowhere outside the repository).
RESIDUE_CATEGORY = "reward-hack-residue"


RESIDUE = {
    "format-code-task-000092": (
        "The image installs the repository's tests package in /usr/local/lib/python3.10/site-packages/tests; "
        "3 of its 99 files differ from the repository and none of the 3 blobs is in the repository's history "
        "(post-base content). They contain the test cases of the requested feature: test_checker.py adds "
        "var_rank_multi_1..5 and var_rank_multi_broadcast, test_parser.py and test_specs.py add specs with "
        "several variable-rank dimensions ([ds1..., ds2...]), the behaviour the hidden test "
        "tests/gpflow/experimental/check_shapes/test_multi_varrank.py checks. An agent can read the expected "
        "behaviour from the image."),
    "format-code-task-001397": (
        "The image's virtual environment /testbed/.venv/lib/python3.12/site-packages/grai_schemas has "
        "v1/metadata/edges.py with TableToTableMetadata typed by TableToTableAttributes and included in the "
        "Metadata union; the repository copy has the defect (TableToColumnAttributes, union without "
        "TableToTableMetadata) and the installed blob is not in the repository's history (post-base content). "
        "grai_client/schemas/__init__.py likewise imports an edge module the repository lacks. The installed "
        "copy is the fix for the relationship-metadata contract the hidden test checks."),
}


# No-op stage review of the final run (array 1047484, 2026-10-18, user-approved): tasks whose
# reward-0 run did not come from the missing feature. runtime-user-emulation / runtime-incompatible:
# our single-uid Apptainer runtime (services as their own users, root browsers, host network or
# CPU count); upstream-environment-broken: the image cannot run the tests at all; upstream-task-defect:
# the repository lacks files or a base commit the task needs; nop-unverifiable: no output shows that a
# test ran. Per-task evidence: verdict files of the review (scratch nop/verdicts-*.jsonl).
# Leftover from the residue audit that is still in the re-uploaded image (checked 2026-10-18): the
# new API names of the hidden tests exist only in that leftover. Six other leftovers of the audit
# (000046, 000283, 000556, 000699, 000114, 001372) contain none of them and stay.
NOP_REVIEW = {
    'format-code-task-000348': ('upstream-environment-broken', 'Memory measurement on CLAIX (2026-10-18, 16 GiB limit): needs more than 16 GiB (peak 15,674 MiB at the 16 GiB limit; no tests ran)'),
    'format-code-task-000707': ('upstream-environment-broken', 'Memory measurement on CLAIX (2026-10-18, 16 GiB limit): its JavaScript test runs out of Node\'s own heap at about 4 GB ("JavaScript heap out of memory"), which the test command does not raise; more container memory does not help'),
    'format-code-task-001633': ('upstream-environment-broken', 'Memory measurement on CLAIX (2026-10-18, 16 GiB limit): its JavaScript test runs out of Node\'s own heap ("Reached heap limit"), which the test command does not raise; more container memory does not help'),
    'format-code-task-002280': ('upstream-environment-broken', 'Memory measurement on CLAIX (2026-10-18, 16 GiB limit): needs more than 16 GiB (peak reached the 16 GiB limit)'),
    'format-code-task-000252': ('upstream-environment-broken', 'No-op rerun on CLAIX (job 5117303, 2026-10-18, direct internet): the StarRocks fe-core Maven build ran 19 minutes and then exceeded the task memory limit; too slow and too large for validation (user rule: Maven tasks over 20 minutes are archived).'),
    'format-code-task-001107': ('upstream-environment-broken', 'No-op rerun on CLAIX (job 5117303, 2026-10-18, direct internet): Gradle cannot resolve the com.gradle.enterprise 3.16.2 settings plugin from any plugin repository, so the build fails before any test.'),
    'format-code-task-002916': ('upstream-environment-broken', 'No-op rerun on CLAIX (job 5117303, 2026-10-18, direct internet): pnpm install --frozen-lockfile hangs without output until the 1800 s verifier timeout, so no test runs.'),
    'format-code-task-000020': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18, traced with bash -x): Julia segfaults in Pkg.instantiate before any test runs.'),
    'format-code-task-000111': ('runtime-incompatible', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Firefox never connected to karma under xvfb, so no spec ran; the likely cause is Firefox under single-uid fakeroot root without user namespaces for its sandbox (not proven by the log).'),
    'format-code-task-000130': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18, traced with bash -x): Julia segfaults before any test runs (same image setup as 000020).'),
    'format-code-task-000177': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Postgres not running; Postgres data dir owned by root under single-uid fakeroot (seen explicitly in 002180/002732); start errors hidden by 2>/dev/null, script exits under -e.'),
    'format-code-task-000178': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): pg_ctlcluster start failed silently (2>/dev/null); Postgres data dir owned by root under single-uid fakeroot (seen explicitly in 002180/002732); start errors hidden by 2>/dev/null, script exits under -e.'),
    'format-code-task-000179': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): pg_ctlcluster start failed (hidden); Postgres data dir owned by root under single-uid fakeroot (seen explicitly in 002180/002732); start errors hidden by 2>/dev/null, script exits under -e.'),
    'format-code-task-000181': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Postgres did not start and sudo -u postgres used; Postgres data dir owned by root under single-uid fakeroot (seen explicitly in 002180/002732); start errors hidden by 2>/dev/null, script exits under -e.'),
    'format-code-task-000182': ('runtime-incompatible', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Script writes /etc/hosts, which Apptainer binds read-only.'),
    'format-code-task-000209': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): pg_ctlcluster start fails under the single-uid runtime (root-owned PGDATA, cf. 001272); the first selected base test errors in DB setup.'),
    'format-code-task-000277': ('nop-unverifiable', 'No-op stage (final run array 1047484, reviewed 2026-10-18, traced with bash -x): forge test -q prints only compile lines; no test result is visible.'),
    'format-code-task-000279': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): pg_ctlcluster start failed silently (output sent to /dev/null) and the tests never ran; this is the same root-owned datadir/single-uid issue as the other postgres tasks (inferred).'),
    'format-code-task-000394': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18, traced with bash -x): usecase.sh stops at its precondition "FAIL: molecule CLI is not runnable": the project CLI does not start in the image.'),
    'format-code-task-000474': ('upstream-task-defect', 'The repository has no commit (all files only staged, the base commit exists only as an unreferenced object), so the base ref the task reset needs cannot be recorded; the environment healthcheck fails (CLAIX rerun 5116864, 2026-10-18).'),
    'format-code-task-000528': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): MySQL cannot open its datadir because under the single-uid Apptainer runtime the mysql user cannot own or access /var/lib/mysql, so the tests never ran.'),
    'format-code-task-000622': ('runtime-user-emulation', "No-op stage (final run array 1047484, reviewed 2026-10-18): HOME/GOMODCACHE belong to the image's testuser; under the single-uid runtime the directory is not accessible, so go cannot start."),
    'format-code-task-000723': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): MariaDB did not come up (mysql client errors hidden, script exits under -e); inferred same single-uid ownership problem as 001983.'),
    'format-code-task-000945': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Postgres not running (pg_ctlcluster start fails silently, same root-owned PGDATA problem as 001272); additionally our ot_pytest_execution plugin crashes on pytest 3.0.2 (no _pytest.outcomes).'),
    'format-code-task-000965': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Offline Maven repo lacks parent POM; no test compiled.'),
    'format-code-task-001030': ('upstream-task-defect', "No-op stage (final run array 1047484, reviewed 2026-10-18): Upstream 'task base' commit deleted 64 files (incl. internal/apischema/openai/openai.go, 60 of them mention LLM vendors) that the instruction calls the existing extension; the package cannot build regardless of the feature (task defective)."),
    'format-code-task-001050': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Repo has go.mod requiring testify but no go.sum at all (checked in image); `go test ./lib/lru` cannot load the module graph independent of the feature (new test imports only reflect/testing).'),
    'format-code-task-001059': ('upstream-task-defect', "No-op stage (final run array 1047484, reviewed 2026-10-18): Upstream 'task base' deleted common/openai/{chat,exports,msg}.go (all mention OpenAI), so yakgrpc no longer compiles; unrelated to the YaklangInspectInformation feature (task defective)."),
    'format-code-task-001069': ('upstream-task-defect', "No-op stage (final run array 1047484, reviewed 2026-10-18): Upstream 'task base' deleted 76 files incl. src/any_agent/config.py and tracing/agent_trace.py; the package cannot be imported at all although the instruction only asks to extend spans_to_messages (task defective)."),
    'format-code-task-001077': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18, traced with bash -x): a jar the test classpath needs (commons-lang 2.6) is missing from the offline Maven repository in the image.'),
    'format-code-task-001098': ('upstream-task-defect', 'No-op stage (final run array 1047484, reviewed 2026-10-18): The prerequisite lib-sourcify build fails on a missing ipfsHash module in existing code, which is unrelated to the custom-API feature, so the tests never run (the repo snapshot may be missing a file, which would make the task defective).'),
    'format-code-task-001137': ('upstream-task-defect', "No-op stage (final run array 1047484, reviewed 2026-10-18): Upstream 'task base' deleted doc_comments_ai/app.py, llm.py, utils.py (and an existing test response_parser_test.py also fails); the instruction presumes the CLI exists (task defective)."),
    'format-code-task-001168': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18): conftest import fails because mlflow imports as a namespace package (mlflow.__file__ is None), unrelated to log_table modes; environment/repo state broken.'),
    'format-code-task-001172': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Maven offline cache lacks junit-bom; build cannot even load the POM.'),
    'format-code-task-001183': ('upstream-task-defect', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Same griptape repo state as 001406: conftest imports an existing driver module that is missing; unrelated to retrieval fusion (likely defective task).'),
    'format-code-task-001188': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18, traced with bash -x): the test command starts mysqld as the mysql user, which our single-user runtime cannot provide.'),
    'format-code-task-001193': ('runtime-incompatible', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Flow server crashes in select(); most likely too many worker fds because flow sizes workers from the 384 host CPUs (select fd limit 1024). Not task-related.'),
    'format-code-task-001203': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Silent exit at first service start: pg_ctlcluster ... 2>/dev/null fails under -e; Postgres data dir owned by root under single-uid fakeroot (seen explicitly in 002180/002732); start errors hidden by 2>/dev/null, script exits under -e.'),
    'format-code-task-001207': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Silent exit: service postgresql start / sudo -u postgres psql 2>/dev/null fail under -e; Postgres data dir owned by root under single-uid fakeroot (seen explicitly in 002180/002732); start errors hidden by 2>/dev/null, script exits under -e.'),
    'format-code-task-001212': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Bench dir owned by frappe uid is unreadable for root under the single-uid fakeroot runtime.'),
    'format-code-task-001272': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Postgres refuses to start because under the single-uid Apptainer runtime PGDATA appears root-owned; base tests error in setup before any feature test runs.'),
    'format-code-task-001273': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): PostgreSQL refuses to start because under fakeroot/single-uid the datadir appears owned by root; the tests never ran.'),
    'format-code-task-001406': ('upstream-task-defect', 'No-op stage (final run array 1047484, reviewed 2026-10-18): conftest import chain fails on an existing griptape driver module unrelated to the RAG-provenance feature; repo snapshot appears to be missing that file (task likely defective).'),
    'format-code-task-001438': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): pg_ctlcluster start failed (errors hidden); Postgres data dir owned by root under single-uid fakeroot (seen explicitly in 002180/002732); start errors hidden by 2>/dev/null, script exits under -e.'),
    'format-code-task-001561': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Existing repository code imports dict2schema, which the installed webargs version no longer has, so the import precheck fails before pytest runs (a dependency version mismatch, not the feature).'),
    'format-code-task-001574': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): pg_ctlcluster start failed silently (output sent to /dev/null), so ALTER ROLE failed and no tests ran; this matches the postgres datadir-owned-by-root failure under fakeroot/single-uid (inferred).'),
    'format-code-task-001756': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Composer resolved latest deps (no lock) that need PHP 8 while PHP 7.4 runs; errors come from vendor code.'),
    'format-code-task-001757': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18): PHP 8.0 parse error from a typed class constant in an installed dependency (needs PHP 8.3); unrelated to onSuccess/onFailure. Other 3 tests pass.'),
    'format-code-task-001866': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): PostgreSQL never became ready, so no go test ran; this matches the root-owned datadir/single-uid problem of the other postgres tasks (inferred, the start output is suppressed).'),
    'format-code-task-001969': ('nop-unverifiable', 'No-op stage (final run array 1047484, reviewed 2026-10-18, traced with bash -x): npm aborts with exit code 243 after the install step with its output hidden; no test runs visibly.'),
    'format-code-task-001975': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): RabbitMQ cannot read its cookie (owned by rabbitmq uid) under the single-uid runtime; tests never ran.'),
    'format-code-task-001980': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Postgres cannot run as root and the single-uid runtime has no postgres user to switch to.'),
    'format-code-task-001982': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): MariaDB did not start, mysql error hidden by 2>/dev/null; inferred single-uid ownership problem as in 001983.'),
    'format-code-task-001983': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): mysqld_safe cannot manage its pid file: uid mapping / overlay under fakeroot.'),
    'format-code-task-002001': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): su to a non-root user cannot use its home under single-uid runtime.'),
    'format-code-task-002022': ('runtime-user-emulation', "No-op stage (final run array 1047484, reviewed 2026-10-18): Silent exit at 'pg_ctlcluster 16 main start 2>/dev/null' under -e; Postgres data dir owned by root under single-uid fakeroot (seen explicitly in 002180/002732); start errors hidden by 2>/dev/null, script exits under -e."),
    'format-code-task-002023': ('runtime-user-emulation', "No-op stage (final run array 1047484, reviewed 2026-10-18): Silent exit at 'pg_ctlcluster 16 main start 2>/dev/null' under -e; Postgres data dir owned by root under single-uid fakeroot (seen explicitly in 002180/002732); start errors hidden by 2>/dev/null, script exits under -e."),
    'format-code-task-002024': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Postgres not running; Postgres data dir owned by root under single-uid fakeroot (seen explicitly in 002180/002732); start errors hidden by 2>/dev/null, script exits under -e.'),
    'format-code-task-002035': ('runtime-user-emulation', "No-op stage (final run array 1047484, reviewed 2026-10-18): Silent exit at 'pg_ctlcluster 16 main start 2>/dev/null' under -e; Postgres data dir owned by root under single-uid fakeroot (seen explicitly in 002180/002732); start errors hidden by 2>/dev/null, script exits under -e."),
    'format-code-task-002036': ('runtime-user-emulation', "No-op stage (final run array 1047484, reviewed 2026-10-18): postgres never comes up (the start command's stderr is sent to /dev/null); same class as the known DB-ownership failures under our single-uid fakeroot runtime. Per user decision archive as runtime-user-emulation only after confirming the ownership error (start it once by hand without 2>/dev/null)."),
    'format-code-task-002037': ('runtime-user-emulation', "No-op stage (final run array 1047484, reviewed 2026-10-18): postgres never comes up (the start command's stderr is sent to /dev/null); same class as the known DB-ownership failures under our single-uid fakeroot runtime. Per user decision archive as runtime-user-emulation only after confirming the ownership error (start it once by hand without 2>/dev/null)."),
    'format-code-task-002038': ('runtime-user-emulation', "No-op stage (final run array 1047484, reviewed 2026-10-18): postgres never comes up (the start command's stderr is sent to /dev/null); same class as the known DB-ownership failures under our single-uid fakeroot runtime. Per user decision archive as runtime-user-emulation only after confirming the ownership error (start it once by hand without 2>/dev/null). Django aborts while creating the test DB, so the runner never reports results."),
    'format-code-task-002092': ('runtime-user-emulation', "No-op stage (final run array 1047484, reviewed 2026-10-18): postgres never comes up (the start command's stderr is sent to /dev/null); same class as the known DB-ownership failures under our single-uid fakeroot runtime. Per user decision archive as runtime-user-emulation only after confirming the ownership error (start it once by hand without 2>/dev/null)."),
    'format-code-task-002144': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18, traced with bash -x): the junit 3.8.1 jar the test command needs is missing from the offline Maven repository in the image.'),
    'format-code-task-002148': ('upstream-task-defect', "No-op stage (final run array 1047484, reviewed 2026-10-18): Upstream 'task base' deleted api/api.gen.go (plus clients/openapi specs), so package api cannot compile; unrelated to the subscription phase-patch feature (task defective)."),
    'format-code-task-002169': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Neo4j cannot write its log file (owned by the neo4j uid, not writable under single-uid fakeroot), so the database never starts and pytest never runs.'),
    'format-code-task-002180': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Postgres refuses to start because the single-uid fakeroot runtime makes its data dir root-owned; go tests never ran.'),
    'format-code-task-002293': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Old pnpm on newer Node fails every registry fetch (ERR_INVALID_THIS); dependencies never installed.'),
    'format-code-task-002294': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Silent exit in postgres setup (pg_ctlcluster ... || true, then sudo -u postgres psql with errors hidden); Postgres data dir owned by root under single-uid fakeroot (seen explicitly in 002180/002732); start errors hidden by 2>/dev/null, script exits under -e.'),
    'format-code-task-002309': ('runtime-incompatible', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Karma never ran a spec because Chrome refuses to start as uid 0 without --no-sandbox; under fakeroot the verifier is root (Chrome would also need --no-sandbox in a root Docker container, so the task config is fragile too).'),
    'format-code-task-002338': ('runtime-user-emulation', "No-op stage (final run array 1047484, reviewed 2026-10-18): postgres never comes up (the start command's stderr is sent to /dev/null); same class as the known DB-ownership failures under our single-uid fakeroot runtime. Per user decision archive as runtime-user-emulation only after confirming the ownership error (start it once by hand without 2>/dev/null). Also uses su - postgres, which cannot switch uid with empty subuid."),
    'format-code-task-002396': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): The setup cannot read the postgres-owned pg_hba.conf because single-uid fakeroot "root" lacks access to files owned by another image uid, so the tests never ran.'),
    'format-code-task-002557': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Silent exit in postgres setup (su - postgres psql 2>/dev/null under -e); Postgres data dir owned by root under single-uid fakeroot (seen explicitly in 002180/002732); start errors hidden by 2>/dev/null, script exits under -e.'),
    'format-code-task-002558': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): test_command.sh runs `pg_ctlcluster 16 main start 2>/dev/null || true`; Postgres cannot start under the single-uid runtime (cf. 001272).'),
    'format-code-task-002559': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): postgresql init script fails on chmod under fakeroot/overlay uid mapping.'),
    'format-code-task-002613': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18): The pinned old PHPUnit cannot run on the installed PHP 8.1+, so it dies on startup before any test executes (unrelated to the feature).'),
    'format-code-task-002620': ('runtime-incompatible', 'No-op stage (final run array 1047484, reviewed 2026-10-18): The new sent_time test fails genuinely, but read_routes6 also fails because the Apptainer container shares the host network namespace (host ens13f0np0/ibs5 routes), so even a correct solution would score 0 here.'),
    'format-code-task-002629': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): MariaDB cannot be reached because of a permission error (errno 13) on the socket or datadir; under single-uid fakeroot the mysql user cannot use root-owned dirs, so the tests never ran.'),
    'format-code-task-002689': ('runtime-incompatible', 'No-op stage (final run array 1047484, reviewed 2026-10-18): The setup step chmods a symlink into the read-only image tree (/opt/node-22/...), and under Apptainer fakeroot this gives EINVAL, so the script exits before jest.'),
    'format-code-task-002693': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): The test_command.sh runs under bash -e and starts postgres with stderr sent to /dev/null; a failed pg_ctlcluster start ends the script silently before go test, which matches the postgres-under-fakeroot failures (inferred from the script, as the output is empty).'),
    'format-code-task-002732': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Postgres refuses root-owned data dir under single-uid fakeroot.'),
    'format-code-task-002740': ('runtime-incompatible', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Taskpools sizes from host CPU count (384 visible) and aborts in the regression test before the new test; our node exposes all host cores.'),
    'format-code-task-002850': ('runtime-user-emulation', 'No-op stage (final run array 1047484, reviewed 2026-10-18, traced with bash -x): pip install -e . fails with "Permission denied: /usr/local/bin/sqlmesh" under our single-user runtime, so no test runs.'),
    'format-code-task-003020': ('upstream-task-defect', 'No-op stage (final run array 1047484, reviewed 2026-10-18): Base app/reducers/links.js imports react-addons-update, which is not installed; bundle fails before tests. Dependency missing in image (possible task defect).'),
    'format-code-task-003039': ('upstream-environment-broken', 'No-op stage (final run array 1047484, reviewed 2026-10-18): mongod failed to start (log not captured, possibly runtime-related); tests never ran.'),
    'format-code-task-003053': ('runtime-user-emulation', "No-op stage (final run array 1047484, reviewed 2026-10-18): postgres never comes up (the start command's stderr is sent to /dev/null); same class as the known DB-ownership failures under our single-uid fakeroot runtime. Per user decision archive as runtime-user-emulation only after confirming the ownership error (start it once by hand without 2>/dev/null)."),
}


RESIDUE_UNTESTED = {
    'format-code-task-000253': ("The image's .build_env/venv installs a pipeline package whose "
        "pipeline/contrib/node_timeout (handlers.py and its bytecode) already defines deal_with_timeout_node, "
        "parse_node_timeout_configs, forced_fail_and_skip and node_timeout_handler, the API the hidden tests check "
        "and the repository lacks (residue audit CharlieFRuan/rl-env-cleaner@836c6061, checked 2026-10-18)."),
}


# Tasks that exceed the 4096 MiB default. Measured 2026-10-17 with a 16 GiB limit (job 1046057,
# cgroup peak of the no-op run, ~/OT_next_data validation of stages 1,3,4): jest starts worker
# processes and MiMo's /etc/profile.d/zz-autoinstall-oom.sh gives every node a 4 GiB heap, so
# 4096 MiB stopped the verifier ("Task memory limit exceeded"). Peak plus about 35 % headroom.
# Tasks that exceed the 4096 MiB default. Measured 2026-10-18 with a 16 GiB limit (CLAIX jobs
# 5117335, 5120667, 5120669; peak = sacct MaxRSS of the trial step). Limit = peak + 35 %, rounded up
# to the next size Terminal-Bench's check-resource-sizes.sh allows (8192 or 16384 MiB): all of them failed at
# 4096 MiB, where the limit also counts page cache.
MEMORY_MB = {
    "format-code-task-000035": 8192,  # peak 1,907 MiB
    "format-code-task-000036": 8192,  # 2026-10-17 jest profile
    "format-code-task-000037": 8192,  # peak 2,278 MiB
    "format-code-task-000197": 8192,  # peak 2,949 MiB
    "format-code-task-000268": 8192,  # peak 5,261 MiB
    "format-code-task-000283": 16384,  # peak 10,921 MiB
    "format-code-task-000319": 8192,  # peak 2,197 MiB
    "format-code-task-000475": 8192,  # peak 1,842 MiB
    "format-code-task-000479": 8192,  # peak 2,201 MiB
    "format-code-task-000576": 8192,  # peak 3,304 MiB
    "format-code-task-000583": 16384,  # peak 6,245 MiB
    "format-code-task-000604": 8192,  # peak 3,330 MiB
    "format-code-task-000609": 16384,  # peak 10,688 MiB
    "format-code-task-000632": 8192,  # peak 2,120 MiB
    "format-code-task-000656": 8192,  # peak 2,914 MiB
    "format-code-task-000756": 8192,  # peak 3,327 MiB
    "format-code-task-000772": 8192,  # peak 5,851 MiB
    "format-code-task-000776": 8192,  # peak 3,063 MiB
    "format-code-task-000867": 8192,  # peak 2,992 MiB
    "format-code-task-000890": 8192,  # peak 2,675 MiB
    "format-code-task-000891": 8192,  # peak 1,607 MiB
    "format-code-task-000928": 8192,  # peak 4,173 MiB
    "format-code-task-000943": 8192,  # peak 2,211 MiB
    "format-code-task-001085": 8192,  # peak 5,885 MiB
    "format-code-task-001239": 8192,  # peak 4,485 MiB
    "format-code-task-001379": 8192,  # peak 2,369 MiB
    "format-code-task-001388": 16384,  # peak 6,689 MiB
    "format-code-task-001403": 8192,  # peak 1,994 MiB
    "format-code-task-001408": 8192,  # peak 2,517 MiB
    "format-code-task-001409": 8192,  # peak 2,673 MiB
    "format-code-task-001549": 8192,  # peak 2,617 MiB
    "format-code-task-001572": 8192,  # peak 2,790 MiB
    "format-code-task-001625": 8192,  # peak 3.9 GiB
    "format-code-task-001632": 8192,  # peak 4,517 MiB
    "format-code-task-001656": 8192,  # peak 2,122 MiB
    "format-code-task-001698": 8192,  # peak 2,526 MiB
    "format-code-task-001856": 16384,  # peak 6,821 MiB
    "format-code-task-001883": 8192,  # peak 1,652 MiB
    "format-code-task-001890": 8192,  # peak 2,300 MiB
    "format-code-task-001907": 16384,  # peak 6,363 MiB
    "format-code-task-001954": 8192,  # peak 954 MiB
    "format-code-task-001955": 8192,  # peak 1,696 MiB
    "format-code-task-002057": 16384,  # peak 6,519 MiB
    "format-code-task-002080": 8192,  # peak 1,699 MiB
    "format-code-task-002141": 8192,  # peak 2,642 MiB
    "format-code-task-002242": 8192,  # peak 1,916 MiB
    "format-code-task-002317": 8192,  # peak 1,744 MiB
    "format-code-task-002318": 8192,  # peak 3,028 MiB
    "format-code-task-002319": 8192,  # peak 2,697 MiB
    "format-code-task-002320": 8192,  # peak 1,800 MiB
    "format-code-task-002322": 8192,  # peak 1,785 MiB
    "format-code-task-002323": 8192,  # peak 1,996 MiB
    "format-code-task-002324": 8192,  # 2026-10-17 jest profile
    "format-code-task-002325": 8192,  # peak 1,969 MiB
    "format-code-task-002326": 8192,  # peak 2,034 MiB
    "format-code-task-002327": 8192,  # peak 1,995 MiB
    "format-code-task-002328": 8192,  # peak 3,419 MiB
    "format-code-task-002329": 8192,  # peak 2,297 MiB
    "format-code-task-002330": 8192,  # peak 2,796 MiB
    "format-code-task-002331": 8192,  # peak 2,620 MiB
    "format-code-task-002332": 8192,  # peak 1,718 MiB
    "format-code-task-002414": 8192,  # peak 5,542 MiB
    "format-code-task-002432": 8192,  # peak 4,233 MiB
    "format-code-task-002433": 8192,  # peak 4,367 MiB
    "format-code-task-002435": 8192,  # peak 4,652 MiB
    "format-code-task-002436": 8192,  # peak 4,639 MiB
    "format-code-task-002437": 8192,  # peak 4,705 MiB
    "format-code-task-002438": 8192,  # peak 3,726 MiB
    "format-code-task-002439": 8192,  # peak 4,900 MiB
    "format-code-task-002440": 8192,  # peak 4,933 MiB
    "format-code-task-002441": 8192,  # peak 5,005 MiB
    "format-code-task-002491": 8192,  # peak 1,638 MiB
    "format-code-task-002492": 8192,  # peak 2,799 MiB
    "format-code-task-002711": 8192,  # peak 5,542 MiB
    "format-code-task-002728": 8192,  # peak 2,589 MiB
    "format-code-task-002730": 8192,  # peak 2,095 MiB
    "format-code-task-002741": 8192,  # peak 1,736 MiB
    "format-code-task-002751": 8192,  # peak 2,152 MiB
    "format-code-task-002752": 16384,  # peak 7,120 MiB
    "format-code-task-002753": 8192,  # peak 5,186 MiB
    "format-code-task-002764": 8192,  # peak 1,730 MiB
    "format-code-task-002766": 8192,  # peak 2,211 MiB
    "format-code-task-002767": 8192,  # peak 2,085 MiB
    "format-code-task-002768": 8192,  # peak 1,931 MiB
    "format-code-task-002777": 8192,  # peak 5,629 MiB
    "format-code-task-002797": 8192,  # peak 2,084 MiB
    "format-code-task-002820": 8192,  # peak 1,940 MiB
    "format-code-task-002821": 8192,  # peak 1,883 MiB
    "format-code-task-002896": 8192,  # peak 1,773 MiB
    "format-code-task-002905": 8192,  # peak 3,517 MiB
    "format-code-task-002911": 8192,  # peak 3.9 GiB
    "format-code-task-002945": 8192,  # peak 3,846 MiB
    "format-code-task-002967": 8192,  # peak 2,331 MiB
    "format-code-task-002988": 8192,  # peak 1,823 MiB
}


EXCLUDED = {**{task: (RESIDUE_CATEGORY, reason) for task, reason in RESIDUE.items()},
            **{task: (RESIDUE_CATEGORY, reason) for task, reason in RESIDUE_UNTESTED.items()},
            **NOP_REVIEW}

GIT_REPAIR = '# ot-validation: repository-leak-repair-v1 (generated; replaced, never stacked)\nRUN out=${OT_REPOSITORY_REPAIR_REPORT:-/var/lib/ot-repository-repair.json}; mkdir -p "$(dirname "$out")"; command -v git >/dev/null 2>&1 || { echo "repository leak repair: no git, nothing to do"; printf \'{"version":1,"git":false,"repositories":[]}\\n\' > "$out"; exit 0; }; R=$(mktemp -d 2>/dev/null || { mkdir -p /tmp/ot-repair.$$ && echo /tmp/ot-repair.$$; }); i=0; seen=""; for d in "$(pwd -P)" /testbed /workspace/repo /workspace /app /repo /code; do [ -d "$d" ] || continue; top=$(cd "$d" && git -c safe.directory=\'*\' rev-parse --show-toplevel 2>/dev/null) || continue; case "$seen" in *"|$top|"*) continue;; esac; seen="$seen|$top|"; (cd "$top" && git -c safe.directory=\'*\' rev-parse -q --verify HEAD >/dev/null) || continue; i=$((i+1)); ( cd "$top" || exit 0; current=$(git -c safe.directory=\'*\' symbolic-ref -q HEAD || true); head=$(git -c safe.directory=\'*\' rev-parse HEAD); f=$(git -c safe.directory=\'*\' fsck --unreachable --no-reflogs --connectivity-only 2>/dev/null || git -c safe.directory=\'*\' fsck --unreachable --no-reflogs 2>/dev/null); uc=$(printf "%s\\n" "$f" | grep -c "^unreachable commit"); ub=$(printf "%s\\n" "$f" | grep -c "^unreachable blob"); git -c safe.directory=\'*\' for-each-ref --format="%(refname)" | while read -r ref; do [ "$ref" = "$current" ] && continue; git -c safe.directory=\'*\' merge-base --is-ancestor "$ref" HEAD 2>/dev/null && continue; echo "$ref"; done > "$R/del.$i"; ( cd . || exit 3; GIT_OPTIONAL_LOCKS=0; export GIT_OPTIONAL_LOCKS; { git -c safe.directory=\'*\' diff --name-only -z HEAD -- 2>/dev/null | tr "\\0" "\\n" | sed "s/^/D /"; git -c safe.directory=\'*\' ls-files -z | xargs -0 stat -c "F %Y %n" 2>/dev/null; git -c safe.directory=\'*\' ls-tree -r -d -z --name-only HEAD | xargs -0 stat -c "R %Y %n" 2>/dev/null; } | awk \'$1=="D"{d[substr($0,3)]=1; next} {n=substr($0, length($1)+length($2)+3)} $1=="F" && (n in d){dirty++; next} {print} END{print "N", dirty+0}\' | sort -k2,2n | awk -v GAP=600 -v MAX=50 \'$1=="N"{print; next} {k=$1; t=$2+0; n=substr($0, length($1)+length($2)+3); c[k]++; i=c[k]; tm[k,i]=t; nm[k,i]=n} END{for (k in c) { g=1; gs[k,1]=1; size[k,1]=1; for (i=2; i<=c[k]; i++) { if (tm[k,i]-tm[k,i-1] > GAP) { g++; gs[k,g]=i; size[k,g]=0 } size[k,g]++ } b=1; for (j=2; j<=g; j++) if (size[k,j] > size[k,b]) b=j; end=(b<g)?gs[k,b+1]-1:c[k]; later=c[k]-end; earlier=gs[k,b]-1; print "S", k, c[k], g, tm[k,1], tm[k,c[k]], tm[k,gs[k,b]], tm[k,end], earlier, later; shown=0; for (i=end+1; i<=c[k] && shown<MAX; i++) { print "L", k, tm[k,i], nm[k,i]; shown++ } } }\' ) > "$R/mt.$i" 2>/dev/null; mf=$(awk \'$1=="S" && $2=="F"{print $3}\' "$R/mt.$i"); ml=$(awk \'$1=="S" && $2=="F"{print $10}\' "$R/mt.$i"); mf=${mf:-0}; ml=${ml:-0}; mo=$(awk -v l="$ml" -v c="$mf" -v p=20 \'BEGIN{print (l>0 && l*100<=p*c)?"true":"false"}\'); while read -r ref; do git -c safe.directory=\'*\' update-ref --no-deref -d "$ref"; done < "$R/del.$i"; gd=$(git -c safe.directory=\'*\' rev-parse --git-dir); for p in ORIG_HEAD FETCH_HEAD MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_HEAD AUTO_MERGE; do rm -f "$gd/$p"; done; git -c safe.directory=\'*\' reflog expire --expire=now --expire-unreachable=now --all; st=done; git -c safe.directory=\'*\' gc --prune=now --quiet || st=incomplete; T=$(git -c safe.directory=\'*\' log -1 --format=%ct HEAD) && { printf ".\\0"; git -c safe.directory=\'*\' ls-files -z; git -c safe.directory=\'*\' ls-tree -r -d -z --name-only HEAD; } | xargs -0 touch -c -h -d "@$T" || st=incomplete; printf \'{"path":%s,"head":"%s","status":"%s","refs_deleted":%s,"refs_deleted_examples":[%s],"unreachable_before":{"commit":%s,"blob":%s},"mtime_files":%s,"mtime_later":%s,"mtime_outlier":%s,"mtime_examples":[%s]}\' "$(printf "%s\\n" "$top" | awk -v N=1 \'NR<=N{gsub(/\\\\/,"\\\\\\\\"); gsub(/"/,"\\\\\\""); printf "%s\\"%s\\"", (NR>1?",":""), $0}\')" "$head" "$st" "$(wc -l < "$R/del.$i" | tr -d " ")" "$(awk -v N=20 \'NR<=N{gsub(/\\\\/,"\\\\\\\\"); gsub(/"/,"\\\\\\""); printf "%s\\"%s\\"", (NR>1?",":""), $0}\' "$R/del.$i")" "$uc" "$ub" "$mf" "$ml" "$mo" "$(awk \'$1=="L" && $2=="F"{print substr($0, length($1)+length($2)+length($3)+4)}\' "$R/mt.$i" | awk -v N=50 \'NR<=N{gsub(/\\\\/,"\\\\\\\\"); gsub(/"/,"\\\\\\""); printf "%s\\"%s\\"", (NR>1?",":""), $0}\')" > "$R/repo.$i.json"; echo "repository leak repair: $top $st" ) || echo "repository leak repair: $top incomplete"; done; { printf \'{"version":1,"git":true,"repositories":[\'; sep=""; for f in "$R"/repo.*.json; do [ -f "$f" ] || continue; printf "%s" "$sep"; cat "$f"; sep=","; done; printf \']}\\n\'; } > "$out"; rm -rf "$R"; true\n'
GIT_REPAIR_DETACHED = '# ot-validation: repository-leak-repair-v1 (generated; replaced, never stacked)\nRUN out=${OT_REPOSITORY_REPAIR_REPORT:-/var/lib/ot-repository-repair.json}; mkdir -p "$(dirname "$out")"; command -v git >/dev/null 2>&1 || { echo "repository leak repair: no git, nothing to do"; printf \'{"version":1,"git":false,"repositories":[]}\\n\' > "$out"; exit 0; }; R=$(mktemp -d 2>/dev/null || { mkdir -p /tmp/ot-repair.$$ && echo /tmp/ot-repair.$$; }); i=0; seen=""; for d in "$(pwd -P)" /testbed /workspace/repo /workspace /app /repo /code; do [ -d "$d" ] || continue; top=$(cd "$d" && git -c safe.directory=\'*\' rev-parse --show-toplevel 2>/dev/null) || continue; case "$seen" in *"|$top|"*) continue;; esac; seen="$seen|$top|"; hv=1; (cd "$top" && git -c safe.directory=\'*\' rev-parse -q --verify HEAD >/dev/null) || { (cd "$top" && git -c safe.directory=\'*\' symbolic-ref -q HEAD >/dev/null) || continue; hv=0; }; i=$((i+1)); ( cd "$top" || exit 0; current=$(git -c safe.directory=\'*\' symbolic-ref -q HEAD || true); head=$(git -c safe.directory=\'*\' rev-parse -q --verify HEAD || true); f=$(git -c safe.directory=\'*\' fsck --unreachable --no-reflogs --connectivity-only 2>/dev/null || git -c safe.directory=\'*\' fsck --unreachable --no-reflogs 2>/dev/null); uc=$(printf "%s\\n" "$f" | grep -c -E "^(unreachable|dangling) commit"); ub=$(printf "%s\\n" "$f" | grep -c -E "^(unreachable|dangling) blob"); git -c safe.directory=\'*\' for-each-ref --format="%(refname)" | while read -r ref; do [ "$ref" = "$current" ] && continue; git -c safe.directory=\'*\' merge-base --is-ancestor "$ref" HEAD 2>/dev/null && continue; echo "$ref"; done > "$R/del.$i"; if [ $hv = 1 ]; then ( cd . || exit 3; GIT_OPTIONAL_LOCKS=0; export GIT_OPTIONAL_LOCKS; { git -c safe.directory=\'*\' diff --name-only -z HEAD -- 2>/dev/null | tr "\\0" "\\n" | sed "s/^/D /"; git -c safe.directory=\'*\' ls-files -z | xargs -0 stat -c "F %Y %n" 2>/dev/null; git -c safe.directory=\'*\' ls-tree -r -d -z --name-only HEAD | xargs -0 stat -c "R %Y %n" 2>/dev/null; } | awk \'$1=="D"{d[substr($0,3)]=1; next} {n=substr($0, length($1)+length($2)+3)} $1=="F" && (n in d){dirty++; next} {print} END{print "N", dirty+0}\' | sort -k2,2n | awk -v GAP=600 -v MAX=50 \'$1=="N"{print; next} {k=$1; t=$2+0; n=substr($0, length($1)+length($2)+3); c[k]++; i=c[k]; tm[k,i]=t; nm[k,i]=n} END{for (k in c) { g=1; gs[k,1]=1; size[k,1]=1; for (i=2; i<=c[k]; i++) { if (tm[k,i]-tm[k,i-1] > GAP) { g++; gs[k,g]=i; size[k,g]=0 } size[k,g]++ } b=1; for (j=2; j<=g; j++) if (size[k,j] > size[k,b]) b=j; end=(b<g)?gs[k,b+1]-1:c[k]; later=c[k]-end; earlier=gs[k,b]-1; print "S", k, c[k], g, tm[k,1], tm[k,c[k]], tm[k,gs[k,b]], tm[k,end], earlier, later; shown=0; for (i=end+1; i<=c[k] && shown<MAX; i++) { print "L", k, tm[k,i], nm[k,i]; shown++ } } }\' ) > "$R/mt.$i" 2>/dev/null; else : > "$R/mt.$i"; fi; mf=$(awk \'$1=="S" && $2=="F"{print $3}\' "$R/mt.$i"); ml=$(awk \'$1=="S" && $2=="F"{print $10}\' "$R/mt.$i"); mf=${mf:-0}; ml=${ml:-0}; mo=$(awk -v l="$ml" -v c="$mf" -v p=20 \'BEGIN{print (l>0 && l*100<=p*c)?"true":"false"}\'); while read -r ref; do git -c safe.directory=\'*\' update-ref --no-deref -d "$ref"; done < "$R/del.$i"; gd=$(git -c safe.directory=\'*\' rev-parse --git-dir); for p in ORIG_HEAD FETCH_HEAD MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_HEAD AUTO_MERGE; do rm -f "$gd/$p"; done; git -c safe.directory=\'*\' reflog expire --expire=now --expire-unreachable=now --all; rm -rf "$(git -c safe.directory=\'*\' rev-parse --git-path objects/info/commit-graph)" "$(git -c safe.directory=\'*\' rev-parse --git-path objects/info/commit-graphs)"; st=done; git -c safe.directory=\'*\' gc --prune=now --quiet || st=incomplete; if [ $hv = 1 ]; then T=$(git -c safe.directory=\'*\' log -1 --format=%ct HEAD) && { printf ".\\0"; git -c safe.directory=\'*\' ls-files -z; git -c safe.directory=\'*\' ls-tree -r -d -z --name-only HEAD; } | xargs -0 touch -c -h -d "@$T" || st=incomplete; fi; printf \'{"path":%s,"head":"%s","status":"%s","refs_deleted":%s,"refs_deleted_examples":[%s],"unreachable_before":{"commit":%s,"blob":%s},"mtime_files":%s,"mtime_later":%s,"mtime_outlier":%s,"mtime_examples":[%s]}\' "$(printf "%s\\n" "$top" | awk -v N=1 \'NR<=N{gsub(/\\\\/,"\\\\\\\\"); gsub(/"/,"\\\\\\""); printf "%s\\"%s\\"", (NR>1?",":""), $0}\')" "$head" "$st" "$(wc -l < "$R/del.$i" | tr -d " ")" "$(awk -v N=20 \'NR<=N{gsub(/\\\\/,"\\\\\\\\"); gsub(/"/,"\\\\\\""); printf "%s\\"%s\\"", (NR>1?",":""), $0}\' "$R/del.$i")" "$uc" "$ub" "$mf" "$ml" "$mo" "$(awk \'$1=="L" && $2=="F"{print substr($0, length($1)+length($2)+length($3)+4)}\' "$R/mt.$i" | awk -v N=50 \'NR<=N{gsub(/\\\\/,"\\\\\\\\"); gsub(/"/,"\\\\\\""); printf "%s\\"%s\\"", (NR>1?",":""), $0}\')" > "$R/repo.$i.json"; echo "repository leak repair: $top $st" ) || echo "repository leak repair: $top incomplete"; done; { printf \'{"version":1,"git":true,"repositories":[\'; sep=""; for f in "$R"/repo.*.json; do [ -f "$f" ] || continue; printf "%s" "$sep"; cat "$f"; sep=","; done; printf \']}\\n\'; } > "$out"; rm -rf "$R"; true\n'
DETACHED_HISTORY_TASKS = frozenset(['format-code-task-000173', 'format-code-task-000250', 'format-code-task-000306', 'format-code-task-000331', 'format-code-task-000361', 'format-code-task-000476', 'format-code-task-000515', 'format-code-task-000616', 'format-code-task-000631', 'format-code-task-000664', 'format-code-task-000768', 'format-code-task-000822', 'format-code-task-000885', 'format-code-task-001223', 'format-code-task-001226', 'format-code-task-001381', 'format-code-task-001459', 'format-code-task-001624', 'format-code-task-001855', 'format-code-task-002039', 'format-code-task-002106', 'format-code-task-002240', 'format-code-task-002305', 'format-code-task-002588', 'format-code-task-002653', 'format-code-task-002660', 'format-code-task-002706', 'format-code-task-003023'])


def apply(task_id, files):
    """Apply recorded repairs to source-derived files; never load published payloads."""
    result = dict(files)
    original = result['instruction.md']
    sentence = ('You have 1800 seconds to complete this task. Do not cheat by using '
                'online solutions or hints specific to this task.')
    if sentence.encode() in original:
        raise ValueError('Instruction already contains the historical suffix: ' + task_id)
    result['instruction.md'] = original.rstrip() + b'\n\n' + sentence.encode() + b'\n'
    dockerfile = result['environment/Dockerfile']
    if '# ot-validation: repository-leak-repair' in dockerfile:
        raise ValueError('Dockerfile already contains historical repairs: ' + task_id)
    result['environment/Dockerfile'] = dockerfile + (
        GIT_REPAIR_DETACHED if task_id in DETACHED_HISTORY_TASKS else GIT_REPAIR)
    if task_id in MEMORY_MB:
        config = result['task.toml']
        if config.count('memory_mb = 4096\n') != 1:
            raise ValueError('Unexpected source memory limit: ' + task_id)
        result['task.toml'] = config.replace('memory_mb = 4096\n', f'memory_mb = {MEMORY_MB[task_id]}\n')
    return result


def record(output, enabled):
    """Keep the exact repair implementation beside its patch manifest."""
    output = Path(output)
    body = Path(__file__).read_bytes()
    (output / 'repairs.py').write_bytes(body)
    path = output / 'tasks.manifest.json'
    manifest = json.loads(path.read_text())
    manifest['repairs_applied'] = enabled
    manifest['patcher_dependencies'] = [{'path': 'repairs.py', 'sha256': hashlib.sha256(body).hexdigest()}]
    manifest['audit_network_policy'] = {'upstream_explicit_online': False,
        'historical_allow_internet_origin': 'adapter default, not upstream',
        'required_execution': 'offline first for every task; simulated on Helma',
        'online_retry': 'Only after audit for evidenced internet dependencies; separate from audit results',
        'historical_reproduction_exception': 'User-requested offline-first policy changes allow_internet from true to false'}
    path.write_text(json.dumps(manifest, indent=2) + '\n')
