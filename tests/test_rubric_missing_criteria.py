from pathlib import Path
import tomllib
from validation.rubrics.utils.review import merge, subset_rubric, valid_checks


def check(outcome):
    return {'outcome': outcome, 'explanation': 'A concrete explanation.'}


def test_repair_cannot_overwrite_valid_judgments():
    old={'checks':{'agentic':check('fail'),'instruction_quality':check('pass')}}
    retry={'checks':{'agentic':check('pass'),'solution_quality':check('pass')}}
    result, origins=merge(old,retry,{'agentic','solution_quality','instruction_concision'})
    assert result['checks']=={'agentic':check('fail'),'solution_quality':check('pass')}
    assert origins=={'agentic':'original','solution_quality':'retry'}
    assert 'instruction_concision' not in result['checks']
    assert old['checks']['agentic']['outcome']=='fail'


def test_invalid_judgment_stays_missing():
    data={'checks':{'agentic':{'outcome':'pass','explanation':''},'solution_quality':check('maybe')}}
    assert valid_checks(data,{'agentic','solution_quality'})=={}


def test_subset_preserves_full_criterion_wording():
    original='''# rubric\n[[criteria]]\nname = "first"\nguidance = """Contains ``` fenced code ``` and newlines.\nSecond line."""\n[[criteria]]\nname = "second"\nguidance = "Different criterion"\n'''
    reduced=subset_rubric(original,{'first'})
    assert tomllib.loads(reduced)['criteria']==tomllib.loads(original)['criteria'][:1]
