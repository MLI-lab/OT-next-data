"""Source proofs behind REVIEWED_WARNING_PAIRS (data/inferredbugs/warning_identity)."""
from pathlib import Path
import sys
import unittest

import pytest

pytest.importorskip('tree_sitter_languages')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'data/inferredbugs/warning_identity'))
from csharp_allocation_identity import allocation_location_proof
from java_field_paths import Sources
from java_null_resource_identity import identity_proof




class JavaIdentity(unittest.TestCase):
    def test_returned_expression(self):
        e={'bug_type':'NULL_DEREFERENCE','qualifier':'object returned by `x.read().body()`  at line 55.'}
        a={'bug_type':'NULL_DEREFERENCE','qualifier':'object returned by `x.read().body()` could be null and is dereferenced at line 55.'}
        self.assertTrue(identity_proof(e,a,b''))
        a['qualifier']=a['qualifier'].replace('x.read()','y.read()')
        self.assertIsNone(identity_proof(e,a,b''))

    def test_explicit_null(self):
        e={'bug_type':'NULL_DEREFERENCE','qualifier':'object `null` could be null and is dereferenced by call to `f(...)` at line 8.'}
        a={'bug_type':'NULL_DEREFERENCE','qualifier':e['qualifier'].replace('could be null and is','is')}
        self.assertTrue(identity_proof(e,a,b''))
        a['qualifier']=a['qualifier'].replace('f(...)','g(...)')
        self.assertIsNone(identity_proof(e,a,b''))

    def test_unique_allocation_and_exception_path(self):
        source=b'class C { void f() {\n var x = new FileWriter("x");\n use(x); } }'
        e={'bug_type':'RESOURCE_LEAK','qualifier':'resource of type `java.io.FileWriter` acquired to `x` by call to `new()` at line 2 is not released after line 3.\n**Note**: potential exception at line 3'}
        a={'bug_type':'RESOURCE_LEAK','qualifier':e['qualifier'].replace(' acquired to `x` by',' acquired by')}
        self.assertTrue(identity_proof(e,a,source))
        self.assertIsNone(identity_proof(e,a,source.replace(b'new FileWriter("x")',b'new FileWriter("x"); var y = new FileWriter("y")')))
        a['qualifier']=a['qualifier'].replace('exception at line 3','exception at line 2')
        self.assertIsNone(identity_proof(e,a,source))




class AllocationIdentity(unittest.TestCase):
    def setUp(self):
        self.source=b'class C { object F() {\n return new ReadSession(\n ix,\n new DocHashReader(file, ix.Offset),\n file); } }'
        self.old={'qualifier':'Leaked resource (output of DocumentTable.DocHashReader::.ctor() at Line 2) of type DocumentTable.DocHashReader.'}
        self.new={'qualifier':self.old['qualifier'].replace('Line 2','Line 4')}

    def test_nested_call_in_same_statement(self):
        self.assertIsNotNone(allocation_location_proof(self.old,self.new,self.source))

    def test_different_resource(self):
        self.assertIsNone(allocation_location_proof(self.old,{'qualifier':self.new['qualifier'].replace('DocHashReader','OtherReader')},self.source))

    def test_duplicate_constructor_is_ambiguous(self):
        source=self.source.replace(b'ix,\n',b'new DocHashReader(file, ix.Offset),\n')
        self.assertIsNone(allocation_location_proof(self.old,self.new,source))

    def test_different_statement(self):
        source=self.source.replace(b'return new ReadSession(',b'Log(); return new ReadSession(')
        old={'qualifier':self.old['qualifier'].replace('Line 2','Line 1')}
        self.assertIsNone(allocation_location_proof(old,self.new,source))

    def test_extra_resource_not_equivalent(self):
        new={'qualifier':self.new['qualifier']+' Leaked resource another.'}
        self.assertIsNone(allocation_location_proof(self.old,new,self.source))




class MemorySources(Sources):
    def __init__(self, files):
        self.checkout = {'revision': 'test', 'target': 'p/Child.java'}
        self.fixture = files.get('p/Child.java')
        self.paths = list(files)
        self.classes, self.files, self.loading = {}, {}, set()
        self.data = files

    def git(self, *args):
        assert args[0] == 'show'
        return self.data[args[1].split(':', 1)[1]]


class FieldOwnershipTests(unittest.TestCase):
    def sources(self, child=''):
        return MemorySources({
            'p/Child.java': ('package p; class Child extends Base {' + child + '}').encode(),
            'p/Base.java': b'package p; class Base { Holder value; }',
            'p/Holder.java': b'package p; class Holder { int result; }',
        })

    def test_inherited_nested_chain(self):
        s = self.sources()
        info, typ, chain = s.field('p.Child', 'value')
        self.assertEqual(info['name'], 'p.Base')
        self.assertEqual(chain, ['p.Base', 'p.Child'])
        receiver = s.resolve_type(typ, info)
        self.assertEqual(s.field(receiver, 'result')[0]['name'], 'p.Holder')

    def test_shadowing_changes_owner(self):
        s = self.sources('Holder value;')
        self.assertEqual(s.field('p.Child', 'value')[0]['name'], 'p.Child')

    def test_missing_field_is_rejected(self):
        with self.assertRaises(AssertionError):
            self.sources().field('p.Child', 'missing')

    def test_duplicate_class_files_are_rejected(self):
        s = self.sources()
        s.paths.append('another/p/Base.java')
        with self.assertRaises(AssertionError):
            s.field('p.Child', 'value')

    def test_fixture_difference_is_rejected(self):
        s = self.sources()
        s.fixture = b'changed source'
        with self.assertRaises(AssertionError):
            s.field('p.Child', 'value')

    def test_member_class_is_resolved(self):
        s = MemorySources({'p/Outer.java': b'package p; class Outer { Inner value; class Inner { int result; } }'})
        info, typ, _ = s.field('p.Outer', 'value')
        self.assertEqual(s.resolve_type(typ, info), 'p.Outer$Inner')
