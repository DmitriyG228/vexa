# fixtures — golden GitHub webhook payloads

Trimmed-but-shaped GitHub App webhook bodies (one per mapped `(event, action)` pair) the tests sign
with a real computed HMAC. The issue/comment bodies deliberately carry prompt-injection text — the
seam tests assert that NONE of it (titles, bodies, urls) ever reaches the persisted Delivery record
or the dispatched event.v1 envelope: only the opaque `github://` ref crosses.
