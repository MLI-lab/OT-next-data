"""Convert pinned SWE-Lego tasks to shared Python images and timed task setup."""
from __future__ import annotations

import argparse
import base64
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REVISION = '8b0d2bed6f04ef571ca04abebf737ea23cbceaf3'
HERE = Path(__file__).resolve().parent
MARKER = 'swelego-shared-python-v2'
COMMON_PACKAGES = {'gcc', 'g++', 'gfortran', 'make', 'git', 'curl', 'wget',
                   'ca-certificates', 'tmux', 'patch', 'pkg-config', 'libffi-dev', 'libssl-dev'}
DATASET = 'PrimeIntellect/SWE-Lego-Real-Data-Verified'
VERIFIER_FIXES = {
    'msgpack__msgpack-python-388': {
        'commit': '7a8ce0f9ca910a851b6835d26b1d6970a188fa4e',
        'label': 'verifier-python-backend',
        'reason': 'Run verification with MSGPACK_PUREPYTHON=1 so the existing regression tests exercise msgpack/fallback.py, which the reference patch repairs. The default compiled backend already passes without the fix. Preserve test assertions, required results, dependency versions and reference patch.'},
    'mtgjson__mtgjson-469': {
        'commit': 'b3d7bc4531bdca514dc1cf9f4ea5f6eac1104f89',
        'label': 'verifier-pythonpath-append',
        'reason': 'The verifier command sets PYTHONPATH=. so the tests import the checkout; it is the only SWE-Lego verifier that assigns PYTHONPATH, and the bare assignment discards any path the grading runner adds, which made pytest abort before running tests (reward 0 for the reference). Append the inherited PYTHONPATH after the checkout directory instead. Test selection, assertions, required results, dependency versions and reference patch are unchanged.'},
    'h2non__pook-111': {
        'commit': 'fac40e9f571152ba09bc16954548ce51d590ccea',
        'label': 'mocked-http',
        'unset_env': ['HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy'],
        'reason': 'The constructor tests mock https://httpbin.org and never reach the network, but with the cluster proxy configured the intercepted request carries the proxy address and no mock matches; removing only the plain-HTTP proxy left the two https cases failing (job 964411) while the other ten tests pass. The verifier command runs with all proxy variables unset; task setup keeps them for its downloads. Test selection, assertions, required results, dependency versions and reference patch are unchanged.'},
    'sdss__sdss_access-69': {
        'commit': '1519bc3870486d8bc3b6293d8c76680fabee909a',
        'label': 'verifier-fixed-date',
        'reason': 'Fix the verifier-local sdss_access.path.path clock at 2025-04-01, before the pinned sdss-tree 4.0.7 DR19 release date of 2025-07-11. This preserves the pre-release regression case that otherwise stops detecting the missing fix after release. Preserve test assertions, required results, dependency versions and reference patch.'},
}
TASK_RESOURCE_FIXES = {
    'dask__dask-1150': {
        'memory_mb': 8192,
        'label': 'task-memory-8gib',
        'reason': 'Raise the task memory limit from 4 GiB to 8 GiB. The unchanged reference verifier passed with a 12 GiB limit and recorded 7,527,284 KiB (about 7.18 GiB) MaxRSS, so 8 GiB provides a modest margin above measured peak while avoiding the unnecessary 12 GiB allocation.'},
    'dask__dask-4050': {
        'memory_mb': 8192,
        'label': 'task-memory-8gib',
        'reason': 'Raise the task memory limit from 4 GiB to 8 GiB. With a 12 GiB limit the unchanged verifier passed reference (reward 1) and no-op (reward 0) in job 960179; its reference step recorded 7.17 GiB MaxRSS and the no-op step 6.30 GiB, the same dask array test load as dask-1150. At 4 GiB the verifier was killed before scoring.'},
    'dask__dask-4181': {
        'memory_mb': 8192,
        'label': 'task-memory-8gib',
        'reason': 'Raise the task memory limit from 4 GiB to 8 GiB. With a 12 GiB limit the unchanged verifier passed reference (reward 1) and no-op (reward 0) in job 960179; its steps recorded 6.49 GiB and 6.65 GiB MaxRSS. At 4 GiB the verifier was killed before scoring.'},
    'tobymao__sqlglot-1889': {
        'memory_mb': 10240,
        'label': 'task-memory-10gib',
        'reason': 'Raise the task memory limit from 4 GiB to 10 GiB. With a 12 GiB limit the unchanged reference verifier passed (reward 1) in job 960179 and recorded 7.82 GiB MaxRSS even with its two bounded worker processes; 8 GiB would leave too little margin. At 4 GiB the verifier was killed before scoring.'},
    'microsoft__electionguard-python-381': {
        'memory_mb': 8192,
        'label': 'task-memory-8gib',
        'reason': 'Raise the task memory limit from 4 GiB to 8 GiB. The big-integer key-ceremony and decryption tests passed reference in four of five 4 GiB attempts and were killed by the task memory limit in the fifth (job 959908); the 12 GiB rerun in job 960179 passed with 3.06 GiB MaxRSS. 8 GiB removes the limit-dependent flakiness without changing tests or dependencies.'},
    'zarr-developers__zarr-python-2784': {
        'memory_mb': 10240,
        'label': 'task-memory-10gib',
        'reason': 'Raise the task memory limit from 4 GiB to 10 GiB. With a 12 GiB limit the verifier ran to completion in job 964135 and its reference and no-op steps recorded 8.11 GiB and 8.09 GiB MaxRSS; at 4 GiB it was killed before scoring. Tests and dependency versions are unchanged.'},
    'tcgdex__python-sdk-2': {
        'no_proxy': 'api.tcgdex.net',
        'label': 'recorded-http',
        'reason': 'The verifier replays recorded vcrpy cassettes (RecordMode.ONCE) for api.tcgdex.net. Through the inherited cluster proxy the live request URI names the proxy host and port, so every cassette lookup fails on the host and port matchers while method, path and query match (job 964137). Send that host directly so the recordings match; no cassette, test or reference change.'},
    'contentful__contentful-management.py-117': {
        'no_proxy': 'api.contentful.com',
        'label': 'recorded-http',
        'reason': 'Thirteen ContentType tests fail with vcrpy CannotOverwriteExistingCassette for api.contentful.com recordings while the other seventeen tests pass (job 959554). The same proxy-rewritten request URI breaks tcgdex-2; send the recorded host directly so the cassettes match. Verified by rerun; no cassette, test or reference change.'},
    'h2non__pook-83': {
        'runtime_unset': ['HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy'],
        'label': 'mocked-http',
        'reason': 'pook intercepts HTTP at the client library and never reaches the network, but with the cluster proxy configured the intercepted request URL becomes http://proxy.nhr.fau.de:80http://httpbin.org/foo and no mock matches (job 964137). The verifier runs without plain-HTTP proxy variables (the mocked URLs are http://); HTTPS keeps the proxy so the git fetch during setup still works, as for conan-5005. Tests and reference patch are unchanged.'},
    'googleapis__google-auth-library-python-424': {
        'runtime_unset': ['HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy'],
        'label': 'mocked-http',
        'reason': 'The required test_connection_error expects a TransportError from a plain-HTTP request to the reserved invalid host test.invalid; through the cluster proxy the request gets an HTTP error reply instead and nothing is raised (jobs 964137 and 964323, all 21 other tests pass). The verifier runs without plain-HTTP proxy variables; HTTPS keeps the proxy for setup downloads. Tests and reference patch are unchanged.'},
}
ARCHIVED_TASKS = {
    'openstates__pyopenstates-15': {
        'category': 'no-op-passes-required-tests',
        'reason': 'The two required tests (testBillSearchMissingFilter, testBillDetailInputs) check input validation that already passes on the unpatched tree, so the no-op run scores 1 (job 958628); the other 26 tests in the module query the live Open States API and fail with NotFound regardless of the patch. The verifier cannot distinguish the reference from no change. Archived with the original payload preserved.'},
    'modin-project__modin-5058': {
        'category': 'verifier-memory-exceeds-limits',
        'reason': 'The reference verifier (modin/test/storage_formats/pandas/test_internals.py on Ray 2.7.2) ran for the full 1800 s and was killed at a 12 GiB task limit (job 960179). With MODIN_CPUS=4 and a 512 MiB MODIN_MEMORY object store, the bound that fixed modin-1842, it was still killed at 12 GiB after 16 minutes with 46 OOM-killed Ray processes (job 964168) and at 4 GiB in job 964134. Archived at user request as exceeding supportable task memory; original payload preserved.'},
    'lundberg__respx-13': {
        'category': 'incompatible-verifier-dependency',
        'reason': 'The reference run crashes inside respx/mock.py:229 with AttributeError: str object has no attribute attachment for every HTTPX mock test (job 959554, batch v66-1). The pinned httpx no longer passes the pattern objects this historical respx expects, so the recorded dependency set cannot execute the suite. Archived with the original payload preserved; see pilot-results/swelego-reference-17-diagnosis-v68.json.'},
    'lundberg__respx-21': {
        'category': 'incompatible-verifier-dependency',
        'reason': 'The same str-versus-pattern AttributeError as respx-13 occurs at respx/mock.py:273 together with unmatched-mock assertions (job 959554, batch v66-1); the pinned httpx is incompatible with this historical respx. Archived with the original payload preserved; see pilot-results/swelego-reference-17-diagnosis-v68.json.'},
    'mdsol__rwslib-111': {
        'category': 'incompatible-verifier-dependency',
        'reason': 'Seventeen tests fail because the pinned HTTPretty exposes no HTTPrettyRequest.headers attribute under Python 3.7 while 24 pass (job 959554, batch v66-1), the same HTTPretty incompatibility that archived PyPeri-8. Archived with the original payload preserved; see pilot-results/swelego-reference-17-diagnosis-v68.json.'},
    'getsentry__sentry-python-79': {
        'category': 'incompatible-verifier-dependency',
        'reason': 'test_safe_repr_never_broken_for_strings fails inside hypothesis 3.69.9, which drives coverage.py internals that the pinned coverage 7.2.7 no longer provides (TypeError: missing should_start_context and file_mapper). test_transport_works additionally saw its local pytest-localserver bypassed by the cluster proxy (job 964137); loopback is now excluded from the proxy, but the hypothesis/coverage pin conflict remains in the recorded environment. Archived with the original payload preserved.'},
    '2gis__k8s-handle-120': {
        'category': 'version-sensitive-test-expectation',
        'reason': 'Only test_generate_templates fails: the rendered YAML has the same lines in a different order and blank-line placement than the expected literal, while the reference patch changes tag filtering, not template serialization (jobs 959554 and batch v66-0). The expectation depends on the exact YAML/Jinja versions of the original environment. Archived with the original payload preserved.'},
    'pytest-dev__pytest-asyncio-1029': {
        'category': 'version-sensitive-test-expectation',
        'reason': 'Three nested pytester tests expect exactly two unclosed-event-loop warnings but observe three: the recorded Python/pytest patch versions emit an additional unraisable ResourceWarning for the loop\'s self-pipe socket (job 964137). The other four selected tests pass. Archived with the original payload preserved.'},
    'ctypesgen__ctypesgen-150': {
        'category': 'unsupported-host-environment',
        'reason': 'Archived because the task does not work with the shared base image we chose: the historical parser reports syntax errors in the glibc 2.36 sys/cdefs.h of the Debian 12 image and its version test exits 2 (jobs 959554 and batch v66-0). It could be repaired with a separate older base image, which would mean one extra image for a single task. Archived with the original payload preserved; see pilot-results/swelego-reference-17-diagnosis-v68.json.'},
    'richardkiss__pycoin-353': {
        'category': 'unreliable-external-service-dependency',
        'reason': 'Reference and no-op verification exceeded 1800 seconds. A diagnostic rerun traced test_tx_create_dump_sign_txt to an unbounded blockchain.info socket connection; the CLI test helper removes proxy settings. Archived at user request, with original payload preserved. Evidence: pilot-results/swelego-correctness-investigation-20261008.json.'},
    'fatiando__pooch-291': {
        'category': 'unreliable-external-service-dependency',
        'reason': 'Required Zenodo download tests intermittently fail resolving DOI 10.5281/zenodo.4924875. The same payload also passed previously; caching files alone would bypass neither the fresh-directory downloads nor their assertions. Archived at user request instead of adding response replay, with original payload preserved. Evidence: pilot-results/swelego-correctness-investigation-20261008.json.'},
    'takeontom__PyPeri-29': {
        'category': 'unreliable-live-service-dependency',
        'reason': 'Reference verification calls the live Periscope service for API, broadcast, and user data; these requests failed TLS verification in the cluster and depend on an external service remaining available. Archived because live-service verification is unreliable here. Original task payload preserved. Evidence: pilot-results/swelego-reference-stage4-remaining-20261009.json.'},
    'takeontom__PyPeri-35': {
        'category': 'unreliable-live-service-dependency',
        'reason': 'Reference verification calls the live Periscope service for API, broadcast, user, and session-token data; these requests failed TLS verification in the cluster and depend on an external service remaining available. Archived because live-service verification is unreliable here. Original task payload preserved. Evidence: pilot-results/swelego-reference-stage4-remaining-20261009.json.'},
    'kivy__kivy-6954': {
        'category': 'unreliable-live-service-dependency',
        'reason': 'Verifier tests issue real requests to google.com and httpbin.org to check callbacks, authentication, and TLS behavior. Those external endpoints failed to complete in the cluster, so reference scores depend on live service and network availability. Archived as unreliable; original task payload preserved. Evidence: pilot-results/swelego-reference-stage4-remaining-20261009.json.'},
    'sphinx-doc__sphinx-5203': {
        'category': 'unreliable-live-service-dependency',
        'reason': 'The linkcheck verifier fetches https://www.w3.org/TR/2006/REC-xml-names-20060816/#defaulting and asserts the remote anchor exists. The current W3C response no longer contains that anchor, so results depend on mutable live site content. Archived as unreliable; original task payload preserved. Evidence: pilot-results/swelego-reference-stage4-remaining-20261009.json.'},
    'cisagov__check-cve-2019-19781-10': {
        'category': 'unreliable-live-service-dependency',
        'reason': 'The valid timeout and retry verifier cases call github.com directly rather than using a local HTTP fixture. Their outcome depends on live DNS/network/service behavior, so verification is unreliable on our cluster. Archived as unreliable; original task payload preserved. Evidence: pilot-results/swelego-reference-stage4-remaining-20261009.json.'},
}
ARCHIVED_TASKS.update({'Azure__azure-cli-3409': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1030': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1040': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1095': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1211': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1229': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1269': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1308': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1404': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1415': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1417': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1498': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'cdent__gabbi-186': {'category': 'proxy-sensitive-verification',
                      'reason': 'HTTP interception tests reject inherited proxy environment variables; '
                                'wsgi-intercept explicitly requires them to be unset. Archived at user '
                                'request because verification is unreliable under our proxy environment. '
                                'This does not establish that the test requires live external network '
                                'access. Original task payload preserved. Evidence: '
                                'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'cdent__gabbi-191': {'category': 'proxy-sensitive-verification',
                      'reason': 'HTTP interception tests reject inherited proxy environment variables; '
                                'wsgi-intercept explicitly requires them to be unset. Archived at user '
                                'request because verification is unreliable under our proxy environment. '
                                'This does not establish that the test requires live external network '
                                'access. Original task payload preserved. Evidence: '
                                'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'cdent__wsgi-intercept-47': {'category': 'proxy-sensitive-verification',
                              'reason': 'HTTP interception tests reject inherited proxy environment '
                                        'variables; wsgi-intercept explicitly requires them to be unset. '
                                        'Archived at user request because verification is unreliable under '
                                        'our proxy environment. This does not establish that the test '
                                        'requires live external network access. Original task payload '
                                        'preserved. Evidence: '
                                        'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'desihub__desitransfer-5': {'category': 'proxy-sensitive-verification',
                             'reason': 'Case-insensitive environment parsing fails because inherited '
                                       'NO_PROXY/no_proxy settings produce duplicate configuration keys. '
                                       'Archived at user request because verification is unreliable under '
                                       'our proxy environment. This does not establish that the test '
                                       'requires live external network access. Original task payload '
                                       'preserved. Evidence: '
                                       'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'getsentry__sentry-python-2009': {'category': 'proxy-sensitive-verification',
                                   'reason': 'Proxy configuration assertions observe the inherited cluster '
                                             'proxy instead of the test-controlled configuration. Archived '
                                             'at user request because verification is unreliable under our '
                                             'proxy environment. This does not establish that the test '
                                             'requires live external network access. Original task payload '
                                             'preserved. Evidence: '
                                             'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'getsentry__sentry-python-3351': {'category': 'proxy-sensitive-verification',
                                   'reason': 'Proxy configuration assertions observe the inherited cluster '
                                             'proxy instead of the test-controlled configuration. Archived '
                                             'at user request because verification is unreliable under our '
                                             'proxy environment. This does not establish that the test '
                                             'requires live external network access. Original task payload '
                                             'preserved. Evidence: '
                                             'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'lundberg__respx-60': {'category': 'proxy-sensitive-verification',
                        'reason': 'Verification reaches the inherited HTTP proxy and receives HTTP 503 '
                                  'instead of the expected mocked transport response. Archived at user '
                                  'request because verification is unreliable under our proxy environment. '
                                  'This does not establish that the test requires live external network '
                                  'access. Original task payload preserved. Evidence: '
                                  'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'pdm-project__pdm-3255': {'category': 'proxy-sensitive-verification',
                           'reason': 'The inherited cluster proxy overrides the localhost proxy selected by '
                                     'the test. Archived at user request because verification is unreliable '
                                     'under our proxy environment. This does not establish that the test '
                                     'requires live external network access. Original task payload '
                                     'preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'radiasoft__pykern-107': {'category': 'proxy-sensitive-verification',
                           'reason': 'Case-insensitive environment parsing fails because inherited '
                                     'NO_PROXY/no_proxy settings produce duplicate configuration keys. '
                                     'Archived at user request because verification is unreliable under our '
                                     'proxy environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'radiasoft__pykern-259': {'category': 'proxy-sensitive-verification',
                           'reason': 'Case-insensitive environment parsing fails because inherited '
                                     'NO_PROXY/no_proxy settings produce duplicate configuration keys. '
                                     'Archived at user request because verification is unreliable under our '
                                     'proxy environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                            'pilot-results/swelego-reference-failure-triage-20261009.json.'}})
ARCHIVED_TASKS.update({
    'Yelp__bravado-410': {
        'category': 'proxy-sensitive-verification',
        'reason': 'The verifier uses local HTTP mocks, but the fresh reference rerun received HTTP 503 responses from the inherited cluster proxy for the HTTP-client tests, including test_real_post. Reference reward remained 0.0; the no-op also scored 0.0 as expected. Archived because the proxy prevents reliable mock verification in this environment, not because the task intentionally requires a live service. Original task payload preserved. Evidence: pilot-results/swelego-reference-stage4-remaining-20261009.json and the reference42-v66-0 stage 4 report.'},
    'takeontom__PyPeri-8': {
        'category': 'incompatible-verifier-dependency',
        'reason': 'The verifier uses HTTP mocks, but its pinned HTTPretty 1.1.4 test adapter raises AttributeError because HTTPrettyRequest has no headers attribute in the pinned Python environment. The reference scored 0.0 while the no-op scored 0.0, so the required reference/no-op distinction is not established. Archived as an incompatible verifier dependency; this is not classified as a live-service failure. A fresh Stage 4/5 retry already in progress is retained as diagnostic evidence only. Original task payload preserved. Evidence: pilot-results/swelego-reference-stage4-remaining-20261009.json and the reference42-v66-2 stage 4 report.'},
    'PyPSA__linopy-77': {
        'category': 'unsupported-expired-proprietary-license',
        'reason': 'The reference verifier exercises Xpress solver cases, but the available Xpress license expired on 2026-02-28. The required proprietary solver cases cannot be validated on our infrastructure without a renewed license. Archived with the original task payload preserved. Evidence: pilot-results/swelego-reference-stage4-remaining-20261009.json.'},
    'airbrake__pybrake-77': {
        'category': 'unreliable-live-service-verification',
        'reason': 'The verifier calls the default Airbrake endpoint and an intentionally invalid external host without HTTP mocks. The unauthorized test received a non-JSON response and raised JSONDecodeError; the invalid-host test also failed instead of observing the expected DNS URLError. The reference scored 0.0 and no-op scored 0.0. These tests depend on live network/proxy behavior, so archive the original payload as unreliable on our cluster.'},
})
SOURCE_MIRRORS = {
    'NREL/hescore-hpxml': 'https://github.com/rubythonode/hescore-hpxml.git',
    'cuenca-mx/cuenca-python': 'https://github.com/ricardo8990/cuenca-python.git',
    'globality-corp/flake8-logging-format': 'https://github.com/pawmarkor/flake8-logging-format.git',
    'tarohi24/typedflow': 'https://github.com/mirror-dump/typedflow.git',
}
AMQP_ARCHIVE_OLD = ('https://github.com/celery/py-amqp/zipball/main#sha256='
                    'bc618e1a51e852a457ee5aca2a8d1b46440d1304ca23f50d250d2fc216e3e499')
# This commit's archive has exactly the original recorded SHA-256.
AMQP_ARCHIVE_PINNED = AMQP_ARCHIVE_OLD.replace('/main#', '/b1f9c2e3d10c35601c9453a074bf8ae8dee9dd5b#')
VINE_ARCHIVE_OLD = ('https://github.com/celery/vine/zipball/master#sha256='
                    '955be2b59aaf8e6b68540d03a5af8dc493f6ef3dacbdcc977907f9909e6c866e')
VINE_ARCHIVE_PINNED = VINE_ARCHIVE_OLD.replace('/master#', '/84c6431a4f57ce7018f2231ba3f363aa050b0e32#')
SELF_REQUIREMENTS = {
    'plone__plone.app.robotframework-117':
        ('f79eead00881762884365424d46df1c8fbf61b33', 'plone.app.robotframework==1.5.3.dev0'),
}
REQUIREMENT_SOURCES = {
    'mne-tools__mne-bids-1388': (
        '4ac800537776b63b8fde1f8ad97bdbfcdeb50389', 'sphinx-gallery @ https://github.com/sphinx-gallery/sphinx-gallery/archive/refs/heads/master.zip#sha256=67ad22fe2187c5799d800fa5219f1bfcc9b47438f1eb4c688f071f58a2e663ac',
        'sphinx-gallery @ https://github.com/sphinx-gallery/sphinx-gallery/archive/d653bfc679b8baffd39577f526fc324e2c97f69d.zip#sha256=5871f8b5be8d077851f51052c0ea3dbecaa341cb5c2f41cb622ce4310ac0afab'),
    'mne-tools__mne-bids-1357': (
        '3492fa01157d921f77b93ea31a4db192c47d3bb0', 'sphinx-gallery @ https://github.com/sphinx-gallery/sphinx-gallery/archive/refs/heads/master.zip#sha256=67ad22fe2187c5799d800fa5219f1bfcc9b47438f1eb4c688f071f58a2e663ac',
        'sphinx-gallery @ https://github.com/sphinx-gallery/sphinx-gallery/archive/d653bfc679b8baffd39577f526fc324e2c97f69d.zip#sha256=5871f8b5be8d077851f51052c0ea3dbecaa341cb5c2f41cb622ce4310ac0afab'),
    'mne-tools__mne-bids-1359': (
        '3f59b0e0ee835549d068ad4ad85936ddf0ed04cb', 'sphinx-gallery @ https://github.com/sphinx-gallery/sphinx-gallery/archive/refs/heads/master.zip#sha256=67ad22fe2187c5799d800fa5219f1bfcc9b47438f1eb4c688f071f58a2e663ac',
        'sphinx-gallery @ https://github.com/sphinx-gallery/sphinx-gallery/archive/d653bfc679b8baffd39577f526fc324e2c97f69d.zip#sha256=5871f8b5be8d077851f51052c0ea3dbecaa341cb5c2f41cb622ce4310ac0afab'),
    'pgmpy__pgmpy-1905': (
        '4b1743dfefcc2b749517df68887ec783f009e5e7', 'daft==0.4.9',
        'getdaft @ https://files.pythonhosted.org/packages/ee/51/9a858ea182857a42b6df9039f917861ff2244b6d9accce26c3f0b28af63b/getdaft-0.4.9-cp39-abi3-manylinux_2_28_x86_64.whl#sha256=5d7f1bde9f272b56a5a51e662fe5a9e3f4ec5689e55f7892b02160049c9f4304'),
    'All-Hands-AI__openhands-resolver-107': (
        '2d68cabf4ea855bbaf9957b3a84e7ab9805a69e6', 'litellm==1.46.0',
        'litellm @ git+https://github.com/BerriAI/litellm.git'
        '@2efdd2a6a4723616b9dea62594560b4094c08373'),
    'dwavesystems__dwave-system-276': (
        '6b4c9f790f41fda29b6391fbb7add702fa4d582a', 'dwave-drivers==0.4.4',
        'dwave-drivers @ https://pypi.dwavesys.com/simple/dwave-drivers/'
        'dwave_drivers-0.4.4-py3-none-any.whl#sha256='
        '8e5b37e97be7610c00005e5f8a3c10f27f7cec4bd2b88c80f9c6fef8172a3c3f'),
    'dwavesystems__dwave-system-373': (
        '01d3c061440d94c22234dbf99ecd277def4259b9', 'dwave-drivers==0.4.4',
        'dwave-drivers @ https://pypi.dwavesys.com/simple/dwave-drivers/'
        'dwave_drivers-0.4.4-py3-none-any.whl#sha256='
        '8e5b37e97be7610c00005e5f8a3c10f27f7cec4bd2b88c80f9c6fef8172a3c3f'),
    'acorg__dark-matter-651': (
        'e48f39aded59ac675d9b250f3cb0c7677968fbe3', 'mysql-connector-python==8.0.11',
        'mysql-connector-python @ https://cdn.mysql.com/archives/mysql-connector-python-8.0/'
        'mysql-connector-python-8.0.11.tar.gz#sha256='
        '66135b1c45158c63d19f9a914cb038b7936e0cc2a68549a6d97425258de2607a'),
    'acorg__dark-matter-637': (
        '7fc47a737e687f6b0f0bfe7414c7f8947bb16bea', 'mysql-connector-python==8.0.11',
        'mysql-connector-python @ https://cdn.mysql.com/archives/mysql-connector-python-8.0/'
        'mysql-connector-python-8.0.11.tar.gz#sha256='
        '66135b1c45158c63d19f9a914cb038b7936e0cc2a68549a6d97425258de2607a'),
}
ADDITIONAL_REQUIREMENT_SOURCES = {
    'pgmpy__pgmpy-1905': (
        '4b1743dfefcc2b749517df68887ec783f009e5e7', 'litellm==1.47.0',
        'litellm @ git+https://github.com/BerriAI/litellm.git'
        '@7ca9165d597d21d957a1071293e79afb298b88de'),
}
METADATA_COLUMNS = ['instance_id', 'repo', 'base_commit', 'image_name', 'version',
                    'install_config', 'environment_setup_commit', 'environment', 'requirements']


def load_recipes(metadata, cache):
    """Fetch missing recipe metadata at the same pinned revision as the tasks."""
    if metadata.exists():
        document = json.loads(metadata.read_text())
        if document.get('revision') != REVISION or document.get('dataset') != DATASET:
            raise ValueError('Recipe metadata must identify the pinned dataset and revision')
        return document['recipes']
    from huggingface_hub import hf_hub_download
    import pyarrow.parquet as pq
    recipes = {}
    shards = []
    for index in range(3):
        name = f'data/resolved-{index:05d}-of-00003.parquet'
        path = Path(hf_hub_download(DATASET, name, repo_type='dataset', revision=REVISION,
                                    cache_dir=str(cache)))
        for batch in pq.ParquetFile(path).iter_batches(columns=METADATA_COLUMNS):
            for row in batch.to_pylist():
                if row['instance_id'] in recipes:
                    raise ValueError('Duplicate upstream task: ' + row['instance_id'])
                recipes[row['instance_id']] = row
        with path.open('rb') as stream:
            shards.append({'name': name, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()})
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(json.dumps({'dataset': DATASET, 'revision': REVISION,
                                    'shards': shards, 'recipes': recipes}, indent=2) + '\n')
    return recipes


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def environment(row):
    python, specs = _environment(row['environment'])
    return python, list(specs)


@lru_cache(maxsize=512)
def _environment(serialized):
    import yaml
    # The pinned Parquets store literal backslash-n in this field.
    env = yaml.safe_load(serialized.replace('\\n', '\n'))
    specs = sorted(x for x in env['dependencies'] if isinstance(x, str))
    python = next(x.split('=')[1] for x in specs if x.startswith('python='))
    return python, tuple(specs)


OEMOF_COMMIT = '585b123e3dc02b191fead4d202ba60c057c473fd'
OEMOF_PREINSTALL = ['apt-get update', 'apt-get install -y gcc',
    'git clone https://github.com/oemof/oemof-solph.git', 'cd oemof-solph',
    'git checkout ' + OEMOF_COMMIT, 'pip install .', 'cd ..']


def local_resolution(row, line):
    """Only resolve documented non-conda entries from the pinned snapshot."""
    name, url = line.split(' @ ', 1)
    name = canonical(name)
    if name == 'swebench-matterhorn' and url == 'file:///swebench_matterhorn':
        # Upstream collection harness; Harbor supplies its own verifier. The
        # affected pinned repository trees contain no references to this package.
        return 'omit-collection-harness', None
    if (row['repo'] == 'dask/dask' and name == 'msgpack'
            and url == 'file:///tmp/build/80754af9/msgpack-python_1612287171716/work'
            and 'msgpack-python==0.5.6' in row['requirements'].splitlines()):
        # The old and renamed distributions share the msgpack module. Restore
        # the explicitly frozen pip installation, not stale conda build metadata.
        return 'superseded-by-frozen-msgpack-python', None
    if (row.get('instance_id') == 'rl-institut__smooth-167' and name == 'oemof'
            and url == 'file:///smooth/oemof-solph'
            and row['install_config'].get('pre_install') == OEMOF_PREINSTALL):
        return 'pinned-preinstall-source', ('oemof @ git+https://github.com/oemof/'
                                           'oemof-solph.git@' + OEMOF_COMMIT)
    return None


def dependencies(row, specs):
    """Retain frozen pip pins; conda restores nonportable file:// packages."""
    conda = {canonical(s.split('=')[0]): s.split('=')[1] for s in specs}
    aliases = {'msgpack': ('msgpack-python',), 'esmf-regrid': ('iris-esmf-regrid',),
               'scitools-iris': ('iris',), 'stratify': ('python-stratify',),
               'brotli': ('brotli-python', 'brotli'), 'dask': ('dask-core',),
               'openforcefields': ('openff-forcefields',), 'torch': ('pytorch',),
               'antlr4-python3-runtime': ('antlr-python-runtime',), 'tables': ('pytables',),
               'lief': ('py-lief',),
               'openff-interchange': ('openff-interchange-base',),
               'openff-toolkit': ('openff-toolkit-base',),
               'openff-nagl': ('openff-nagl-base',),
               'matplotlib': ('matplotlib-base',), 'ruamel-yaml-conda': ('ruamel-yaml',)}
    lines = []
    own = 'git+https://github.com/' + row['repo'] + '.git@'
    for line in row['requirements'].splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('-e ' + own):
            continue  # Install the task checkout, never a second VCS copy.
        own_pin = SELF_REQUIREMENTS.get(row.get('instance_id'))
        if own_pin and line == own_pin[1]:
            if row['base_commit'] != own_pin[0]:
                raise ValueError('Self requirement source commit mismatch')
            continue  # setup.py at this exact commit supplies the recorded version.
        line = line.replace(AMQP_ARCHIVE_OLD, AMQP_ARCHIVE_PINNED)
        line = line.replace(VINE_ARCHIVE_OLD, VINE_ARCHIVE_PINNED)
        for sources in (REQUIREMENT_SOURCES, ADDITIONAL_REQUIREMENT_SOURCES):
            source = sources.get(row.get('instance_id'))
            if source and line == source[1]:
                if row['base_commit'] != source[0]:
                    raise ValueError('Requirement source task commit mismatch')
                line = source[2]
        if ' @ file://' in line:
            resolution = local_resolution(row, line)
            if resolution is not None:
                if resolution[1] is not None:
                    lines.append(resolution[1])
                continue
            name = canonical(line.split(' @ ', 1)[0])
            if name == canonical(row['repo'].split('/')[1]):
                continue  # A local installation of the task repository.
            candidates = (name, 'python-' + name, name + '-core', name + '-split',
                          name + '-ext', *aliases.get(name, ()))
            if not any(candidate in conda for candidate in candidates):
                raise ValueError('Local requirement has no frozen conda package: ' + line)
            continue
        if '==' in line:
            name, version = line.split('==', 1)
            name = canonical(name)
            candidates = (name, 'python-' + name, name + '-core', name + '-split',
                          name + '-ext', *aliases.get(name, ()))
            if any(conda.get(candidate) == version for candidate in candidates):
                continue
        lines.append(line)
    if row.get('instance_id') in {'goodmami__wn-174', 'goodmami__wn-178'}:
        # Both pinned pyprojects require flit_core >=3.4,<4. The isolated
        # upstream build backend is absent from the runtime freeze.
        lines.append('flit_core==3.9.0')
    if row.get('instance_id') == 'encode__starlette-1715':
        # Its pinned pyproject requires hatchling, originally installed only in
        # pip's ephemeral build environment and absent from the frozen snapshot.
        # 1.17.1 supports Python 3.7; 1.18.0 requires Python 3.8.
        lines += ['hatchling==1.17.1', 'editables==0.3', 'trove-classifiers==2023.8.7']
    if row.get('instance_id') == 'astropy__astropy-16127':
        # Its pinned pyproject declares these build dependencies; upstream's
        # isolated build environment was not included in the runtime freeze.
        lines += ['extension-helpers==1.2.0', 'Cython==3.0.12', 'setuptools-scm==8.2.0']
    if row.get('instance_id') == 'sphinx-doc__sphinx-12875':
        lines.append('flit_core==3.9.0')
    if row.get('instance_id') in {'fatiando__pooch-291', 'fatiando__pooch-315', 'fatiando__pooch-365'}:
        # These pinned pyprojects generate pooch/_version.py through this
        # build plugin, absent from the recorded runtime environment.
        lines.append('setuptools-scm==8.2.0')
    if row.get('instance_id') == 'rsagroup__rsatoolbox-375':
        lines += ['setuptools-scm==8.2.0', 'Cython==3.0.12']
    if row.get('instance_id') == 'mwouts__itables-352':
        lines += ['hatchling==1.27.0', 'pathspec==0.12.1', 'trove-classifiers==2025.3.19.19',
                  'hatch-jupyter-builder==0.9.1', 'editables==0.5']
    backend = build_backends().get(row.get('instance_id'))
    if backend:
        if backend['base_commit'] != row['base_commit']:
            raise ValueError('Build backend source commit mismatch')
        recorded = dict(conda)
        for requirement in row['requirements'].splitlines():
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s;]+)', requirement.strip())
            if match:
                recorded[canonical(match[1])] = match[2]
        for requirement in backend['install']:
            name, version = requirement.split('==', 1)
            if recorded.get(canonical(name), version) != version:
                raise ValueError('Build dependency conflicts with frozen runtime: ' + requirement)
        lines += backend['install']
    repair = environment_repair(row)
    for old, new in repair.get('runtime_replacements', {}).items():
        if lines.count(old) != 1:
            raise ValueError('Runtime repair requires exactly one original pin: ' + old)
        lines[lines.index(old)] = new
    lines += repair.get('test_dependencies', [])
    return '\n'.join(lines) + '\n'


