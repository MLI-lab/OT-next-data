"""Check local tool transport, reasoning history, and truncation handling."""
import importlib.util
import json
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest

path = Path(__file__).resolve().parents[1] / 'validation/rubrics/run_review.py'
spec = importlib.util.spec_from_file_location('local_judge', path)
judge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(judge)


@pytest.mark.parametrize('truncated', [False, True])
def test_tool_roundtrip(tmp_path, monkeypatch, truncated):
    script = tmp_path / 'mcp.py'
    script.write_text('''import json,sys
for line in sys.stdin:
 r=json.loads(line)
 result={'tools':[{'name':'inspect','description':'Isolated inspection','inputSchema':{'type':'object'}}]} if r['method']=='tools/list' else {'content':[{'type':'text','text':'task evidence'}]}
 print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}),flush=True)
''')
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(payload)
            message = ({'role':'assistant','content':None,'reasoning_content':'inspect first',
                'tool_calls':[{'id':'call1','type':'function','function':{'name':'inspect','arguments':'{"command":"cat instruction.md"}'}}]}
                if len(seen)==1 else {'role':'assistant','content':'{"checks":{}}'})
            body=json.dumps({'choices':[{'message':message,'finish_reason':'length' if truncated else 'tool_calls' if len(seen)==1 else 'stop'}],
                             'usage':{'prompt_tokens':10,'completion_tokens':5}}).encode()
            self.send_response(200);self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('LOCAL_JUDGE_URL',f'http://127.0.0.1:{server.server_port}/v1')
    config={'mcpServers':{'image':{'command':sys.executable,'args':[str(script),'--log',str(tmp_path/'calls.jsonl')]}}}
    try:
        if truncated:
            with pytest.raises(ValueError,match='output budget'):judge.run_local('test',config,'blind prompt')
        else:
            result=judge.run_local('test',config,'blind prompt')
            assert result['modelUsage']['test']=={'input_tokens':20,'output_tokens':10}
            assert seen[1]['messages'][1]['reasoning_content']=='inspect first'
            assert seen[1]['messages'][2]['tool_call_id']=='call1'
            assert result['total_cost_usd'] is None
    finally:
        server.shutdown();server.server_close();thread.join()
