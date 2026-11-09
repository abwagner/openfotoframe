# Release frame schedules and connection information

The opt-in release server stores TV schedules per frame. The native advanced web
editor uses `/api/tv-schedules`; the versioned endpoint is
`GET`/`PUT /api/v1/households/{household}/frames/{frame}/schedules`.
The selected household/frame are selectors, never credentials. Frame viewers and
controllers can read schedules. Only household owners/admins can change them;
browser changes require the session CSRF token. Collection access alone does not
grant frame access.

Schedules have a unique opaque `id`, strict 24-hour `on_time`/`off_time` in
`HH:MM`, distinct weekday integers (`0` Monday through `6` Sunday), boolean
`enabled`, and an IANA `timezone` such as `America/New_York`. There are at most 32
schedules per frame. Saving replaces that frame's complete schedule list in one
transaction. Unsupported or invalid input rejects the entire update.

A missing timezone receives the operator's `SCHEDULE_TIMEZONE` configuration, or
`UTC` when it is unset. The old web editor has no timezone picker: operators must
configure this value before saving schedules. Imported legacy schedules retain
their original stored values; review their intended timezone before activation.
The API reports the effective timezone explicitly. Never derive it from the
current UTC offset: a timezone must retain its daylight-saving rules.

**Physical TV power execution is not enabled in this release backend.** Responses
explicitly report `execution_enabled: false`, `cec_available: false`, and
`execution_status: power_agent_not_configured`. A saved schedule is configuration,
not evidence that a TV turned on or off. The existing server automation remains
unchanged and must stay active until a separate migration is reviewed and tested.
The new service does not reuse legacy CEC agent credentials, shared global queues,
local process execution, or requests that trust loopback/identity headers.

A production power agent still requires a reviewed per-frame credential protocol,
a durable command queue with acknowledgements, bounded retries and expiry,
explicit device transport (for example Wake-on-LAN or CEC), and restart-safe
scheduling. Define wall-clock DST behavior, including skipped/repeated local times,
and test an actual TV wake/fullscreen sequence before switching automation over.
Hosted service agents must operate from the user's own network; the central
service must never probe or execute arbitrary user-provided network addresses.

`GET /api/network-info` requires a selected frame and household owner/admin access.
It returns the configured canonical `server_url` and frame ID. It deliberately
returns no server-private interface or Tailscale IP and performs no host/network
or VPN discovery. The client connects to the server address already selected.
