"""Private persistence metadata stamped on live message dicts.

The incremental SessionDB flush (``AIAgent._flush_messages_to_session_db``)
tracks durability directly on the message dicts it writes, so repeated
flushes stay idempotent without positional slices or ``id()``-keyed sets.
These keys are private wire metadata: the API payload build strips every
top-level ``_``-prefixed key before a request leaves the process, and the
JSON session snapshot / context compressor strip them before reusing a
message. Define them ONCE here — every producer (steer injection), consumer
(flush), and stripper (compressor, session log) must agree on the exact
string or markers silently leak or stop being honoured.
"""

# Stamped by the flush on each message dict it has written to state.db.
_DB_PERSISTED_MARKER = "_db_persisted"

# Stamped by intentional post-INSERT content mutations (mid-turn /steer
# appending its marker to an already-flushed tool result) so the next flush
# updates the durable row in place instead of skipping the dict.
_DB_CONTENT_UPDATE_PENDING = "_db_content_update_pending"

# The SQLite messages.id of the durable row backing this dict, stamped by
# the flush on INSERT and by live-replay history loads, so a pending content
# update can target the exact row.
_DB_MESSAGE_ROW_ID = "_db_message_row_id"

# Exact /steer suffix temporarily protected from aggregate tool-result budget
# replacement. It survives the incremental flush between per-tool delivery and
# whole-turn budget enforcement, which consumes the key before the next API
# call. Snapshot/compaction stripping is defence-in-depth for exceptional exits.
_STEER_BUDGET_PROTECTED_SUFFIX = "_steer_budget_protected_suffix"
