"""SWE-Lego changes learned from reference/no-op runs and rule review.

Build reconstruction remains in patch.py. These repairs are enabled by default;
patch.py --no-repairs builds the audit baseline without them. Historical evidence:
git aff637d:data/swelego/pilot-results/swelego-environment-repairs-20261009.json.
"""
from functools import lru_cache
import json
from pathlib import Path
import re
import shlex

HERE = Path(__file__).resolve().parent / 'assets'
REVISION = '8b0d2bed6f04ef571ca04abebf737ea23cbceaf3'

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
        'memory_mb': 16384,
        'label': 'task-memory-16gib',
        'reason': 'Raise the task memory limit from 4 GiB to 16 GiB. With a 12 GiB limit the unchanged reference verifier passed (reward 1) in job 960179 and recorded 7.82 GiB MaxRSS even with its two bounded worker processes; 8 GiB would leave too little margin, and 16 GiB is the next size the static resource check allows (1, 2, 4, 8, 16, 32 GiB; a 10 GiB limit failed that check in job 965822). At 4 GiB the verifier was killed before scoring.'},
    'microsoft__electionguard-python-381': {
        'memory_mb': 8192,
        'label': 'task-memory-8gib',
        'reason': 'Raise the task memory limit from 4 GiB to 8 GiB. The big-integer key-ceremony and decryption tests passed reference in four of five 4 GiB attempts and were killed by the task memory limit in the fifth (job 959908); the 12 GiB rerun in job 960179 passed with 3.06 GiB MaxRSS. 8 GiB removes the limit-dependent flakiness without changing tests or dependencies.'},
    'zarr-developers__zarr-python-2784': {
        'memory_mb': 16384,
        'label': 'task-memory-16gib',
        'reason': 'Raise the task memory limit from 4 GiB to 16 GiB. With a 12 GiB limit the verifier ran to completion in job 964135 and its reference and no-op steps recorded 8.11 GiB and 8.09 GiB MaxRSS, above 8 GiB; 16 GiB is the next size the static resource check allows (1, 2, 4, 8, 16, 32 GiB; a 10 GiB limit failed that check in job 965822). At 4 GiB it was killed before scoring. Tests and dependency versions are unchanged.'},
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
        'reason': 'The reference run crashes inside respx/mock.py:229 with AttributeError: str object has no attribute attachment for every HTTPX mock test (job 959554, batch v66-1). The pinned httpx no longer passes the pattern objects this historical respx expects, so the recorded dependency set cannot execute the suite. Archived with the original payload preserved; see https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-17-diagnosis-v68.json.'},
    'lundberg__respx-21': {
        'category': 'incompatible-verifier-dependency',
        'reason': 'The same str-versus-pattern AttributeError as respx-13 occurs at respx/mock.py:273 together with unmatched-mock assertions (job 959554, batch v66-1); the pinned httpx is incompatible with this historical respx. Archived with the original payload preserved; see https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-17-diagnosis-v68.json.'},
    'mdsol__rwslib-111': {
        'category': 'incompatible-verifier-dependency',
        'reason': 'Seventeen tests fail because the pinned HTTPretty exposes no HTTPrettyRequest.headers attribute under Python 3.7 while 24 pass (job 959554, batch v66-1), the same HTTPretty incompatibility that archived PyPeri-8. Archived with the original payload preserved; see https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-17-diagnosis-v68.json.'},
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
        'reason': 'Archived because the task does not work with the shared base image we chose: the historical parser reports syntax errors in the glibc 2.36 sys/cdefs.h of the Debian 12 image and its version test exits 2 (jobs 959554 and batch v66-0). It could be repaired with a separate older base image, which would mean one extra image for a single task. Archived with the original payload preserved; see https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-17-diagnosis-v68.json.'},
    'richardkiss__pycoin-353': {
        'category': 'unreliable-external-service-dependency',
        'reason': 'Reference and no-op verification exceeded 1800 seconds. A diagnostic rerun traced test_tx_create_dump_sign_txt to an unbounded blockchain.info socket connection; the CLI test helper removes proxy settings. Archived at user request, with original payload preserved. Evidence: https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-correctness-investigation-20261008.json.'},
    'fatiando__pooch-291': {
        'category': 'unreliable-external-service-dependency',
        'reason': 'Required Zenodo download tests intermittently fail resolving DOI 10.5281/zenodo.4924875. The same payload also passed previously; caching files alone would bypass neither the fresh-directory downloads nor their assertions. Archived at user request instead of adding response replay, with original payload preserved. Evidence: https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-correctness-investigation-20261008.json.'},
    'takeontom__PyPeri-29': {
        'category': 'unreliable-live-service-dependency',
        'reason': 'Reference verification calls the live Periscope service for API, broadcast, and user data; these requests failed TLS verification in the cluster and depend on an external service remaining available. Archived because live-service verification is unreliable here. Original task payload preserved. Evidence: https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-stage4-remaining-20261009.json.'},
    'takeontom__PyPeri-35': {
        'category': 'unreliable-live-service-dependency',
        'reason': 'Reference verification calls the live Periscope service for API, broadcast, user, and session-token data; these requests failed TLS verification in the cluster and depend on an external service remaining available. Archived because live-service verification is unreliable here. Original task payload preserved. Evidence: https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-stage4-remaining-20261009.json.'},
    'kivy__kivy-6954': {
        'category': 'unreliable-live-service-dependency',
        'reason': 'Verifier tests issue real requests to google.com and httpbin.org to check callbacks, authentication, and TLS behavior. Those external endpoints failed to complete in the cluster, so reference scores depend on live service and network availability. Archived as unreliable; original task payload preserved. Evidence: https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-stage4-remaining-20261009.json.'},
    'sphinx-doc__sphinx-5203': {
        'category': 'unreliable-live-service-dependency',
        'reason': 'The linkcheck verifier fetches https://www.w3.org/TR/2006/REC-xml-names-20060816/#defaulting and asserts the remote anchor exists. The current W3C response no longer contains that anchor, so results depend on mutable live site content. Archived as unreliable; original task payload preserved. Evidence: https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-stage4-remaining-20261009.json.'},
    'cisagov__check-cve-2019-19781-10': {
        'category': 'unreliable-live-service-dependency',
        'reason': 'The valid timeout and retry verifier cases call github.com directly rather than using a local HTTP fixture. Their outcome depends on live DNS/network/service behavior, so verification is unreliable on our cluster. Archived as unreliable; original task payload preserved. Evidence: https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-stage4-remaining-20261009.json.'},
}

