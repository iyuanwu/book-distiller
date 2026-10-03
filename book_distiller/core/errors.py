"""Small public exception hierarchy."""


class BookDistillerError(Exception):
    """Base exception for application failures."""


class ConfigurationError(BookDistillerError):
    """Invalid or missing environment configuration."""


class StorageError(BookDistillerError):
    """Filesystem or storage failure."""


class PipelineError(BookDistillerError):
    """Pipeline operation failure (reserved for later phases)."""


class ValidationError(BookDistillerError):
    """Application validation failure, distinct from Pydantic validation."""
