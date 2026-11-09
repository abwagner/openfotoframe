# Collection export and restore limits

Release exports are scoped to one currently authorized collection. The durable job
belongs to its requester: the job list, status, download and deletion APIs do not
expose another member's jobs. Workers and downloads recheck current collection
permissions. A revoked grant cannot be bypassed with a previously queued job ID.

Exports run through the separate release worker, never inline in a browser request.
The archive includes that collection's photos, visual metadata, groups and settings;
it excludes account credentials, invitations, other collections and frame credentials.
Restoring requires household owner/admin authority and imports into the explicitly
selected collection, assigning fresh resource IDs.

A collection export must fit the same byte limit accepted by restore. The effective
export cap is the smaller of `MAX_EXPORT_BYTES` and `MAX_UPLOAD_BYTES`; the latter
is **25 MiB by default**. The completed ZIP, including its manifest and ZIP headers,
must fit that cap. A collection that exceeds it produces a failed job with
`error_code: export_too_large` and no downloadable result. The service does not
create an archive that its own restore endpoint cannot accept.

The `MAX_CONTENT_LENGTH` request bound also applies to multipart restore requests;
its default 26 MiB accommodates the default archive cap plus form overhead. Raising
archive limits requires an explicit review of request limits, process memory and
private storage capacity. This preparation does not increase those runtime budgets.
Large collection export splitting or streaming restore remains future work.
