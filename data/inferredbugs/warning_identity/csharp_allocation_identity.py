"""Prove statement-start versus nested-call source locations using C# syntax."""
import re
from tree_sitter_languages import get_parser


def allocation_location_proof(expected, actual, source):
    old,new=expected.get('qualifier',''),actual.get('qualifier','')
    if old==new or re.sub(r'at Line \d+', 'at Line <site>',old)!=re.sub(r'at Line \d+', 'at Line <site>',new):
        return None
    calls=re.findall(r'(?:output of|returned from) (.*?) at Line (\d+)',old)
    new_lines=re.findall(r'(?:output of|returned from) .*? at Line (\d+)',new)
    if not calls or len(calls)!=len(new_lines):return None
    tree=get_parser('c_sharp').parse(source)

    def walk(node):
        yield node
        for child in node.named_children:yield from walk(child)

    nodes=list(walk(tree.root_node))
    def call_name(node,constructor):
        if constructor and node.type=='object_creation_expression':
            typ=node.child_by_field_name('type')
            return typ.text.decode().split('.')[-1] if typ else None
        if not constructor and node.type=='invocation_expression':
            func=node.child_by_field_name('function')
            if func:
                name=func.child_by_field_name('name') or func
                return name.text.decode()
        return None

    proof=[]
    for (signature,old_line),new_line in zip(calls,new_lines):
        old_line,new_line=int(old_line),int(new_line)
        if old_line==new_line:continue
        if '::' not in signature:return None
        owner,method=signature.rsplit('::',1)
        constructor=method=='.ctor()'
        name=owner.split('.')[-1].split('$')[-1] if constructor else method.split('(')[0]
        candidates=[n for n in nodes if n.start_point[0]+1==new_line and call_name(n,constructor)==name]
        if len(candidates)!=1:return None
        call=candidates[0];statement=call.parent
        while statement is not None and not statement.type.endswith('_statement'):
            statement=statement.parent
        if statement is None or statement.has_error or statement.start_point[0]+1!=old_line:return None
        same_calls=[n for n in walk(statement) if call_name(n,constructor)==name]
        if len(same_calls)!=1:return None
        proof.append({'signature':signature,'statement_start_line':old_line,'call_start_line':new_line,
                      'statement':statement.text.decode(errors='replace'),
                      'call':call.text.decode(errors='replace')})
    return proof or None
