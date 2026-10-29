"""Compute-node network namespace diagnostic; container validation is separate."""
import fcntl
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys


def connect(family, address):
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(3)
        try:
            sock.connect(address)
            return {'connected': True}
        except OSError as exc:
            return {'connected': False, 'errno': exc.errno, 'error': str(exc)}


def inspect():
    return {'namespace': os.readlink('/proc/self/ns/net'),
            'ipv4_routes': Path('/proc/net/route').read_text(),
            'ipv6_routes': Path('/proc/net/ipv6_route').read_text(),
            'external_ipv4': connect(socket.AF_INET, ('1.1.1.1', 443)),
            'external_ipv6': connect(socket.AF_INET6, ('2606:4700:4700::1111', 443))}


def isolated(configure_loopback=True):
    # Bring up only loopback, retaining no external interface or default route.
    if configure_loopback:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
            fcntl.ioctl(control, 0x8914, struct.pack('16sH14x', b'lo', 1))
    result = inspect()
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        result['localhost_tcp'] = connect(socket.AF_INET, server.getsockname())['connected']
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(3)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b'probe', receiver.getsockname())
        result['localhost_udp'] = receiver.recv(32) == b'probe'
    result['passed'] = (result['localhost_tcp'] and result['localhost_udp']
                        and all(result[k].get('errno') in (101, 113)
                                for k in ('external_ipv4', 'external_ipv6')))
    print(json.dumps(result), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    if '--isolated' in sys.argv or '--container' in sys.argv:
        sys.exit(isolated(configure_loopback='--container' not in sys.argv))
    result = {'host': socket.gethostname(), 'job': os.environ.get('SLURM_JOB_ID'),
              'max_net_namespaces': Path('/proc/sys/user/max_net_namespaces').read_text().strip(),
              'baseline': inspect(), 'scope': 'kernel namespace, not task container'}
    child = subprocess.run(['unshare', '--user', '--map-root-user', '--net',
                            sys.executable, __file__, '--isolated'],
                           capture_output=True, text=True, timeout=30)
    result.update(returncode=child.returncode, stdout=child.stdout, stderr=child.stderr)
    if child.returncode == 0:
        result['isolated'] = json.loads(child.stdout)
        result['passed'] = (result['isolated']['passed'] and
                            result['isolated']['namespace'] != result['baseline']['namespace'])
    else:
        result['passed'] = False
    print(json.dumps(result, indent=2), flush=True)
    sys.exit(0 if result['passed'] else 1)
