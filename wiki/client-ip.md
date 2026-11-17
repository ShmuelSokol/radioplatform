# Client address (who is calling)

One helper decides the caller's address: `backend/app/core/client_ip.py::client_ip`.
Every rate limit (staff login, song requests, CRM PINs, bug reports, web call-ins,
the slowapi default), listener sessions and the activity log use it. Do not read
`X-Forwarded-For` by hand in a new route.

Rule: Railway's proxy appends the address it saw to the right end of
`X-Forwarded-For`. Everything to the left came from the caller and can be made up.
So the address is the **rightmost hop that is not internal** (10/8, 172.16/12,
192.168/16, 100.64/10, fc00::/7, loopback, link-local are skipped, because Railway's
own network can add a private hop after the edge). No header: `X-Real-IP`, then the
socket address.

Why: the old code took the FIRST hop. A script could send `X-Forwarded-For: 6.6.6.6`
and get round the bug-report limit, inflate listener counts from anywhere, or pose as
any address in the activity log. And slowapi's `get_remote_address` used the socket
address, which behind the proxy is the same for everyone: 11 operators signing in
within a minute locked each other out.

Note: the documentation ranges 198.51.100/24 and 203.0.113/24 count as public here
(tests use them). Python's `ip.is_private`/`is_global` would treat them as private,
which is why the helper has its own internal list.

Tests: `tests/test_listener_stats.py::test_spoofed_forwarded_for_is_not_trusted`,
`tests/test_bug_report_limits.py::test_rate_limit_cannot_be_dodged_with_forwarded_for`,
`tests/test_auth_security.py::test_login_limit_counts_each_real_client_separately`,
`tests/test_activity_log_internals.py::test_client_ip_order`.
