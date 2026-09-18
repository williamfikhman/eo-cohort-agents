"""Accounts Receivable follow-up agent for Chief Marketplace Officer, Inc.

Two standing rules, enforced in code rather than remembered:

1. No writes to QuickBooks Online. The API client refuses every HTTP method
   except GET.
2. No client-facing email without an explicit human approval. The daily run
   only ever emails the digest to William; client reminders are sent by a
   separate command that requires a matching approval record.
"""

__version__ = "1.0.0"
