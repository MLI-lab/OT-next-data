"""Apply reviewed instruction and verifier repairs to pinned Scale-SWE tasks."""
from __future__ import annotations

# Support both direct execution and python -m data.<source>.patch.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


import argparse
import copy
import hashlib
import io
import inspect
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

import pyarrow as pa
import pyarrow.parquet as pq

from data.utils.patch_reporting import write_patch_report
from data.utils.resolve_absolute_paths import apply_reviewed_replacements, collect_reports
from validation.contract import files_digest

REVISION = '8935f8e55244fd56080cdb8dcd0819a57e8a003c'
HERE = Path(__file__).resolve().parent

APPIUM_IMPORT_TASKS = frozenset({
    'appium_python-client_pr446',
    'appium_python-client_pr521',
    'appium_python-client_pr594',
})

VERIFIER_IMPORT_TASKS = {
    **{task: ('python-client', 'Expose Appium repository test helpers when grading through /tests/score.py.')
       for task in APPIUM_IMPORT_TASKS},
    'dbcli_pgcli_pr942': ('pgcli', 'Expose the repository root so restored tests.utils imports resolve after the reference removes tests/__init__.py.'),
    'sbdchd_flake8-pie_pr93': ('flake8-pie', 'Import flake8_pie from the task checkout instead of the installed package, which lacks the selected feature modules.'),
    **{f'sbdchd_flake8-pie_pr{pr}': ('flake8-pie', 'Import flake8_pie from the task checkout: the image installs a non-editable copy of a later commit in site-packages, so the unchanged checkout also scored 1 under no-op.')
       for pr in (40, 42, 44, 54)},
    'joseph-roitman_pytest-snapshot_pr30': ('pytest-snapshot', 'Load the pytest_snapshot plugin from the task checkout: the image installs a non-editable copy of a later commit in site-packages, so the unchanged checkout also scored 1 under no-op.'),
    # No-op reward 1 in the 2026-10-08 full run: the selected tests imported the package installed in
    # site-packages instead of the task checkout, so the unchanged checkout passed. Confirmed by the
    # 2026-10-10 rerun (job 963978): with the repository root first on PYTHONPATH every reference scores 1
    # and every no-op scores 0.
    'ethereum_eth-utils_pr78': ('eth-utils', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'ethereum_eth-utils_pr84': ('eth-utils', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'hussein-awala_spark-on-k8s_pr1': ('spark-on-k8s', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'hussein-awala_spark-on-k8s_pr11': ('spark-on-k8s', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'hussein-awala_spark-on-k8s_pr12': ('spark-on-k8s', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'hussein-awala_spark-on-k8s_pr13': ('spark-on-k8s', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'joseph-roitman_pytest-snapshot_pr10': ('pytest-snapshot', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'joseph-roitman_pytest-snapshot_pr13': ('pytest-snapshot', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'joseph-roitman_pytest-snapshot_pr32': ('pytest-snapshot', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'joseph-roitman_pytest-snapshot_pr34': ('pytest-snapshot', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'joseph-roitman_pytest-snapshot_pr36': ('pytest-snapshot', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'lasp_cdflib_pr38': ('cdflib', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'lasp_cdflib_pr49': ('cdflib', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'onnx_tensorflow-onnx_pr832': ('tensorflow-onnx', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'onnx_tensorflow-onnx_pr839': ('tensorflow-onnx', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'onnx_tensorflow-onnx_pr850': ('tensorflow-onnx', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'onnx_tensorflow-onnx_pr867': ('tensorflow-onnx', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'onnx_tensorflow-onnx_pr882': ('tensorflow-onnx', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'onnx_tensorflow-onnx_pr949': ('tensorflow-onnx', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'onnx_tensorflow-onnx_pr968': ('tensorflow-onnx', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'onnx_tensorflow-onnx_pr974': ('tensorflow-onnx', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'pytest-dev_pytest-bdd_pr335': ('pytest-bdd', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'pytest-dev_pytest-bdd_pr398': ('pytest-bdd', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'pytransitions_transitions_pr73': ('transitions', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    's4v4g3_otel-extensions-python_pr4': ('otel-extensions-python', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
    'stephrdev_pytest-isort_pr40': ('pytest-isort', 'Import the package under test from the task checkout instead of the installed site-packages copy, which made the unchanged checkout score 1 under no-op.'),
}

# Tasks archived after the 2026-10-10 recheck (reference, no-op and environment evidence per task); see archived_tasks.json.
RECHECK_ARCHIVE = json.loads((HERE / 'archived_tasks.json').read_text()) if (HERE / 'archived_tasks.json').is_file() else {}

ARCHIVED_TASKS = {
    **{f'matan1008_pykdebugparser_pr{pr}': {
        'category': 'verifier-execution-error',
        'reason': 'Reference and no-op cannot collect tests because the repository is absent from the verifier import path. A diagnostic import-path repair exposes a missing construct dependency in the test interpreter, still preventing execution in both modes. Archived without dependency repair at user request; no reference implementation defect has been established.'}
       for pr in (5, 6)},
    'biocommons_hgvs_pr699': {
        'category': 'verifier-external-dependency',
        'reason': 'The reference verifier requires a connection to the external UTA database at uta.biocommons.org:5432. The service timed out during validation, so test results depend on an unavailable external database. Archived at user request because this verifier dependency is not reliable.'},
    'biocommons_hgvs_pr775': {
        'category': 'verifier-external-dependency',
        'reason': 'The reference verifier requires a connection to the external UTA database at uta.biocommons.org:5432. The service timed out during validation, so test results depend on an unavailable external database. Archived at user request because this verifier dependency is not reliable.'},
    'kurtbrose_pyjks_pr19': {
        'category': 'reference-solution-reward-zero',
        'reason': 'Reference scores 0 because verifier baseline restoration deletes the new tests/expected/custom_entry_passwords.py helper while leaving its import in tests/expected/__init__.py. Pytest fails during collection before executing tests. This is a verifier preparation defect, not evidence of a broken reference implementation. Archived without repair at user request.'},
    'turner-townsend_flask-pydantic-spec_pr78': {
        'category': 'verifier-execution-error',
        'reason': 'Verifier test patch omits tests/common.py defining DemoModelV1, causing no-op collection failure. Reference executes 38 passing tests but scores zero because expected unparameterized selectors do not match parameterized results. Archived without repair at user request.'},
    'meerk40t_svgelements_pr24': {
        'category': 'verifier-execution-error',
        'reason': 'Verifier test patch deletes test/test_distance.py and moves tests to test/test_length.py, but selected test IDs still reference the deleted file. Reference and no-op both execute zero tests. Archived without repair at user request.'},
}
ARCHIVED_TASKS = {**RECHECK_ARCHIVE, **ARCHIVED_TASKS}

# Reference failures from the 2026-10-08 full run whose gold patch adds or modifies test-support files that the
# verifier reset discards (2026-10-10 static scan of gold.patch against f2p.patch). The restoration repair applies
# only to these tasks and to TEST_FIXTURE_REPAIRS; previously validated tasks keep their verifiers unchanged.
FIXTURE_RESTORATION_TASKS = frozenset({
    'appium_python-client_pr531',
    'ariebovenberg_slotscheck_pr93',
    'audreyr_cookiecutter_pr1419',
    'audreyr_cookiecutter_pr1468',
    'audreyr_cookiecutter_pr1874',
    'audreyr_cookiecutter_pr1981',
    'audreyr_cookiecutter_pr1995',
    'audreyr_cookiecutter_pr839',
    'bottlepy_bottle_pr787',
    'burnash_gspread_pr1010',
    'burnash_gspread_pr1012',
    'burnash_gspread_pr1013',
    'burnash_gspread_pr1021',
    'burnash_gspread_pr1030',
    'burnash_gspread_pr1063',
    'burnash_gspread_pr1095',
    'burnash_gspread_pr1138',
    'burnash_gspread_pr1207',
    'burnash_gspread_pr1215',
    'burnash_gspread_pr1225',
    'burnash_gspread_pr1233',
    'burnash_gspread_pr1299',
    'burnash_gspread_pr1305',
    'burnash_gspread_pr1308',
    'burnash_gspread_pr1364',
    'burnash_gspread_pr1392',
    'burnash_gspread_pr1402',
    'burnash_gspread_pr1404',
    'burnash_gspread_pr1417',
    'burnash_gspread_pr1498',
    'burnash_gspread_pr945',
    'businho_pytest-ruff_pr25',
    'clemense_yourdfpy_pr4',
    'clld_clldutils_pr93',
    'cloudtools_troposphere_pr2069',
    'cookiecutter_cookiecutter_pr1200',
    'cookiecutter_cookiecutter_pr1468',
    'cookiecutter_cookiecutter_pr1493',
    'cookiecutter_cookiecutter_pr1981',
    'cookiecutter_cookiecutter_pr1995',
    'dantebben_nox-uv_pr4',
    'dapr_python-sdk_pr89',
    'drgarcia1986_simple-settings_pr11',
    'drgarcia1986_simple-settings_pr8',
    'eerkunt_terraform-compliance_pr352',
    'eerkunt_terraform-compliance_pr448',
    'eerkunt_terraform-compliance_pr563',
    'executablebooks_myst-nb_pr604',
    'executablebooks_sphinx-design_pr13',
    'fonttools_fonttools_pr1970',
    'fonttools_fonttools_pr2080',
    'fonttools_fonttools_pr3912',
    'fortran-lang_fortls_pr49',
    'globus_globus-sdk-python_pr1179',
    'globus_globus-sdk-python_pr903',
    'google_flatbuffers_pr5631',
    'googleapis_proto-plus-python_pr112',
    'googleapis_proto-plus-python_pr118',
    'googlefonts_ufo2ft_pr866',
    'googlefonts_ufo2ft_pr870',
    'gorakhargosh_watchdog_pr968',
    'hadialqattan_pycln_pr120',
    'hakancelikdev_unimport_pr289',
    'hhatto_autopep8_pr288',
    'iterative_gto_pr148',
    'iterative_gto_pr309',
    'jackdewinter_pymarkdown_pr1413',
    'jackdewinter_pymarkdown_pr1414',
    'jackdewinter_pymarkdown_pr279',
    'jackdewinter_pymarkdown_pr280',
    'jackdewinter_pymarkdown_pr455',
    'jackdewinter_pymarkdown_pr559',
    'jackdewinter_pymarkdown_pr636',
    'jackdewinter_pymarkdown_pr80',
    'jsh9_pydoclint_pr155',
    'jsh9_pydoclint_pr188',
    'jsh9_pydoclint_pr192',
    'jsh9_pydoclint_pr199',
    'jsh9_pydoclint_pr205',
    'jsh9_pydoclint_pr206',
    'jsh9_pydoclint_pr207',
    'jsh9_pydoclint_pr225',
    'jsh9_pydoclint_pr227',
    'jsh9_pydoclint_pr230',
    'jsh9_pydoclint_pr259',
    'jsh9_pydoclint_pr260',
    'jsh9_pydoclint_pr271',
    'jsh9_pydoclint_pr277',
    'jxtech_wechatpy_pr328',
    'jxtech_wechatpy_pr333',
    'jxtech_wechatpy_pr66',
    'jxtech_wechatpy_pr69',
    'jxtech_wechatpy_pr74',
    'jxtech_wechatpy_pr94',
    'keboola_python-component_pr63',
    'localstack_plux_pr28',
    'ludeeus_awesomeversion_pr290',
    'mesonbuild_meson-python_pr177',
    'mesonbuild_meson-python_pr467',
    'mesonbuild_meson-python_pr569',
    'mesonbuild_meson-python_pr96',
    'mitmproxy_mitmproxy_pr4860',
    'neovim_python-client_pr534',
    'olofk_fusesoc_pr711',
    'p1c2u_jsonschema-spec_pr6',
    'pdm-project_pdm-backend_pr105',
    'pepkit_peppy_pr489',
    'pepkit_peppy_pr495',
    'praw-dev_praw_pr563',
    'praw-dev_praw_pr575',
    'praw-dev_praw_pr602',
    'praw-dev_praw_pr612',
    'praw-dev_prawcore_pr112',
    'praw-dev_prawcore_pr116',
    'praw-dev_prawcore_pr119',
    'praw-dev_prawcore_pr13',
    'praw-dev_prawcore_pr15',
    'praw-dev_prawcore_pr206',
    'praw-dev_prawcore_pr25',
    'praw-dev_prawcore_pr26',
    'praw-dev_prawcore_pr32',
    'praw-dev_prawcore_pr4',
    'praw-dev_prawcore_pr42',
    'praw-dev_prawcore_pr57',
    'praw-dev_prawcore_pr59',
    'praw-dev_prawcore_pr61',
    'praw-dev_prawcore_pr62',
    'praw-dev_prawcore_pr66',
    'pydantic_bump-pydantic_pr90',
    'pyinfra-dev_pyinfra_pr192',
    'pyinfra-dev_pyinfra_pr194',
    'pyinfra-dev_pyinfra_pr196',
    'pypa_pyproject-metadata_pr206',
    'pypa_pyproject-metadata_pr232',
    'pypa_twine_pr83',
    'python-distro_distro_pr106',
    'python-distro_distro_pr247',
    'python-distro_distro_pr30',
    'python-poetry_poetry-core_pr101',
    'python-poetry_poetry-core_pr108',
    'python-poetry_poetry-core_pr221',
    'python-poetry_poetry-core_pr228',
    'python-poetry_poetry-core_pr368',
    'python-poetry_poetry-core_pr462',
    'python-poetry_poetry-core_pr469',
    'python-poetry_poetry-core_pr57',
    'python-poetry_poetry-core_pr578',
    'python-poetry_poetry-core_pr620',
    'python-poetry_poetry-core_pr629',
    'python-poetry_poetry-core_pr634',
    'python-poetry_poetry-core_pr661',
    'python-poetry_poetry-core_pr666',
    'python-poetry_poetry-core_pr675',
    'python-poetry_poetry-core_pr710',
    'python-poetry_poetry-core_pr734',
    'python-poetry_poetry-core_pr81',
    'python-poetry_poetry-core_pr830',
    'python-poetry_poetry-core_pr874',
    'python-trio_flake8-async_pr100',
    'python-trio_flake8-async_pr22',
    'python-trio_flake8-async_pr34',
    'python-trio_flake8-async_pr98',
    'radish-bdd_radish_pr447',
    'ros-infrastructure_rosdep_pr789',
    'sigmavirus24_github3.py_pr1199',
    'sphinx-contrib_confluencebuilder_pr1044',
    'sphinx-contrib_confluencebuilder_pr769',
    'sphinx-doc_sphinx-argparse_pr49',
    'spulec_freezegun_pr119',
    'tconbeer_sqlfmt_pr16',
    'tconbeer_sqlfmt_pr221',
    'tconbeer_sqlfmt_pr25',
    'tefra_xsdata_pr653',
    'tlambert03_mkdocs-api-autonav_pr12',
    'ultrabug_mkdocs-static-i18n_pr183',
})

# Fixture paths confirmed from failure logs in the 2026-10-09 audit; the repair below must still restore each of them.
TEST_FIXTURE_REPAIRS = {
    'andialbrecht_sqlparse_pr317': ('tests/files/encoding_utf8.sql',),
    'audreyr_cookiecutter_pr1692': ('tests/test-generate-context/nested_dict.json',),
    'audreyr_cookiecutter_pr2010': ('tests/test-generate-context/nested_dict_additional.json',),
    'awslabs_serverless-application-model_pr1756': ('tests/translator/input/http_api_lambda_auth.yaml',),
    'awslabs_serverless-application-model_pr2242': ('tests/translator/input/function_with_event_filtering.yaml',),
    'awslabs_serverless-application-model_pr2261': ('tests/translator/input/http_api_multiple_authorizers.yaml',),
    'beetbox_beets_pr3641': ('test/rsrc/lyrics/geniuscom/Wutangclancreamlyrics.txt',),
    'beetbox_beets_pr5352': ('test/rsrc/lyrics/geniuscom/2pacalleyezonmelyrics.txt',),
    'best-doctor_flake8-class-attributes-order_pr13': ('tests/test_files/special_method.py',),
    'best-doctor_flake8-functions_pr5': ('tests/test_files/file_pure_function.py',),
    'bufbuild_protovalidate-python_pr308': ('tests/testdata/string_ext_supplemental.textproto',),
    'buriy_python-readability_pr190': ('tests/samples/summary-keep-all-images.sample.html',),
    'butler54_mdformat-frontmatter_pr5': ('tests/simple-md-test.md',),
    'c4deszes_ldfparser_pr106': ('tests/ldf/ldf_with_sporadic_frames.ldf',),
    'c4deszes_ldfparser_pr116': ('tests/ldf/j2602_1.ldf',),
    'c4deszes_ldfparser_pr80': ('tests/ldf/lin_schedules.ldf',),
    'canonical_operator_pr800': ('test/charms/test_smoke/charmcraft.yaml',),
    'censys_censys-python_pr312': ('tests/cli/test.xml',),
    'cookiecutter_cookiecutter_pr1692': ('tests/test-generate-context/nested_dict.json',),
    'cookiecutter_cookiecutter_pr2010': ('tests/test-generate-context/nested_dict_additional.json',),
    'cthoyt_pystow_pr17': ('tests/resources/test_1.tsv',),
    'demisto_demisto-sdk_pr102': ('tests/test_files/indicator-field-exact-scheme.json',),
    'django-crispy-forms_crispy-bootstrap5_pr151': ('tests/results/modal.html',),
    'django-crispy-forms_crispy-bootstrap5_pr44': ('tests/results/field_with_buttons.html',),
    'django-crispy-forms_crispy-bootstrap5_pr81': ('tests/results/alert.html',),
    'drgarcia1986_simple-settings_pr19': ('tests/samples/simple_yaml_file.yaml',),
    'fastavro_fastavro_pr482': ('tests/load_schema_test_3/A.avsc',),
    'fastavro_fastavro_pr491': ('tests/load_schema_test_7/A.avsc',),
    'fastavro_fastavro_pr492': ('tests/load_schema_test_8/A.avsc',),
    'fastavro_fastavro_pr497': ('tests/load_schema_test_12/com/namespace/E.avsc',),
    'fonttools_fonttools_pr1460': ('Tests/feaLib/data/bug1459.fea',),
    'fonttools_fonttools_pr1669': ('Tests/cffLib/data/TestCFF2Widths.ttx',),
    'fonttools_fonttools_pr1752': ('Tests/varLib/data/SingleMaster.designspace',),
    'fonttools_fonttools_pr1827': ('Tests/varLib/data/VarLibLocationTest.designspace',),
    'fonttools_fonttools_pr1876': ('Tests/feaLib/data/GSUB_error.fea',),
    'fonttools_fonttools_pr1878': ('Tests/feaLib/data/aalt_chain_contextual_subst.fea',),
    'fonttools_fonttools_pr1905': ('Tests/feaLib/data/MultipleLookupsPerGlyph2.fea',),
    'fonttools_fonttools_pr1944': ('Tests/subset/data/TestContextSubstFormat3.ttx',),
    'fonttools_fonttools_pr1957': ('Tests/varLib/data/FeatureVarsWholeRange.designspace',),
    'fonttools_fonttools_pr1973': ('Tests/feaLib/data/include/test.ufo/features.fea',),
    'fonttools_fonttools_pr2112': ('Tests/subset/data/layout_scripts.ttx',),
    'fonttools_fonttools_pr2161': ('Tests/ttLib/data/woff2_overlap_offcurve_in.ttx',),
    'fonttools_fonttools_pr2170': ('Tests/feaLib/data/delete_glyph.fea',),
    'fonttools_fonttools_pr2277': ('Tests/feaLib/data/bug2276.fea',),
    'fonttools_fonttools_pr2313': ('Tests/subset/data/GPOS_SinglePos_no_value_issue_2312.ttx',),
    'fonttools_fonttools_pr2432': ('Tests/feaLib/data/variable_scalar_anchor.fea',),
    'fonttools_fonttools_pr2447': ('Tests/merge/data/CFFFont1.ttx',),
    'fonttools_fonttools_pr2462': ('Tests/subset/data/BungeeColor-Regular.ttx',),
    'fonttools_fonttools_pr2555': ('Tests/varLib/instancer/data/SinglePos.ttx',),
    'fonttools_fonttools_pr2660': ('Tests/varLib/data/TestVariableCOLR.designspace',),
    'fonttools_fonttools_pr2673': ('Tests/varLib/instancer/data/STATInstancerTest.ttx',),
    'fonttools_fonttools_pr2684': ('Tests/designspaceLib/data/DS5BreakTest.designspace',),
    'fonttools_fonttools_pr2776': ('Tests/feaLib/data/variable_bug2772.fea',),
    'fonttools_fonttools_pr2789': ('Tests/ttLib/data/TestTTF_normalizeLocation.ttx',),
    'fonttools_fonttools_pr3024': ('Tests/varLib/data/master_no_overwrite_stat/Test-CondensedThin.ttx',),
    'fonttools_fonttools_pr3027': ('Tests/ttLib/tables/data/COLRv1-clip-boxes-glyf.ttx',),
    'fonttools_fonttools_pr3034': ('Tests/varLib/data/test_results/InterpolateLayoutGPOS_7_diff.ttx',),
    'fonttools_fonttools_pr3075': ('Tests/varLib/data/SparseMasters_ufo.designspace',),
    'fonttools_fonttools_pr3092': ('Tests/ttLib/tables/data/_g_l_y_f_instructions.ttx',),
    'fonttools_fonttools_pr3115': ('Tests/ttx/data/roundtrip_DSIG_split_at_XML_parse_buffer_size.ttx',),
    'fonttools_fonttools_pr3305': ('Tests/subset/data/NotoSansCJKjp-Regular.subset.ttx',),
    'fonttools_fonttools_pr3330': ('Tests/feaLib/data/variable_mark_anchor.fea',),
    'fonttools_fonttools_pr3506': ('Tests/varLib/instancer/data/CFF2Instancer-VF-1.ttx',),
    'fonttools_fonttools_pr3520': ('Tests/feaLib/data/duplicate_lookup_reference.fea',),
    'fonttools_fonttools_pr3559': ('Tests/feaLib/data/contextual_inline_multi_sub_format_2.fea',),
    'fonttools_fonttools_pr3672': ('Tests/subset/data/cmap14_font1.ttx',),
    'fonttools_fonttools_pr3726': ('Tests/feaLib/data/contextual_inline_format_4.fea',),
    'fonttools_fonttools_pr3783': ('Tests/feaLib/data/spec9a2.fea',),
    'fonttools_fonttools_pr3800': ('Tests/feaLib/data/CursivePosSubtable.fea',),
    'fonttools_fonttools_pr3803': ('Tests/feaLib/data/single_pos_NULL.fea',),
    'fonttools_fonttools_pr3804': ('Tests/feaLib/data/class_pair_pos_duplicates.fea',),
    'fonttools_fonttools_pr3811': ('Tests/feaLib/data/spec8a_2.fea',),
    'fonttools_fonttools_pr3838': ('Tests/varLib/data/test_results/FeatureVars_latn_dflt_var.ttx',),
    'fonttools_fonttools_pr3849': ('Tests/feaLib/data/bug3846_1.fea',),
    'fonttools_fonttools_pr3853': ('Tests/subset/data/PreserveSillyNamesTest.ttx',),
    'fonttools_fonttools_pr3874': ('Tests/feaLib/data/combo_mult_and_lig_sub.fea',),
    'fonttools_fonttools_pr3895': ('Tests/feaLib/data/identical_feature_lookups.fea',),
    'frictionlessdata_frictionless-py_pr1052': ('tests/fixtures/output-markdown/package.md',),
    'goodmami_wn_pr264': ('tests/data/mini-lmf-1.4.xml',),
    'google_flatbuffers_pr6217': ('tests/optional_scalars/ScalarStuff.cs',),
    'googlefonts_ufo2ft_pr289': ('tests/data/TestFont-NoOptimize-CFF.ttx',),
    'googlefonts_ufo2ft_pr318': ('tests/data/IncompatibleMasters/NewFont-Regular.ufo/metainfo.plist',),
    'googlefonts_ufo2ft_pr335': ('tests/data/Instructions.ufo/metainfo.plist',),
    'googlefonts_ufo2ft_pr359': ('tests/data/ColorTest.ufo/metainfo.plist',),
    'googlefonts_ufo2ft_pr816': ('tests/data/TestVarFont.designspace',),
    'googlefonts_ufo2ft_pr817': ('tests/data/OTestFont.designspace',),
    'googlefonts_ufo2ft_pr869': ('tests/data/ContextualAnchorsTest-Regular.ufo/metainfo.plist',),
    'googlefonts_ufo2ft_pr909': ('tests/data/Bug908.ufo/metainfo.plist',),
    'googlefonts_ufo2ft_pr942': ('tests/data/BagelFatOne-Regular.designspace',),
    'goose3_goose3_pr157': ('tests/data/publishdate/test_publish_date_unix_milliseconds.json',),
    'goose3_goose3_pr180': ('tests/data/title/test_title_sitename_list.json',),
    'goose3_goose3_pr25': ('tests/data/images/test_base64_image.json',),
    'goose3_goose3_pr54': ('tests/data/metas/test_meta_encoding.json',),
    'goose3_goose3_pr60': ('tests/data/title/test_title_splitter.json',),
    'goose3_goose3_pr64': ('tests/data/opengraph/test_opengraph_type.json',),
    'goose3_goose3_pr66': ('tests/data/content/test_retry_top_node.json',),
    'goose3_goose3_pr90': ('tests/data/opengraph/test_opengraph_types_mult.json',),
    'gruebel_pycep_pr151': ('tests/test_function/examples/array/flatten/result.json',),
    'gruebel_pycep_pr181': ('tests/examples/basic/06-type/result.json',),
    'gruebel_pycep_pr320': ('tests/examples/complex/10-extension/result.json',),
    'gruebel_pycep_pr321': ('tests/examples/complex/11-typed-var/result.json',),
    'gruebel_pycep_pr322': ('tests/examples/complex/12-nullable-type/result.json',),
    'gruebel_pycep_pr323': ('tests/examples/complex/13-import/result.json',),
    'gruebel_pycep_pr326': ('tests/examples/complex/14-type-decorator/result.json',),
    'gruebel_pycep_pr43': ('tests/test_function/examples/date/date_time_to_epoch/result.json',),
    'gruebel_pycep_pr52': ('tests/test_function/examples/file/load_json_content/result.json',),
    'home-assistant-libs_python-supervisor-client_pr152': ('tests/fixtures/host_disk_usage.json',),
    'iterative_gto_pr269': ('tests/resources/sample_remote_repo_expected_registry.json',),
    'jackdewinter_pymarkdown_pr1050': ('test/resources/rules/md010/issue-1015.md',),
    'jacksmith15_json-ref-dict_pr13': ('tests/schemas/slash-key.yaml',),
    'jacksmith15_json-ref-dict_pr4': ('tests/schemas/bad-circular.yaml',),
    'jacksmith15_json-ref-dict_pr9': ('tests/schemas/with-tabs.json',),
    'jazzband_icalevents_pr133': ('test/test_data/google_2024.ics',),
    'jazzband_icalevents_pr140': ('test/test_data/regression_offset_native.ics',),
    'jazzband_icalevents_pr79': ('test/test_data/google_tz.ics',),
    'jazzband_icalevents_pr80': ('test/test_data/cest.ics',),
    'jazzband_icalevents_pr88': ('test/test_data/rrule_until_only_date.ics',),
    'jazzband_icalevents_pr91': ('test/test_data/empty.ics',),
    'jazzband_icalevents_pr92': ('test/test_data/status_and_url.ics',),
    'jazzband_icalevents_pr95': ('test/test_data/multi_attendee_response.ics',),
    'jazzband_icalevents_pr97': ('test/test_data/multi_exdate_same_line_ms.ics',),
    'jazzband_tablib_pr534': ('tests/files/issue_524.yaml',),
    'joshtemple_lkml_pr10': ('tests/resources/duplicate_top_level_keys.view.lkml',),
    'jsh9_pydoclint_pr123': ('tests/data/edge_cases/09_double_quotes_in_Literal/google.py',),
    'jsh9_pydoclint_pr129': ('tests/data/edge_cases/10_absent_return_anno/numpy.py',),
    'jsh9_pydoclint_pr130': ('tests/data/google/class_attributes/cases.py',),
    'jsh9_pydoclint_pr153': ('tests/data/edge_cases/12_property_methods_as_class_attr/google.py',),
    'jsh9_pydoclint_pr86': ('tests/data/edge_cases/06_no_type_hints_in_doc/numpy.py',),
    'jsh9_pydoclint_pr89': ('tests/data/edge_cases/07_underscore_args/google.py',),
    'jxtech_wechatpy_pr381': ('tests/fixtures/changeopenid.json',),
    'jxtech_wechatpy_pr412': ('tests/fixtures/sns_jscode2session.json',),
    'jxtech_wechatpy_pr456': ('tests/fixtures/enterprise/user_list.json',),
    'lovesegfault_beautysh_pr36': ('tests/indent_test1_raw.sh',),
    'marshallward_f90nml_pr120': ('tests/long_string.nml',),
    'marshallward_f90nml_pr180': ('tests/null_target_end_comma.nml',),
    'mattijn_topojson_pr141': ('tests/files_topojson/gm.topo.json',),
    'maxb2_typer-config_pr15': ('tests/config.env',),
    'maxb2_typer-config_pr17': ('tests/config.ini',),
    'meeb_whoisit_pr7': ('tests/data_rdap_response_domain3.json',),
    'meeb_whoisit_pr8': ('tests/data_rdap_response_ip_v4_2.json',),
    'mesonbuild_meson-python_pr219': ('tests/packages/unknown-user-args-top-level/pyproject.toml',),
    'mesonbuild_meson-python_pr377': ('tests/packages/purelib-platlib-split/pyproject.toml',),
    'mesonbuild_meson-python_pr455': ('tests/packages/missing-meson-version/pyproject.toml',),
    'mesonbuild_meson-python_pr71': ('tests/packages/unsupported-python-version/pyproject.toml',),
    'mhe_pynrrd_pr40': ('tests/test1d_ascii.nrrd',),
    'mirumee_ariadne-codegen_pr106': ('tests/main/graphql_schemas/example/pyproject.toml',),
    'mirumee_ariadne-codegen_pr264': ('tests/main/graphql_schemas/example/pyproject-schema-graphql.toml',),
    'ncclient_ncclient_pr299': ('test/unit/ssh_config',),
    'ncclient_ncclient_pr628': ('test/unit/transport/certs/id_ed25519_test.pub',),
    'nicklambourne_slackblocks_pr32': ('test/samples/blocks/section_block_empty_text_field_value.json',),
    'nicklambourne_slackblocks_pr49': ('test/samples/messages/message_with_optional_arguments.json',),
    'olofk_fusesoc_pr645': ('tests/capi2_cores/parser/inheritance.core',),
    'pappasam_toml-sort_pr91': ('tests/examples/gradle-version-catalog.toml',),
    'pdm-project_pdm-backend_pr14': ('tests/fixtures/projects/demo-package-include-old/pyproject.toml',),
    'pdm-project_pdm-backend_pr20': ('tests/fixtures/projects/demo-package-with-deep-path/pyproject.toml',),
    'pdm-project_pdm-backend_pr21': ('tests/fixtures/projects/demo-cextension/pyproject.toml',),
    'pdm-project_pdm-backend_pr49': ('tests/fixtures/projects/demo-no-name-nor-version/pyproject.toml',),
    'pdm-project_pdm-backend_pr5': ('tests/fixtures/projects/demo-legacy/pyproject.toml',),
    'pdm-project_pdm-backend_pr65': ('tests/fixtures/projects/demo-purelib-with-build/pyproject.toml',),
    'pdm-project_pdm-backend_pr8': ('tests/fixtures/projects/demo-explicit-package-dir/pyproject.toml',),
    'python-poetry_poetry-core_pr118': ('tests/fixtures/with_readme_files/README-1.rst',),
    'python-poetry_poetry-core_pr248': ('tests/fixtures/with_readme_files/README-1.rst',),
    'radish-bdd_radish_pr457': ('tests/output/unix/filter-when-using-tags-with-args.txt',),
    'salesforce_cloudsplaining_pr90': ('test/files/shared/test_exclusions_for_service_roles_expected.json',),
    'sayanarijit_expandvars_pr18': ('tests/data/foo.txt',),
    'snowflakedb_snowflake-cli_pr505': ('tests/test_data/projects/snowpark_procedures/snowflake.yml',),
    'spdx_tools-python_pr236': ('tests/data/formats/SPDXSBOMExample.tag',),
    'stac-utils_stac-pydantic_pr108': ('tests/example_stac/example-item_geometry-null.json',),
    'stac-utils_stac-pydantic_pr81': ('tests/example_stac/example-collection-list.json',),
    'tconbeer_sqlfmt_pr126': ('tests/data/unformatted/110_other_identifiers.sql',),
    'tconbeer_sqlfmt_pr127': ('tests/data/unformatted/111_chained_boolean_between.sql',),
    'tconbeer_sqlfmt_pr143': ('tests/data/unformatted/201_basic_snapshot.sql',),
    'tconbeer_sqlfmt_pr146': ('tests/data/preformatted/301_multiline_jinjafmt.sql',),
    'tconbeer_sqlfmt_pr21': ('tests/data/unit_tests/test_parser/test_multiline_wrapping.sql',),
    'tconbeer_sqlfmt_pr210': ('tests/data/unformatted/116_chained_booleans.sql',),
    'tconbeer_sqlfmt_pr216': ('tests/data/unformatted/213_gitlab_fct_sales_funnel_target.sql',),
    'tconbeer_sqlfmt_pr228': ('tests/data/unformatted/118_within_group.sql',),
    'tconbeer_sqlfmt_pr233': ('tests/data/unformatted/119_psycopg_placeholders.sql',),
    'tconbeer_sqlfmt_pr238': ('tests/data/unformatted/120_array_literals.sql',),
    'tconbeer_sqlfmt_pr47': ('tests/data/unit_tests/test_splitter/test_comment_split_impact_on_open_brackets.sql',),
    'tilezen_mapbox-vector-tile_pr70': ('tests/error_nested_multipolygon.wkt',),
    'twitterdev_twitter-python-ads-sdk_pr212': ('tests/fixtures/promoted_tweets_attach.json',),
    'valohai_valohai-yaml_pr118': ('tests/warning_examples/override-with-extra-fields-warning.yaml',),
    'valohai_valohai-yaml_pr94': ('tests/error_examples/invalid-indentation-with-valid-YAML.yaml',),
    'wbond_asn1crypto_pr79': ('tests/fixtures/ocsp-with-pkup.pem',),
    'xmunoz_sodapy_pr17': ('tests/test_data/successblobres.txt',),
    'xmunoz_sodapy_pr23': ('tests/test_data/update_song_metadata.txt',),
    'yu-iskw_dbt-artifacts-parser_pr19': ('tests/resources/v7/jaffle_shop/manifest.json',),
    'zapier_email-reply-parser_pr14': ('test/emails/email_headers_no_delimiter.txt',),
    'zapier_email-reply-parser_pr31': ('test/emails/email_2_3.txt',),
    'zheller_flake8-quotes_pr52': ('test/data/multiline_string.py',),
    'zheller_flake8-quotes_pr78': ('test/data/doubles_escaped.py',),
}

NETWORK_TIMEOUT_GROUPS = {
    'swagger-api_swagger-codegen': ((2765, 2796, 2801, 2831, 2863, 2878, 2899, 2929, 2987, 3015, 3031, 3034, 3075, 3090, 3126, 3130), 'public Petstore API'),
    'elastic_elastic-transport-python': ((26, 55, 127), 'httpbin.org test service'),
    'developmentseed_rio-stac': ((65, 71), 'remote STAC schemas'),
    'richardkiss_pycoin': ((392, 400), 'public blockchain transaction services'),
}
for _repo, (_prs, _dependency) in NETWORK_TIMEOUT_GROUPS.items():
    for _pr in _prs:
        ARCHIVED_TASKS[f'{_repo}_pr{_pr}'] = {
            'category': 'verifier-network-dependency',
            'reason': f'Original reference and no-op verifiers hit the 1800-second limit. Diagnostics on a representative task from this repository confirmed blocking access to {_dependency}. Archived at user request as part of this network-dependent timeout group; this task was not individually diagnosed unless included in the diagnostic sample.'}

EXACT_ID_SCORING_TASKS = frozenset({
    'comtravo_ctparse_pr129',
    'falconry_falcon_pr2366',
    'falconry_falcon_pr2503',
    'falconry_falcon_pr2572',
    'falconry_falcon_pr2581',
})


class ExactPytestResults:
    """Record actual pytest identities without rewriting parameter text."""
    def __init__(self, expected):
        self.expected = set(expected)
        self.collected = set()
        self.coverage = {}
        self.phases = {}
        self.failed = False

    def pytest_collection_finish(self, session):
        self.collected = {item.nodeid for item in session.items}
        for selector in self.expected:
            self.coverage[selector] = ({selector} if selector in self.collected else {
                node for node in self.collected
                if node.startswith(selector + '[') or node.startswith(selector + '::')})

    def pytest_collectreport(self, report):
        if report.failed:
            self.failed = True

    def pytest_runtest_logreport(self, report):
        if not report.passed:
            self.failed = True
        self.phases.setdefault(report.nodeid, set()).add(report.when)

    def all_passed(self, exitcode):
        return (exitcode == 0 and not self.failed and bool(self.expected)
                and set(self.coverage) == self.expected
                and all(self.coverage.values())
                and set().union(*self.coverage.values()) == self.collected
                and all(self.phases.get(node, set()) >= {'setup', 'call', 'teardown'}
                        for node in self.collected))


def exact_pytest_score_main():
    import json
    import sys
    import pytest
    with open(sys.argv[1]) as stream:
        expected = json.load(stream)
    if not expected:
        print('<score>0.0</score>', flush=True)
        return
    results = ExactPytestResults(expected)
    exitcode = pytest.main(['-vv', '-o', 'addopts=', '--rootdir=.', *expected], plugins=[results])
    print('<score>1.0</score>' if results.all_passed(exitcode) else '<score>0.0</score>', flush=True)


def repair_exact_test_scoring(contents, task_id):
    if task_id not in EXACT_ID_SCORING_TASKS:
        return []
    replacement = ('"""Score exact pytest identities; require complete successful execution."""\n\n'
                   + inspect.getsource(ExactPytestResults) + '\n'
                   + inspect.getsource(exact_pytest_score_main)
                   + '\nif __name__ == "__main__":\n    exact_pytest_score_main()\n')
    text = contents['tests/score.py'].decode()
    if text == replacement:
        return []
    if 'def all_passed(xml_content: str, expected: list[str])' not in text:
        raise ValueError('ScaleSWE scorer changed since exact-ID review')
    contents['tests/score.py'] = replacement.encode()
    return [{'file': 'tests/score.py', 'phase': 'verifier-exact-test-ids',
             'reason': 'Replace lossy JUnit name reconstruction with exact pytest collection and execution identities. Preserve parameter text, require every selected test and all setup/call/teardown phases to pass, and reject skipped tests, collection failures and unsuccessful pytest exit.'}]


JSONARGPARSE_DEBUG_TASKS = frozenset(
    f'omni-us_jsonargparse_pr{pr}' for pr in
    (162, 164, 176, 177, 186, 190, 194, 197, 198, 210, 211, 214, 216,
     218, 222, 223, 233, 239, 242, 253, 266, 267, 268, 270, 272, 273))

JSONARGPARSE_DEBUG_FIXTURE = '''

# ScaleSWE verifier: completion tests must not take ownership of pytest's fd 9.
import pytest as _scaleswe_pytest

@_scaleswe_pytest.fixture(autouse=True)
def _scaleswe_argcomplete_debug_stream(monkeypatch):
    import io
    import sys
    import argcomplete.io as completion_io
    from argcomplete.finders import CompletionFinder
    class DebugStream(io.StringIO):
        target = None
        def write(self, text):
            self.target.write(text)
            return super().write(text)
        def flush(self):
            self.target.flush()
            return super().flush()
    with DebugStream() as debug_stream:
        def init_debug_stream(self):
            debug_stream.target = sys.stderr
        with monkeypatch.context() as patch:
            patch.setattr(completion_io, 'debug_stream', debug_stream)
            patch.setattr(CompletionFinder, '_init_debug_stream', init_debug_stream)
            yield
'''


def repair_argcomplete_test_fixture(fixture):
    from pathlib import Path
    path = Path('jsonargparse_tests/test_argcomplete.py')
    text = path.read_text()
    if 'class ArgcompleteTests(' not in text:
        raise ValueError('Jsonargparse completion tests changed since review')
    if 'def _scaleswe_argcomplete_debug_stream(' not in text:
        path.write_text(text + fixture)


def repair_argcomplete_debug_stream(contents, task_id):
    if task_id not in JSONARGPARSE_DEBUG_TASKS:
        return []
    name = 'tests/score.py'
    text = contents[name].decode()
    if 'def repair_argcomplete_test_fixture(' in text:
        return []
    entry = 'if __name__ == "__main__":\n    main()'
    if text.count(entry) != 1:
        raise ValueError('Jsonargparse scorer entrypoint changed since review')
    replacement = inspect.getsource(repair_argcomplete_test_fixture) + '\n' + entry.replace(
        '    main()', '    repair_argcomplete_test_fixture(' + repr(JSONARGPARSE_DEBUG_FIXTURE) + ')\n    main()')
    contents[name] = text.replace(entry, replacement).encode()
    return [{'file': name, 'phase': 'verifier-debug-stream-isolation',
             'reason': 'Jsonargparse completion tests invoke argcomplete in the pytest process. Argcomplete wraps fixed descriptor 9 as its debug stream; replacing that owning wrapper closes pytest faulthandler\'s descriptor and crashes after passing tests. Give completion tests an isolated in-memory debug stream that forwards warnings to the stderr selected at autocomplete entry without owning its descriptor, and restore original state after each test. Preserve assertions, selectors, dependency versions, scoring and pytest cleanup.'}]


def repair_werkzeug_request_test():
    from pathlib import Path
    path = Path('tests/test_test.py')
    text = path.read_text()
    old = ('    req = builder.get_request()\n'
           '    strict_eq(req.form["foo"], u"bar")\n'
           '    strict_eq(req.files["blafasel"].read(), b"foo")\n')
    new = ('    req = builder.get_request()\n'
           '    try:\n'
           '        strict_eq(req.form["foo"], u"bar")\n'
           '        strict_eq(req.files["blafasel"].read(), b"foo")\n'
           '    finally:\n'
           '        req.close()  # ScaleSWE: release the uploaded temporary file.\n')
    if text.count(new) == 1:
        return
    if text.count(old) != 1:
        raise ValueError('Werkzeug multipart request test changed since review')
    path.write_text(text.replace(old, new, 1))


def repair_werkzeug_request_cleanup(contents, task_id):
    if task_id != 'pallets_werkzeug_pr1694':
        return []
    name = 'tests/score.py'
    text = contents[name].decode()
    if 'def repair_werkzeug_request_test(' in text:
        return []
    entry = 'if __name__ == "__main__":\n    main()'
    if text.count(entry) != 1:
        raise ValueError('Werkzeug scorer entrypoint changed since review')
    replacement = inspect.getsource(repair_werkzeug_request_test) + '\n' + entry.replace(
        '    main()', '    repair_werkzeug_request_test()\n    main()')
    contents[name] = text.replace(entry, replacement).encode()
    return [{'file': name, 'phase': 'verifier-request-cleanup',
             'reason': 'Werkzeug test_environ_builder_content_type leaves its multipart request open, leaking an uploaded SpooledTemporaryFile. Pytest promotes the resulting unraisable ResourceWarning to an error during shutdown after all 211 reference tests pass. Close that request in a finally block after its existing assertions. Preserve assertions, selectors, scoring, dependency versions and warning checks.'}]


PYOUT_CLEANUP_TASKS = frozenset(f'pyout_pyout_pr{pr}' for pr in (108, 109, 111))

PYOUT_CLEANUP_FIXTURE = '''

# ScaleSWE verifier: release delayed test work after assertions, then join pools.
@pytest.fixture(autouse=True)
def _scaleswe_cleanup_delayed_workers(monkeypatch):
    import pyout.interface as interface
    delays, pools = [], []
    original_init = Delayed.__init__
    original_pool = interface.Pool

    def tracked_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        delays.append(self)

    class TrackedPool(original_pool):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            pools.append(self)

    monkeypatch.setattr(Delayed, '__init__', tracked_init)
    monkeypatch.setattr(interface, 'Pool', TrackedPool)
    try:
        yield
    finally:
        for delay in delays:
            delay.now = True
        for pool in pools:
            pool.shutdown(wait=True)
'''


def repair_pyout_test_cleanup(fixture):
    from pathlib import Path
    path = Path('pyout/tests/test_tabular.py')
    text = path.read_text()
    if 'class Delayed(' not in text or 'def test_tabular_write_callable_kb_interrupt_in_exit(' not in text:
        raise ValueError('Pyout delayed-work tests changed since review')
    if 'def _scaleswe_cleanup_delayed_workers(' not in text:
        path.write_text(text + fixture)


def repair_flask_worker_fixtures():
    """Size test-owned pools from the container CPU affinity, then join them."""
    import os
    from pathlib import Path
    try:
        workers = max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        workers = 1
    path = Path('tests/conftest.py')
    text = path.read_text()
    if '# ScaleSWE affinity-aware fixture pools' in text:
        return
    if text.count('    return app') != 2:
        raise ValueError('Flask-executor app fixtures changed since review')
    replacement = """    # ScaleSWE affinity-aware fixture pools
    app.config['EXECUTOR_MAX_WORKERS'] = {workers}
    try:
        yield app
    finally:
        for value in app.extensions.values():
            if isinstance(value, Executor):
                value.shutdown(wait=True)""".format(workers=workers)
    path.write_text(text.replace('    return app', replacement))
    print('ScaleSWE fixture worker count from CPU affinity:', workers, flush=True)


def run_joblib_with_available_cpus(main):
    """Give the old multiprocessing API the verifier's allowed CPU count."""
    import multiprocessing
    import os
    original = multiprocessing.cpu_count

    def available_cpus():
        try:
            return max(1, len(os.sched_getaffinity(0)))
        except (AttributeError, OSError):
            return 1

    multiprocessing.cpu_count = available_cpus
    try:
        print('ScaleSWE available verifier CPUs:', available_cpus(), flush=True)
        return main()
    finally:
        multiprocessing.cpu_count = original


RECHUNKER_CPU_TASKS = frozenset(f'pangeo-data_rechunker_pr{pr}' for pr in (22, 27, 30, 48))


def run_dask_with_available_cpus(main):
    import os
    import dask
    try:
        cpus = max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        cpus = 1
    print('ScaleSWE available verifier CPUs:', cpus, flush=True)
    with dask.config.set(num_workers=cpus):
        return main()


def repair_timeout_verifiers(contents, task_id):
    edits = []
    if task_id in ('joblib_joblib_pr449', 'joblib_joblib_pr541'):
        name = 'tests/score.py'
        text = contents[name].decode()
        entry = 'if __name__ == "__main__":\n    main()'
        if 'def run_joblib_with_available_cpus(' not in text:
            if text.count(entry) != 1:
                raise ValueError('Joblib score entrypoint changed since review')
            replacement = inspect.getsource(run_joblib_with_available_cpus) + '\n' + entry.replace(
                '    main()', '    run_joblib_with_available_cpus(main)')
            contents[name] = text.replace(entry, replacement).encode()
            edits.append({'file': name, 'phase': 'verifier-cpu-affinity' if task_id == 'joblib_joblib_pr541' else 'verifier-worker-cleanup',
                          'reason': 'Host CPU discovery can size pools for 384 CPUs despite only four allocated CPUs, causing out-of-memory termination. Use process CPU affinity for multiprocessing.cpu_count during verification, falling back to one CPU; preserve Joblib negative n_jobs semantics, explicit worker counts, tests, scoring and the 4096 MB memory limit'})
    if task_id in RECHUNKER_CPU_TASKS:
        name = 'tests/score.py'
        text = contents[name].decode()
        entry = 'if __name__ == "__main__":\n    main()'
        if 'def run_dask_with_available_cpus(' not in text:
            if text.count(entry) != 1:
                raise ValueError('Rechunker score entrypoint changed since review')
            replacement = inspect.getsource(run_dask_with_available_cpus) + '\n' + entry.replace(
                '    main()', '    run_dask_with_available_cpus(main)')
            contents[name] = text.replace(entry, replacement).encode()
            edits.append({'file': name, 'phase': 'verifier-cpu-affinity',
                          'reason': 'Dask defaults to 384 host CPUs despite only four allocated CPUs, causing out-of-memory termination. Set verifier Dask num_workers from process CPU affinity, falling back to one CPU, and restore configuration afterward. Preserve tests, assertions, selectors, scoring and the 4096 MB memory limit.'})
    if task_id == 'dchevell_flask-executor_pr12':
        name = 'tests/score.py'
        text = contents[name].decode()
        entry = 'if __name__ == "__main__":\n    main()'
        if 'def repair_flask_worker_fixtures(' not in text:
            if text.count(entry) != 1:
                raise ValueError('Flask-executor score entrypoint changed since review')
            replacement = inspect.getsource(repair_flask_worker_fixtures) + '\n' + entry.replace(
                '    main()', '    repair_flask_worker_fixtures()\n    main()')
            contents[name] = text.replace(entry, replacement).encode()
            edits.append({'file': name, 'phase': 'verifier-worker-cleanup',
                          'reason': 'Set test fixture default workers from container CPU affinity (one worker if unavailable) and join fixture executors after each test; preserve explicit worker-count tests, assertions, scoring and the 4096 MB task memory limit'})
    if task_id in PYOUT_CLEANUP_TASKS:
        name = 'tests/score.py'
        text = contents[name].decode()
        entry = 'if __name__ == "__main__":\n    main()'
        if 'def repair_pyout_test_cleanup(' not in text:
            if text.count(entry) != 1:
                raise ValueError('Pyout score entrypoint changed since review')
            replacement = inspect.getsource(repair_pyout_test_cleanup) + '\n' + entry.replace(
                '    main()', '    repair_pyout_test_cleanup(' + repr(PYOUT_CLEANUP_FIXTURE) + ')\n    main()')
            contents[name] = text.replace(entry, replacement).encode()
            edits.append({'file': name, 'phase': 'verifier-worker-cleanup',
                          'reason': 'Release delayed test helper work after assertions and join test-created pools; preserve selected tests and assertions'})
    if task_id == 'rom1504_embedding-reader_pr9':
        name = 'tests/f2p.patch'
        if 'diff --git a/tests/fixtures.py b/tests/fixtures.py' not in contents[name].decode():
            chunks = contents['solution/gold.patch'].decode().split('diff --git ')
            chunk = next(c for c in chunks if c.startswith('a/tests/fixtures.py b/tests/fixtures.py\n'))
            contents[name] += ('\ndiff --git ' + chunk).encode()
            script = contents['tests/test.sh'].decode()
            anchor = 'if [ -s /tests/f2p.patch ]; then'
            if script.count(anchor) != 1:
                raise ValueError('Embedding-reader test preparation changed since review')
            contents['tests/test.sh'] = script.replace(anchor, 'git checkout "$base" -- tests/fixtures.py || exit $?\n' + anchor).encode()
            score = contents['tests/score.py'].decode()
            anchor = 'pytest.main(["-vv",'
            if score.count(anchor) != 1:
                raise ValueError('Embedding-reader grader changed since review')
            contents['tests/score.py'] = score.replace(anchor, 'pytest.main(["-vv", "--forked", "--timeout=60", "--timeout-method=thread",').encode()
            edits.append({'file': 'tests', 'phase': 'verifier-worker-cleanup',
                          'reason': 'Include and consistently restore the missing id2 fixture update; isolate each selected test with pytest-forked and a 60-second test timeout so broken reader worker shutdown cannot wedge the verifier; preserve assertions and reward rules'})
    return edits

DEFERRED_IMPORTS = {
    'keboola_python-component_pr39': {'tests/test_base.py': {'keboola.component.base': ['sync_action']}},
    'bluetooth-devices_cached-ipaddress_pr26': {'tests/test_ipaddress.py': {'cached_ipaddress': ['*'], 'cached_ipaddress.ipaddress': ['*']}},
    'semuconsulting_pynmeagps_pr84': {'tests/test_static.py': {'pynmeagps.nmeahelpers': ['leapsecond']}},
    'semuconsulting_pynmeagps_pr78': {
        'tests/test_static.py': {'pynmeagps.nmeatypes_decodes': ['*']},
        'tests/test_stream.py': {'pynmeagps': ['FMI_STATUS']}},
    'ouhammmourachid_mermaid-py_pr105': {
        'mermaid/tests/test_flowchart.py': {'mermaid': ['Direction'], 'mermaid.style': ['Style']},
        'mermaid/tests/test_statediagram.py': {'mermaid': ['Direction'], 'mermaid.style': ['Style']},
        'mermaid/tests/test_style.py': {'mermaid.style': ['Style']}},
}


def defer_test_imports(plan):
    """Move reviewed module imports into their consuming functions, preserving test bodies."""
    import ast
    from pathlib import Path

    for filename, modules in plan.items():
        path = Path(filename)
        source = path.read_text()
        tree = ast.parse(source)
        lines = source.splitlines(keepends=True)
        moved = {}
        edits = []
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or node.level or node.module not in modules:
                continue
            selected = modules[node.module]
            remaining = []
            for alias in node.names:
                if '*' in selected or alias.name in selected:
                    if alias.name == '*':
                        raise ValueError('Cannot defer a wildcard import: ' + filename)
                    name = alias.asname or alias.name
                    moved[name] = 'from ' + node.module + ' import ' + alias.name + (' as ' + alias.asname if alias.asname else '')
                else:
                    remaining.append(alias)
            if len(remaining) != len(node.names):
                replacement = ast.unparse(ast.ImportFrom(module=node.module, names=remaining, level=0)) + '\n' if remaining else ''
                edits.append((node.lineno - 1, node.end_lineno, replacement))
        if not moved:
            raise ValueError('Reviewed imports missing from ' + filename)

        def visit_scope(nodes):
            for node in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    headers = [*node.decorator_list, node.args, *([node.returns] if node.returns else [])]
                    if any(isinstance(n, ast.Name) and n.id in moved for h in headers for n in ast.walk(h)):
                        raise ValueError('Deferred import used in function signature: ' + filename)
                    used = {n.id for stmt in node.body for n in ast.walk(stmt)
                            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)} & moved.keys()
                    if used:
                        first = node.body[0]
                        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                            first = node.body[1]
                        indent = ' ' * first.col_offset
                        edits.append((first.lineno - 1, first.lineno - 1,
                                      ''.join(indent + moved[name] + '\n' for name in sorted(used))))
                elif isinstance(node, ast.ClassDef):
                    if any(isinstance(n, ast.Name) and n.id in moved for h in [*node.bases, *node.keywords, *node.decorator_list] for n in ast.walk(h)):
                        raise ValueError('Deferred import used in class definition: ' + filename)
                    visit_scope(node.body)
                elif not isinstance(node, (ast.Import, ast.ImportFrom)):
                    if any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in moved for n in ast.walk(node)):
                        raise ValueError('Deferred import used outside a function: ' + filename)
        visit_scope(tree.body)
        for start, end, replacement in sorted(edits, reverse=True):
            lines[start:end] = [replacement]
        updated = ''.join(lines)
        ast.parse(updated)
        path.write_text(updated)


def repair_collection_imports(contents, task_id):
    if task_id not in DEFERRED_IMPORTS:
        return []
    filename = 'tests/score.py'
    text = contents[filename].decode()
    marker = '# ScaleSWE reviewed call-time test imports v1\n'
    if marker in text:
        return []
    entry = 'if __name__ == "__main__":\n    main()'
    if text.count(entry) != 1:
        raise ValueError('ScaleSWE score entrypoint changed since review')
    replacement = marker + inspect.getsource(defer_test_imports) + '\n' + entry.replace('    main()', '    defer_test_imports(' + repr(DEFERRED_IMPORTS[task_id]) + ')\n    main()')
    contents[filename] = text.replace(entry, replacement).encode()
    return [{'file': filename, 'phase': 'verifier-call-time-imports',
             'reason': 'Defer reviewed feature imports to consuming test functions; preserve selected tests, assertions and reward rules',
             'imports': DEFERRED_IMPORTS[task_id]}]


def repair_verifier_imports(contents, task_id):
    if task_id not in VERIFIER_IMPORT_TASKS:
        return []
    name = 'tests/test.sh'
    text = contents[name].decode()
    command = 'python /tests/score.py /tests/test_ids.json | tee /logs/verifier/score.txt'
    prefix = 'export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"\n'
    if prefix + command in text:
        return []
    repo, reason = VERIFIER_IMPORT_TASKS[task_id]
    if text.count(command) != 1 or f'cd /workspace/{repo} || exit 1' not in text:
        raise ValueError('Verifier changed since import-path review')
    contents[name] = text.replace(command, prefix + command, 1).encode()
    return [{'file': name, 'old': command, 'new': prefix + command,
             'phase': 'verifier-import-path',
             'reason': reason + ' Prepend the repository root to PYTHONPATH; preserve assertions, selectors and reward rules.'}]


SKIPPED_REQUIRED_TESTS = {
    # dimod's test_nocpp_error checks the fallback used when the C++ extension is absent and skips
    # itself when the extension is present. Scale's images build the extension, so the test can never
    # pass there, yet Scale lists it among the required tests; the scorer treats a skip as not passed.
    **{f'dwavesystems_dimod_pr{pr}': ('tests/test_fixedvariablecomposite.py::TestRoofDualityComposite::test_nocpp_error',)
       for pr in (432, 445, 462, 471, 512)},
}
SKIPPED_REQUIRED_TEST_REASON = ('Remove a required test that the task image can never run: it checks the fallback used when the dimod C++ '
                                'extension is absent and skips itself because the image builds the extension. The scorer counts a skip as '
                                'not passed, so the reference solution scored 0 although every other required test passed. The remaining '
                                'required tests still cover the change; tests, assertions and the scorer are unchanged.')


def repair_skipped_required_tests(contents, task_id):
    """Drop required test IDs that are skipped by design in the task image."""
    remove = SKIPPED_REQUIRED_TESTS.get(task_id)
    if not remove or 'tests/test_ids.json' not in contents:
        return []
    ids = json.loads(contents['tests/test_ids.json'].decode())
    missing = [t for t in remove if t not in ids]
    if missing:
        raise ValueError(f'Required test list changed since review for {task_id}: {missing}')
    kept = [t for t in ids if t not in remove]
    if not kept:
        raise ValueError(f'No required tests would remain for {task_id}')
    contents['tests/test_ids.json'] = json.dumps(kept).encode()
    return [{'file': 'tests/test_ids.json', 'phase': 'verifier-required-test-skipped', 'removed': list(remove),
             'reason': SKIPPED_REQUIRED_TEST_REASON}]


RESET_TEST_DIRS = ('test', 'tests', 'Test', 'Tests')
FIXTURE_RESTORATION_REASON = (
    'Scoring resets the test directories to the base commit and then applies tests/f2p.patch, the tests that verify '
    'the fix. The pull request also added or changed test-support files under those directories (fixtures, recorded '
    'cassettes, sample inputs) that f2p.patch leaves out, so the reset removes them and the selected tests fail even '
    'with the reference solution. Append the pull request\'s own diff for exactly these files to f2p.patch so the '
    'verifier restores the complete post-PR test tree. Source files, other tests and the reset itself are unchanged.')
TEST_MODULE_RE = re.compile(r'(^|/)(test_[^/]*\.py|[^/]*_test\.py|conftest\.py)$')


def _diff_chunks(patch):
    """Map each same-path file diff in a git patch to its chunk text."""
    chunks = {}
    for chunk in patch.split('diff --git ')[1:]:
        header = chunk.splitlines()[0] if chunk.splitlines() else ''
        match = re.fullmatch(r'a/(.+) b/(.+)', header)
        if match and match.group(1) == match.group(2):
            chunks[match.group(1)] = 'diff --git ' + chunk
    return chunks


def _repair_hunk_headers(patch):
    """Make every hunk header state the line counts the hunk actually carries.

    Some Scale-SWE patches lost their final context line, so the last hunk
    header claims one line more than follows it. git apply rejects such a
    patch and patch(1) only tolerates the shortfall at end of file; once more
    diff chunks follow, patch(1) stops at the junction with "malformed patch".
    Headers whose counts already match are kept byte-identical; correcting the
    others changes no patch content.
    """
    lines = patch.split('\n')
    out, i = [], 0
    while i < len(lines):
        match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$', lines[i])
        if not match:
            out.append(lines[i]); i += 1
            continue
        j = i + 1
        while j < len(lines) and not lines[j].startswith('@@') and not lines[j].startswith('diff --git '):
            j += 1
        body = lines[i + 1:j]
        old = sum(1 for l in body if l[:1] in (' ', '-'))
        new = sum(1 for l in body if l[:1] in (' ', '+'))
        declared = (int(match.group(2) or 1), int(match.group(4) or 1))
        header = lines[i] if declared == (old, new) else f'@@ -{match.group(1)},{old} +{match.group(3)},{new} @@{match.group(5)}'
        out.append(header); out.extend(body); i = j
    return '\n'.join(out)


def _in_verifier_reset_scope(path):
    """Paths ScaleSWE's test.sh checks out from the base commit or removes before applying f2p.patch."""
    return PurePosixPath(path).parts[0] in RESET_TEST_DIRS or bool(TEST_MODULE_RE.search(path))


def repair_added_test_fixtures(contents, task_id):
    """Give the verifier the complete post-PR test tree.

    Scoring rebuilds the test tree in two steps: reset the test directories to
    the base commit (so agent edits to tests are discarded), then apply
    tests/f2p.patch, the tests that verify the fix. The pull request also added
    or changed test-support files under those directories (fixtures, recorded
    cassettes, sample inputs), but f2p.patch leaves them out. The reset removes
    them, so the selected tests fail even with the reference solution, and no
    agent can make them pass because anything it creates there is reset too.

    This repair appends the pull request's own diff for exactly those files to
    f2p.patch: files under the top-level test directories that f2p.patch does
    not already contain, excluding source files and new test modules that the
    selected test IDs do not use. Hunk headers whose counts no longer match
    their lines (a truncated final context line in the dataset) are corrected so
    the combined patch applies as one. The reset and every other gold change are
    untouched. It applies only to tasks whose reference failed for this reason.
    """
    if task_id not in FIXTURE_RESTORATION_TASKS and task_id not in TEST_FIXTURE_REPAIRS:
        return []
    gold = contents.get('solution/gold.patch', b'').decode(errors='replace')
    if not gold or 'tests/f2p.patch' not in contents:
        return []
    f2p = contents['tests/f2p.patch'].decode(errors='replace')
    gold_chunks, f2p_chunks = _diff_chunks(gold), _diff_chunks(f2p)
    try:
        selected_files = {entry.split('::')[0] for entry in json.loads(contents.get('tests/test_ids.json', b'[]').decode())}
    except ValueError:
        selected_files = set()
    restored, additions = [], []
    for path, chunk in gold_chunks.items():
        if path in f2p_chunks or 'deleted file mode ' in chunk or not _in_verifier_reset_scope(path):
            continue
        if PurePosixPath(path).parts[0] not in RESET_TEST_DIRS:
            continue  # test-named files outside the test trees stay with the gold/f2p split
        if TEST_MODULE_RE.search(path) and not ('new file mode ' in chunk and path in selected_files):
            continue
        additions.append(chunk)
        restored.append(path)
    required = set(TEST_FIXTURE_REPAIRS.get(task_id, ()))
    if required - set(restored) - set(f2p_chunks):
        raise ValueError(f'Reviewed test fixtures not restored for {task_id}: {sorted(required - set(restored) - set(f2p_chunks))}')
    if not additions:
        return []
    f2p = _repair_hunk_headers(f2p)
    additions = [_repair_hunk_headers(chunk) for chunk in additions]
    separator = '' if f2p.endswith('\n') or not f2p else '\n'
    contents['tests/f2p.patch'] = (f2p + separator + '\n'.join(additions)).encode()
    return [{'file': 'tests/f2p.patch', 'phase': 'verifier-test-fixture-restoration',
             'paths': restored,
             'reason': FIXTURE_RESTORATION_REASON}]


# Explicitly reviewed instruction corrections; keep the task's other hints intact.
INSTRUCTION_CORRECTIONS = {
    'appium_python-client_pr517': (
        'b1f4a2dc528408bb6651e198b76963c04069b6558453c3da42233ed54848bea2',
        [('Attempt to run iOS functional tests in parallel using the `-n` flag.',
          'Attempt to run iOS functional tests in parallel using the existing test file:'),
         ('pytest -n 2 test/functional/ios/find_by_ios_class_chain_tests.py',
          'pytest -n 2 /workspace/python-client/test/functional/ios/search_context/find_by_ios_class_chain_tests.py')]),
    'beetbox_mediafile_pr86': (
        '25ddb4ef83ecc81c538044f8bf4ebfeb367be92adb7a8e2eb5ea283927184e24',
        [('**`mediafile/utils.py`**: For general utility functions and helpers.',
          '**`/workspace/mediafile/mediafile/utils/`**: For general utility functions and helpers.')]),
}


def patch_blob(blob, diagnostic=None, decisions=(), oracle_passed=False, task_id=None, automatic_paths=False):
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        members = archive.getmembers()
        contents = {}
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe task archive member')
            if member.isfile():
                if str(name) in contents:
                    raise ValueError('Duplicate archive member')
                contents[str(name)] = archive.extractfile(member).read()
        if (decisions or automatic_paths) and files_digest(contents.items()) != (diagnostic or {}).get('agent_baseline', {}).get('task_sha256'):
            raise ValueError('Task changed since path resolution')
        original = contents['instruction.md'].decode()
        changed, edits = (apply_reviewed_replacements(original, diagnostic, decisions, oracle_passed=oracle_passed)
                          if decisions else (original, []))
        if automatic_paths:
            from validation.checks.instruction_paths import automatic
            changed, edits = automatic(original, diagnostic, oracle_passed)
            # Task-specific corrections take precedence over generic spans.
            protected = []
            for old, _ in INSTRUCTION_CORRECTIONS.get(task_id, ('', []))[1]:
                start = original.find(old)
                if start >= 0:
                    protected.append((start, start + len(old)))
            edits = [e for e in edits if not any(e['start'] < end and start < e['end'] for start, end in protected)]
            changed = original
            for edit in reversed(edits):
                changed = changed[:edit['start']] + edit['new'] + changed[edit['end']:]
        if task_id in INSTRUCTION_CORRECTIONS:
            digest, replacements = INSTRUCTION_CORRECTIONS[task_id]
            if hashlib.sha256(original.encode()).hexdigest() != digest:
                raise ValueError('Instruction changed since task-specific correction review')
            for old, new in replacements:
                if changed.count(old) != 1:
                    raise ValueError('Reviewed instruction correction is missing or conflicts with another edit')
                changed = changed.replace(old, new, 1)
                edits.append({'old': old, 'new': new, 'phase': 'explicit-instruction-review',
                              'reason': 'User-approved minimal path correction; preserve remaining task wording'})
        edits.extend(repair_verifier_imports(contents, task_id))
        edits.extend(repair_collection_imports(contents, task_id))
        edits.extend(repair_timeout_verifiers(contents, task_id))
        edits.extend(repair_exact_test_scoring(contents, task_id))
        edits.extend(repair_argcomplete_debug_stream(contents, task_id))
        edits.extend(repair_werkzeug_request_cleanup(contents, task_id))
        edits.extend(repair_added_test_fixtures(contents, task_id))
        edits.extend(repair_skipped_required_tests(contents, task_id))
        if not edits:
            return blob, []
        contents['instruction.md'] = changed.encode()
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as target:
            for member in members:
                member = copy.copy(member)
                if member.isfile():
                    data = contents[str(PurePosixPath(member.name))]
                    member.size = len(data)
                    target.addfile(member, io.BytesIO(data))
                else:
                    target.addfile(member)
        return output.getvalue(), edits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new output directory')
    parser.add_argument('--resolution-report', type=Path, action='append', default=[])
    parser.add_argument('--automatic-paths', action='store_true', help='use shared automatic normalization, without a review file')
    parser.add_argument('--review', type=Path, help='review JSON: tasks maps IDs to decisions and unresolved reasons')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a new output directory')
    review = json.loads(args.review.read_text())['tasks'] if args.review else {}
    evidence = collect_reports(args.resolution_report)
    source = pq.read_table(args.source)
    rows = source.to_pylist()
    names = {row['path'] for row in rows}
    if set(review) - names:
        raise ValueError('Review contains tasks outside the source')
    labels, reasons, unresolved, changes = {}, {}, {}, {}
    dropped = {name: ARCHIVED_TASKS[name] for name in names & ARCHIVED_TASKS.keys()}
    rows = [row for row in rows if row['path'] not in dropped]
    for row in rows:
        name = row['path']
        decision = review.get(name, {})
        automatic_paths = args.automatic_paths and name in evidence and bool(evidence[name]['selected'].get('diagnostic', {}).get('agent_baseline', {}).get('task_sha256'))
        selected = evidence[name]['selected'] if automatic_paths or decision.get('replacements') else {}
        row['task_binary'], edits = patch_blob(
            row['task_binary'], selected.get('diagnostic'), decision.get('replacements', []),
            oracle_passed=selected.get('status') == 'passed' and bool(selected.get('rewards')) and all(r == 1 for r in selected['rewards']),
            task_id=name, automatic_paths=automatic_paths)
        if not edits:
            continue
        changes[name] = edits
        labels[name] = []
        descriptions = []
        if any(e.get('phase') not in ('verifier-import-path', 'verifier-call-time-imports', 'verifier-worker-cleanup', 'verifier-exact-test-ids', 'verifier-debug-stream-isolation', 'verifier-request-cleanup', 'verifier-cpu-affinity', 'verifier-test-fixture-restoration', 'verifier-required-test-skipped') for e in edits):
            labels[name].append('instruction-path-clarified')
            descriptions.append('User-approved minimal instruction correction' if name in INSTRUCTION_CORRECTIONS
                                else 'Absolute paths supported by task-setup/reference-solution observations')
        if any(e.get('phase') == 'verifier-import-path' for e in edits):
            labels[name].append('verifier-import-path')
            descriptions.extend(e['reason'] for e in edits if e.get('phase') == 'verifier-import-path')
        fixture_repairs = [e for e in edits if e.get('phase') == 'verifier-test-fixture-restoration']
        if fixture_repairs:
            labels[name].append('verifier-test-fixture-restoration')
            descriptions.extend(e['reason'] for e in fixture_repairs)
        if any(e.get('phase') == 'verifier-call-time-imports' for e in edits):
            labels[name].append('verifier-call-time-imports')
            descriptions.append('Move reviewed feature imports into consuming test functions so missing functionality fails during test execution; preserve selected tests, assertions and reward rules')
        cleanup = [e['reason'] for e in edits if e.get('phase') == 'verifier-worker-cleanup']
        if cleanup:
            labels[name].append('verifier-worker-cleanup')
            descriptions.extend(cleanup)
        exact_ids = [e['reason'] for e in edits if e.get('phase') == 'verifier-exact-test-ids']
        if exact_ids:
            labels[name].append('verifier-exact-test-ids')
            descriptions.extend(exact_ids)
        debug_stream = [e['reason'] for e in edits if e.get('phase') == 'verifier-debug-stream-isolation']
        if debug_stream:
            labels[name].append('verifier-debug-stream-isolation')
            descriptions.extend(debug_stream)
        request_cleanup = [e['reason'] for e in edits if e.get('phase') == 'verifier-request-cleanup']
        if request_cleanup:
            labels[name].append('verifier-request-cleanup')
            descriptions.extend(request_cleanup)
        cpu_affinity = [e['reason'] for e in edits if e.get('phase') == 'verifier-cpu-affinity']
        if cpu_affinity:
            labels[name].append('verifier-cpu-affinity')
            descriptions.extend(cpu_affinity)
        skipped = [e['reason'] for e in edits if e.get('phase') == 'verifier-required-test-skipped']
        if skipped:
            labels[name].append('verifier-required-test-skipped')
            descriptions.extend(skipped)
        reasons[name] = '; '.join(descriptions)
        if decision.get('unresolved'):
            unresolved[name] = decision['unresolved']
    args.output.mkdir(parents=True)
    output = args.output / 'tasks.parquet'
    from validation.checks.path_cache import KEY, blob_fingerprint, completed
    cache = {row['path']: blob_fingerprint(row['task_binary']) for row in rows
             if row['path'] in evidence and completed(evidence[row['path']]['selected']['diagnostic'])}
    metadata = dict(source.schema.metadata or {})
    metadata[KEY] = json.dumps(cache).encode()
    pq.write_table(pa.Table.from_pylist(rows, schema=source.schema).replace_schema_metadata(metadata), output)
    (args.output / 'path-edits.json').write_text(json.dumps(changes, indent=2)+'\n')
    (args.output / 'unresolved-paths.json').write_text(json.dumps(unresolved, indent=2)+'\n')
    write_patch_report(args.source, output, patcher=__file__, source={
        'dataset': 'PrimeIntellect/Scale-SWE-Verified', 'revision': REVISION,
        'url': f'https://huggingface.co/datasets/PrimeIntellect/Scale-SWE-Verified/tree/{REVISION}'},
        dropped=dropped, change_labels=labels, change_reasons=reasons,
        patches=[{'version': 'absolute-paths-v1', 'resolver_reports': [str(p.resolve()) for p in args.resolution_report],
                  'automatic_paths': args.automatic_paths, 'review': str(args.review.resolve()) if args.review else None},
                 {'version': 'verifier-import-path-v2',
                  'tasks': sorted(name for name in changes if name in VERIFIER_IMPORT_TASKS)},
                 {'version': 'reviewed-collection-repairs-v1', 'tasks': sorted(set(changes) & DEFERRED_IMPORTS.keys()),
                  'archived_tasks': sorted(dropped)},
                 {'version': 'gold-added-test-fixtures-v1',
                  'tasks': sorted(name for name, task_edits in changes.items()
                                  if any(e.get('phase') == 'verifier-test-fixture-restoration'
                                         for e in task_edits))},
                 {'version': 'skipped-required-tests-v1', 'tasks': sorted(name for name in changes if name in SKIPPED_REQUIRED_TESTS)}])
    print(json.dumps({'tasks': len(rows), 'changed': len(changes), 'output': str(output)}))


if __name__ == '__main__':
    main()
