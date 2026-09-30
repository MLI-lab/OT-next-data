"""Narrow Java diagnostic identity proofs; callers still enforce kind/file/method/line."""
import re
from tree_sitter_languages import get_parser


def identity_proof(expected, actual, source):
    if expected.get('bug_type') != actual.get('bug_type'):return None
    old,new=expected.get('qualifier',''),actual.get('qualifier','')
    if expected.get('bug_type')=='NULL_DEREFERENCE':
        a=re.fullmatch(r'object returned by `([^`]+)`\s+at line (\d+)\.',old)
        b=re.fullmatch(r'object returned by `([^`]+)` could be null and is dereferenced at line (\d+)\.',new)
        if a and b and a.groups()==b.groups():
            return [{'rule':'same complete returned expression and dereference line; omitted null boilerplate','expression':a[1],'line':int(a[2])}]
        if old.startswith('object `null` could be null and is dereferenced by call to ') and old.replace('could be null and is','is',1)==new:
            return [{'rule':'same literal null and complete dereference-call text'}]
        return None
    if expected.get('bug_type')!='RESOURCE_LEAK':return None
    pattern=r'resource of type `([^`]+)` acquired(?: to `([^`]+)`)? by call to `([^`]+)` at line (\d+) is not released after line (\d+)\.(.*)'
    a,b=re.fullmatch(pattern,old,re.S),re.fullmatch(pattern,new,re.S)
    if not a or not b or bool(a[2])==bool(b[2]):return None
    if tuple(a[i] for i in (1,3,4,5,6))!=tuple(b[i] for i in (1,3,4,5,6)):return None
    # An omitted variable label is safe only when the same call/type/site identifies
    # one allocation in the real source. Exception notes must match exactly.
    short_type=a[1].replace('$','.').split('.')[-1]
    method=a[3].split('(')[0]
    constructor=method in ('new',short_type)
    tree=get_parser('java').parse(source)
    def walk(n):
        yield n
        for c in n.named_children:yield from walk(c)
    matches=[]
    for node in walk(tree.root_node):
        if node.start_point[0]+1!=int(a[4]) or node.has_error:continue
        if constructor and node.type=='object_creation_expression':
            typ=node.child_by_field_name('type')
            if typ and typ.text.decode().split('<')[0].split('.')[-1]==short_type:matches.append(node)
        elif not constructor and node.type=='method_invocation':
            name=node.child_by_field_name('name')
            if name and name.text.decode()==method:matches.append(node)
    if len(matches)!=1:return None
    return [{'rule':'same resource type, unique source allocation/call, allocation and leak lines, and exception note; one variable label omitted',
             'resource_type':a[1],'allocation_line':int(a[4]),'call':matches[0].text.decode(errors='replace')}]