@lru_cache(maxsize=1)
def environment_repairs():
    document = json.loads((HERE / 'environment-repairs.json').read_text())
    if document['source_revision'] != REVISION:
        raise ValueError('Wrong environment repair source revision')
    return document['tasks']


def environment_repair(row):
    repair = environment_repairs().get(row.get('instance_id'), {})
    if repair and repair['base_commit'] != row['base_commit']:
        raise ValueError('Environment repair source commit mismatch')
    return repair


@lru_cache(maxsize=1)
def build_backends():
    document = json.loads((HERE / 'build-backends.json').read_text())
    if document['source_revision'] != REVISION:
        raise ValueError('Wrong build backend source revision')
    return document['tasks']


@lru_cache(maxsize=1)
def wheel_cache():
    document = json.loads((HERE / 'wheel-cache.json').read_text())
    if document['source_revision'] != REVISION:
        raise ValueError('Wrong wheel cache source revision')
    return document['profiles']


def wheel_batches(pins):
    """One version per distribution per pip invocation, with no dependency solving."""
    batches = []
    for pin in pins:
        name = canonical(pin.split('==', 1)[0])
        for batch in batches:
            if all(canonical(other.split('==', 1)[0]) != name for other in batch):
                batch.append(pin)
                break
        else:
            batches.append([pin])
    return batches


