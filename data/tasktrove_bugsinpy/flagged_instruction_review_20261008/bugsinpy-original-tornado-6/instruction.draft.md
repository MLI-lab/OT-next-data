# Task

Work in the tornado project at `/app` to fix the issue described below.

# Issue

Synchronous HTTPClient leaks memory in 5.0.1

The synchronous HTTPClient leaks memory for each request made since upgrading to Tornado 5.0.1.

```python
from tornado import httpclient

while True:
    try:
        http_client = httpclient.HTTPClient()
        response = http_client.fetch("http://localhost")
    except Exception:
        pass
    finally:
        http_client.close()
```

Running this script for 3 minutes in docker (`python:3.6-stretch`): 
- `tornado==4.5.3`: 18.52MiB / 1.952GiB
- `tornado==5.0.1`: 350.28MiB / 1.952GiB

Note, I cannot seem to reproduce the issue on macOS.

Modify the project files to resolve this issue.

You have 900 seconds to complete this task. Do not cheat by using online solutions or hints specific to this task.
