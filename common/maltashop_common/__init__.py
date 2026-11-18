"""Shared contract (event schema, topology) and messaging helpers.

This package contains NO business logic and NO database access to another
service's data. Each service keeps its own database; the package only provides
the message format and the plumbing to move messages reliably.
"""