def select_image(row, lock):
    """Promote measured expensive environments; keep the default shared image."""
    python, specs = environment(row)
    key = hashlib.sha256('\n'.join(specs).encode()).hexdigest()
    selected = dict(lock)
    identity = python
    if key in lock.get('setup_profiles', {}):
        if lock['setup_profiles'][key] != specs:
            raise ValueError('Promoted environment mismatch')
        selected['environments'] = {**lock['environments'], python: specs}
        selected['_omit_wheels'] = True
        identity += ':conda-' + key[:12]
    requirements = set(dependencies(row, specs).splitlines())
    profiles = [(name, profile) for name, profile in lock.get('pip_layers', {}).items()
                if python == profile['python'] and set(profile['requires']).issubset(requirements)]
    for name, profile in profiles:
        # A complete frozen environment already includes its smaller shared
        # scientific core. Preserve the more specific existing image.
        if any(set(profile['requires']) < set(other['requires']) for _, other in profiles):
            continue
        if python == profile['python'] and set(profile['requires']).issubset(requirements):
            if '_pip_layer' in selected:
                raise ValueError('Overlapping pip image profiles')
            selected['_pip_layer'] = profile['install']
            if profile.get('wheel_subset'):
                selected['_wheel_subset'] = profile['install']
            if profile.get('omit_wheels'):
                selected['_omit_wheels'] = True
            if profile.get('system_packages'):
                selected['system_packages'] = {**selected.get('system_packages', {}), python:
                    sorted(set(selected.get('system_packages', {}).get(python, []))
                           | set(profile['system_packages']))}
            identity += ':' + name
    system = (lock.get('system_tasks', {}).get(row.get('instance_id'))
              or lock.get('system_repositories', {}).get(row['repo']))
    if system:
        selected['system_packages'] = {**selected.get('system_packages', {}), python:
            sorted(set(selected.get('system_packages', {}).get(python, [])) | set(system))}
        identity += ':system-' + hashlib.sha256('\n'.join(system).encode()).hexdigest()[:12]
    if row.get('instance_id') in lock.get('compiled_checkouts', {}):
        if row['base_commit'] != lock['compiled_checkouts'][row['instance_id']]:
            raise ValueError('Compiled checkout commit mismatch')
        selected['_compiled'] = True
        identity += ':compiled-' + row['base_commit'][:12]
    direct_http = {
        'cuenca-mx/cuenca-python': 'api.cuenca.com,sandbox.cuenca.com',
        'edgi-govdata-archiving/wayback': 'web.archive.org',
        'conan-io/conan': 'localhost,127.0.0.1,::1',
    }
    if row['repo'] in direct_http:
        # Preserve recorded request hosts and access local test servers directly.
        selected['_no_proxy'] = direct_http[row['repo']]
        identity += ':recorded-http'
    if row.get('instance_id') == 'modin-project__modin-1842':
        # Ray 0.8 reads host resources rather than the task's cgroup limits.
        selected['_runtime_env'] = {'MODIN_CPUS': '4', 'MODIN_MEMORY': '536870912'}
        identity += ':bounded-ray'
    resource_fix = TASK_RESOURCE_FIXES.get(row.get('instance_id'), {})
    if resource_fix.get('runtime_env'):
        # Reviewed per-task runtime bounds for libraries that size themselves from the host.
        selected['_runtime_env'] = {**selected.get('_runtime_env', {}), **resource_fix['runtime_env']}
        identity += ':' + resource_fix['label']
    if resource_fix.get('no_proxy'):
        # Recorded or mocked hosts must keep their original URI through the verifier.
        hosts = [h for h in selected.get('_no_proxy', '').split(',') if h] + [resource_fix['no_proxy']]
        selected['_no_proxy'] = ','.join(hosts)
        identity += ':' + resource_fix['label']
    if resource_fix.get('runtime_unset'):
        selected['_runtime_unset'] = sorted(set(selected.get('_runtime_unset', [])) | set(resource_fix['runtime_unset']))
        identity += ':' + resource_fix['label']
    if row.get('instance_id') == 'conan-io__conan-5005':
        # This historical Conan requester copies HTTP proxies into explicit
        # request arguments, bypassing NO_PROXY for its local HTTP server.
        # HTTPS downloads used by setup retain their configured proxy.
        selected['_runtime_unset'] = ['HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy']
        identity += ':local-http'
    return selected, identity