ARCHIVED_TASKS.update({'Azure__azure-cli-3409': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1030': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1040': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1095': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1211': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1229': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1269': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1308': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1404': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1415': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1417': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'burnash__gspread-1498': {'category': 'proxy-sensitive-verification',
                           'reason': 'Inherited cluster proxy changes the HTTP request host and prevents '
                                     'matching the recorded HTTP responses used by verification. Archived at '
                                     'user request because verification is unreliable under our proxy '
                                     'environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'cdent__gabbi-186': {'category': 'proxy-sensitive-verification',
                      'reason': 'HTTP interception tests reject inherited proxy environment variables; '
                                'wsgi-intercept explicitly requires them to be unset. Archived at user '
                                'request because verification is unreliable under our proxy environment. '
                                'This does not establish that the test requires live external network '
                                'access. Original task payload preserved. Evidence: '
                                'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'cdent__gabbi-191': {'category': 'proxy-sensitive-verification',
                      'reason': 'HTTP interception tests reject inherited proxy environment variables; '
                                'wsgi-intercept explicitly requires them to be unset. Archived at user '
                                'request because verification is unreliable under our proxy environment. '
                                'This does not establish that the test requires live external network '
                                'access. Original task payload preserved. Evidence: '
                                'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'cdent__wsgi-intercept-47': {'category': 'proxy-sensitive-verification',
                              'reason': 'HTTP interception tests reject inherited proxy environment '
                                        'variables; wsgi-intercept explicitly requires them to be unset. '
                                        'Archived at user request because verification is unreliable under '
                                        'our proxy environment. This does not establish that the test '
                                        'requires live external network access. Original task payload '
                                        'preserved. Evidence: '
                                        'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'desihub__desitransfer-5': {'category': 'proxy-sensitive-verification',
                             'reason': 'Case-insensitive environment parsing fails because inherited '
                                       'NO_PROXY/no_proxy settings produce duplicate configuration keys. '
                                       'Archived at user request because verification is unreliable under '
                                       'our proxy environment. This does not establish that the test '
                                       'requires live external network access. Original task payload '
                                       'preserved. Evidence: '
                                       'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'getsentry__sentry-python-2009': {'category': 'proxy-sensitive-verification',
                                   'reason': 'Proxy configuration assertions observe the inherited cluster '
                                             'proxy instead of the test-controlled configuration. Archived '
                                             'at user request because verification is unreliable under our '
                                             'proxy environment. This does not establish that the test '
                                             'requires live external network access. Original task payload '
                                             'preserved. Evidence: '
                                             'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'getsentry__sentry-python-3351': {'category': 'proxy-sensitive-verification',
                                   'reason': 'Proxy configuration assertions observe the inherited cluster '
                                             'proxy instead of the test-controlled configuration. Archived '
                                             'at user request because verification is unreliable under our '
                                             'proxy environment. This does not establish that the test '
                                             'requires live external network access. Original task payload '
                                             'preserved. Evidence: '
                                             'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'lundberg__respx-60': {'category': 'proxy-sensitive-verification',
                        'reason': 'Verification reaches the inherited HTTP proxy and receives HTTP 503 '
                                  'instead of the expected mocked transport response. Archived at user '
                                  'request because verification is unreliable under our proxy environment. '
                                  'This does not establish that the test requires live external network '
                                  'access. Original task payload preserved. Evidence: '
                                  'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'pdm-project__pdm-3255': {'category': 'proxy-sensitive-verification',
                           'reason': 'The inherited cluster proxy overrides the localhost proxy selected by '
                                     'the test. Archived at user request because verification is unreliable '
                                     'under our proxy environment. This does not establish that the test '
                                     'requires live external network access. Original task payload '
                                     'preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'radiasoft__pykern-107': {'category': 'proxy-sensitive-verification',
                           'reason': 'Case-insensitive environment parsing fails because inherited '
                                     'NO_PROXY/no_proxy settings produce duplicate configuration keys. '
                                     'Archived at user request because verification is unreliable under our '
                                     'proxy environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                                     'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'},
 'radiasoft__pykern-259': {'category': 'proxy-sensitive-verification',
                           'reason': 'Case-insensitive environment parsing fails because inherited '
                                     'NO_PROXY/no_proxy settings produce duplicate configuration keys. '
                                     'Archived at user request because verification is unreliable under our '
                                     'proxy environment. This does not establish that the test requires live '
                                     'external network access. Original task payload preserved. Evidence: '
                            'https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-failure-triage-20261009.json.'}})

ARCHIVED_TASKS.update({
    'Yelp__bravado-410': {
        'category': 'proxy-sensitive-verification',
        'reason': 'The verifier uses local HTTP mocks, but the fresh reference rerun received HTTP 503 responses from the inherited cluster proxy for the HTTP-client tests, including test_real_post. Reference reward remained 0.0; the no-op also scored 0.0 as expected. Archived because the proxy prevents reliable mock verification in this environment, not because the task intentionally requires a live service. Original task payload preserved. Evidence: https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-stage4-remaining-20261009.json and the reference42-v66-0 stage 4 report.'},
    'takeontom__PyPeri-8': {
        'category': 'incompatible-verifier-dependency',
        'reason': 'The verifier uses HTTP mocks, but its pinned HTTPretty 1.1.4 test adapter raises AttributeError because HTTPrettyRequest has no headers attribute in the pinned Python environment. The reference scored 0.0 while the no-op scored 0.0, so the required reference/no-op distinction is not established. Archived as an incompatible verifier dependency; this is not classified as a live-service failure. A fresh Stage 4/5 retry already in progress is retained as diagnostic evidence only. Original task payload preserved. Evidence: https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-stage4-remaining-20261009.json and the reference42-v66-2 stage 4 report.'},
    'PyPSA__linopy-77': {
        'category': 'unsupported-expired-proprietary-license',
        'reason': 'The reference verifier exercises Xpress solver cases, but the available Xpress license expired on 2026-02-28. The required proprietary solver cases cannot be validated on our infrastructure without a renewed license. Archived with the original task payload preserved. Evidence: https://github.com/MLI-lab/OT-next-data/blob/33c7a1648a5eca45310eb4515f11e14f363fabb2/data/swelego/pilot-results/swelego-reference-stage4-remaining-20261009.json.'},
    'airbrake__pybrake-77': {
        'category': 'unreliable-live-service-verification',
        'reason': 'The verifier calls the default Airbrake endpoint and an intentionally invalid external host without HTTP mocks. The unauthorized test received a non-JSON response and raised JSONDecodeError; the invalid-host test also failed instead of observing the expected DNS URLError. The reference scored 0.0 and no-op scored 0.0. These tests depend on live network/proxy behavior, so archive the original payload as unreliable on our cluster.'},
})


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


def resource_config(updated, row):
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
    return updated


def verifier_files(updated, files, row):
    test = files['tests/test.sh'].decode()
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
    return updated


def image_options(selected, identity, row):
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


def dependency_pins(lines, row):
    repair = environment_repair(row)
    for old, new in repair.get('runtime_replacements', {}).items():
        if lines.count(old) != 1:
            raise ValueError('Runtime repair requires exactly one original pin: ' + old)
        lines[lines.index(old)] = new
    lines += repair.get('test_dependencies', [])
    return lines


def setup_parts(repair, row, depth, install, version_tag):
    if repair.get('full_history'):
        depth = '2147483647'
    if repair.get('leap_second_table'):
        install += ('\npython -c ' + shlex.quote(
            'import astropy_iers_data, shutil; '
            'shutil.copyfile("/setup_files/Leap_Second.dat", astropy_iers_data.IERS_LEAP_SECOND_FILE)'))
    if repair.get('branch'):
        version_tag += '\ngit checkout -B ' + shlex.quote(repair['branch']) + ' ' + shlex.quote(row['base_commit'])
    return depth, install, version_tag


# These additions followed reference collection failures, after the original
# build passed. Other build-backend pins (for example Cython) remain in patch.py.
REVIEWED_BACKEND_ADDITIONS = {
    'vyperlang__vyper-3384': {'setuptools-scm==7.1.0'},
    'vyperlang__vyper-4253': {'setuptools-scm==7.1.0'},
    'pimutils__khal-1305': {'setuptools-scm==8.2.0'},
    'statsmodels__statsmodels-8339': {'setuptools-scm==6.4.2'},
    'statsmodels__statsmodels-8831': {'setuptools-scm==7.0.5'},
    'pytest-dev__pytest-xdist-667': {'setuptools-scm==8.2.0'},
    'zarr-developers__zarr-python-659': {'setuptools-scm==8.2.0'},
}


def baseline_backend(task, backend):
    removed = REVIEWED_BACKEND_ADDITIONS.get(task, set())
    if removed and not removed.issubset(backend['install']):
        raise ValueError('Reviewed backend repair changed')
    return {**backend, 'install': [pin for pin in backend['install'] if pin not in removed]}


def instruction_suffix(files):
    """Reproduce the later automatic static-quality edit, without setup guidance."""
    import tomllib
    seconds = tomllib.loads(files['task.toml'].decode()).get('agent', {}).get('timeout_sec')
    if seconds is None:
        return files
    if type(seconds) is not int or seconds <= 0:
        raise ValueError('Unreviewed historical instruction timeout')
    result = dict(files)
    suffix = ('\n\nYou have ' + str(seconds) + ' seconds to complete this task. '
              'Do not cheat by using online solutions or hints specific to this task.\n').encode()
    result['instruction.md'] = files['instruction.md'].rstrip() + suffix
    return result

# Later reference/no-op exclusions recorded by the final publication run.
ARCHIVED_TASKS.update({'bridgecrewio__checkov-3007': {'category': 'verifier-execution-error', 'reason': 'In the reference run two test modules fail to import (cannot import name LegacyVersion from packaging.version) while six tests pass (same result in all 4 attempts)', 'historical_stage': 4, 'evidence': '65cc78ebebb1201ab7bd4590cd359d55bc57a912/runs/2026-10-11-swelego-real-data-verified-d15f2a1ef8c8.json'}, 'tensorly__tensorly-318': {'category': 'no-op-reward-one', 'reason': 'The no-op run passes the required tests (13 passed, 2 skipped; reward 1 without the reference patch), so the verifier cannot tell the reference from no change (same result in all 4 attempts)', 'historical_stage': 5, 'evidence': '65cc78ebebb1201ab7bd4590cd359d55bc57a912/runs/2026-10-11-swelego-real-data-verified-d15f2a1ef8c8.json'}, 'tortoise__tortoise-orm-1010': {'category': 'verifier-execution-error', 'reason': 'The no-op verifier ends with pytest usage error 4 before running any test, leaving no execution evidence (same result in all 4 attempts)', 'historical_stage': 5, 'evidence': '65cc78ebebb1201ab7bd4590cd359d55bc57a912/runs/2026-10-11-swelego-real-data-verified-d15f2a1ef8c8.json'}})
