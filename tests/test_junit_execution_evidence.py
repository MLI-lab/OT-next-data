"""JUnit-report, Maven and CTest execution evidence (harbor_patches/ot_execution)."""
import os
import subprocess
from pathlib import Path

from validation.stages.execution_evidence import scope_output

SHIMS = Path(__file__).resolve().parents[1] / 'harbor_patches/ot_execution'
TOKEN = 'c' * 32


def report(path, tests, failures=0, errors=0, skipped=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'<?xml version="1.0"?>\n<testsuite name="x" time="1"\n tests="{tests}" failures="{failures}" '
                    f'errors="{errors}" skipped="{skipped}">\n<testcase name="a"/></testsuite>\n')


def scan(tmp_path, *roots):
    out = tmp_path / 'ot-junit-execution.txt'
    subprocess.run(['sh', str(SHIMS / 'junit_reports.sh'), TOKEN, str(tmp_path / 'start'), str(out), *map(str, roots)],
                   check=True)
    return scope_output(out.read_text() if out.exists() else '', TOKEN)[1]


def test_reports_written_during_the_call_are_recorded(tmp_path):
    repo = tmp_path / 'repo'
    report(repo / 'old/target/surefire-reports/TEST-Old.xml', 50)
    os.utime(repo / 'old/target/surefire-reports/TEST-Old.xml', (1_000_000_000, 1_000_000_000))
    (tmp_path / 'start').touch()
    os.utime(tmp_path / 'start', (1_500_000_000, 1_500_000_000))
    report(repo / 'a/target/surefire-reports/TEST-A.xml', 10, failures=2, skipped=1)
    report(repo / 'b/build/test-results/test/TEST-B.xml', 5)
    runs = scan(tmp_path, repo)
    assert [(r['executed'], r['exitstatus'], r['reports']) for r in runs] == [(14, 1, 2)]


def test_no_reports_means_no_record(tmp_path):
    (tmp_path / 'start').touch()
    assert scan(tmp_path, tmp_path) == []


def _fake(tmp_path, name, body):
    real = tmp_path / 'real'
    real.mkdir(exist_ok=True)
    (real / name).write_text('#!/bin/sh\n' + body)
    (real / name).chmod(0o755)
    return {**os.environ, 'PATH': f'{SHIMS / "bin"}:{real}:/usr/bin:/bin', 'OT_VERIFIER_EXECUTION_TOKEN': TOKEN,
            'TMPDIR': str(tmp_path)}


def test_maven_failure_before_any_test_is_a_collection_error(tmp_path):
    env = _fake(tmp_path, 'mvn', 'echo "[ERROR] COMPILATION ERROR"; exit 1\n')
    result = subprocess.run(['mvn', 'clean', 'test'], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    (run,) = scope_output(result.stdout, TOKEN)[1]
    assert (run['executed'], run['collection_errors'], run['exitstatus']) == (0, 1, 2)


def test_maven_with_reports_leaves_counting_to_the_reports(tmp_path):
    env = _fake(tmp_path, 'mvn', 'mkdir -p target/surefire-reports && '
                'printf \'<testsuite tests="3" failures="0" errors="0" skipped="0"></testsuite>\' '
                '> target/surefire-reports/TEST-X.xml; exit 0\n')
    result = subprocess.run(['mvn', 'test'], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0 and scope_output(result.stdout, TOKEN)[1] == []


def test_nested_maven_and_calls_without_token_run_the_real_binary(tmp_path):
    env = _fake(tmp_path, 'mvn', 'exit 3\n')
    for extra in ({'OT_MAVEN_EXECUTION_ACTIVE': TOKEN}, {'OT_VERIFIER_EXECUTION_TOKEN': ''}):
        result = subprocess.run(['mvn'], cwd=tmp_path, env={**env, **extra}, capture_output=True, text=True)
        assert result.returncode == 3 and 'OT_VERIFIER_EXECUTION' not in result.stdout


def test_ctest_writes_its_junit_report_into_the_results(tmp_path):
    results = tmp_path / 'results'
    results.mkdir()
    env = _fake(tmp_path, 'ctest', 'if [ "$1" = --help ]; then echo "  --output-junit <file>"; exit 0; fi\n'
                'for a in "$@"; do [ "$prev" = --output-junit ] && '
                'printf \'<testsuite tests="7" failures="1" disabled="0" skipped="0"></testsuite>\' > "$a"; prev=$a; done\n'
                'exit 8\n')
    (tmp_path / 'start').touch()
    os.utime(tmp_path / 'start', (1_500_000_000, 1_500_000_000))
    result = subprocess.run(['ctest', '-j', '4'], cwd=tmp_path, env={**env, 'OT_VERIFIER_EXECUTION_DIR': str(results)},
                            capture_output=True, text=True)
    assert result.returncode == 8
    assert [(r['executed'], r['exitstatus']) for r in scan(tmp_path, results)] == [(7, 1)]


def test_git_proxy_is_added_for_github_only_and_removed(tmp_path):
    from harbor_patches import verifier_setup
    target = tmp_path / 'target'
    target.mkdir()
    config = tmp_path / 'gitconfig'
    env = {**os.environ, 'GIT_CONFIG_SYSTEM': str(config), 'https_proxy': 'http://proxy.example:80'}
    subprocess.run(['sh', '-c', verifier_setup.git_proxy_command(str(target))], env=env, check=True)
    get = lambda key: subprocess.run(['git', 'config', '--system', '--get', key], env=env, capture_output=True, text=True).stdout.strip()
    assert get(verifier_setup.GIT_PROXY_KEY) == 'http://proxy.example:80' and get('http.proxy') == ''
    subprocess.run(['sh', '-c', verifier_setup.remove_git_proxy_command(str(target))], env=env, check=True)
    assert get(verifier_setup.GIT_PROXY_KEY) == ''


def test_image_git_proxy_setting_is_kept(tmp_path):
    from harbor_patches import verifier_setup
    target = tmp_path / 'target'
    target.mkdir()
    config = tmp_path / 'gitconfig'
    config.write_text('[http]\n\tproxy = http://image-proxy:3128\n')
    env = {**os.environ, 'GIT_CONFIG_SYSTEM': str(config), 'https_proxy': 'http://proxy.example:80'}
    subprocess.run(['sh', '-c', verifier_setup.git_proxy_command(str(target)) + '; '
                    + verifier_setup.remove_git_proxy_command(str(target))], env=env, check=True)
    assert config.read_text() == '[http]\n\tproxy = http://image-proxy:3128\n'