def isolated_wheels(python, lock):
    return {**lock.get('isolated_wheels', {}),
            **lock.get('isolated_wheels_by_python', {}).get(python, {})}


def image_wheels(python, lock):
    pins = [] if lock.get('_omit_wheels') else wheel_cache().get(python, [])
    if '_wheel_subset' in lock:
        pins = [pin for pin in dict.fromkeys(lock['_wheel_subset'])
                if pin in pins or pin in isolated_wheels(python, lock)]
    return pins


def dockerfile(python, lock):
    specs = lock['environments'][python]
    explicit = explicit_conda(sorted(specs))
    channels = ''.join('-c ' + shlex.quote(channel) + ' '
                       for channel in lock.get('extra_channels', {}).get(python, []))
    apt = COMMON_PACKAGES | set(lock.get('system_packages', {}).get(python, []))
    commands = [
        'if ! dpkg-statoverride --list /usr/lib/x86_64-linux-gnu/utempter/utempter; then dpkg-statoverride --add root root 0755 /usr/lib/x86_64-linux-gnu/utempter/utempter; fi',
        'apt-get update && apt-get install -y --no-install-recommends ' + ' '.join(sorted(apt)) + ' && rm -rf /var/lib/apt/lists/*',
        '/opt/conda/bin/conda create -y -n testbed --solver libmamba --override-channels ' + channels + '-c defaults -c conda-forge ' + shlex.join(specs) + ' && /opt/conda/bin/conda clean -afy',
        '/opt/conda/bin/conda clean -afy && mkdir -p /testbed && ln -s /opt/conda /opt/miniconda3',
    ]
    # Install conda before apt; the upstream static check otherwise mistakes
    # conda version pins following an apt RUN for apt package pins.
    commands = [commands[2], commands[0], commands[1], commands[3]]
    if 'ghostscript' in apt:
        # fontconfig-config otherwise creates this directory and chowns it to
        # the unmapped staff group in a single-UID build namespace.
        commands[1] += ' && mkdir -p /usr/local/share/fonts && chmod 2775 /usr/local/share/fonts'
    if explicit:
        # The Apptainer fallback defers COPY until container startup, after
        # build RUN commands. Materialize the lock inside RUN instead.
        commands[0] = ("printf '%s\\n' " + shlex.join(explicit.splitlines())
                       + ' > /opt/base-conda.txt && /opt/conda/bin/conda create -y -n testbed '
                       '--file /opt/base-conda.txt && /opt/conda/bin/conda clean -afy '
                       '&& rm /opt/base-conda.txt')
    pins = image_wheels(python, lock)
    if pins:
        commands.append('mkdir -p /opt/swelego-wheels')
        isolated = isolated_wheels(python, lock)
        for batch in wheel_batches([pin for pin in pins if pin not in isolated]):
            commands.append('/opt/conda/envs/testbed/bin/python -m pip wheel '
                            '--no-deps --no-build-isolation --wheel-dir /opt/swelego-wheels '
                            + shlex.join(batch) + ' && rm -rf /root/.cache/pip')
        for pin in pins:
            if pin in isolated:
                commands.append("printf '%s\\n' " + shlex.join(isolated[pin])
                                + ' > /opt/wheel-build-constraints.txt && '
                                'PIP_CONSTRAINT=/opt/wheel-build-constraints.txt '
                                '/opt/conda/envs/testbed/bin/python -m pip wheel --no-deps '
                                '--wheel-dir /opt/swelego-wheels ' + shlex.quote(pin)
                                + ' && rm /opt/wheel-build-constraints.txt && rm -rf /root/.cache/pip')
    if lock.get('_pip_layer'):
        options = '--no-index --find-links /opt/swelego-wheels '
        if not set(lock['_pip_layer']).issubset(pins):
            options = '--no-build-isolation ' + ('--find-links /opt/swelego-wheels ' if pins else '')
        commands.append('/opt/conda/envs/testbed/bin/python -m pip install --no-deps '
                        + options + shlex.join(lock['_pip_layer']))
    runtime_env = ''
    if lock.get('_no_proxy'):
        domains = lock['_no_proxy']
        # VCR replays these recorded endpoints locally. Preserve their original
        # host names instead of routing them through an inherited HTTP proxy.
        lines = ['export ' + name + '="${' + name + ':+${' + name + '},}' + domains + '"'
                 for name in ('NO_PROXY', 'no_proxy')]
        commands.append("printf '%s\\n' " + shlex.join(lines)
                        + ' > /etc/profile.d/swelego-no-proxy.sh')
        runtime_env = 'ENV NO_PROXY=' + domains + ' no_proxy=' + domains + '\n'
    if lock.get('_runtime_env'):
        pairs = [name + '=' + shlex.quote(value)
                 for name, value in sorted(lock['_runtime_env'].items())]
        commands.append("printf '%s\\n' " + shlex.join(['export ' + pair for pair in pairs])
                        + ' > /etc/profile.d/swelego-runtime.sh')
        runtime_env += 'ENV ' + ' '.join(pairs) + '\n'
    if lock.get('_runtime_unset'):
        names = lock['_runtime_unset']
        commands.append("printf '%s\\n' " + shlex.quote('unset ' + ' '.join(names))
                        + ' > /etc/profile.d/swelego-direct-http.sh')
        runtime_env += 'ENV ' + ' '.join(name + '=""' for name in names) + '\n'
    return ('FROM ' + lock['base'] + '\n# swelego-shared-python-v1\nUSER root\n'
            + ''.join('RUN ' + c + '\n' for c in commands)
            + runtime_env
            + 'ENV PATH=/opt/conda/envs/testbed/bin:/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\nWORKDIR /testbed\n')


