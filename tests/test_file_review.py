import json
from pathlib import Path
import tempfile
import unittest
from validation.rubrics.utils.file_review import Files, definition
from validation.rubrics.utils.evaluation import CONFIGS, prediction


class FileReviewTests(unittest.TestCase):
    def test_paths_and_paging(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'task';root.mkdir()
            (root/'test.py').write_text('one\ntwo\nthree\n')
            secret=Path(d)/'private';secret.write_text('secret')
            (root/'escape').symlink_to(secret)
            fs=Files(root,'rubric')
            args=dict(action='read',path='test.py',start=1,count=1,filter='')
            self.assertEqual(fs.invoke('inspect_files',args)['text'],'2: two')
            for bad in ('../private','escape',str(secret)):
                with self.assertRaises(ValueError):fs.invoke('inspect_files',{**args,'path':bad})
            listing=fs.invoke('inspect_files',{**args,'action':'list','path':'.','start':0,'count':100})
            self.assertEqual(listing['files'],['test.py'])
            with self.assertRaises(ValueError):fs.invoke('shell',{})

    def test_submit_requires_inspection_and_valid_schema(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'instruction.md').write_text('task')
            fs=Files(root,'[[criteria]]\nname="one"\n')
            verdict={'checks':{'one':{'outcome':'pass','explanation':'Evidence'}}}
            with self.assertRaises(ValueError):fs.invoke('submit_review',{'verdict_json':json.dumps(verdict)})
            fs.invoke('inspect_files',dict(action='read',path='instruction.md',start=0,count=1,filter=''))
            with self.assertRaises(ValueError):fs.invoke('submit_review',{'verdict_json':'{}'})
            self.assertTrue(fs.invoke('submit_review',{'verdict_json':json.dumps(verdict)})['accepted'])

    def test_all_six_templates_load_and_not_applicable_is_neutral(self):
        for key,rubric,fmt,_ in CONFIGS:
            text,skips,prompt=definition({'rubric':rubric,'format':fmt},Path('/tmp/task'))
            self.assertIn('FILE-ONLY',prompt)
            self.assertTrue(text)
        self.assertEqual(prediction({'status':'passed','verdicts':[{'checks':{
            'a':{'outcome':'pass'},'b':{'outcome':'not_applicable'}}}]}),0)
