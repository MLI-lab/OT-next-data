"""MCP stdio client for the review's local, authenticated container inspector."""
import json
import os
import socket
import sys


def exchange(port, token, arguments):
    with socket.create_connection(('127.0.0.1', port), timeout=125) as connection:
        connection.sendall((json.dumps({'token': token, **arguments}) + '\n').encode())
        with connection.makefile('rb') as stream:
            return json.loads(stream.readline(1000000))


def serve(port, token):
    for line in sys.stdin:
        request = json.loads(line)
        if 'id' not in request:
            continue
        response = {'jsonrpc': '2.0', 'id': request['id']}
        method = request['method']
        if method == 'initialize':
            result = {'protocolVersion': request['params']['protocolVersion'], 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'validation-task-image-inspector', 'version': '1.0'}}
        elif method == 'tools/list':
            result = {'tools': [{'name': 'inspect', 'description': 'Execute a shell command in a fresh task container. The host and cached image are not writable through this tool. Review-only task files are under /app/task-under-review; write /app/verdicts.json in the agent container. Select agent or a listed separate verifier environment. Changes persist only within this disposable review attempt.',
                      'inputSchema': {'type': 'object', 'properties': {'command': {'type': 'string'}, 'environment': {'type': 'string', 'default': 'agent'}}, 'required': ['command'], 'additionalProperties': False}}]}
        elif method == 'tools/call':
            try:
                params = request.get('params', {})
                if params.get('name') != 'inspect':
                    raise ValueError('Expected inspect tool')
                payload = exchange(port, token, params.get('arguments', {}))
            except Exception as exc:
                payload = {'error': str(exc)}
            result = {'content': [{'type': 'text', 'text': json.dumps(payload)}], 'isError': 'error' in payload}
        else:
            response['error'] = {'code': -32601, 'message': 'Unsupported method'}
            print(json.dumps(response), flush=True)
            continue
        response['result'] = result
        print(json.dumps(response), flush=True)


if __name__ == '__main__':
    serve(int(sys.argv[1]), os.environ['OT_REVIEW_INSPECT_TOKEN'])