@lru_cache(maxsize=1)
def explicit_lock():
    document = json.loads((HERE / 'conda-explicit-lock.json').read_text())
    if document['source_revision'] != REVISION or document['unresolved']:
        raise ValueError('Invalid explicit conda lock')
    return document


def explicit_conda(specs):
    """Use complete recorded environments without loading/solving repodata."""
    document = explicit_lock()
    key = hashlib.sha256('\n'.join(specs).encode()).hexdigest()
    profile = document['profiles'].get(key)
    if profile is None:
        return None
    if profile['specs'] != specs:
        raise ValueError('Explicit conda profile mismatch')
    return '@EXPLICIT\n' + ''.join(document['packages'][spec]['url'] + '#'
                                   + document['packages'][spec]['md5'] + '\n'
                                   for spec in specs)


def task_setup(row, specs, lock):
    python, _ = environment(row)
    config = row['install_config']
    install = config.get('install') or 'true'
    data_manifest = lock.get('data_manifests', {}).get(row.get('instance_id'))
    if data_manifest:
        if row['base_commit'] != data_manifest['base_commit']:
            raise ValueError('Data manifest task commit mismatch')
        command = 'mykrobe panels update_metadata'
        if install.count(command) != 1:
            raise ValueError('Unexpected panel metadata installation recipe')
        install = install.replace(command, command + ' --filename /setup_files/data-manifest.json')
    if install in {'poetry install', 'poetry install --with dev,cli',
                   'poetry install --all-extras', 'poetry install --with test --with dev'}:
        # Frozen dependencies are already installed in the task environment.
        install = 'POETRY_VIRTUALENVS_CREATE=false poetry install --only-root'
        if row.get('instance_id') == 'Informasjonsforvaltning__fdk-fulltext-search-138':
            # Its old virtualenv imports distutils while Poetry starts. The
            # Python 3.8 standard implementation matches its frozen packaging.
            install = 'SETUPTOOLS_USE_DISTUTILS=stdlib ' + install
    if row.get('instance_id') == 'elastic__apm-agent-python-1510':
        # New setuptools calls a packaging API absent from this frozen runtime.
        # Build with a compatible pinned backend without changing runtime pins.
        install = ("printf '%s\\n' setuptools==70.3.0 wheel==0.45.1 > /setup_files/build-constraints.txt\n"
                   'PIP_CONSTRAINT=/setup_files/build-constraints.txt PIP_NO_BUILD_ISOLATION=1 '
                   + install)
    backend = build_backends().get(row.get('instance_id'), {})
    if backend.get('legacy_setup'):
        if not install.startswith('pip install -e .'):
            raise ValueError('Unexpected legacy editable installation recipe')
        install = 'python setup.py develop --no-deps'
    if backend.get('isolated_build_constraints'):
        if not install.startswith('pip install ') or '\n' in install:
            raise ValueError('Unsupported isolated backend installation recipe')
        # Build tools can require versions that conflict with the frozen runtime.
        # Allow dependency resolution only inside pip's isolated build environment;
        # the explicit CLI flag keeps local project runtime dependencies untouched.
        options = '--no-deps ' + ('--use-pep517 ' if backend.get('force_pep517') else '')
        install = ("printf '%s\\n' " + shlex.join(backend['isolated_build_constraints'])
                   + ' > /setup_files/build-constraints.txt\n'
                   'PIP_CONSTRAINT=/setup_files/build-constraints.txt PIP_NO_DEPS=0 '
                   'PIP_NO_BUILD_ISOLATION=1 '
                   + install.replace('pip install ', 'pip install ' + options, 1))
    env_vars = config.get('env_vars') or {}
    if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) for k in env_vars):
        raise ValueError('Invalid environment variable name')
    exports = ''.join('export ' + k + '=' + shlex.quote(v) + '\n' for k, v in sorted(env_vars.items()))
    delta = ''
    if not set(specs).issubset(lock['environments'][python]):
        import yaml
        recorded = yaml.safe_load(row['environment'].replace('\\n', '\n'))
        standard = {'defaults', 'conda-forge', 'https://repo.anaconda.com/pkgs/main',
                    'https://repo.anaconda.com/pkgs/r'}
        extra_channels = ''.join('-c ' + shlex.quote(channel) + ' '
                                 for channel in recorded.get('channels', [])
                                 if channel not in standard)
        delta = ('/opt/conda/bin/conda install -y -n testbed --solver libmamba --override-channels '
                 + extra_channels + '-c defaults -c conda-forge ' + shlex.join(specs) + '\n')
    if delta and explicit_conda(specs) is not None:
        delta = ('/opt/conda/bin/conda install -y -n testbed --file /setup_files/conda-explicit.txt\n'
                 '/opt/conda/bin/conda clean -afy\n')
    pre = []
    if row.get('instance_id') == 'skypilot-org__skypilot-4908':
        # Frozen sdists import pkg_resources, removed by newer isolated backends.
        # Pin only their isolated build backend; runtime setuptools stays frozen.
        if row['base_commit'] != '86e242f811bbab00fc896a3f6a6b3dd591eb6626':
            raise ValueError('Dependency build constraint commit mismatch')
        pre += [
            "printf '%s\\n' setuptools==75.8.0 > /setup_files/dependency-build-constraints.txt",
            'export PIP_CONSTRAINT=/setup_files/dependency-build-constraints.txt',
        ]
    if row.get('instance_id') in {'chaostoolkit__chaostoolkit-lib-53',
                                  'chaostoolkit__chaostoolkit-lib-70',
                                  'chaostoolkit__chaostoolkit-lib-89'}:
        # pyhcl imports ply while building its wheel, before pip installs the
        # rest of the requirements. Install the recorded ply pin first.
        ply = [line for line in dependencies(row, specs).splitlines()
               if line.startswith('ply==')]
        if len(ply) != 1:
            raise ValueError('Missing frozen ply build dependency')
        pre.append('python -m pip install --no-deps ' + shlex.quote(ply[0]))
    if row['repo'] == 'lightkurve/lightkurve':
        # Its conftest creates a child directory but assumes this parent exists.
        pre.append('mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}"')
    if row.get('instance_id') == 'matplotlib__matplotlib-18184':
        # Match setupext.py's pinned bundled FreeType. GNU tar keeps files owned
        # by the current build namespace instead of restoring archive UID 1000.
        pre += [
            'mkdir -p build',
            'curl -fsSL https://downloads.sourceforge.net/project/freetype/freetype2/2.6.1/freetype-2.6.1.tar.gz '
            '-o /setup_files/freetype-2.6.1.tar.gz',
            "printf '%s\\n' '0a3c7dfbda6da1e8fce29232e8e96d987ababbbf71ebc8c75659e4132c367014  "
            "/setup_files/freetype-2.6.1.tar.gz' | sha256sum -c -",
            'tar --no-same-owner -xzf /setup_files/freetype-2.6.1.tar.gz -C build',
            'chmod u+x build/freetype-2.6.1/configure build/freetype-2.6.1/builds/unix/configure',
        ]
    preinstall = config.get('pre_install') or []
    if row.get('instance_id') == 'rl-institut__smooth-167' and preinstall == OEMOF_PREINSTALL:
        preinstall = []  # Exact source is installed with frozen dependencies.
    for command in preinstall:
        words = shlex.split(command)
        if command.strip() == 'apt-get update':
            continue
        if words[:3] == ['apt-get', 'install', '-y']:
            baked = COMMON_PACKAGES | set(lock.get('system_packages', {}).get(python, []))
            extra = sorted(set(words[3:]) - baked)
            if extra:
                options = '--no-install-recommends ' if row.get('instance_id') in lock.get('no_recommends_tasks', []) else ''
                pre.append('apt-get update && apt-get install -y ' + options + shlex.join(extra))
        else:
            if row.get('instance_id') == '12rambau__sepal_ui-758' and words[:2] == ['pip', 'install']:
                # Its wheel index contains multiple GDAL releases. Preserve
                # the captured GDAL/localtileserver pins during pre-install.
                command = 'PIP_CONSTRAINT=/setup_files/requirements.txt ' + command
            pre.append(command)
    depth, version_tag = '1', ''
    if row['repo'] == 'beeware/briefcase':
        # Briefcase queries setuptools-scm at import time with warnings treated
        # as errors. Fetch the pinned commit's complete ancestry, without later
        # commits, so it has real history rather than a shallow-checkout warning.
        depth = '2147483647'
    history = lock.get('checkout_tags', {}).get(row.get('instance_id'))
    if history:
        if row['base_commit'] != history['base_commit']:
            raise ValueError('Historical version tag commit mismatch')
        depth = str(history['depth'])
        version_tag = ('git merge-base --is-ancestor ' + shlex.quote(history['tag_commit']) + ' HEAD\n'
                       'git tag -f ' + shlex.quote(history['tag']) + ' ' + shlex.quote(history['tag_commit']))
        if history.get('tag_object'):
            # git describe without --tags requires the original annotated tag.
            tag_ref = 'refs/tags/' + history['tag']
            tag_object = shlex.quote(history['tag_object'])
            fetch_depth = '' if history.get('complete_history') else '--depth 1 '
            version_tag += ('\ngit fetch ' + fetch_depth + 'origin ' + tag_object
                            + '\ngit update-ref ' + shlex.quote(tag_ref) + ' ' + tag_object
                            + '\ntest "$(git rev-parse ' + shlex.quote(tag_ref + '^{}')
                            + ')" = ' + shlex.quote(history['tag_commit']))
    if row.get('instance_id') == 'just-work__fffw-100':
        release = '660e60664812b377a9ac43c0a6b9bb807bc7e394'
        if row['base_commit'] != release:
            raise ValueError('Unexpected fffw release commit')
        version_tag = 'git tag -f 3.3.1 ' + release
    if row.get('instance_id') == 'robotpy__robotpy-cppheaderparser-42':
        release = 'dd564dda795c3b78fc4843a80a5906577533521d'
        if row['base_commit'] != release:
            raise ValueError('Unexpected robotpy release commit')
        version_tag = 'git tag -f 5.0.3 ' + release
    if row.get('instance_id') == 'openforcefield__openff-toolkit-2026':
        # The frozen version 0.16.8.post2+g459a7334 needs two ancestor commits
        # and its release tag. Fetch only that history, never later fixes.
        depth = '3'
        release = 'b7a97ebb8590750e7c5c82f5ce7b1f5ad2ebd6df'
        version_tag = ('git merge-base --is-ancestor ' + release + ' HEAD\n'
                       'git tag -f 0.16.8 ' + release)
    repair = environment_repair(row)
    if repair.get('remove_directory_metadata'):
        metadata = PurePosixPath(repair['remove_directory_metadata'])
        if metadata.is_absolute() or '..' in metadata.parts:
            raise ValueError('Unsafe malformed package metadata path')
        pre.append(
            'site_packages="$(python -c \'import sysconfig; print(sysconfig.get_paths()["purelib"])\')"\n'
            'metadata_path="$site_packages/' + str(metadata) + '"\n'
            'if [ -d "$metadata_path" ] && [ ! -L "$metadata_path" ]; then '
            'rm -rf -- "$metadata_path"; fi')
    if repair.get('full_history'):
        depth = '2147483647'
    if repair.get('leap_second_table'):
        install += ('\npython -c ' + shlex.quote(
            'import astropy_iers_data, shutil; '
            'shutil.copyfile("/setup_files/Leap_Second.dat", astropy_iers_data.IERS_LEAP_SECOND_FILE)'))
    if repair.get('branch'):
        version_tag += '\ngit checkout -B ' + shlex.quote(repair['branch']) + ' ' + shlex.quote(row['base_commit'])
    identity = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    cache_pins = set(image_wheels(python, lock))
    pip_cache = ''
    if any(line.startswith('torch==') and '+cpu' in line
           for line in dependencies(row, specs).splitlines()):
        # The recorded CPU build is published on PyTorch's own wheel index.
        pip_cache = ' --extra-index-url https://download.pytorch.org/whl/cpu'
    if cache_pins:
        pip_cache += ' --find-links /opt/swelego-wheels'
        if set(dependencies(row, specs).splitlines()).issubset(cache_pins):
            pip_cache += ' --no-index'
    template = (HERE / 'setup.sh').read_text()
    prepared = ''
    if lock.get('_compiled'):
        prepared = (f'if [ -f /opt/swelego-ready/{identity} ]; then\n'
                    '    cached_started=$SECONDS\n    mkdir -p /testbed\n'
                    f'    tar -xzf /opt/swelego-ready/{identity}.tar.gz -C /testbed\n'
                    '    test "$(git -C /testbed rev-parse HEAD)" = ' + shlex.quote(row['base_commit']) + '\n'
                    '    printf \'{"seconds":%s,"exit_code":0,"phases":{"prepared_checkout":%s}}\\n\' '
                    '"$((SECONDS-cached_started))" "$((SECONDS-cached_started))" > /setup_files/setup-timing.json\n'
                    '    touch "$marker"\n    exit 0\nfi\n')
    return (template.replace('@IDENTITY@', identity)
            .replace('@PREPARED@\n', prepared)
            .replace('@REPO@', shlex.quote(SOURCE_MIRRORS.get(
                row['repo'], 'https://github.com/' + row['repo'] + '.git')))
            .replace('@COMMIT@', shlex.quote(row['base_commit']))
            .replace('@DEPTH@', depth)
            .replace('@VERSION_TAG@\n', version_tag + '\n' if version_tag else '')
            .replace('@EXPORTS@', exports).replace('@CONDA@', delta)
            .replace('@PIP_CACHE@', pip_cache)
            .replace('@PREINSTALL@', '\n'.join(pre))
            .replace('@INSTALL@', install))


