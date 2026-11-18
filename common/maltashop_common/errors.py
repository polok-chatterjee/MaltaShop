class TransientError(Exception):
    """A failure that may succeed if retried (database busy, dependency down)."""


class PermanentError(Exception):
    """A failure that will never succeed on retry (bad data). Dead-lettered at once."""


class PublishError(Exception):
    """The broker did not accept (confirm) a published message."""
