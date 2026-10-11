"""Keep command < lifecycle RPC < result-wait deadlines.

Pinned Harbor can stop polling before our lifecycle RPC finishes; both client
and worker use these grace periods so a timeout never triggers duplicate work.
"""
EXEC_RPC_GRACE = 60.0
EXEC_RESULT_GRACE = 30.0
# Directory upload: mkdir including safe stall retries (up to 330 seconds
# with the default worker policy), extraction (120), verification (60),
# plus staging and RPC delivery. The enclosing preparation/verifier budget
# still limits the whole phase.
UPLOAD_RPC_TIMEOUT = 600.0
UPLOAD_RESULT_GRACE = 30.0


def upload_result_timeout():
    return UPLOAD_RPC_TIMEOUT + UPLOAD_RESULT_GRACE


def exec_rpc_timeout(command_timeout):
    return float(command_timeout or 600) + EXEC_RPC_GRACE


def exec_result_timeout(command_timeout):
    return exec_rpc_timeout(command_timeout) + EXEC_RESULT_GRACE
