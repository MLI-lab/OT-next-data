HTTPie (source in `/app`) shows a duplicate `Host` header in the printed request headers when the user supplies a custom `Host` header in a case other than `Host` (e.g. `host:example.org`).

Reproduction (request to an IP address, overriding the host with a lowercase header name):

```
http --print=hH http://<ip-of-httpbin.org>/get host:httpbin.org
```

The printed request headers contain both `host: httpbin.org` and an auto-added `Host: <ip>` line.

Expected behavior: header names are case-insensitive, so when the user provides a `Host` header in any letter case, HTTPie must not add its own. The printed output should contain exactly one `Host` header (counted case-insensitively), and the request should still succeed (`HTTP/1.1 200 OK`).
