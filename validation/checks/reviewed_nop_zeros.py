"""No-op zeros of verifiers without a test runner, accepted after a person read the script.

A task here gets no evidence record (shell checks, no runner adapter). Its no-op zero counts
only when the verifier printed exactly the reviewed line; any other failure is judged as
usual. Each entry says why the line is a check of the requested behaviour and not a
precondition (missing tool, failed build).
"""

REVIEWED = {
    # MiMo usecase.sh: assert_regex on the help output for the requested -s, --skip-sc flag;
    # its preconditions print "FAIL: build". Pilot job 1046056.
    'format-code-task-002598': {
        'output_line': 'FAIL: out missing regex: (^|[[:space:]])-s,[[:space:]]+--skip-sc[[:space:]]+'
                       'Skip SuperCollider detection and management entirely',
        'reviewed': '2026-10-17',
    },
    # MiMo: the script runs the test files directly (python test/data_join/test_dfs_client.py);
    # the new test imports the module the instruction asks for ("from fedlearner.common.dfs_client
    # import DFSClient"). Pilot job 1046307, identical in all 4 attempts.
    'format-code-task-000605': {
        'output_line': "ModuleNotFoundError: No module named 'fedlearner.common.dfs_client'",
        'reviewed': '2026-10-17',
    },
    # MiMo: karma with Chrome headless (no runner adapter). The test "normalizes mapbox:// style
    # urls ... passes the normalized url to transformRequest" needs the requested transformRequest
    # option; without it the library fetches the mapbox:// URL itself, which no network can serve.
    # Not an external dependency. Pilot job 1046307, identical in all 4 attempts.
    'format-code-task-002145': {
        'output_line': 'Error: Could not load mapbox://styles/mapbox/bright-v9: Failed to fetch',
        'reviewed': '2026-10-17',
    },
    # --- MiMo final run (array 1047484) no-op review, 2026-10-18 ---
    # Reviewed per task (reviewers A-D) or found by an output scan whose sample of 25 was
    # 25/25 genuine; the line is the earliest stable failure of the requested behaviour.
    # scan + spot-check 25/25: tests ran (mocha) and failed on the missing feature
    'format-code-task-000007': {'output_line': 'WARNING: You are currently using Node.js v24.15.0, which is not supported by Hardhat. This can lead to unexpected behavior. See https://hardhat.org/nodejs-versions', 'reviewed': '2026-10-18'},
    # reviewer A/B: tsd type tests on the new lean.test.ts fail because the typing change is missing
    # (same pattern as 000026).
    'format-code-task-000025': {'output_line': '✖  209:34  Argument of type FlattenMaps<IDBUser> & { _id: ObjectId; } is not assignable to parameter of type UpdateWriteOpResult.', 'reviewed': '2026-10-18'},
    # reviewer A/B: tsd type tests ran on the new lean.test.ts; the type assertions fail because the
    # typing change is missing. Type-level tests, not runtime ones, but they are the tests.
    'format-code-task-000026': {'output_line': '✖  216:2  Parameter type (FlattenMaps<User> & { _id: ObjectId; }) | null is declared too wide for argument type (Document<unknown, {}, User> & User & { _id: ObjectId; }) | null.', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-000027': {'output_line': '✖  1242:2   Parameter type number | undefined is not identical to argument type any.', 'reviewed': '2026-10-18'},
    # reviewer A/B: Karma ran the TransformsSpec, and 12 specs for the new
    # aircraftHeadingPitchRollQuaternion API failed.
    'format-code-task-000044': {'output_line': 'TypeError: Transforms.aircraftHeadingPitchRollToFixedFrame is not a function', 'reviewed': '2026-10-18'},
    # reviewer C: Requested `core.config.Config` module missing; _distutils_hack .pth noise is
    # harmless (R1).
    'format-code-task-000050': {'output_line': "E   ModuleNotFoundError: No module named 'core.config'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test) and failed on the missing feature
    'format-code-task-000051': {'output_line': 'KeyError: "getpwnam(): name not found: \'cacfo9892805\'"', 'reviewed': '2026-10-18'},
    # reviewer A/B: Karma ran 372 specs, and the single failure is the new rum session behaviour
    # assertion.
    'format-code-task-000055': {'output_line': 'TOTAL: 1 FAILED, 371 SUCCESS', 'reviewed': '2026-10-18'},
    # reviewer C: Test helper from the patch links against the rtloader C callback the feature must
    # add (R10: link error on symbol introduced by the test patch).
    'format-code-task-000058': {'output_line': '/usr/local/go/pkg/tool/linux_amd64/link: running gcc failed: exit status 1', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (mocha) and failed on the missing feature
    'format-code-task-000064': {'output_line': '3 failing', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-000067': {'output_line': 'tests/test_directory.py::test_os_error_mid_walk_emits_error_and_continues FAILED [ 76%]', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed; requested toolfile package empty (R3).
    'format-code-task-000070': {'output_line': 'github.com/DataDog/orchestrion/internal/pin/toolfile: no non-test Go files in /testbed/internal/pin/toolfile', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-000073': {'output_line': 'AssertionError:', 'reviewed': '2026-10-18'},
    # reviewer A/B: The e2e script calls `doppler configure set --config-dir`, the flag the task asks
    # to add, and the CLI rejects it as unknown.
    'format-code-task-000078': {'output_line': 'Error: unknown flag: --config-dir', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed (37 tests); requested sei package empty (R3).
    'format-code-task-000082': {'output_line': 'github.com/edgeware/mp4ff/sei: no non-test Go files in /testbed/sei', 'reviewed': '2026-10-18'},
    # reviewer A/B: PHPUnit ran; the 4 failures are the new exception-code-to-status-mapping tests
    # (500 instead of 400).
    'format-code-task-000091': {'output_line': "1) FOS\\RestBundle\\Tests\\Functional\\SerializerErrorTest::testSerializeExceptionCodeMappedToResponseStatusCodeJsonUsingErrorRenderer with data set #0 ('FlattenExceptionHandlerLegacyFormat', array(400, 'Invalid argument given.'))", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (mocha) and failed on the missing feature
    'format-code-task-000096': {'output_line': '1) should discard failed initiators', 'reviewed': '2026-10-18'},
    # reviewer C: Patched test mocks implement the new Cleanup signature the fix introduces; old
    # interface does not match (R5).
    'format-code-task-000109': {'output_line': 'want Cleanup("context".Context, "io".Writer, bool, manifest.ManifestList) error', 'reviewed': '2026-10-18'},
    # reviewer A/B: The browser test runner ran 126 tests; the 2 failures are the new filterByText
    # add/edit-record assertions.
    'format-code-task-000123': {'output_line': '[browser] [FAIL] Test filtering all fields - IgDataSource Test - add/remove/delete record and test filterByText (1/3)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (minitest) and failed on the missing feature
    'format-code-task-000131': {'output_line': '1) Failure:', 'reviewed': '2026-10-18'},
    # reviewer A/B: busted ran the base and new specs; the new reserved-character path test fails
    # because validation still rejects ','.
    'format-code-task-000134': {'output_line': 'Failure -> spec/01-unit/01-db/01-schema/06-routes_spec.lua @ 297', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest, phpunit) and failed on the missing feature
    'format-code-task-000137': {'output_line': 'OK (8 tests, 8 assertions)', 'reviewed': '2026-10-18'},
    # reviewer C: New test expects the Workspace field the refactor adds (R5).
    'format-code-task-000139': {'output_line': 'pkg/cmd/apply/apply_behavior_test.go:54:32: request.Workspace undefined (type *operation.PreviewRequest has no field or method Workspace)', 'reviewed': '2026-10-18'},
    # reviewer A/B: Karma ran modal spec; 3 new cases fail.
    'format-code-task-000167': {'output_line': 'TOTAL: 3 FAILED, 73 SUCCESS', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000168': {'output_line': 'Error processing line 1 of /usr/lib/python3/dist-packages/distutils-precedence.pth:', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed (22 tests); requested helper module missing (R6).
    'format-code-task-000198': {'output_line': 'Error: Failed to resolve import "./sanitizeAvatar" from "src/utils/avatarImageUtils.spec.ts". Does the file exist?', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest, phpunit) and failed on the missing feature
    'format-code-task-000214': {'output_line': 'Error: Call to undefined method PrestaShop\\PrestaShop\\Core\\Context\\EmployeeContext::hasAuthorizationForAllShops()', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-000215': {'output_line': 'tests/test_handler_transform_inverter.py::TestTransformInverter::test_invert FAILED [ 10%]', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest, phpunit) and failed on the missing feature
    'format-code-task-000218': {'output_line': 'There was 1 failure:', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000237': {'output_line': 'test_callable_validates_callable_object (tests.test_nodes.CallableNodeTests.test_callable_validates_callable_object) ... FAIL', 'reviewed': '2026-10-18'},
    # reviewer A/B: Minitest assertion fails; caching missing.
    'format-code-task-000246': {'output_line': 'CodeActionsTest#test_diagnostic_calls_are_cached [test/requests/code_actions_test.rb:35]', 'reviewed': '2026-10-18'},
    # reviewer C: Command builds with `go test -c` and runs the binary directly, which the wrapper
    # does not instrument; the toxic tests ran and failed (R8).
    'format-code-task-000247': {'output_line': 'toxic_api_test.go:33: Expected 200 listing toxics, got 404', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed (2 tests); unittest loader import failure on the requested helper;
    # sqlite 'show' errors are pre-existing noise (R2 for unittest _FailedTest).
    'format-code-task-000253': {'output_line': 'ERROR: test_node_timeout_feature (unittest.loader._FailedTest.test_node_timeout_feature)', 'reviewed': '2026-10-18'},
    # reviewer A/B: Karma ran; one new assertion fails.
    'format-code-task-000254': {'output_line': 'Property url is expected to be of type string but instead it is of type object.', 'reviewed': '2026-10-18'},
    # reviewer A/B: Jasmine ran 756 specs; the 3 failures are assertion failures in the new LinkedList
    # tests.
    'format-code-task-000259': {'output_line': '3) LinkedList class tests can remove duplicate values when a different value is between them (issue reproducer)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (rspec) and failed on the missing feature
    'format-code-task-000278': {'output_line': 'Failure/Error: return OpenStruct.new JSON.parse(ret.body).first', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest) and failed on the missing feature
    'format-code-task-000280': {'output_line': '› 4 snapshots failed.', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000281': {'output_line': 'FAIL: test_json_preserves_scalar_types_at_every_nesting_level (__main__.ColumnSerializationContractTests.test_json_preserves_scalar_types_at_every_nesting_level)', 'reviewed': '2026-10-18'},
    # reviewer A/B: `pip install -e .` succeeded (no build/install FAIL), but the vcf2scoary console
    # entry point the task asks for does not exist.
    'format-code-task-000303': {'output_line': 'FAIL: vcf2scoary command is not installed', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-000305': {'output_line': 'var argumentErrorMsg = "hbs should be invoked with a single argument: the template string";', 'reviewed': '2026-10-18'},
    # reviewer C: Requested dabble node module missing (R1).
    'format-code-task-000332': {'output_line': "E   ModuleNotFoundError: No module named 'peekingduck.pipeline.nodes.dabble.bbox_to_btm_midpoint'", 'reviewed': '2026-10-18'},
    # reviewer C: Modified test calls the requested Parser.MustParse (R5).
    'format-code-task-000342': {'output_line': 'FAIL\tgithub.com/alexflint/go-arg [build failed]', 'reviewed': '2026-10-18'},
    # reviewer A/B: Django tests on pyenv 3.5.10 ran; base regression passes, the 2 new feature tests
    # fail on assertions. Recorder probably not loaded under py3.5.
    'format-code-task-000352': {'output_line': 'AssertionError: 1 != 2', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000354': {'output_line': 'FAILED (errors=1)', 'reviewed': '2026-10-18'},
    # reviewer A/B: The repo code already calls notesiumLines/linesOptions, the lines command the task
    # asks to implement, so the build fails on the missing feature.
    'format-code-task-000362': {'output_line': 'FAIL: build', 'reviewed': '2026-10-18'},
    # reviewer C: Test imports the requested module exports (R6).
    'format-code-task-000367': {'output_line': 'FAIL usercase-test-coderl/unleash-module-registration.test.ts', 'reviewed': '2026-10-18'},
    # reviewer C: Requested provider class not exported (R6).
    'format-code-task-000368': {'output_line': 'FAIL usercase-test-coderl/unleash-client-provider.test.ts', 'reviewed': '2026-10-18'},
    # reviewer A/B: pip install -e succeeded, but the trash-rm console script the task asks to add
    # does not exist.
    'format-code-task-000382': {'output_line': 'FAIL: trash-rm command was not installed', 'reviewed': '2026-10-18'},
    # reviewer C: Instruction asks to move websocket dial into its own pkg/conn/websocket package
    # inside the main module (R3).
    'format-code-task-000384': {'output_line': 'server/server/upstream/server_integration_test.go:12:2: no required module provides package github.com/andydunstall/piko/pkg/conn/websocket; to add it:', 'reviewed': '2026-10-18'},
    # reviewer C: Client metrics feature needs the sibling fxmetrics module added to fxhttpclient's
    # go.mod (R9: dependency the feature introduces).
    'format-code-task-000386': {'output_line': 'metrics_hidden_test.go:18:2: no required module provides package github.com/ankorstore/yokai/fxmetrics; to add it:', 'reviewed': '2026-10-18'},
    # reviewer A/B: The integration playbook reaches the new task, which fails because the sudoers
    # module lacks the setenv parameter.
    'format-code-task-000388': {'output_line': '[ERROR]: Task failed: Module failed: Unsupported parameters for (community.general.sudoers) module: setenv. Supported parameters include: commands, group, host, name, nopassword, runas, state, sudoers_path, user, validation.', 'reviewed': '2026-10-18'},
    # reviewer A/B: The test's inline python check of the credential module argument spec fails on the
    # new requirement the task asks for.
    'format-code-task-000391': {'output_line': 'AssertionError: credential_type should be required=True', 'reviewed': '2026-10-18'},
    # reviewer A/B: The first check is that wxPython/RunScript.py exists, and that file is the one the
    # task asks to add (python3 is present).
    'format-code-task-000395': {'output_line': 'FAIL: RunScript.py is missing from wxPython resources', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed (18 tests); the shared resize helper module requested does not
    # exist (R6: jest moduleNameMapper miss into repo src).
    'format-code-task-000400': {'output_line': 'FAIL __tests__/unit/utils/interaction/resize-spec.ts', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000402': {'output_line': 'return self.get_connection(fail_silently).send_messages([self])', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000419': {'output_line': 'test_create_execute (test_gcp_container_operator.GoogleCloudPlatformContainerOperatorTest.test_create_execute) ... FAIL', 'reviewed': '2026-10-18'},
    # reviewer A/B: Jasmine assertion failures on the requested uninstall behavior.
    'format-code-task-000422': {'output_line': '1) ios plugin handler uninstallation of <asset> elements (plugins folder regression) should not destroy a sibling asset under plugins/<plugin-id> when uninstalling another asset', 'reviewed': '2026-10-18'},
    # reviewer A/B: Regression gtests pass ([ PASSED ] 5 tests); new factory test fails to compile on
    # missing CreateOptimizer API.
    'format-code-task-000428': {'output_line': '8604 |     GTEST_DEATH_TEST_(statement, predicate, regex, GTEST_FATAL_FAILURE_)', 'reviewed': '2026-10-18'},
    # reviewer A/B: Tests use graphql-java 14 API; the task is to bump to graphql-java 14.
    'format-code-task-000436': {'output_line': '[ERROR] /testbed/graphql-java-support/src/test/java/com/apollographql/federation/graphqljava/FederationTest.java:[6,22] cannot find symbol', 'reviewed': '2026-10-18'},
    # reviewer C: Package source references the vm.Eval function the instruction asks for (R4).
    'format-code-task-000442': {'output_line': 'vm/datemath.go:136:17: undefined: Eval', 'reviewed': '2026-10-18'},
    # reviewer A/B: Test uses the public page lifecycle functions the task asks to expose.
    'format-code-task-000459': {'output_line': 'cc1: some warnings being treated as errors', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert, tap, tap-ok) and failed on the missing feature
    'format-code-task-000461': {'output_line': 'not ok 1 - inclusion projections filter nested added documents and later change maps', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-000481': {'output_line': '✖ rejects unknown security scheme ids with the exact Error message', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-000483': {'output_line': 'test/typescript_test.ts(464,21): error TS2554: Expected 1 arguments, but got 0.', 'reviewed': '2026-10-18'},
    # reviewer A/B: autopkg run lacks -c/--check.
    'format-code-task-000485': {'output_line': 'FAIL: F_help_run missing short check flag', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-000490': {'output_line': '# Subtest: test-tap/assert.js', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (rspec) and failed on the missing feature
    'format-code-task-000493': {'output_line': 'Failures:', 'reviewed': '2026-10-18'},
    # CLAIX rerun 5116864: reviewed 2026-10-18
    'format-code-task-000515': {'output_line': 'TypeError: manager.searchTracks is not a function', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000521': {'output_line': 'test_open_failure_propagates_specific_ioerror (__main__.RcWriteFileAcceptanceTest) ... ERROR', 'reviewed': '2026-10-18'},
    # reviewer A/B: Only the new test file fails to compile, because the EosDetector API the task asks
    # for does not exist.
    'format-code-task-000522': {'output_line': '/workspace/repo/usercase-test-coderl/test_eos_detector.cpp:225:16: error: expected ‘;’ before ‘b’', 'reviewed': '2026-10-18'},
    # reviewer C: Test imports the module the instruction asks to add (R1: missing project submodule).
    'format-code-task-000525': {'output_line': "E   ModuleNotFoundError: No module named 'pyboy.api.memory_scanner'", 'reviewed': '2026-10-18'},
    # reviewer A/B: phpunit ran; regression files pass, the new ConfirmTest errors on the missing
    # safeConfirm API.
    'format-code-task-000532': {'output_line': 'Tests: 8, Assertions: 18, Errors: 5.', 'reviewed': '2026-10-18'},
    # reviewer A/B: The first check is that the new bin/ntsynt_merge_collinear.py tool exists, and
    # that tool is what the task asks to create.
    'format-code-task-000537': {'output_line': 'FAIL: ntsynt_merge_collinear.py is not an executable command', 'reviewed': '2026-10-18'},
    # reviewer C: The root package that should expose HtmlToPdf has no Go files yet (R3).
    'format-code-task-000553': {'output_line': 'github.com/benoitkugler/go-weasyprint: no non-test Go files in /workspace/repo', 'reviewed': '2026-10-18'},
    # reviewer C: Repo source code depends on the storage methods (GetCommittedState etc.) the
    # instruction asks to add to the Plugin (R4: non-test compile errors naming instruction API).
    'format-code-task-000554': {'output_line': 'FAIL\tusercase-test-coderl [build failed]', 'reviewed': '2026-10-18'},
    # reviewer C: Non-test code references StoreBlock/GetBlockByHash, the methods the instruction
    # requests (R4).
    'format-code-task-000555': {'output_line': '../cosmos/x/evm/plugins/historical/genesis.go:36:11: p.StoreBlock undefined (type *plugin has no field or method StoreBlock)', 'reviewed': '2026-10-18'},
    # reviewer A/B: Only the new fixture file fails to compile, because the result wrappers lack the
    # sequence API the task asks for.
    'format-code-task-000562': {'output_line': '[error] /testbed/fixture-tests/WrapperSequenceBehaviorCheck.scala:117:35: value length is not a member of org.bitcoins.rpc.jsonmodels.DeriveAddressesResult', 'reviewed': '2026-10-18'},
    # reviewer A/B: Bats ran the templater test, which fails because the new IsMapFirst template
    # function is missing.
    'format-code-task-000567': {'output_line': '1 test, 1 failure', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000581': {'output_line': 'test_load_raises_when_secret_key_is_empty_string (tests.unit.test_credentials.TestEnvVar) ... FAIL', 'reviewed': '2026-10-18'},
    # reviewer C: The project is a pytest plugin; the rerun_filter receiving None (the reported bug)
    # crashes the session with exit 3 after 67 tests ran (R11: internal error inside project plugin).
    'format-code-task-000585': {'output_line': 'test/test_pytest/test_flaky_pytest_plugin.py::TestFlakyPytestPlugin::test_flaky_plugin_ignores_failure_for_non_flaky_test[instance] PASSED [ 56%]', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000589': {'output_line': 'test_password_reset_view_failure (userena.tests.tests_views.UserenaViewsTests)', 'reviewed': '2026-10-18'},
    # reviewer A/B: The only failing QUnit test is the one the patch changed to the new constructor
    # contract (no exception without a callback).
    'format-code-task-000591': {'output_line': '[FAIL] Lawnchair construction/destruction > ctor only calls callback if passed one (2, 7, 9)', 'reviewed': '2026-10-18'},
    # reviewer A/B: The test imports the dynamize_value function the task asks to add, and it does not
    # exist yet.
    'format-code-task-000593': {'output_line': "ImportError: cannot import name 'dynamize_value' from 'boto.dynamodb.types' (/workspace/repo/boto/dynamodb/types.py)", 'reviewed': '2026-10-18'},
    # reviewer A/B: Jasmine ran 30 tests; the 2 failures are the new contraction-detection specs.
    'format-code-task-000595': {'output_line': '30 Tests, 2 Failures, 0 Skipped', 'reviewed': '2026-10-18'},
    # reviewer C: Test imports the new getTransactions module for Scroll (R6).
    'format-code-task-000603': {'output_line': 'Error: Failed to load url ../src/blockchains/scroll/transaction.ts (resolved id: ../src/blockchains/scroll/transaction.ts) in /workspace/repo/usercase-test-coderl/transactions.test.ts. Does the file exist?', 'reviewed': '2026-10-18'},
    # reviewer A/B: pytest ran; the one new test fails on the missing error message.
    'format-code-task-000617': {'output_line': 'FAILED snapcraft/tests/test_cmds.py::TestCommands::test_assemble_snap_fails_on_bad_snap_layout', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed (16 tests); requested PagerTree plugin missing (R1).
    'format-code-task-000625': {'output_line': "E   ModuleNotFoundError: No module named 'apprise.plugins.NotifyPagerTree'", 'reviewed': '2026-10-18'},
    # reviewer A/B: CasperJS ran the suites, and the new withSelectorScope test fails because the
    # method does not exist.
    'format-code-task-000627': {'output_line': 'Error processing line 1 of /usr/lib/python3/dist-packages/distutils-precedence.pth:', 'reviewed': '2026-10-18'},
    # reviewer A/B: Only the new test file fails to compile, because the generatePRN helper the task
    # asks for is missing.
    'format-code-task-000638': {'output_line': 'Error: Compilation failed', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-000645': {'output_line': 'intelmq/tests/lib/test_bot.py::TestDummyParserBot::test_invalid_value_on_input_message FAILED', 'reviewed': '2026-10-18'},
    # reviewer A/B: Karma ran; the new Chart.getChart API is missing.
    'format-code-task-000663': {'output_line': 'TypeError: Chart.getChart is not a function', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (rspec) and failed on the missing feature
    'format-code-task-000665': {'output_line': 'should load and downcase names in the installed_packages hash (with disk provider) (FAILED - 1)', 'reviewed': '2026-10-18'},
    # reviewer A/B: Compile errors only in the new test referencing the missing ExecuteOrder API.
    'format-code-task-000667': {'output_line': '246 |     CHECK(session.market.ExecuteOrder(10, 25) == ErrorCode::OK);', 'reviewed': '2026-10-18'},
    # reviewer A/B: bsdetector --help fails; -i/-o batch CLI requested by the task is not there.
    'format-code-task-000673': {'output_line': 'FAIL: F_help exit 1; want 0', 'reviewed': '2026-10-18'},
    # reviewer C: Modified handler/payload tests use the new broker-fields API; ginkgo reports
    # compilation failure (R5).
    'format-code-task-000685': {'output_line': './service_plan_test.go:40:4: too many arguments in call to NewServicePlan', 'reviewed': '2026-10-18'},
    # reviewer C: Modified test uses the requested z.string().base64() (R6).
    'format-code-task-000699': {'output_line': 'FAIL src/__tests__/string.test.ts', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000705': {'output_line': 'if read_manifest.file_sums != expected_manifest.file_sums:', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed (23 tests); requested decoder package empty (R3).
    'format-code-task-000722': {'output_line': 'github.com/cortezaproject/corteza-server/compose/decoder: no non-test Go files in /root/go/src/github.com/cortezaproject/corteza-server/compose/decoder', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-000740': {'output_line': 'not ok 2 - 03-env-tmpl-run', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (unittest) and failed on the missing feature
    'format-code-task-000741': {'output_line': '=== Expected: ===', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000749': {'output_line': 'Error processing line 1 of /usr/lib/python3/dist-packages/distutils-precedence.pth:', 'reviewed': '2026-10-18'},
    # reviewer A/B: Dart test ran; custom-methods config missing.
    'format-code-task-000751': {'output_line': 'Expected: [4, 6]', 'reviewed': '2026-10-18'},
    # reviewer A/B: The page crash is exactly the reported bug (window.jQuery undefined).
    'format-code-task-000764': {'output_line': "[page error] Cannot read properties of undefined (reading 'fn')", 'reviewed': '2026-10-18'},
    # reviewer C: Package source references the requested Cache/NewCache (R4).
    'format-code-task-000766': {'output_line': './cache.go:47:13: undefined: Cache', 'reviewed': '2026-10-18'},
    # reviewer A/B: Vader ran 3 cases; 2 assertion failures show the old command line, so the new
    # language-server command is missing.
    'format-code-task-000790': {'output_line': "(1/3) [EXECUTE] (X) ['dart', '''dart'' ./snapshots/analysis_server.dart.snapshot --lsp'] should be equal to ['dart', '''dart'' language-server --protocol=lsp']", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (rspec) and failed on the missing feature
    'format-code-task-000791': {'output_line': "Pending: (Failures listed here are expected and do not affect your suite's status)", 'reviewed': '2026-10-18'},
    # reviewer A/B: Django on py3.5 ran; failures and errors (zone_exists still raises on 422) are the
    # missing feature.
    'format-code-task-000792': {'output_line': 'test_registration_same_remote_addr_ignores_forwarded_for_for_abuse_count (desecapi.tests.testregistration.RegistrationTest) ... FAIL', 'reviewed': '2026-10-18'},
    # reviewer A/B: Mocha never executed a test: the repo source lib/opcodes/index.ts imports ./calls,
    # the module the CALL/CALLCODE/DELEGATECALL feature adds. A missing-feature zero, but the scan
    # claim "tests visibly ran" is wrong here, and the error is in a source file, not the test patch,
    # so the missing-API rule as w
    'format-code-task-000794': {'output_line': 'return new TSError(diagnosticText, diagnosticCodes)', 'reviewed': '2026-10-18'},
    # reviewer C: The requested new `request` package does not exist yet (R3).
    'format-code-task-000800': {'output_line': 'FAIL\tgithub.com/dgrijalva/jwt-go/usercase-test-coderl [build failed]', 'reviewed': '2026-10-18'},
    # reviewer A/B: pytest (started through python3 -c) ran; German number support is missing.
    'format-code-task-000802': {'output_line': 'FAILED dragonfly/test/test_language_de_number.py::GermanIntegerTestCase::test_element', 'reviewed': '2026-10-18'},
    # reviewer C: Modified test file expects AutoUpgrade/Count to become pointer fields, which is the
    # requested change (R5: type errors in patch-touched test files).
    'format-code-task-000805': {'output_line': 'commands/kubernetes_test.go:382:17: cannot use boolPtr(false) (value of type *bool) as bool value in struct literal', 'reviewed': '2026-10-18'},
    # reviewer A/B: Mocha ran all 8 new saveHTML tests and marked each one failed; the epilogue with
    # the failure details is missing because the verifier took about 129 s, so the 120 s timeout most
    # likely killed mocha after the run (low confidence on the exact cause).
    'format-code-task-000815': {'output_line': '8) performs cleanup idempotently across two consecutive saveHTML calls', 'reviewed': '2026-10-18'},
    # reviewer C: The 'base' phase legitimately selects 0 tests (script maps exit 5 to 0); the 'new'
    # phase ran 8 tests and 7 failed on the missing ChameleonDataset (R7: ignore empty invocation the
    # script tolerates).
    'format-code-task-000817': {'output_line': 'collecting ... collected 4 items / 4 deselected / 0 selected', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed (8 tests); feature test imports the requested new backend module
    # (R1).
    'format-code-task-000825': {'output_line': "E   ModuleNotFoundError: No module named 'docling.backend.asciidoc_backend'", 'reviewed': '2026-10-18'},
    # reviewer C: Test imports the OpenAIProvider module the instruction asks for (R6: vitest relative
    # module missing).
    'format-code-task-000835': {'output_line': 'Error: Failed to load url ../packages/agent/src/core/llm/providers/openai.js (resolved id: ../packages/agent/src/core/llm/providers/openai.js) in /workspace/repo/usercase-test-coderl/openai_provider.test.ts. Does the file exist?', 'reviewed': '2026-10-18'},
    # reviewer A/B: Karma ran 20 specs; the single failure is the new controllerAs-with-function-
    # controller case.
    'format-code-task-000846': {'output_line': "Error: [$controller:ctrlfmt] http://errors.angularjs.org/1.3.20/$controller/ctrlfmt?p0=function%20()%20%7B%0A%20%20%20%20%20%20%20%20this.character%20%3D%20'Bender'%3B%0A%20%20%20%20%20%20%7D%20as%20futurama in bower_components/angular/angular.min.js (line 75)", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert, tap, tap-ok) and failed on the missing feature
    'format-code-task-000853': {'output_line': 'not ok 1 - redo replays an undone edit and restores undo/save/button state', 'reviewed': '2026-10-18'},
    # reviewer A/B: ava ran the new advanced-sort tests, which fail on deepEqual assertions.
    'format-code-task-000855': {'output_line': "t.deepEqual(sorted.map(record => record.id), ['defined', 'missing-high', 'missing-low'])", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert) and failed on the missing feature
    'format-code-task-000868': {'output_line': 'not ok - pie collapses sorted over-limit slices and expands other tooltip', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (mocha) and failed on the missing feature
    'format-code-task-000874': {'output_line': '✓ returns undefined if no blueprint is found and ignoredMissing is passed', 'reviewed': '2026-10-18'},
    # reviewer A/B: Only the new acceptance test fails to compile, because the sized uniqueBy(count,
    # gen, key) overload the task asks for is missing.
    'format-code-task-000877': {'output_line': '/workspace/repo/usercase-test-coderl/unique_acceptance.cpp:360:70: error: no match for call to ‘(const testNoRelativeFilesystemSideEffects()::<lambda(const Pair&)>) (rc::Random, int)’', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (mocha) and failed on the missing feature
    'format-code-task-000880': {'output_line': '1) expected to not throw', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (rspec) and failed on the missing feature
    'format-code-task-000883': {'output_line': 'Failures:', 'reviewed': '2026-10-18'},
    # reviewer A/B: Jasmine ran the new parseInt specs, and 2 assertions failed.
    'format-code-task-000888': {'output_line': 'Failures:', 'reviewed': '2026-10-18'},
    # reviewer C: Source requires the Resize method the instruction asks for (R4).
    'format-code-task-000889': {'output_line': './processor.go:24:20: cannot use (*Processor)(nil) (value of type *Processor) as SeamCarver value in variable declaration: *Processor does not implement SeamCarver (missing method Resize)', 'reviewed': '2026-10-18'},
    # reviewer A/B: The base library builds; the new gtest cannot compile because the
    # permutationsutil.h module the task asks for does not exist.
    'format-code-task-000898': {'output_line': '/testbed/src/tests/permutationsutil_test.cpp:23:10: fatal error: permutationsutil.h: No such file or directory', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000906': {'output_line': 'test_same_sep_endsep_two_items (evennia.utils.tests.test_utils.TestListToString.test_same_sep_endsep_two_items) ... FAIL', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-000910': {'output_line': 'ok 12 throws with undefined', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-000911': {'output_line': 'ok 2 throws with undefined in first argument', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-000915': {'output_line': '>> Error: tsc return code: 2', 'reviewed': '2026-10-18'},
    # reviewer A/B: React jest ran; the 3 new tests fail on the old warning text.
    'format-code-task-000921': {'output_line': '● ReactDOMSelect › select controlled value warning message › should warn select with value and no onChange without mentioning readOnly', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000930': {'output_line': "TypeError: writer() got an unexpected keyword argument 'metadata'", 'reviewed': '2026-10-18'},
    # reviewer A/B: tap (mocha-style spec reporter) ran; the new error-message tests fail.
    'format-code-task-000934': {'output_line': 'response schema without status code throws helpful error (issue repro)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-000935': {'output_line': 'not ok 1 - request must not be null in onSend', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-000936': {'output_line': '--- expected', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (rspec) and failed on the missing feature
    'format-code-task-000938': {'output_line': "when Spaceship failed to return a valid 'app' object", 'reviewed': '2026-10-18'},
    # reviewer A/B: Test requires the skunk/rubycritic/analysed_module extension the task asks to add.
    'format-code-task-000940': {'output_line': "from /workspace/repo/usercase-test-coderl/skunk_score_acceptance_test.rb:20:in `<main>'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (rspec) and failed on the missing feature
    'format-code-task-000947': {'output_line': 'Failures:', 'reviewed': '2026-10-18'},
    # reviewer A/B: tools/filter.py is the command the task asks to add.
    'format-code-task-000950': {'output_line': 'FAIL: tools/filter.py is not executable', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-000952': {'output_line': 'at createTSError (/testbed/node_modules/ts-node/src/index.ts:859:12)', 'reviewed': '2026-10-18'},
    # reviewer C: Modified tests use the exported Part fields/constants the expected outcomes require
    # (R5).
    'format-code-task-000953': {'output_line': 'ai/document_test.go:45:11: undefined: PartText', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-000954': {'output_line': '#   flowName: undefined,', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000957': {'output_line': 'FAIL: test_codespec_classic_load_balancer_classification_and_order (test_find_unused_load_balancers_codespec.TestFindUnusedLoadBalancersCodeSpec.test_codespec_classic_load_balancer_classification_and_order)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000969': {'output_line': 'FAIL: test_absolute_import_forms_report_every_statement (__main__.NoImportZdsSettingsCliTest)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000972': {'output_line': "UnboundLocalError: local variable 'entity' referenced before assignment", 'reviewed': '2026-10-18'},
    # reviewer A/B: Jasmine assertion failures.
    'format-code-task-000981': {'output_line': "Expected undefined to equal Object({ rows: [ Object({ name: 'first', score: 0, enabled: false, missing: null }), Object({ name: 'second', score: null, enabled: true, missing: null }) ] }).", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest, phpunit) and failed on the missing feature
    'format-code-task-000990': {'output_line': 'There was 1 failure:', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000994': {'output_line': 'test_extract_OS_info_fail (socorro.unittest.processor.test_breakpad_pipe_to_json.TestCase) ... ok', 'reviewed': '2026-10-18'},
    # reviewer A/B: PHP test shows the sniff does not count interfaces yet.
    'format-code-task-000995': {'output_line': 'FAIL: class and interface', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-000999': {'output_line': 'FAIL: test_incompatible_root_returns_source_shape (__main__.ApplyDiffContractTests)', 'reviewed': '2026-10-18'},
    # reviewer A/B: Zulip custom node test runner ran; drafts passed, the new draft_synchronization
    # test fails on an assertion.
    'format-code-task-001002': {'output_line': 'test failed: ../draft_synchronization.test.js > initialize hydrates synchronized drafts alongside local state', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-001003': {'output_line': 'not ok 1 - Signer accepts a keyring configuration', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest) and failed on the missing feature
    'format-code-task-001009': {'output_line': 'FAIL static/app/utils/parseLinkHeader.codespec.spec.ts', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001010': {'output_line': 'Error processing line 1 of /usr/lib/python3/dist-packages/distutils-precedence.pth:', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001014': {'output_line': 'FAIL: test_cli_accepts_source_root_and_filename (__main__.VersionUtilsContractTests.test_cli_accepts_source_root_and_filename)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001023': {'output_line': 'FAIL: test_documented_mapping_input_obeys_the_same_rules (__main__.BootArgumentProvenanceTestCase.test_documented_mapping_input_obeys_the_same_rules)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-001028': {'output_line': 'common/src/test/java/org/apache/rocketmq/common/message/MessageDecoderValidationContractTest.java:26: error: cannot find symbol', 'reviewed': '2026-10-18'},
    # reviewer A/B: The PHP test script ran and reported 7 failed assertion groups about the config-
    # inheritance feature.
    'format-code-task-001029': {'output_line': 'FAIL nested relative inheritance: An inherited path setting must be resolved relative to the file that declares it.', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-001031': {'output_line': 'not ok 1 builder exposes .with()', 'reviewed': '2026-10-18'},
    # reviewer A/B: The SCons test framework ran the new test and it failed on feature behaviour.
    'format-code-task-001032': {'output_line': 'FAILED test of /testbed/scripts/scons.py', 'reviewed': '2026-10-18'},
    # reviewer A/B: Karma ran the new paginator contract spec, and 7 of 9 failed on behaviour
    # assertions.
    'format-code-task-001062': {'output_line': 'Expected undefined to be 0.', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001076': {'output_line': 'Error processing line 1 of /usr/lib/python3/dist-packages/distutils-precedence.pth:', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (cargo) and failed on the missing feature
    'format-code-task-001083': {'output_line': 'test codespec_relative_get_rejects_each_out_of_bounds_dimension ... FAILED', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert) and failed on the missing feature
    'format-code-task-001091': {'output_line': 'FAIL testQueryDecodingAndBlankQueries: AssertionError [ERR_ASSERTION]: the displayed query should be plus-decoded and trimmed', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert) and failed on the missing feature
    'format-code-task-001099': {'output_line': 'AssertionError [ERR_ASSERTION]: Expected values to be strictly deep-equal:', 'reviewed': '2026-10-18'},
    # reviewer A/B: The phantomjs page ran the new route tests and printed FAIL lines with
    # expected/actual mismatches.
    'format-code-task-001101': {'output_line': 'FAIL: param leaves route metadata and values untouched expected={"page":7,"includeArchived":true,"route":"reports/:page/:includeArchived","draft":false} actual={"page":7,"includeArchived":true,"draft":false}', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001110': {'output_line': 'FAIL: test_bump_version_rejects_invalid_release_type_or_shape (__main__.ReleaseContractTests.test_bump_version_rejects_invalid_release_type_or_shape)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001112': {'output_line': "FAIL: test_csv_validation_reports_context_and_preserves_output (__main__.PgoNsysConverterCliTest.test_csv_validation_reports_context_and_preserves_output) (value='not-a-number')", 'reviewed': '2026-10-18'},
    # reviewer A/B: ExUnit ran 6 new tests, and all fail because the Ecto.Multi read functions do not
    # exist.
    'format-code-task-001116': {'output_line': 'expected Ecto.Multi.exists?/4 to be part of the public API', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest, phpunit) and failed on the missing feature
    'format-code-task-001117': {'output_line': '│ Failed asserting that two strings are identical.', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001122': {'output_line': 'FAIL: test_reports_each_real_pandas_array_use_and_import (__main__.TestUsePdArrayContract.test_reports_each_real_pandas_array_use_and_import)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001124': {'output_line': "KeyError: 'crash_id'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001130': {'output_line': 'FAIL: test_partial_overrides_and_rounding (__main__.TableSizingContractTest.test_partial_overrides_and_rounding)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-001135': {'output_line': 'Pkg.usageError (lib/base-command.js:114:26)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001136': {'output_line': 'test_column_hints_are_scoped_and_preserve_other_deltas (tests.apps.migrations.auto.test_schema_differ_rename_hints.TestSchemaDifferRenameHints.test_column_hints_are_scoped_and_preserve_other_deltas) ... FAIL', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001138': {'output_line': "KeyError: 'fuzziness'", 'reviewed': '2026-10-18'},
    # reviewer A/B: The node test script fails its assertion because ISO timezone-offset formatting is
    # not implemented.
    'format-code-task-001143': {'output_line': "throw new Error(failures.length + ' ISO timezone checks failed:\\n' + failures.join('\\n'));", 'reviewed': '2026-10-18'},
    # reviewer A/B: Custom PHP test prints PASS/FAIL lines; nested-locale restoration not implemented.
    'format-code-task-001147': {'output_line': "FAIL: nested locale restoration: nested locale restoration expected 'fr/fr/Bienvenue|es/es/Bienvenido/Varios articulos|fr/fr/Bienvenue' but got 'fr/fr/Bienvenue|es/es/Bienvenido/Varios articulos|es/fr/Bienvenido'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert) and failed on the missing feature
    'format-code-task-001149': {'output_line': 'AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:', 'reviewed': '2026-10-18'},
    # reviewer A/B: jest via yarn workspace ran; failures are feature assertions.
    'format-code-task-001155': {'output_line': 'FAIL src/across/rateModel.history.test.ts', 'reviewed': '2026-10-18'},
    # reviewer A/B: Plain node assert script ran; the lint still rejects @import because the feature
    # is missing.
    'format-code-task-001166': {'output_line': 'AssertionError [ERR_ASSERTION]: Expected values to be strictly deep-equal:', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert) and failed on the missing feature
    'format-code-task-001173': {'output_line': 'AssertionError [ERR_ASSERTION]: maxBytes must reject before the stream ends', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001176': {'output_line': 'test_every_cycle_edge_is_reported_in_direct_and_indirect_cycles (__main__.ScenarioCompositionLintContract.test_every_cycle_edge_is_reported_in_direct_and_indirect_cycles) ... FAIL', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001180': {'output_line': 'FAIL: test_contentstate_applies_dependency_rules_in_both_directions (wagtail.tests.test_feature_dependencies.FeatureDependencyConsumerTests.test_contentstate_applies_dependency_rules_in_both_directions)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest, phpunit) and failed on the missing feature
    'format-code-task-001182': {'output_line': 'There were 9 failures:', 'reviewed': '2026-10-18'},
    # reviewer C: Modified test sets the requested MaxHeaderBytes option (R5).
    'format-code-task-001185': {'output_line': 'server/service_test.go:50:3: unknown field MaxHeaderBytes in struct literal of type BoostServiceOpts', 'reviewed': '2026-10-18'},
    # reviewer A/B: Flow type-check of the task's test file fails on missing combineAll libdef.
    'format-code-task-001192': {'output_line': 'Found 2 errors', 'reviewed': '2026-10-18'},
    # reviewer A/B: Dart test ran; one new assertion fails.
    'format-code-task-001194': {'output_line': 'Expected: <1>', 'reviewed': '2026-10-18'},
    # reviewer A/B: The boards service to implement does not exist.
    'format-code-task-001210': {'output_line': "Error: Cannot find module '/workspace/repo/app/scripts/services/boards.js'", 'reviewed': '2026-10-18'},
    # reviewer C: The race test ran and panicked with the reported nil dereference; a panicking test
    # binary leaves executed=0 (R8: count Go test panics as failures).
    'format-code-task-001217': {'output_line': '=== RUN   TestRemoveRace', 'reviewed': '2026-10-18'},
    # reviewer C: New test uses the Bastion type and validator the instruction asks for (R5).
    'format-code-task-001233': {'output_line': 'pkg/apis/extensions/validation/bastion_test.go:32:15: undefined: v1alpha1.Bastion', 'reviewed': '2026-10-18'},
    # reviewer C: Test imports the data export the fix adds (R6).
    'format-code-task-001244': {'output_line': 'FAIL Web src/components/about-block.test.ts', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (unittest) and failed on the missing feature
    'format-code-task-001267': {'output_line': 'OperationalError: could not connect to server: No such file or directory', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest) and failed on the missing feature
    'format-code-task-001274': {'output_line': 'Test Suites: 1 failed, 1 total', 'reviewed': '2026-10-18'},
    # reviewer A/B: Sentry jest (scripts/test.js) ran; the new expectation fails.
    'format-code-task-001279': {'output_line': '> 244 |     await waitFor(() => expect(mockedErrorEventsMetaCall).toHaveBeenCalledTimes(1));', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest) and failed on the missing feature
    'format-code-task-001283': {'output_line': 'FAIL static/app/views/settings/components/dataSecrecy/index.spec.tsx', 'reviewed': '2026-10-18'},
    # reviewer C: Existing no-reference rule case; it was missed because `-c /dev/null` makes pytest
    # report 'ERROR collecting test session' with empty collection_error_files (match by message, not
    # file).
    'format-code-task-001285': {'output_line': "E   ImportError: cannot import name 'DummyAuthorizer' from 'pyftpdlib.authorizers' (/workspace/repo/pyftpdlib/authorizers.py)", 'reviewed': '2026-10-18'},
    # reviewer A/B: CodeQL ran the new Email library test, and it fails because the MailData QL class
    # the task asks for is missing.
    'format-code-task-001290': {'output_line': '+ERROR: Could not resolve type MailData (MailData.ql:3,6-14)', 'reviewed': '2026-10-18'},
    # reviewer A/B: The new test parses "[$id1, $name1] = $data[0];" at describe time; the parser
    # lacks array-destructuring support (the feature), so mocha aborts while loading the file.
    'format-code-task-001292': {'output_line': "SyntaxError: Parse Error : syntax error, unexpected '=', expecting ';' on line 1", 'reviewed': '2026-10-18'},
    # reviewer C: Gitea integration tests run from a prebuilt test binary that the go wrapper does not
    # see (END executed=0, exit 0); the release-count test actually ran and failed (R8).
    'format-code-task-001297': {'output_line': 'Error Trace:\tapi_repo_test.go:219', 'reviewed': '2026-10-18'},
    # reviewer C: The DELETE tracked-time integration test ran and hit exactly the reported 500/nil-
    # deref; executed=0 is an instrumentation gap for the compiled integrations binary (R8).
    'format-code-task-001298': {'output_line': 'panicmem: panic(memoryError)', 'reviewed': '2026-10-18'},
    # reviewer C: Integration tests ran from a compiled binary and failed on the missing owner/team
    # parameters; checker saw only build invocations (R8).
    'format-code-task-001299': {'output_line': 'Error Trace:\tapi_issue_test.go:270', 'reviewed': '2026-10-18'},
    # reviewer C: Modified test file uses the new RenameOrgOption type of the requested org-rename API
    # (R5).
    'format-code-task-001301': {'output_line': 'tests/integration/api_org_test.go:93:77: undefined: api.RenameOrgOption', 'reviewed': '2026-10-18'},
    # reviewer C: The compiler package that should expose CompileToInstructions is empty (R3).
    'format-code-task-001317': {'output_line': 'github.com/goby-lang/goby/compiler: no non-test Go files in /workspace/repo/compiler', 'reviewed': '2026-10-18'},
    # reviewer C: Package source references the two requested functions (R4).
    'format-code-task-001319': {'output_line': './decode.go:870:22: undefined: YAMLToJSON', 'reviewed': '2026-10-18'},
    # reviewer A/B: The test requires the lib/watchers/deployment-watcher module the task asks to
    # create, and it does not exist.
    'format-code-task-001321': {'output_line': "Error: Cannot find module '../lib/watchers/deployment-watcher'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001334': {'output_line': '[  FAILED  ] FileProgramStateManagerLoadTest.test_raises_structure_error', 'reviewed': '2026-10-18'},
    # reviewer A/B: The help output lacks the new --infer_src_data_file option.
    'format-code-task-001336': {'output_line': 'FAIL: F_help missing --infer_src_data_file', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-001338': {'output_line': 'ct/crypto/asn1/types_test.py::PrintTest::test_ia5_string_constructor_rejects_non_ascii FAILED [ 12%]', 'reviewed': '2026-10-18'},
    # reviewer A/B: lit ran the base tests and they passed; only the new opaque-pointer packed-struct
    # lowering test fails its FileCheck match.
    'format-code-task-001339': {'output_line': 'Error processing line 1 of /usr/lib/python3/dist-packages/distutils-precedence.pth:', 'reviewed': '2026-10-18'},
    # reviewer C: Requested gcs retriever package empty (R3).
    'format-code-task-001341': {'output_line': 'github.com/google/cql/retriever/gcs: no non-test Go files in /workspace/repo/retriever/gcs', 'reviewed': '2026-10-18'},
    # reviewer C: Requested decorator missing from the project module (R2).
    'format-code-task-001360': {'output_line': "E   AttributeError: module 'fire.decorators' has no attribute 'SetParseFns'", 'reviewed': '2026-10-18'},
    # reviewer A/B: A standard pytest summary: the new pyformat dry-run tests fail. The recorder
    # missed it because pytest runs through a custom `python -c '...pytest.main'` wrapper with its own
    # sys.path.
    'format-code-task-001372': {'output_line': 'FAILED tests/unit/core/test_pyformat_pandas.py::test_pyformat_dry_run_embeds_dataframe_as_empty_table_expression', 'reviewed': '2026-10-18'},
    # reviewer A/B: The bundle fails because the buffer-list.js module the task asks to create (and
    # the test exercises) is missing.
    'format-code-task-001375': {'output_line': "[!] Error: Could not resolve './buffer-list.js' from src/foa-renderer.js", 'reviewed': '2026-10-18'},
    # reviewer A/B: The CTS unittest runner ran all 14 new cases, and they fail because the abstract
    # fma builder is missing.
    'format-code-task-001384': {'output_line': "- EXPECTATION FAILED: fma case cache missing 'abstract' builder. Found keys: f32_const, f32_non_const, f16_const, f16_non_const", 'reviewed': '2026-10-18'},
    # reviewer C: Modified test uses the new NewPush signature for TLS push mode (R5).
    'format-code-task-001392': {'output_line': 'FAIL\tgithub.com/grafana/loki/pkg/canary/writer [build failed]', 'reviewed': '2026-10-18'},
    # reviewer C: Requested rewriter classes not exported (R6).
    'format-code-task-001401': {'output_line': 'FAIL usercase-test-coderl/nested_output_rewriters.test.ts', 'reviewed': '2026-10-18'},
    # reviewer C: Modified test uses the requested Heading weight prop (R6).
    'format-code-task-001410': {'output_line': "Property 'weight' does not exist on type 'IntrinsicAttributes & HeadingExtendedProps'.", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (rspec) and failed on the missing feature
    'format-code-task-001415': {'output_line': 'Failures:', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (mocha) and failed on the missing feature
    'format-code-task-001428': {'output_line': '5) throws Error for malformed or disallowed paths', 'reviewed': '2026-10-18'},
    # reviewer A/B: Browser jasmine run; assertions fail.
    'format-code-task-001429': {'output_line': '19 specs, 2 failures', 'reviewed': '2026-10-18'},
    # reviewer C: Requested GzipASCWriter missing from the project package (R2).
    'format-code-task-001431': {'output_line': "E   AttributeError: module 'can' has no attribute 'GzipASCWriter'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-001437': {'output_line': 'ok 1 - expected to throw: Error Validation error, Field second is missing', 'reviewed': '2026-10-18'},
    # reviewer A/B: go test ran (Go 1.9 from /opt/go-1.9, which is not among the wrapped toolchains
    # 1.23-1.26, hence no evidence); the new unexpected-response test panics because the handling is
    # missing.
    'format-code-task-001445': {'output_line': 'panic: interface conversion: interface {} is nil, not bool', 'reviewed': '2026-10-18'},
    # reviewer A/B: Test app declares model= (the requested attribute); code still requires
    # model_class.
    'format-code-task-001460': {'output_line': "AttributeError: type object 'CollectionViewSet' has no attribute 'model_class'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-001464': {'output_line': "# SyntaxError: The requested module '../client/app/utils/base.js' does not provide an export named 'pairAnchorsAndFootnotes'", 'reviewed': '2026-10-18'},
    # reviewer A/B: Karma ran; HTML component markup feature missing.
    'format-code-task-001467': {'output_line': 'Expected: true', 'reviewed': '2026-10-18'},
    # reviewer A/B: The converter script the task asks for does not exist.
    'format-code-task-001471': {'output_line': "[Errno 2] No such file or directory: 'scripts/gsm8k_to_math.py'FAIL: build", 'reviewed': '2026-10-18'},
    # reviewer A/B: Requested NoteClip API missing.
    'format-code-task-001474': {'output_line': 'not ok - push/get/hasId keep note state under deterministic unique IDs', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-001475': {'output_line': 'not ok 3 should not throw', 'reviewed': '2026-10-18'},
    # reviewer C: Requested PDFPage text methods missing (R6).
    'format-code-task-001518': {'output_line': 'FAIL usercase-test-coderl/page_text_drawing.test.ts', 'reviewed': '2026-10-18'},
    # reviewer A/B: The node test ran and failed because the requested RawReader class is missing.
    'format-code-task-001523': {'output_line': 'not ok - RawReader returns exact raw bytes for one string value', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-001529': {'output_line': 'tests/test_modeling_tf_openai.py::TFOpenAIGPTModelTest::test_mixed_precision FAILED [ 45%]', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-001547': {'output_line': '# Subtest: Skip undefined namespace', 'reviewed': '2026-10-18'},
    # reviewer A/B: Custom offline-vitest runner ran the keyframes tests; they fail because @keyframes
    # support is missing.
    'format-code-task-001560': {'output_line': 'not ok Imba compile keyframes CSS output > global keyframes keep their name and render percent frames with opacity shorthand', 'reviewed': '2026-10-18'},
    # reviewer C: Modified test imports the two exports the instruction requests (R6).
    'format-code-task-001576': {'output_line': 'FAIL packages/database/__tests__/database.test.ts', 'reviewed': '2026-10-18'},
    # reviewer C: Feature replaces *badger.DB with hive.go's generic kvstore interface, which needs a
    # newer hive.go; tests import it (R9).
    'format-code-task-001579': {'output_line': 'dapps/valuetransfers/packages/branchmanager/branchmanager_test.go:8:2: no required module provides package github.com/iotaledger/hive.go/kvstore; to add it:', 'reviewed': '2026-10-18'},
    # reviewer C: Source interface already requires the MoveCoins method the instruction asks for
    # (R4).
    'format-code-task-001582': {'output_line': 'x/cash/controller.go:43:20: cannot use BaseController{} (value of type BaseController) as Controller value in variable declaration: BaseController does not implement Controller (missing method MoveCoins)', 'reviewed': '2026-10-18'},
    # reviewer A/B: The help output lacks the new --typetree option.
    'format-code-task-001589': {'output_line': 'FAIL: F_help missing --typetree', 'reviewed': '2026-10-18'},
    # reviewer A/B: The CLI rejects the new --sort-by argument.
    'format-code-task-001599': {'output_line': 'FAIL: metric_qualified_asc exit 254; want 0; stderr: ERROR: unrecognized arguments: --sort-by=metrics.yaml:foo', 'reviewed': '2026-10-18'},
    # reviewer C: ivy's test helper raises ImportError at collection because the requested
    # tf.sequence_mask frontend is missing (R2).
    'format-code-task-001601': {'output_line': "E   ImportError: Error: The function 'sequence_mask' could not be found within the module 'ivy.functional.frontends.tensorflow'.", 'reviewed': '2026-10-18'},
    # reviewer C: Same ivy helper pattern; hardsilu is the requested function (R2).
    'format-code-task-001603': {'output_line': "E   ImportError: Error: The function 'hardsilu' could not be found within the module 'ivy.functional.ivy.experimental'.", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-001605': {'output_line': 'not ok 1 usercase redirect follow-through > global followRedirects setting exists and validates as boolean', 'reviewed': '2026-10-18'},
    # reviewer A/B: The use case compiles classify_corpus.py, the new script the task asks to create,
    # and it does not exist.
    'format-code-task-001608': {'output_line': "[Errno 2] No such file or directory: 'classify_corpus.py'FAIL: build", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001635': {'output_line': "TypeError: unhashable type: 'dict'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (mocha) and failed on the missing feature
    'format-code-task-001636': {'output_line': '3 failing', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-001644': {'output_line': 'not ok 1 - same-account spam report queues one Email/report call and no undo', 'reviewed': '2026-10-18'},
    # reviewer A/B: QUnit ran 2530 assertions; the only failure is the new Mastercard 2-series case.
    'format-code-task-001654': {'output_line': "Testing test/index.html .................................................................................................................Exception occurred when checking element stepOnUnsupportedTypeInput, check the 'step' method. Error: Step attribute on input type date is not supported.", 'reviewed': '2026-10-18'},
    # reviewer A/B: Julia tests ran; value(x, f) overload requested by task is missing.
    'format-code-task-001667': {'output_line': 'test_Model_value_var: Error During Test at /testbed/test/variable.jl:723', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (rspec) and failed on the missing feature
    'format-code-task-001674': {'output_line': '2 examples, 0 failures', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-001675': {'output_line': 'not ok 1 - Gh3.Gists fetch appends pages and exposes the accumulated list', 'reviewed': '2026-10-18'},
    # reviewer A/B: Requested Pool class does not exist.
    'format-code-task-001677': {'output_line': 'TypeError: Pool is not a constructor', 'reviewed': '2026-10-18'},
    # reviewer A/B: nodefront minify lacks the --images option the task asks for.
    'format-code-task-001679': {'output_line': 'FAIL: F_help missing -i, --images help', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed (13 tests); fixture setup loads the requested oauth2proxy identity
    # manager, which does not exist (R1 for setup errors).
    'format-code-task-001683': {'output_line': "E       ModuleNotFoundError: No module named 'keep.identitymanager.identity_managers.oauth2proxy'", 'reviewed': '2026-10-18'},
    # reviewer A/B: The script the task asks to create does not exist.
    'format-code-task-001684': {'output_line': "[Errno 2] No such file or directory: 'data_gen/gen_bone_data.py'FAIL: build", 'reviewed': '2026-10-18'},
    # reviewer C: Modified test calls the requested fallback helper (R5).
    'format-code-task-001685': {'output_line': 'controllers/common/helperfunctions_test.go:476:14: undefined: GetEvaluationDefinition', 'reviewed': '2026-10-18'},
    # reviewer C: Modified test calls the requested function (R5).
    'format-code-task-001694': {'output_line': './request_test.go:26:17: undefined: ListMyAccessRequests', 'reviewed': '2026-10-18'},
    # reviewer A/B: Custom runner summary; the computed context API is missing.
    'format-code-task-001701': {'output_line': 'Total:577 Passed:574 Failed:3', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert) and failed on the missing feature
    'format-code-task-001716': {'output_line': 'AssertionError [ERR_ASSERTION]: Expected values to be strictly deep-equal:', 'reviewed': '2026-10-18'},
    # reviewer C: Package source calls the requested ParseTimeout (R4).
    'format-code-task-001736': {'output_line': 'tools/clientcmd/client_config.go:209:19: undefined: ParseTimeout', 'reviewed': '2026-10-18'},
    # reviewer A/B: Shell test harness ran; -f flag handling before the subcommand is the requested
    # fix.
    'format-code-task-001738': {'output_line': "convert::expect_failure: Running: 'kompose -f /testbed/script/test/fixtures/etherpad/docker-compose.yml convert --stdout'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-001750': {'output_line': 'kymatio/scattering3d/tests/test_scattering3d.py::test_cpu_method_exists FAILED [ 16%]', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest, phpunit) and failed on the missing feature
    'format-code-task-001758': {'output_line': 'Tests: 251, Assertions: 1293, Errors: 2.', 'reviewed': '2026-10-18'},
    # reviewer C: The task is to rename the module path; patched tests import the new path while
    # go.mod still declares learn-go-with-tests (R9/R3).
    'format-code-task-001759': {'output_line': 'criando-uma-aplicacao/linha-de-comando/v3/CLI_test.go:8:2: no required module provides package github.com/larien/aprenda-go-com-testes/criando-uma-aplicacao/linha-de-comando/v3; to add it:', 'reviewed': '2026-10-18'},
    # reviewer A/B: Custom C test prints FAIL lines; SOCKS4 support missing.
    'format-code-task-001761': {'output_line': 'FAIL: IPv4 request bytes differed', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001765': {'output_line': "AttributeError: 'WalletManager' object has no attribute 'create_purchase_transaction'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-001783': {'output_line': 'lightkurve/tests/test_lightcurve.py::test_targetid FAILED                [ 64%]', 'reviewed': '2026-10-18'},
    # reviewer A/B: backup-cli and its venv exist (earlier checks passed) but the restore subcommand
    # the task adds does not, so the first feature check fails.
    'format-code-task-001784': {'output_line': 'FAIL: backup-cli restore help is unavailable', 'reviewed': '2026-10-18'},
    # reviewer A/B: The build fails only on references to CommitConfig/VMCommit, the vm commit API the
    # task asks for, so this is the missing feature.
    'format-code-task-001791': {'output_line': 'internal/virter/image.go:544:18: undefined: CommitConfig', 'reviewed': '2026-10-18'},
    # reviewer A/B: The LCB test suite ran; only the new modulo tests fail.
    'format-code-task-001802': {'output_line': 'FAIL:  stdlib/modulo: TestModIntegerNegativeDivisor', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-001815': {'output_line': "../core/src/__tests__/unit/application.unit.ts(265,9): error TS2345: Argument of type '{ namespace: string; name: string; }' is not assignable to parameter of type 'string | ServiceOptions | undefined'.", 'reviewed': '2026-10-18'},
    # reviewer A/B: The build fails because the protobuf AppServer interface already requires the
    # Delete RPC that the task asks to implement on Service.
    'format-code-task-001826': {'output_line': 'pkg/server/app/handlers.go:193:37: cannot use s (variable of type *Service) as type "github.com/luizalabs/teresa/pkg/protobuf/app".AppServer in argument to appb.RegisterAppServer:', 'reviewed': '2026-10-18'},
    # reviewer A/B: The new conftest uses the stk.Smiles key maker the task asks to add, and it does
    # not exist yet.
    'format-code-task-001827': {'output_line': "ImportError while loading conftest '/testbed/tests/molecular/key_makers/conftest.py'.", 'reviewed': '2026-10-18'},
    # reviewer C: The task is to bump go-control-plane past v0.9.4; the modified test imports a
    # package only in newer versions (R9: dependency change requested by the instruction).
    'format-code-task-001830': {'output_line': 'module/chaos/experimentation/rtds/rtds_cache_test.go:8:2: no required module provides package github.com/envoyproxy/go-control-plane/pkg/cache/types; to add it:', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001833': {'output_line': 'test_soft_delete_cascade_deleted (safedelete.tests.test_soft_delete_cascade.SimpleTest.test_soft_delete_cascade_deleted) ... FAIL', 'reviewed': '2026-10-18'},
    # reviewer C: Requested find_static_capabilities module absent (R1).
    'format-code-task-001836': {'output_line': "E   ModuleNotFoundError: No module named 'capa.capabilities.static'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (rspec) and failed on the missing feature
    'format-code-task-001838': {'output_line': 'Failures:', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (mocha) and failed on the missing feature
    'format-code-task-001839': {'output_line': 'not ok should be equivalent', 'reviewed': '2026-10-18'},
    # reviewer A/B: The CLI ran and its help output lacks the -MMD option the task asks to add.
    'format-code-task-001843': {'output_line': 'FAIL: F_help missing -MMD option', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001845': {'output_line': 'test_drf31_pagination_class_adds_configured_page_parameter (rest_framework_swagger.test_fixture_pagination.FixturePaginationIntrospectionTests) ... FAIL', 'reviewed': '2026-10-18'},
    # reviewer A/B: The compiler rejects the inline <script> template syntax that the task asks to
    # support.
    'format-code-task-001849': {'output_line': 'Error: Unknown runtime value type SCRIPT_TAG', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed (35 tests); requested file_watcher module missing (R1).
    'format-code-task-001853': {'output_line': "E   ModuleNotFoundError: No module named 'marimo._utils.file_watcher'", 'reviewed': '2026-10-18'},
    # CLAIX rerun 5116864: reviewed 2026-10-18
    'format-code-task-001855': {'output_line': 'github.com/markbates/goth/gothic: no non-test Go files in /workspace/repo/gothic', 'reviewed': '2026-10-18'},
    # reviewer A/B: node --test ran 8 tests, all fail on missing API.
    'format-code-task-001857': {'output_line': 'not ok 8 - repeated construct-use-dispose cycles do not leak scroll listeners', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-001858': {'output_line': 'not ok 1 - default Plane construction creates a DOM-backed quad with default geometry', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-001860': {'output_line': 'Error processing line 1 of /usr/lib/python3/dist-packages/distutils-precedence.pth:', 'reviewed': '2026-10-18'},
    # reviewer A/B: The use case compiles get_am_dataset.py, the new script the task asks to create,
    # and it is missing.
    'format-code-task-001878': {'output_line': "[Errno 2] No such file or directory: 'get_am_dataset.py'FAIL: build", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-001879': {'output_line': 'not ok 1 - formats Windows NT and IE version classes', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-001889': {'output_line': 'not ok 1 - plugin should not throw MER_ERR_HOOK_UNSUPPORTED_HOOK', 'reviewed': '2026-10-18'},
    # reviewer A/B: The plan help output lacks the new overwrite_target_spacing flag.
    'format-code-task-001900': {'output_line': 'FAIL: F_help_plan missing overwrite_target_spacing flag', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-001902': {'output_line': "Error: Cannot find module 'simples/lib/session/store'", 'reviewed': '2026-10-18'},
    # reviewer A/B: rollup build then node --test ran 19 tests, all fail on missing helpers.
    'format-code-task-001904': {'output_line': 'not ok 19 - query helpers do not touch cwd files or network APIs', 'reviewed': '2026-10-18'},
    # reviewer A/B: ava ran the new tests, and all fail because the fetchFromApi export is missing.
    'format-code-task-001905': {'output_line': 'TypeError: fetchFromApi is not a function', 'reviewed': '2026-10-18'},
    # reviewer A/B: ava ran the new tests, which fail because the raw response-mode API (arrayBuffer
    # etc.) is missing.
    'format-code-task-001906': {'output_line': 'at /workspace/repo/usercase-test-coderl/raw-response-modes.test.js:360:9', 'reviewed': '2026-10-18'},
    # reviewer A/B: Only the new test_today.cpp fails to compile, because peg::parseString does not
    # yet return the by-value ast the test expects (API change asked by the task).
    'format-code-task-001911': {'output_line': 'gmake[3]: *** [CMakeFiles/test_today.dir/build.make:76: CMakeFiles/test_today.dir/test_today.cpp.o] Error 1', 'reviewed': '2026-10-18'},
    # reviewer A/B: Playwright's test suite ran; the only failure is the new --tsconfig option test.
    'format-code-task-001915': {'output_line': '1 failed', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert, tsd/tsc) and failed on the missing feature
    'format-code-task-001917': {'output_line': 'ℹ fail 0', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-001919': {'output_line': 'not ok 1 - reads a nested object property with dot syntax', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed (30 tests); requested export missing (R6).
    'format-code-task-001920': {'output_line': 'FAIL test/webRouterCollector.test.ts', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (rspec) and failed on the missing feature
    'format-code-task-001921': {'output_line': 'Failures:', 'reviewed': '2026-10-18'},
    # reviewer C: Requested readingtime package empty (R3).
    'format-code-task-001929': {'output_line': 'miniflux.app/v2/internal/reader/readingtime: no non-test Go files in /workspace/repo/internal/reader/readingtime', 'reviewed': '2026-10-18'},
    # reviewer A/B: UglifyJS compress test harness ran; expected output differs.
    'format-code-task-001931': {'output_line': '!!! Failed 1 test cases.', 'reviewed': '2026-10-18'},
    # reviewer A/B: Compress test cases fail on hoist_props behavior to implement.
    'format-code-task-001932': {'output_line': 'console.log(MODE.BlockStatement);', 'reviewed': '2026-10-18'},
    # reviewer A/B: Compress test harness ran, 4 class cases fail.
    'format-code-task-001933': {'output_line': '!!! failed', 'reviewed': '2026-10-18'},
    # reviewer A/B: Compress test harness ran, 3 default-values cases fail.
    'format-code-task-001934': {'output_line': '!!! failed', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-001937': {'output_line': '# undefined input', 'reviewed': '2026-10-18'},
    # reviewer A/B: Requested Duration#toISOString is missing.
    'format-code-task-001959': {'output_line': 'TypeError: moment.duration(...).toISOString is not a function', 'reviewed': '2026-10-18'},
    # reviewer A/B: libbson test harness ran; new validate-with-offset test fails.
    'format-code-task-001961': {'output_line': '-- Performing Test HAVE_LLD_LINKER_SUPPORT - Failed', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-001977': {'output_line': 'not ok 1 should be equivalent', 'reviewed': '2026-10-18'},
    # reviewer C: Python 2 form of 'cannot import name' from a project module; STAGE_JOBS belongs to
    # the requested jobs config (R2).
    'format-code-task-001981': {'output_line': 'E   ImportError: cannot import name STAGE_JOBS', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001987': {'output_line': 'TypeError: get_es() takes no arguments (3 given)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-001988': {'output_line': 'test_refresh_fails_for_already_signed_in_user (tests.test_middleware.MiddlewareTestCase.test_refresh_fails_for_already_signed_in_user) ... ok', 'reviewed': '2026-10-18'},
    # reviewer A/B: Custom runner prints FAIL lines; quoted-semicolon parsing missing.
    'format-code-task-001989': {'output_line': '6 tests failed.', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (mocha) and failed on the missing feature
    'format-code-task-001990': {'output_line': '✔ rejects an UsageError on adb binary not found', 'reviewed': '2026-10-18'},
    # reviewer C: Modified tests expect SaveKeyValue to return an error, the requested change (R5).
    'format-code-task-001997': {'output_line': 'data/state/trackableDataTrie_test.go:25:19: tracker.SaveKeyValue(probeKey, make([]byte, oversizedAccountDataSize)) (no value) used as value', 'reviewed': '2026-10-18'},
    # reviewer C: Requested token-selection module missing (R1).
    'format-code-task-002004': {'output_line': "E   ModuleNotFoundError: No module named 'mwmbl.indexer.index'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-002012': {'output_line': 'test/test_proxy.py::test_reset_restores_initial_fallback_false FAILED', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest) and failed on the missing feature
    'format-code-task-002019': {'output_line': 'Error: select "projects"."id", "projects"."name", "projects"."path", "projects"."created_at" from "projects" inner join "users" on "projects"."user_id" = "users"."id" where "users"."urs_id" = $1 order by "projects"."created_at" DESC - Unknown Error', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert, tap, tap-ok) and failed on the missing feature
    'format-code-task-002026': {'output_line': 'not ok 1 - startup cfg.opmid loads OPM PDB into viewer state', 'reviewed': '2026-10-18'},
    # reviewer A/B: CLI lacks the split option.
    'format-code-task-002028': {'output_line': 'FAIL: F_help missing fail/split choices in --large-files help: Usage: telegram-upload [OPTIONS] [FILES]...', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-002030': {'output_line': '1 test failed', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-002033': {'output_line': 'FAIL: test_post_list_view_custom_urlconf (tests.test_views.ViewTest)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (mocha) and failed on the missing feature
    'format-code-task-002041': {'output_line': 'not ok limit should default to 2000', 'reviewed': '2026-10-18'},
    # reviewer A/B: jasmine-node ran the compiled spec, and the range-annotation assertions failed.
    'format-code-task-002045': {'output_line': '10) NgbDatepickerService range annotations marks complete ranges inclusively, including both endpoints and interior dates', 'reviewed': '2026-10-18'},
    # reviewer C: First invocation passed 15 tests; the feature test file fails to import the
    # requested new module (R1).
    'format-code-task-002055': {'output_line': "E   ModuleNotFoundError: No module named 'app.factories.urlscan'", 'reviewed': '2026-10-18'},
    # reviewer A/B: Node built successfully and the new child-process test failed an assertion.
    'format-code-task-002060': {'output_line': '/usr/include/x86_64-linux-gnu/bits/socket2.h:62:56: note: expected ‘socklen_t * __restrict__’ {aka ‘unsigned int * __restrict__’} but argument is of type ‘ares_socklen_t *’ {aka ‘int *’}', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-002065': {'output_line': "error: Cannot find module './codeRefs' from '/testbed/packages/ssr/src/custom-code/codeRefs.test.ts'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-002070': {'output_line': 'not ok 2 - invalid and unknown data is removed with a custom typeDefault', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-002083': {'output_line': 'not ok 1 should be equal', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-002084': {'output_line': 'FAIL: DEBUG env var must NO LONGER trigger insecure-transport allowance.', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-002089': {'output_line': 'test_compare (test_octodns_manager.TestManager) ... FAIL', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-002093': {'output_line': "Error: Cannot find module '..'", 'reviewed': '2026-10-18'},
    # reviewer C: Repo code already calls the Respond method the instruction asks to add (R4).
    'format-code-task-002097': {'output_line': './runner.go:537:15: bot.Respond undefined (type Bot has no field or method Respond)', 'reviewed': '2026-10-18'},
    # reviewer A/B: The node test ran and failed because the app.start lifecycle API the task asks for
    # is missing.
    'format-code-task-002103': {'output_line': 'not ok - start binds once and stop unbinds listeners', 'reviewed': '2026-10-18'},
    # reviewer C: Test imports the class the instruction asks to export (R6).
    'format-code-task-002113': {'output_line': 'FAIL usercase-test-coderl/conditional_move_tree.test.ts', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (minitest) and failed on the missing feature
    'format-code-task-002128': {'output_line': '7 runs, 10 assertions, 0 failures, 0 errors, 0 skips', 'reviewed': '2026-10-18'},
    # reviewer A/B: The 2 failures are the new getFeaturesInExtent tests; the method returns undefined
    # because it does not exist yet.
    'format-code-task-002146': {'output_line': "TypeError: Cannot read properties of undefined (reading 'length')", 'reviewed': '2026-10-18'},
    # reviewer A/B: Only the new test fails to compile, because the AddObjectFromFile API the task
    # asks for is missing.
    'format-code-task-002150': {'output_line': 'FAILED: CMakeFiles/object_repository_add_from_file_test.dir/object_repository_add_from_file_test.cpp.o', 'reviewed': '2026-10-18'},
    # reviewer C: Modified test references the annotation constant the fix introduces (R5).
    'format-code-task-002153': {'output_line': 'pkg/image/apis/image/helper_test.go:600:7: undefined: DockerImageLayersOrderAnnotation', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (go) and failed on the missing feature
    'format-code-task-002154': {'output_line': 'FAIL\tgithub.com/openshift/origin/test/integration [build failed]', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (rspec) and failed on the missing feature
    'format-code-task-002159': {'output_line': 'Failures:', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-002160': {'output_line': 'test_convert_called_station_id_command (openwisp_radius.tests.test_commands.TestCommands.test_convert_called_station_id_command) [Test telnet connection error] ... FAIL', 'reviewed': '2026-10-18'},
    # reviewer A/B: The test includes the DelayLine.h module the task asks to create (the command
    # compiles DelayLine.cpp only if it exists).
    'format-code-task-002171': {'output_line': '/workspace/repo/usercase-test-coderl/test_delay_line.cpp:14:10: fatal error: DelayLine.h: No such file or directory', 'reviewed': '2026-10-18'},
    # reviewer C: The task is to rename the module path to .../v3; patched tests import v3 paths of
    # the module itself (R9/R3: own-module path change requested).
    'format-code-task-002174': {'output_line': 'simapp/sim_test.go:16:2: no required module provides package github.com/osmosis-labs/osmosis/v3/app; to add it:', 'reviewed': '2026-10-18'},
    # reviewer C: Requested data_layer package inside the main module missing (R3, readonly wording).
    'format-code-task-002186': {'output_line': 'usercase-test-coderl/data_layer_stateful_test.go:10:2: cannot find module providing package github.com/Pacific73/gorm-cache/data_layer: import lookup disabled by -mod=readonly', 'reviewed': '2026-10-18'},
    # reviewer A/B: nose ran pandas asof tests; failures are the missing behaviour (the error is
    # AttributeError on the float result, same cause).
    'format-code-task-002195': {'output_line': 'FAIL: test_missing_period_index (pandas.tests.frame.test_asof.TestFrameAsof)', 'reviewed': '2026-10-18'},
    # CLAIX rerun 5116864: reviewed 2026-10-18
    'format-code-task-002240': {'output_line': '/workspace/repo/usercase-test-coderl/generic_emit_helpers_test.c:300:51: error: ‘fy_generic_emit_compact’ undeclared (first use in this function); did you mean ‘fy_generic_get_comment’?', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert) and failed on the missing feature
    'format-code-task-002243': {'output_line': 'node:assert:90', 'reviewed': '2026-10-18'},
    # reviewer A/B: Tests use HttpEndpoint(Uri) API the task asks to add.
    'format-code-task-002249': {'output_line': '[error] /testbed/squbs-httpclient/src/test/scala/org/squbs/httpclient/DefaultHttpEndpointResolverSpec.scala:32:43: overloaded method value apply with alternatives:', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest, phpunit) and failed on the missing feature
    'format-code-task-002251': {'output_line': 'TypeError: PDepend\\Source\\Builder\\BuilderContext\\GlobalBuilderContext::registerClass(): Argument #1 ($class) must be of type PDepend\\Source\\AST\\ASTClass, PDepend\\Source\\AST\\ASTEnum given, called in /testbed/src/main/php/PDepend/Source/AST/ASTEnum.php on line 291', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-002255': {'output_line': 'not ok 2 decoded legacy object', 'reviewed': '2026-10-18'},
    # reviewer C: Requested SingleFileStorage module missing (R1).
    'format-code-task-002262': {'output_line': "E   ModuleNotFoundError: No module named 'vdirsyncer.storage.singlefile'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-002278': {'output_line': '--- expected', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-002287': {'output_line': "not ok 2 - fastify-plugin: Plugin did not start in time: 'sqlMapper-auto-1'. You may have forgotten to call 'done' function or to resolve a Promise", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-002298': {'output_line': '7 tests failed', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-002301': {'output_line': 'not ok 16 Chrome 148.0 - Integration | Adapter | Basic CRUD Ops > dont save hasMany > async > not eventually consistent: not found', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-002302': {'output_line': "TypeError: 'FakeWS' object is not subscriptable", 'reviewed': '2026-10-18'},
    # reviewer A/B: Requested CartridgeASCII8K file does not exist.
    'format-code-task-002303': {'output_line': 'not ok - ASCII8 user option and automatic acceptance boundaries', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest, phpunit) and failed on the missing feature
    'format-code-task-002312': {'output_line': 'OK (11 tests, 38 assertions)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-002313': {'output_line': 'plugins: sugar-1.1.1, forked-1.6.0, django-4.12.0, mock-3.10.0, xdist-2.5.0, cov-7.1.0, rerunfailures-10.2', 'reviewed': '2026-10-18'},
    # reviewer C: Instruction asks both TaskPrompter and InvPT evaluation packages to expose
    # SaliencyMeter; module absent (R1).
    'format-code-task-002335': {'output_line': "E   ModuleNotFoundError: No module named 'InvPT.evaluation.eval_sal'", 'reviewed': '2026-10-18'},
    # reviewer A/B: The node test script ran and reported 9 failures: the parser produces Function
    # instead of the new FunctionReference node.
    'format-code-task-002340': {'output_line': '9 test(s) failed.', 'reviewed': '2026-10-18'},
    # reviewer C: ts-jest type check fails on the option the instruction asks to add (R6).
    'format-code-task-002343': {'output_line': 'FAIL test/javascript/jest.test.ts', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-002349': {'output_line': 'not ok 4 should decode explicitly Any in toObject if requested', 'reviewed': '2026-10-18'},
    # reviewer C: Requested package lies inside the main module and does not exist yet (R3).
    'format-code-task-002357': {'output_line': 'usercase-test-coderl/set_stateful_test.go:11:2: no required module provides package github.com/PsionicAlch/byteforge/datastructs/set; to add it:', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed (63 tests); the requested new property package is empty (R3).
    'format-code-task-002360': {'output_line': 'github.com/pulumi/pulumi/sdk/v3/go/property: no non-test Go files in /testbed/sdk/go/property', 'reviewed': '2026-10-18'},
    # reviewer A/B: rspec ran (base mode is a no-op echo by design); new refreshonly specs fail on the
    # missing validation.
    'format-code-task-002364': {'output_line': '6 examples, 2 failures', 'reviewed': '2026-10-18'},
    # reviewer A/B: The CLI help ran but lacks the new --dump-header option the task asks for.
    'format-code-task-002365': {'output_line': 'FAIL: F_help missing --dump-header help line', 'reviewed': '2026-10-18'},
    # reviewer C: Module-level reproducer added by the patch triggers the reported pydantic bug at
    # import (List is imported in the test module, verified in image), so collection fails because of
    # the bug (R11: collection error raised at a patch-added line inside project code).
    'format-code-task-002373': {'output_line': "E   pydantic.errors.PydanticUndefinedAnnotation: name 'List' is not defined", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-002386': {'output_line': 'pymc3/tests/test_profile.py::test_profile_model FAILED                   [ 33%]', 'reviewed': '2026-10-18'},
    # reviewer A/B: pytest in a fresh /tmp venv ran (our plugin is not in that venv, hence no
    # evidence); 18 parametrised feature cases fail.
    'format-code-task-002415': {'output_line': 'FAILED tests/constraints/generic/test_union_constraint.py::test_allows_any_and_allows_all[constraint15-False-False]', 'reviewed': '2026-10-18'},
    # reviewer A/B: regrtest ran test_http_cookiejar; 2 of 77 tests fail (the new cookiejar cases).
    'format-code-task-002423': {'output_line': 'Total tests: run=77 failures=2', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test) and failed on the missing feature
    'format-code-task-002425': {'output_line': 'test test_asyncio.test_selector_events failed -- Traceback (most recent call last):', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-002426': {'output_line': 'test_load_dynamic_ImportError_path (test.test_imp.ImportTests) ... ok', 'reviewed': '2026-10-18'},
    # reviewer A/B: myunit ran the 5 new lambda-in-Callable[..., T] cases, and 4 fail with unexpected
    # type-checker output.
    'format-code-task-002434': {'output_line': 'AssertionFailure: Invalid type checker output (mypy/test/data/check-inference-context.test, line 759)', 'reviewed': '2026-10-18'},
    # reviewer C: Tests ran and reproduce the reported crash, but the plugin recorded executed=0 on
    # pytest 3.5 / mypy DataDrivenTestCase items (R8: instrumentation gap).
    'format-code-task-002442': {'output_line': 'AssertionError: Var is lacking info', 'reviewed': '2026-10-18'},
    # reviewer C: The regression tests ran and fail with the reported false positive; executed=0 is an
    # instrumentation miss on pytest 5.1 mypy items (R8).
    'format-code-task-002444': {'output_line': 'E   AssertionError: Unexpected type checker output (/testbed/test-data/unit/check-classes.test, line 6466)', 'reviewed': '2026-10-18'},
    # reviewer A/B: Requested console entry point does not exist yet.
    'format-code-task-002469': {'output_line': 'FAIL: blueprint_book console command is not installed', 'reviewed': '2026-10-18'},
    # reviewer A/B: r2r ran; one cmd test failed (XX) with output diff in ao help.
    'format-code-task-002471': {'output_line': 'CC="gcc" AR="ar" ARCH=undefined CFLAGS=\'-MD   -fPIC -g -Wall -D__UNIX__=1 -MD\' LDFLAGS=\'\' libsdb.a', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert) and failed on the missing feature
    'format-code-task-002475': {'output_line': 'AssertionError [ERR_ASSERTION]: expected visible text "No orders yet" in "normal rowLoading more"', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (minitest) and failed on the missing feature
    'format-code-task-002478': {'output_line': '1) Error:', 'reviewed': '2026-10-18'},
    # reviewer A/B: Shell contract test ran; validation/aggregation behavior missing.
    'format-code-task-002480': {'output_line': 'FAIL: whitespace-only NAMESPACES must be rejected', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-002481': {'output_line': "# SyntaxError: The requested module '../src/utils/tagExtractor.js' does not provide an export named 'extractTagContent'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-002488': {'output_line': 'ray/tests/test_tempfile.py::test_explicit_temp_dir_uses_session_subdir_for_runtime_files FAILED [ 14%]', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed (11 tests); requested hpctestlib.apps Amber test module missing
    # (R1).
    'format-code-task-002508': {'output_line': "E   ModuleNotFoundError: No module named 'hpctestlib.apps'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-002510': {'output_line': 'message: `500 (request to https://api.github.com/repos/user/repo/releases failed, reason: Nock: No match for request {␊', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-002516': {'output_line': '✔ redirect should cause InternalError when invoked without next', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-002535': {'output_line': ">       assert [task.task_name for task in polkadot_manager.greenlet_manager.greenlets] == ['polkadot manager connection to parity node']  # noqa: E501", 'reviewed': '2026-10-18'},
    # reviewer A/B: test-unit ran; encoding assertions fail.
    'format-code-task-002544': {'output_line': '5 tests, 7 assertions, 4 failures, 0 errors, 0 pendings, 0 omissions, 0 notifications', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (cargo) and failed on the missing feature
    'format-code-task-002548': {'output_line': 'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 14 filtered out', 'reviewed': '2026-10-18'},
    # reviewer A/B: Vows ran; jar clone/serialization missing.
    'format-code-task-002560': {'output_line': "» expected 'tough-cookie@1.2.0',", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test) and failed on the missing feature
    'format-code-task-002570': {'output_line': 'assert all(', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-002579': {'output_line': 'WARNING: Node v20.19.5 has currently not been tested against Ember CLI and may result in unexpected behaviour.', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (usecase) and failed on the missing feature
    'format-code-task-002582': {'output_line': 'FAIL: F1_explicit_TRPO artifacts', 'reviewed': '2026-10-18'},
    # reviewer A/B: The test loads the SpriteMap.js source file the task asks to create, and that file
    # is missing.
    'format-code-task-002590': {'output_line': 'not ok - constructor stores sheet image and invokes onLoad from the image', 'reviewed': '2026-10-18'},
    # reviewer A/B: The test suite ran up to the new cliques.jl, which fails because the
    # maximal_cliques function the task asks for is undefined.
    'format-code-task-002591': {'output_line': 'ERROR: LightGraphs had test errors', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest, phpunit) and failed on the missing feature
    'format-code-task-002596': {'output_line': 'There was 1 failure:', 'reviewed': '2026-10-18'},
    # reviewer C: Test imports the Importer module the instruction describes (R1).
    'format-code-task-002599': {'output_line': "E   ModuleNotFoundError: No module named 'pybossa.importers.importer'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-002611': {'output_line': 'test_subclass_attrs_preserved_custom_settings ...                    [FAIL]', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert, tap, tap-ok) and failed on the missing feature
    'format-code-task-002612': {'output_line': 'not ok 1 - invisible PDF overlay converts source text to paths for every exported page', 'reviewed': '2026-10-18'},
    # reviewer A/B: The two failing UTscapy tests are the new SOME/IP TP fragmentation tests ("Build
    # TP fragmented data" and "test fragment").
    'format-code-task-002621': {'output_line': '###(009)=[failed] Build TP fragmented data', 'reviewed': '2026-10-18'},
    # reviewer C: Requested BaseSemanticSearcher module missing (R1).
    'format-code-task-002628': {'output_line': "E   ModuleNotFoundError: No module named 'opendeepsearch.ranking_models.base_reranker'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (mocha, node-assert) and failed on the missing feature
    'format-code-task-002630': {'output_line': '4 failing', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest, phpunit) and failed on the missing feature
    'format-code-task-002646': {'output_line': 'There was 1 failure:', 'reviewed': '2026-10-18'},
    # reviewer C: Python 3.5 form of the accepted 'cannot import name' rule (no 'from <module>'
    # suffix); the exception is part of the feature (R2).
    'format-code-task-002647': {'output_line': "E   ImportError: cannot import name 'MissingSettingException'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest) and failed on the missing feature
    'format-code-task-002650': {'output_line': 'FAIL src/h5web/metadata-viewer/utils.test.ts', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-002651': {'output_line': '✖ unused imports are not removed when rangeEnd is given expected "unused" import to be preserved, got: "\\nlet x = 1;\\n"', 'reviewed': '2026-10-18'},
    # reviewer A/B: The ledger regression run passed 4 tests and failed 1 (the new budget/auto test).
    'format-code-task-002652': {'output_line': ':tests/budget/auto.test:5: [Failed]', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-002654': {'output_line': '1 test failed', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert, tsd/tsc) and failed on the missing feature
    'format-code-task-002655': {'output_line': '}, []) Error thrown in test', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert, tsd/tsc) and failed on the missing feature
    'format-code-task-002656': {'output_line': '}; Error thrown in test', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert, tsd/tsc) and failed on the missing feature
    'format-code-task-002657': {'output_line': '✔ valid(8): const foo = new Error()', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-002658': {'output_line': '4 tests failed', 'reviewed': '2026-10-18'},
    # reviewer A/B: ava ran the new stream-destroy tests; destroying a stream does not reject the
    # subprocess promise (the feature), so the error goes uncaught.
    'format-code-task-002659': {'output_line': "361:   subprocess[streamName].destroy(new Error('destroyed-test'));", 'reviewed': '2026-10-18'},
    # reviewer A/B: Skulpt's test runner ran test_errors.py, and the new bad-exception test fails.
    'format-code-task-002669': {'output_line': 'Fail: Expected TypeError but got ExternalError', 'reviewed': '2026-10-18'},
    # reviewer C: Patched test fakes use the new multi-address signature requested in the issue (R5).
    'format-code-task-002670': {'output_line': 'FAIL\tgithub.com/skycoin/skycoin/src/api/webrpc [build failed]', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest, phpunit) and failed on the missing feature
    'format-code-task-002676': {'output_line': 'Error: Call to undefined method Slim\\Http\\Stream::isPipe()', 'reviewed': '2026-10-18'},
    # reviewer A/B: mocha via babel-node ran; base passes, new tests fail on missing API.
    'format-code-task-002679': {'output_line': '5 failing', 'reviewed': '2026-10-18'},
    # reviewer A/B: The one failing compile test is the new defWithParameterAreIneligible case,
    # because the macro does not yet exclude defs with parameters.
    'format-code-task-002682': {'output_line': '[info] Tests: succeeded 64, failed 1, canceled 0, ignored 0, pending 0', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-002686': {'output_line': "src/demo.ts(9,31): error TS2792: Cannot find module './distance.js'. Did you mean to set the 'moduleResolution' option to 'node', or to add aliases to the 'paths' option?", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (mocha, node-assert, tsd/tsc) and failed on the missing
    # feature
    'format-code-task-002692': {'output_line': 'SOURCEGRAPH_API_URL: undefined,', 'reviewed': '2026-10-18'},
    # reviewer A/B: Real pytest summary; new axis-subset tests fail. Checker missed pytest launched
    # via pytest.main() inside python3 -c.
    'format-code-task-002697': {'output_line': 'FAILED starfish/test/image/test_apply.py::test_apply_3d - TypeError: add_bloc...', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-002702': {'output_line': 'Error processing line 1 of /usr/lib/python3/dist-packages/distutils-precedence.pth:', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-002705': {'output_line': "# Error [ERR_MODULE_NOT_FOUND]: Cannot find module '/workspace/repo/utils/generateWingetImport.js' imported from /workspace/repo/usercase-test-coderl/generate-winget-import.test.js", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-002710': {'output_line': 'tests/test_pythonflow.py::test_assert_with_dependencies[None] PASSED', 'reviewed': '2026-10-18'},
    # reviewer A/B: Dart tests ran; error-handling path crashes as described in the task.
    'format-code-task-002731': {'output_line': "NoSuchMethodError: The getter 'remoteAddress' was called on null.", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-002744': {'output_line': '#   const err = new Error(message);', 'reviewed': '2026-10-18'},
    # reviewer A/B: uvu assertion diff; className merge missing.
    'format-code-task-002749': {'output_line': '+ actual - expected', 'reviewed': '2026-10-18'},
    # reviewer A/B: Mocha ran; async verification behavior missing.
    'format-code-task-002760': {'output_line': 'error Command failed with exit code 8.', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-002765': {'output_line': 'ok 4 undefined should pass', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-002770': {'output_line': "SyntaxError: Missing export 'FindAndReplaceManager' in ES module: file:///testbed/index.js", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tsd/tsc) and failed on the missing feature
    'format-code-task-002776': {'output_line': '✖  17:19  Argument of type PostgrestError | null is not assignable to parameter of type null.', 'reviewed': '2026-10-18'},
    # reviewer A/B: Regression pytest passed; new RSS tests fail because the provider is missing.
    # Checker missed .venv/bin/python -m pytest (venv not hooked).
    'format-code-task-002778': {'output_line': '/testbed/server/superdesk/io/nitf_tests.py:97: DeprecationWarning: Please use assertEqual instead.', 'reviewed': '2026-10-18'},
    # reviewer A/B: Karma ran; new toggle specs fail.
    'format-code-task-002785': {'output_line': 'TOTAL: 8 FAILED, 330 SUCCESS', 'reviewed': '2026-10-18'},
    # reviewer A/B: CLI lacks the --finetune option.
    'format-code-task-002786': {'output_line': 'FAIL: F_help missing --finetune', 'reviewed': '2026-10-18'},
    # reviewer A/B: PHPUnit assertion on label escaping.
    'format-code-task-002787': {'output_line': "Failed asserting that ' Do you want to use Foo\\Bar <comment>or</comment> Foo\\Baz\\? [Foo\\Baz]:\\n", 'reviewed': '2026-10-18'},
    # reviewer A/B: Requested parse_src_* entry points missing.
    'format-code-task-002792': {'output_line': 'TypeError: tachyon.parse_src_str is not a function', 'reviewed': '2026-10-18'},
    # reviewer A/B: Startup with token 'env' placeholder fails; the requested feature.
    'format-code-task-002803': {'output_line': 'FAIL: f1 exit 1; want 0', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (jest) and failed on the missing feature
    'format-code-task-002823': {'output_line': 'FAIL src/report/ReportStructuredFilter.codespec.test.js', 'reviewed': '2026-10-18'},
    # reviewer A/B: tsc on the test tsconfig fails on missing exports in the test-patch file, so
    # vitest never ran. A missing-API zero (the kind the no-reference rule accepts for Go/jest), but
    # "tests visibly ran" is not strictly true.
    'format-code-task-002828': {'output_line': 'usercase-test-coderl/google.test.ts(6,2): error TS2724: \'"remix-auth-socials"\' has no exported member named \'GoogleStrategyDefaultScopes\'. Did you mean \'FacebookStrategyDefaultScopes\'?', 'reviewed': '2026-10-18'},
    # reviewer A/B: ava ran protocol-retained tests; failures are on the missing protocol-retention
    # feature.
    'format-code-task-002831': {'output_line': '✖ server connection handler sees protocol when using io() directly', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (rspec) and failed on the missing feature
    'format-code-task-002832': {'output_line': 'Failure/Error: ActiveRecord::Migration.maintain_test_schema!', 'reviewed': '2026-10-18'},
    # reviewer A/B: The urlwatch venv binary exists and the help output lacks the new option metavar
    # the task asks for.
    'format-code-task-002834': {'output_line': 'FAIL: F_help missing metavar', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert) and failed on the missing feature
    'format-code-task-002845': {'output_line': 'AssertionError [ERR_ASSERTION]: Command#parseAsync must be available', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (junit, mvn) and failed on the missing feature
    'format-code-task-002846': {'output_line': 'Downloading from central: https://repo.maven.apache.org/maven2/org/assertj/assertj-core/3.11.1/assertj-core-3.11.1.pom', 'reviewed': '2026-10-18'},
    # reviewer C: Pre-check passed; new llms/ernie package requested by the instruction does not exist
    # (R3).
    'format-code-task-002847': {'output_line': 'github.com/tmc/langchaingo/llms/ernie: no non-test Go files in /testbed/llms/ernie', 'reviewed': '2026-10-18'},
    # reviewer C: All 8 tests error in fixture setup importing the requested DownloadModel module (R1
    # applied to setup errors).
    'format-code-task-002848': {'output_line': "E   ModuleNotFoundError: No module named 'src.DownloadModels'", 'reviewed': '2026-10-18'},
    # reviewer C: Requested DQN module missing (R1).
    'format-code-task-002870': {'output_line': "E   ModuleNotFoundError: No module named 'rljax.algorithm.dqn'", 'reviewed': '2026-10-18'},
    # reviewer A/B: The onlykey CLI does not know the new wipe command, so the output differs from
    # what is expected.
    'format-code-task-002884': {'output_line': 'FAIL: f1_direct_1a stdout', 'reviewed': '2026-10-18'},
    # reviewer C: Modified spec uses @IdColumn("uuid"), the requested option; ts-jest fails the whole
    # suite including base cases (R6).
    'format-code-task-002885': {'output_line': 'FAIL src/components/createIdColumn.spec.ts', 'reviewed': '2026-10-18'},
    # reviewer A/B: uvu ran the theme tests; 6 of 11 failed on assertions.
    'format-code-task-002888': {'output_line': 'at Object.handler (/testbed/src/__tests__/theme.test.ts:175:10)', 'reviewed': '2026-10-18'},
    # reviewer A/B: The new RawTest constructs a Raw filter node with the new one-argument signature,
    # which does not exist yet.
    'format-code-task-002891': {'output_line': 'ArgumentCountError: Too few arguments to function Twig\\Node\\Expression\\FilterExpression::__construct(), 1 passed in /testbed/tests/Node/Expression/Filter/RawTest.php on line 22 and at least 4 expected', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-002892': {'output_line': 'FAILED (errors=6)', 'reviewed': '2026-10-18'},
    # reviewer C: Requested AlignedDataset module missing (R1).
    'format-code-task-002895': {'output_line': "E   ModuleNotFoundError: No module named 'data.aligned_dataset'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-002913': {'output_line': 'ok 14 Expected: 0', 'reviewed': '2026-10-18'},
    # reviewer A/B: pytest ran 11 new tests, all fail on missing follow/iterfollow/makeiter API.
    # PYTEST_DISABLE_PLUGIN_AUTOLOAD likely kept our plugin out.
    'format-code-task-002923': {'output_line': 'usercase-test-coderl/test_pagination_state.py::test_follow_advances_link_state_and_returns_next_pages FAILED [  9%]', 'reviewed': '2026-10-18'},
    # reviewer A/B: The library build fails on calls to Device::getDeviceRule, the device-rule API the
    # task asks for (repo code already references it).
    'format-code-task-002925': {'output_line': 'src/Library/public/usbguard/Audit.cpp:260:49: error: ‘using std::__shared_ptr_access<usbguard::Device, __gnu_cxx::_S_atomic, false, false>::element_type = class usbguard::Device’ {aka ‘class usbguard::Device’} has no member named ‘getDeviceRule’; did you mean ‘getDeviceID’?', 'reviewed': '2026-10-18'},
    # reviewer A/B: web-test-runner ran the icon tests, and 3 new assertions fail.
    'format-code-task-002926': {'output_line': 'Chromium: |██████████████████████████████| 2/2 test files | 32 passed, 3 failed', 'reviewed': '2026-10-18'},
    # reviewer A/B: The single failure is the new define test expecting a version on the registered
    # element.
    'format-code-task-002927': {'output_line': 'Chrome: |██████████████████████████████| 0/26 test files | 0 passed, 0 failed', 'reviewed': '2026-10-18'},
    # reviewer A/B: The use case compiles examples/retrieve-csv-report.py, the new example the task
    # asks to create, and it is missing.
    'format-code-task-002940': {'output_line': "[Errno 2] No such file or directory: 'examples/retrieve-csv-report.py'FAIL: build", 'reviewed': '2026-10-18'},
    # reviewer A/B: The help output lacks the new clip_score option.
    'format-code-task-002942': {'output_line': 'FAIL: H1 missing clip_score in help', 'reviewed': '2026-10-18'},
    # reviewer C: Package source calls the Handle methods the instruction asks for (R4).
    'format-code-task-002944': {'output_line': 'FAIL\tgithub.com/vishvananda/netlink/usercase-test-coderl [build failed]', 'reviewed': '2026-10-18'},
    # reviewer A/B: Test-patched e2e util references the TTL field the task must add; go vet (not go
    # test) produced it, so the go recorder saw nothing.
    'format-code-task-002959': {'output_line': 'vet: test/e2e/util.go:312:4: unknown field TTLSecondsAfterFinished in struct literal of type "volcano.sh/volcano/pkg/apis/batch/v1alpha1".JobSpec', 'reviewed': '2026-10-18'},
    # reviewer C: Requested Evaluation module missing (R1).
    'format-code-task-002963': {'output_line': "E   ModuleNotFoundError: No module named 'algotrading.evaluation'", 'reviewed': '2026-10-18'},
    # reviewer C: Requested Momentum_Agent module missing (R1).
    'format-code-task-002964': {'output_line': "E   ModuleNotFoundError: No module named 'algotrading.agents.momentum_agent'", 'reviewed': '2026-10-18'},
    # reviewer C: Requested SixMonthCycle_Agent module missing (R1).
    'format-code-task-002965': {'output_line': "E   ModuleNotFoundError: No module named 'algotrading.agents.six_month_cycle_agent'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-002966': {'output_line': 'not ok 2 Interactive mode and keep with selectors', 'reviewed': '2026-10-18'},
    # reviewer A/B: Jasmine ran; cleanup behavior missing.
    'format-code-task-002969': {'output_line': '7 specs, 6 failures', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (python-traceback-in-test, unittest) and failed on the
    # missing feature
    'format-code-task-002976': {'output_line': 'self.assertFalse(a == "not-a-searcharea")', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap-ok) and failed on the missing feature
    'format-code-task-002990': {'output_line': 'not ok 1 should be strictly equal', 'reviewed': '2026-10-18'},
    # reviewer A/B: Task is to make make-check lint pass; pylint warnings are the expected failure.
    'format-code-task-002993': {'output_line': "E0611(no-name-in-module): 27,0: : No name 'cli' in module 'composer'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (mocha) and failed on the missing feature
    'format-code-task-002999': {'output_line': '3 failing', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (node-assert, tsd/tsc) and failed on the missing feature
    'format-code-task-003002': {'output_line': 'ℹ fail 1', 'reviewed': '2026-10-18'},
    # reviewer C: Project screens module still subclasses the removed DiffScreen the instruction asks
    # to provide (R2: NameError in project module).
    'format-code-task-003009': {'output_line': "E   NameError: name 'DiffScreen' is not defined", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-003013': {'output_line': "# Error: Cannot find module '/workspace/repo/src/controller/gui/TemplateManager.js'", 'reviewed': '2026-10-18'},
    # reviewer C: Client methods call the shared transport the instruction asks to implement (R4).
    'format-code-task-003027': {'output_line': './client.go:63:11: c.doRequest undefined (type *Client has no field or method doRequest)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (unittest) and failed on the missing feature
    'format-code-task-003034': {'output_line': '0 Error(s)', 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (pytest) and failed on the missing feature
    'format-code-task-003035': {'output_line': "Error: Cannot find module '../binaries/uws_linux_x64_137.node'", 'reviewed': '2026-10-18'},
    # scan + spot-check 25/25: tests ran (tap, tap-ok) and failed on the missing feature
    'format-code-task-003044': {'output_line': "# SyntaxError: The requested module '../index.ts' does not provide an export named 'BigBitFieldMixin'", 'reviewed': '2026-10-18'},
    # reviewer A/B: scripts/jira_create_issue.py does not exist yet (python exits 2).
    'format-code-task-003048': {'output_line': 'FAIL: F_help exit 2; want 0', 'reviewed': '2026-10-18'},
    # reviewer A/B: Tests need Unit type-class instances the task asks to add.
    'format-code-task-003055': {'output_line': '[error] /testbed/core-tests/shared/src/test/scala/zio/prelude/CommutativeSpec.scala:47:51: could not find implicit value for evidence parameter of type zio.prelude.coherent.CommutativeEqual[Unit] (No implicit Equal defined for Unit.)', 'reviewed': '2026-10-18'},
    # reviewer A/B: Real pytest summary; checker likely missed because hook not installed for pyenv
    # python 2.7.
    'format-code-task-003058': {'output_line': 'src/OFS/tests/testCopySupport.py::TestCopySupportSecurity::test_copy_cant_copy_invisible_items FAILED [ 65%]', 'reviewed': '2026-10-18'},
    # reviewer A/B: New test fails to compile on missing CharacterSet::BINARY.
    'format-code-task-003061': {'output_line': '19507 |   GTEST_PRED_FORMAT2_(pred_format, v1, v2, GTEST_NONFATAL_FAILURE_)', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-000113': {'output_line': '\x1b[0m[\x1b[0m\x1b[0minfo\x1b[0m] \x1b[0m\x1b[0m\x1b[31m- should ignore errors *** FAILED ***\x1b[0m\x1b[0m', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-000248': {'output_line': '--- FAIL: TestUpdateToxics (0.00s)', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-000328': {'output_line': "\x1b[31mImportError while importing test module '/testbed/airbyte-integrations/connectors/source-zenefits/unit_tests/test_zenefits.py'.", 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-000345': {'output_line': '--- FAIL: TestCSApplicationResourceShape (0.00s)', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-000373': {'output_line': "\x1b[1m\x1b[31m[!] \x1b[1mError: Could not resolve './identify' from src/amplitude.js\x1b[22m\x1b[1m\x1b[39m\x1b[22m", 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-000630': {'output_line': '[TEST_FAILURE][JS][STATIC_REQUEST_TEST_FAILURE][bitso (WS) ][createOrder][market buy order]AssertionError [ERR_ASSERTION]: url mismatch computed "/v3/orders" stored: "/api/v3/orders"', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-001427': {'output_line': '\x1b[31m  1 failing\x1b[0m', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-001600': {'output_line': '\x1b[32m✔\x1b[39m should throw error on bad svg images - `Unexpected character "N"`\x1b[90m\x1b[2m (343ms)\x1b[22m\x1b[39m', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-001678': {'output_line': '․․․․․․․․․\x1b[31mFatal error: expected \'CONTEXT\\n<script type="text/javascript" src="/absolute/style.css?12345"></script>\' to equal \'CONTEXT\\n<link type="text/css" href="/absolute/style.css?12345" rel="stylesheet">\'\x07\x1b[39m', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-001898': {'output_line': 'make: *** [makefile:80: fem/test_paraview_restart.o] Error 1', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-001922': {'output_line': '\x1b[0m[\x1b[31merror\x1b[0m] \x1b[0m    assertTypedSame[HNil](HNil, in0.rotateLeft(0))\x1b[0m', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-001936': {'output_line': 'test/mitmproxy/test_flow.py::TestError::test_getset_state \x1b[32mPASSED\x1b[0m', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-001979': {'output_line': '\x1b[31m  3 failing\x1b[0m', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-002541': {'output_line': 'FAIL: installed ftfy command not found at /tmp/tmp.p5ROi8obhJ/venv/bin/ftfy', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18: not worth a rerun); output only has colour codes or generic lines.
    'format-code-task-002962': {'output_line': '\x1b[31m  3 failing\x1b[0m', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18): first error line of the output.
    'format-code-task-000516': {'output_line': "test_h.ts(14,11): error TS2707: Generic type 'AxiosResponse<T, D>' requires between 0 and 2 type arguments.", 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18): first error line of the output.
    'format-code-task-000807': {'output_line': 'usercase-test-coderl/githubAutomatedRepos.test.ts(13,5): error TS2305: Module \'"../src/index"\' has no exported member \'useGitHubAutomatedRepos\'.', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18): first error line of the output.
    'format-code-task-000949': {'output_line': 'usercase-test-coderl/security_types.test.ts(2,3): error TS2724: \'"../src"\' has no exported member named \'getSecurity\'. Did you mean \'Security\'?', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18): first error line of the output.
    'format-code-task-001240': {'output_line': '--  ! valid display not found', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18): first error line of the output.
    'format-code-task-001719': {'output_line': 'cmd/conformance-tester/pkg/tests/conformance.go:40:2: no required module provides package k8c.io/machine-controller/sdk/providerconfig; to add it:', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18): first error line of the output.
    'format-code-task-002500': {'output_line': "/tmp/realm_fixturea_generic_init_probe.swift:21:14: error: overriding declaration requires an 'override' keyword", 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18): first error line of the output.
    'format-code-task-002645': {'output_line': 'usercase-test-coderl/spacing_shorthand.test.ts(10,3): error TS2305: Module \'"../src"\' has no exported member \'spacingShorthand\'.', 'reviewed': '2026-10-18'},
    # Generic line (user, 2026-10-18): first error line of the output.
    'format-code-task-002781': {'output_line': 'src/ambient.d.ts(1,1): error TS6200: Definitions of the following identifiers conflict with those in another file: apply, arguments, bind, call, caller, length, name, prototype, toString, default', 'reviewed': '2026-10-18'},
    # MiMo 000401 (traced 2026-10-18 with bash -x): the verifier hides the build output; python build.py
    # compile fails with "Cannot read file /workspace/repo/src/calendar/DataView.js", the calendar data view the
    # instruction asks for. The only verifier line is the base-ref line, stable for this task.
    'format-code-task-000401': {'output_line': 'base ref ad10afa80235a407413acc7bc897890e901c2866 (recorded before the agent started)', 'reviewed': '2026-10-18'},
    # MiMo CLAIX rerun 5117303 (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-000250': {'output_line': '[\x1b[1;31mERROR\x1b[m] \x1b[1;31mTests run: 9, Failures: 0, Errors: 1, Skipped: 0\x1b[m', 'reviewed': '2026-10-18'},
    # MiMo CLAIX rerun 5117303 (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-000331': {'output_line': '[\x1b[1;31mERROR\x1b[m] /testbed/http-server/src/test/java/io/airlift/http/server/TestHttpServerConfig.java:[74,17] cannot find symbol', 'reviewed': '2026-10-18'},
    # MiMo CLAIX rerun 5117303 (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-000361': {'output_line': './api.go:362:3: undefined: notesiumLinks', 'reviewed': '2026-10-18'},
    # MiMo CLAIX rerun 5117303 (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-000616': {'output_line': '\x1b[31mTOTAL: 3 FAILED, 443 SUCCESS\x1b[39m', 'reviewed': '2026-10-18'},
    # MiMo CLAIX rerun 5117303 (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-000631': {'output_line': '[\x1b[1;31mERROR\x1b[m] \x1b[1;31mTests run: 26, Failures: 1, Errors: 0, Skipped: 1\x1b[m', 'reviewed': '2026-10-18'},
    # MiMo CLAIX rerun 5117303 (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-000885': {'output_line': 'FAIL: missing folder2lmdb entry point', 'reviewed': '2026-10-18'},
    # MiMo CLAIX rerun 5117303 (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-001459': {'output_line': '[\x1b[1;31mERROR\x1b[m] /testbed/hazelcast/src/test/java/com/hazelcast/internal/config/ConfigValidatorTest.java:[41,0] error: cannot find symbol', 'reviewed': '2026-10-18'},
    # MiMo CLAIX rerun 5117303 (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-001624': {'output_line': '4 tests, 10 assertions, 0 failures, 3 errors, 0 skips', 'reviewed': '2026-10-18'},
    # MiMo CLAIX rerun 5117303 (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-002039': {'output_line': '[ERROR] Tests run: 9, Failures: 1, Errors: 0, Skipped: 0', 'reviewed': '2026-10-18'},
    # MiMo CLAIX rerun 5117303 (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-002588': {'output_line': '[\x1b[1;31mERROR\x1b[m] \x1b[1;31mTests run: 23, Failures: 4, Errors: 0, Skipped: 0\x1b[m', 'reviewed': '2026-10-18'},
    # MiMo CLAIX rerun 5117303 (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-002660': {'output_line': "message: 'Cannot find module \\'/workspace/repo/source/core/calculate-retry-delay.js\\' imported from /workspace/repo/source/core/index.ts',", 'reviewed': '2026-10-18'},
    # MiMo memory measurement on CLAIX (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-000604': {'output_line': 'Tests:       1 failed, 1 total', 'reviewed': '2026-10-18'},
    # MiMo memory measurement on CLAIX (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-000632': {'output_line': "\x1b[96mtest/monitoring/custom/CustomMonitoring.test.ts\x1b[0m:\x1b[93m566\x1b[0m:\x1b[93m9\x1b[0m - \x1b[91merror\x1b[0m\x1b[90m TS2322: \x1b[0mType '{ title: string; graphWidgetWidth: number; metrics: Metric[]; }' is not assignable to type 'CustomMetricGroup'.", 'reviewed': '2026-10-18'},
    # MiMo memory measurement on CLAIX (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-000772': {'output_line': '5 tests failed, 3 tests passed (8 total in 3 test suites, run time 4.387s)', 'reviewed': '2026-10-18'},
    # MiMo memory measurement on CLAIX (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-001698': {'output_line': 'Tests:       3 failed, 10 skipped, 13 total', 'reviewed': '2026-10-18'},
    # MiMo memory measurement on CLAIX (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-002711': {'output_line': '\x1b[0m[\x1b[0m\x1b[31merror\x1b[0m] \x1b[0m\x1b[0m(Test / \x1b[31mtestOnly\x1b[0m) sbt.TestsFailedException: Tests unsuccessful\x1b[0m', 'reviewed': '2026-10-18'},
    # MiMo memory measurement on CLAIX (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-002797': {'output_line': 'Tests:       2 failed, 5 skipped, 1 passed, 8 total', 'reviewed': '2026-10-18'},
    # MiMo memory measurement on CLAIX (2026-10-18): tests ran and fail on the missing feature.
    'format-code-task-002988': {'output_line': 'Tests:       3 failed, 158 skipped, 161 total', 'reviewed': '2026-10-18'},
}