def patch_files(files, row, lock):
    if lock['source_revision'] != REVISION:
        raise ValueError('Wrong base environment source revision')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', row['repo']):
        raise ValueError('Invalid repository')
    if not re.fullmatch(r'[a-f0-9]{40}', row['base_commit']):
        raise ValueError('Invalid base commit')
    if 'setup_files/swelego.json' in files:
        raise ValueError('Patch the immutable source, not an already converted task')
    expected = f"FROM {row['image_name']}\nWORKDIR /testbed\n".encode()
    if files['environment/Dockerfile'] != expected:
        raise ValueError('Task image and upstream metadata do not match')
    if any(k.startswith('setup_files/') for k in files):
        raise ValueError('Existing setup requires review')
    if ('base=' + row['base_commit'] + '\n').encode() not in files['tests/test.sh']:
        raise ValueError('Task base commit and upstream metadata do not match')
    python, specs = environment(row)
    lock, image_identity = select_image(row, lock)
    updated = dict(files)
    resource_fix = TASK_RESOURCE_FIXES.get(row.get('instance_id'))
    if resource_fix and 'memory_mb' in resource_fix:
        if 'task.toml' not in updated:
            raise ValueError('Task resource fix requires task.toml')
        task_toml = updated['task.toml'].decode()
        memory_pattern = re.compile(r'(?m)^(memory_mb\s*=\s*)\d+(\s*(?:#.*)?)$')
        task_toml, replacements = memory_pattern.subn(
            lambda match: match.group(1) + str(resource_fix['memory_mb']) + match.group(2), task_toml)
        if replacements != 1:
            raise ValueError('Task resource fix requires exactly one memory_mb in task.toml')
        updated['task.toml'] = task_toml.encode()
    updated['environment/Dockerfile'] = dockerfile(python, lock).encode()
    base_explicit = explicit_conda(sorted(lock['environments'][python]))
    if base_explicit:
        updated['environment/base-conda.txt'] = base_explicit.encode()
    updated['setup_files/requirements.txt'] = dependencies(row, specs).encode()
    explicit = explicit_conda(specs)
    if explicit is not None:
        updated['setup_files/conda-explicit.txt'] = explicit.encode()
    updated['setup_files/setup.sh'] = task_setup(row, specs, lock).encode()
    if environment_repair(row).get('leap_second_table'):
        table = (HERE / 'Leap_Second.dat').read_bytes()
        if hashlib.sha256(table).hexdigest() != environment_repair(row)['leap_second_table']['sha256']:
            raise ValueError('Leap-second table checksum mismatch')
        updated['setup_files/Leap_Second.dat'] = table
    if lock.get('_compiled'):
        build_files = {k: v for k, v in updated.items() if k.startswith('setup_files/')}
        commands = ['mkdir -p /setup_files']
        for name, content in sorted(build_files.items()):
            commands.append("printf '%s' " + shlex.quote(base64.b64encode(content).decode())
                            + ' | base64 -d > /' + name)
        identity = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
        commands += ['bash /setup_files/setup.sh', 'mkdir -p /opt/swelego-ready',
                     'tar -czf /opt/swelego-ready/' + identity + '.tar.gz -C /testbed .',
                     'touch /opt/swelego-ready/' + identity,
                     'find /testbed -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +',
                     'rm -rf /setup_files']
        updated['environment/Dockerfile'] += ''.join('RUN ' + c + '\n' for c in commands).encode()
    data_manifest = lock.get('data_manifests', {}).get(row.get('instance_id'))
    if data_manifest:
        updated['setup_files/data-manifest.json'] = json.dumps(data_manifest['manifest'], indent=2).encode()
    updated['setup_files/swelego.json'] = json.dumps({
        'version': MARKER, 'revision': REVISION, 'repo': row['repo'],
        'base_commit': row['base_commit'], 'original_image': row['image_name'],
        **({'environment_repair': environment_repair(row)} if environment_repair(row) else {}),
        **({'checkout_mirror': SOURCE_MIRRORS[row['repo']]} if row['repo'] in SOURCE_MIRRORS else {}),
        'python': python, 'conda_specs': specs, 'explicit_conda': explicit is not None,
        'image_profile': image_identity, 'compiled_checkout': bool(lock.get('_compiled')),
        'conda_adjustment': not set(specs).issubset(lock['environments'][python]),
        'local_resolutions': [{'requirement': line, 'action': resolution[0],
                               'replacement': resolution[1]}
                              for line in row['requirements'].splitlines()
                              if ' @ file://' in line
                              if (resolution := local_resolution(row, line)) is not None],
        **({'archive_resolution': {'original': AMQP_ARCHIVE_OLD, 'replacement': AMQP_ARCHIVE_PINNED,
                                   'same_sha256': True}}
           if AMQP_ARCHIVE_OLD in row['requirements'] else {}),
        **({'vine_archive_resolution': {'original': VINE_ARCHIVE_OLD, 'replacement': VINE_ARCHIVE_PINNED,
                                        'same_sha256': True}}
           if VINE_ARCHIVE_OLD in row['requirements'] else {}),
        **({'self_requirement': SELF_REQUIREMENTS[row['instance_id']][1]}
           if row.get('instance_id') in SELF_REQUIREMENTS else {}),
        **({'requirement_source': {'original': REQUIREMENT_SOURCES[row['instance_id']][1],
                                    'replacement': REQUIREMENT_SOURCES[row['instance_id']][2]}}
           if row.get('instance_id') in REQUIREMENT_SOURCES else {}),
        **({'additional_requirement_source': {
            'original': ADDITIONAL_REQUIREMENT_SOURCES[row['instance_id']][1],
            'replacement': ADDITIONAL_REQUIREMENT_SOURCES[row['instance_id']][2]}}
           if row.get('instance_id') in ADDITIONAL_REQUIREMENT_SOURCES else {}),
        **({'data_manifest_source': data_manifest['source']} if data_manifest else {}),
    }, indent=2).encode()
    solve = files['solution/solve.sh'].decode()
    if not solve.startswith('#!/bin/bash\nset -e\n'):
        raise ValueError('Unknown solution entrypoint')
    updated['solution/solve.sh'] = solve.replace('set -e\n', 'set -e\nbash /setup_files/setup.sh\n', 1).encode()
    test = files['tests/test.sh'].decode()
    test = test.replace('mkdir -p /logs/verifier\n', 'mkdir -p /logs/verifier\n'
                        'bash /setup_files/setup.sh || exit $?\n'
                        'cp /setup_files/setup-timing.json /logs/verifier/setup-timing.json\n', 1)
    if row.get('instance_id') == 'F5Networks__f5-common-python-967':
        entry = 'bash /tests/eval.sh | tee /logs/verifier/test-output.txt'
        if test.count(entry) != 1:
            raise ValueError('Unknown F5 verifier entrypoint')
        updated['tests/f5_test_imports.py'] = (HERE / 'f5_test_imports.py').read_bytes()
        test = test.replace(entry, 'python /tests/f5_test_imports.py || exit $?\n' + entry)
    verifier_fix = VERIFIER_FIXES.get(row.get('instance_id'))
    if verifier_fix:
        if row['base_commit'] != verifier_fix['commit']:
            raise ValueError('Verifier fix task commit mismatch')
        entry = 'bash /tests/eval.sh | tee /logs/verifier/test-output.txt'
        if test.count(entry) != 1:
            raise ValueError('Unknown verifier fix entrypoint')
        if row['instance_id'] == 'msgpack__msgpack-python-388':
            test = test.replace(entry, 'MSGPACK_PUREPYTHON=1 ' + entry)
        elif verifier_fix.get('unset_env'):
            test = test.replace(entry, 'env ' + ' '.join('-u ' + name for name in verifier_fix['unset_env']) + ' ' + entry)
        elif row['instance_id'] == 'mtgjson__mtgjson-469':
            evaluate = files['tests/eval.sh'].decode()
            if evaluate.count(' PYTHONPATH=. pytest ') != 1:
                raise ValueError('Unknown mtgjson verifier command')
            updated['tests/eval.sh'] = evaluate.replace(
                ' PYTHONPATH=. pytest ', ' PYTHONPATH=.${PYTHONPATH:+:$PYTHONPATH} pytest ').encode()
        else:
            updated['tests/swelego_sdss_clock.py'] = (HERE / 'sdss_verifier_clock.py').read_bytes()
            test = test.replace(entry,
                                'export PYTHONPATH=/tests${PYTHONPATH:+:$PYTHONPATH}\n'
                                'export PYTEST_PLUGINS=swelego_sdss_clock${PYTEST_PLUGINS:+,$PYTEST_PLUGINS}\n'
                                + entry)
    repair = environment_repair(row)
    if repair.get('verifier_script'):
        script = repair['verifier_script']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.py', script):
            raise ValueError('Invalid verifier repair script name')
        entry = 'bash /tests/eval.sh | tee /logs/verifier/test-output.txt'
        if test.count(entry) != 1:
            raise ValueError('Unknown verifier script repair entrypoint')
        updated['tests/' + script] = (HERE / script).read_bytes()
        test = test.replace(entry, 'python /tests/' + script
                            + ' /testbed/tests/test_affinity.py || exit $?\n' + entry)
    if repair.get('pytest_workers'):
        entry = ' pytest '
        evaluate = files['tests/eval.sh'].decode()
        if evaluate.count(entry) != 1:
            raise ValueError('Unknown parallel verifier command')
        # An explicit -n on the command line overrides the repository's addopts.
        evaluate = evaluate.replace(entry, ' pytest -n ' + str(repair['pytest_workers']) + ' ')
        updated['tests/eval.sh'] = evaluate.encode()
        test = test.replace('bash /tests/eval.sh | tee',
                            'OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 '
                            'NUMEXPR_NUM_THREADS=1 bash /tests/eval.sh | tee')
    if repair.get('pytest_plugin'):
        plugin = repair['pytest_plugin']
        entry = 'bash /tests/eval.sh | tee /logs/verifier/test-output.txt'
        if test.count(entry) != 1:
            raise ValueError('Unknown environment repair verifier entrypoint')
        updated['tests/' + plugin + '.py'] = (HERE / (plugin + '.py')).read_bytes()
        test = test.replace(entry,
                            'export PYTHONPATH=/tests${PYTHONPATH:+:$PYTHONPATH}\n'
                            'export PYTEST_PLUGINS=' + plugin + '${PYTEST_PLUGINS:+,$PYTEST_PLUGINS}\n' + entry)
    updated['tests/test.sh'] = test.encode()
    updated['instruction.md'] = files['instruction.md'].rstrip() + (
        '\n\nThe repository is at `/testbed`. Before working, run `bash /setup_files/setup.sh`. '
        'It initializes the task once; later calls preserve your changes.\n').encode()
    return updated


