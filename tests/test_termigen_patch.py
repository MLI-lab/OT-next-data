import pytest

from data.termigen import patch as P

BASE = b"FROM x\nRUN pip3 install pandas==2.3.2 scipy==1.16.1 pytest==8.4.1\n"


def test_r2_pins_from_same_dockerfile_and_numpy_keeping_other_bytes():
    original = BASE + b"RUN pip3 install pandas numpy \\\n    scipy && echo hi\nCOPY a /w/\n"
    new, rule, replacements = P.pin_dockerfile(original, {})
    assert rule == "R2"
    assert new == BASE + b"RUN pip3 install pandas==2.3.2 numpy==%s \\\n    scipy==1.16.1 && echo hi\nCOPY a /w/\n" % P.NUMPY_PIN.encode()
    assert replacements == {"pandas": "pandas==2.3.2", "numpy": f"numpy=={P.NUMPY_PIN}", "scipy": "scipy==1.16.1"}
    assert not P.flagged(new.decode())[0]


def test_r2_skips_dockerfile_with_other_unpinned_package():
    assert P.pin_dockerfile(BASE + b"RUN pip install torch numpy\n", {}) is None


def test_r2_conflicting_pins_raise():
    with pytest.raises(ValueError):
        P.pin_dockerfile(BASE + b"RUN pip install pandas==1.0\nRUN pip install pandas\n", {})


def test_r3_uses_lock_only_when_every_name_is_locked():
    original = BASE + b"RUN pip install torch 'dask[complete]' yara-python\n".replace(b"'", b"")
    key = P.sha256(original)
    partial = {key: {"versions": {"torch": "2.0.0"}}}
    assert P.pin_dockerfile(original, partial) is None
    full = {key: {"versions": {"torch": "2.0.0", "dask": "1.0", "yara_python": "4.5.4"}}}
    new, rule, _ = P.pin_dockerfile(original, full)
    assert rule == "R3" and b"torch==2.0.0 dask[complete]==1.0 yara-python==4.5.4" in new


def test_r3_locked_version_must_satisfy_specifier():
    original = BASE + b"RUN pip install torch>=2.1\n"
    with pytest.raises(ValueError):
        P.pin_dockerfile(original, {P.sha256(original): {"versions": {"torch": "1.0"}}})


def test_r1_copy_sources_ignore_globs_urls_and_stages():
    text = "COPY --from=b /x /y\nCOPY a.pyc b/ /w/\nCOPY *.txt /w/\nADD http://h/f /w/\n"
    assert list(P.copy_sources(text)) == ["a.pyc", "b"]
