"""Run a local OpenAI-compatible judge with the existing isolated MCP inspector."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


def run(model, config, prompt):
    server = config['mcpServers']['image']
    output = Path(server['args'][server['args'].index('--log') + 1]).parent
    process = subprocess.Popen([server['command'], *server['args']], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True,
                               env={**os.environ, **server.get('env', {})})
    request_id = 0
    usage = {'input_tokens': 0, 'output_tokens': 0}
    started = time.monotonic()
    def rpc(method, params):
        nonlocal request_id
        request_id += 1
        process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': request_id,
                                       'method': method, 'params': params}) + '\n')
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        if response.get('id') != request_id or 'error' in response:
            raise RuntimeError(str(response))
        return response['result']
    messages = [{'role': 'user', 'content': prompt}]
    # No provider credentials or external API fallback. Bypass HTTP proxies for localhost.
    endpoint = os.environ['LOCAL_JUDGE_URL'].rstrip('/')
    if not endpoint.startswith('http://127.0.0.1:'):
        raise ValueError('Local judge must use the allocation-local endpoint')
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                           'clientInfo': {'name': 'local-rubric-judge', 'version': '1'}})
        definitions = rpc('tools/list', {})['tools']
        tools = [{'type': 'function', 'function': {'name': t['name'],
                  'description': t['description'], 'parameters': t['inputSchema']}} for t in definitions]
        for turn in range(42):
            payload = {'model': model, 'messages': messages, 'tools': tools,
                       'temperature': 1.0, 'top_p': 0.95, 'max_tokens': 16384,
                       'seed': int(os.environ.get('LOCAL_JUDGE_SEED', '42')),
                       'chat_template_kwargs': {'reasoning_effort': 'max', 'clear_thinking': False}}
            request = urllib.request.Request(endpoint + '/chat/completions',
                        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
            with http.open(request, timeout=max(1, 1750 - (time.monotonic() - started))) as response:
                answer = json.load(response)
            with (output / 'local-events.jsonl').open('a') as stream:
                stream.write(json.dumps(answer) + '\n')
            usage['input_tokens'] += answer.get('usage', {}).get('prompt_tokens', 0)
            usage['output_tokens'] += answer.get('usage', {}).get('completion_tokens', 0)
            choice = answer['choices'][0]
            if choice['finish_reason'] == 'length':
                raise ValueError('Model response exceeded the recorded output budget')
            message = choice['message']
            messages.append({k: v for k, v in message.items() if k in
                             ('role', 'content', 'reasoning_content', 'reasoning', 'tool_calls') and v is not None})
            calls = message.get('tool_calls') or []
            if not calls:
                return {'is_error': False, 'result': message.get('content') or '',
                        'modelUsage': {model: usage}, 'total_cost_usd': None,
                        'elapsed_seconds': time.monotonic() - started,
                        'cost_basis': 'Local inference; no API charge. GPU compute cost not priced.'}
            for call in calls:
                function = call['function']
                if function['name'] != 'inspect':
                    raise ValueError('Unexpected tool: ' + function['name'])
                result = rpc('tools/call', {'name': 'inspect', 'arguments': json.loads(function['arguments'])})
                messages.append({'role': 'tool', 'tool_call_id': call['id'],
                                 'content': json.dumps(result)})
        raise ValueError('Review turn limit reached')
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--mcp-config', required=True)
    args = parser.parse_args()
    try:
        result = run(args.model, json.loads(args.mcp_config), sys.stdin.read())
    except Exception as exc:
        result = {'is_error': True, 'result': str(exc), 'total_cost_usd': None}
    print(json.dumps(result))