def patch_blob(blob, row, lock):
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        files = {}
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe task archive member')
            if member.isfile():
                if str(name) in files:
                    raise ValueError('Duplicate task archive member')
                files[str(name)] = archive.extractfile(member).read()
    updated = patch_files(files, row, lock)
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w') as archive:
        for name, data in sorted(updated.items()):
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if name.endswith('.sh') else 0o644
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return result.getvalue()


def main():
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.utils.patch_reporting import write_patch_report
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, help='Pinned upstream recipe JSON; defaults to recipes.json beside source')
    args = parser.parse_args()
    metadata = args.metadata or (args.source.parent if args.source.is_file() else args.source) / 'recipes.json'
    recipes = load_recipes(metadata, args.output.parent / 'swelego-upstream-cache')
    lock = json.loads((HERE / 'base-environments.json').read_text())
    sources = [args.source] if args.source.is_file() else sorted(args.source.glob('*.parquet'))
    if not sources:
        raise ValueError('No source Parquets')
    args.output.mkdir(parents=True, exist_ok=False)
    for source in sources:
        table = pq.read_table(source)
        original_rows = table.to_pylist()
        dropped = {item['path']: ARCHIVED_TASKS[item['path']] for item in original_rows
                   if item['path'] in ARCHIVED_TASKS}
        rows = [item for item in original_rows if item['path'] not in dropped]
        labels, images, unresolved, reasons = {}, set(), {}, {}
        for item in rows:
            recipe = recipes[item['path']]
            if recipe['instance_id'] != item['path']:
                raise ValueError('Recipe task ID mismatch')
            try:
                item['task_binary'] = patch_blob(item['task_binary'], recipe, lock)
            except ValueError as exc:
                if not str(exc).startswith('Local requirement has no frozen conda package:'):
                    raise
                unresolved[item['path']] = str(exc)
                images.add(recipe['image_name'])
                continue
            selected, identity = select_image(recipe, lock)
            images.add(identity)
            labels[item['path']] = ['shared-python-image', 'pinned-dependencies-in-setup', 'setup-timing']
            reasons[item['path']] = (
                'Uses image profile ' + identity + ' with the pinned Python and dependency versions. '
                'Task setup restores the pinned source checkout and remaining dependencies, records phase timings, '
                'and preserves task IDs, grading inputs and reference patches.')
            if selected.get('_pip_layer') or ':conda-' in identity:
                reasons[item['path']] += ' Measured expensive dependency installation runs during the image build.'
            if selected.get('_compiled'):
                reasons[item['path']] += ' The image precompiles only the pinned base checkout, which setup restores into a fresh workspace.'
            if item['path'] in REQUIREMENT_SOURCES:
                reasons[item['path']] += ' Restores unavailable frozen dependency artifacts from pinned upstream sources; setup_files/swelego.json records each original requirement and replacement.'
            if item['path'] == 'pgmpy__pgmpy-1905':
                reasons[item['path']] += ' Daft 0.4.9 uses its official getdaft distribution alias, built from the same release with only the distribution name changed; the imported module remains daft.'
            if item['path'] == 'F5Networks__f5-common-python-967':
                labels[item['path']].append('verifier-call-time-imports')
                reasons[item['path']] += ' Defer the reviewed Import_Policy test import to consuming functions so the unpatched task executes tests instead of aborting collection; preserve test selection, assertions, required results and reference patch.'
            if item['path'] in VERIFIER_FIXES:
                labels[item['path']].append(VERIFIER_FIXES[item['path']]['label'])
                reasons[item['path']] += ' ' + VERIFIER_FIXES[item['path']]['reason']
            if item['path'] in TASK_RESOURCE_FIXES:
                fix = TASK_RESOURCE_FIXES[item['path']]
                labels[item['path']].append(fix['label'])
                reasons[item['path']] += ' ' + fix['reason']
            if item['path'] in environment_repairs():
                repair = environment_repairs()[item['path']]
                labels[item['path']].append(repair['label'])
                labels[item['path']] += repair.get('additional_labels', [])
                reasons[item['path']] += ' ' + repair['reason']
            if item['path'] in build_backends() or item['path'] == 'encode__starlette-1715':
                labels[item['path']].append('pinned-build-backend')
                reasons[item['path']] += ' Restores a declared build backend or plugin omitted from the runtime dependency freeze.'
        output = args.output / source.name
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), output)
        write_patch_report(source, output, patcher=__file__, source={
            'dataset': 'PrimeIntellect/SWE-Lego-Real-Data-Verified', 'revision': REVISION,
            'url': 'https://huggingface.co/datasets/PrimeIntellect/SWE-Lego-Real-Data-Verified/tree/' + REVISION},
            dropped=dropped, change_labels=labels, change_reasons=reasons, patches=[{
                'version': MARKER, 'images': sorted(images), 'unconverted': unresolved,
                'metadata_sha256': hashlib.sha256(metadata.read_bytes()).hexdigest(),
                'base_environments_sha256': hashlib.sha256((HERE / 'base-environments.json').read_bytes()).hexdigest(),
                'conda_explicit_lock_sha256': hashlib.sha256((HERE / 'conda-explicit-lock.json').read_bytes()).hexdigest(),
                'wheel_cache_sha256': hashlib.sha256((HERE / 'wheel-cache.json').read_bytes()).hexdigest(),
                'build_backends_sha256': hashlib.sha256((HERE / 'build-backends.json').read_bytes()).hexdigest(),
                'setup_sha256': hashlib.sha256((HERE / 'setup.sh').read_bytes()).hexdigest(),
                'environment_repairs_sha256': hashlib.sha256((HERE / 'environment-repairs.json').read_bytes()).hexdigest(),
                'sympy_pytest_sha256': hashlib.sha256((HERE / 'swelego_sympy_pytest.py').read_bytes()).hexdigest(),
                'sqlglot_workers_sha256': hashlib.sha256((HERE / 'swelego_sqlglot_workers.py').read_bytes()).hexdigest(),
                'rope_workers_sha256': hashlib.sha256((HERE / 'swelego_rope_workers.py').read_bytes()).hexdigest(),
                'rpyc_affinity_sha256': hashlib.sha256((HERE / 'swelego_rpyc_affinity.py').read_bytes()).hexdigest(),
                'pytrakt_clock_sha256': hashlib.sha256((HERE / 'swelego_pytrakt_clock.py').read_bytes()).hexdigest(),
                'sdss_verifier_clock_sha256': hashlib.sha256((HERE / 'sdss_verifier_clock.py').read_bytes()).hexdigest()}])
        (args.output / (source.stem + '.images.json')).write_text(json.dumps({
            'tasks': len(rows), 'archived': len(dropped), 'converted': len(labels), 'unique_images': len(images),
            'unconverted': unresolved}, indent=2) + '\n')
        for name in ('base-environments.json', 'conda-explicit-lock.json', 'wheel-cache.json', 'build-backends.json', 'setup.sh', 'f5_test_imports.py', 'sdss_verifier_clock.py', 'swelego_pytrakt_clock.py', 'environment-repairs.json', 'swelego_sympy_pytest.py', 'swelego_sqlglot_workers.py', 'swelego_rope_workers.py', 'swelego_rpyc_affinity.py', 'Leap_Second.dat'):
            (args.output / name).write_bytes((HERE / name).read_bytes())
        print(f'{output}: {len(rows)} tasks, {len(images)} images, {len(unresolved)} explicitly unconverted, {len(dropped)} archived')


if __name__ == '__main__':
    main()
