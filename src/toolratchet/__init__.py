"""toolratchet: a compatibility, token-budget and integrity gate for LLM tool definitions."""

__version__ = "0.1.0"

from .diff import compare  # noqa: E402
from .ingest import parse_surface  # noqa: E402
from .schema import check_subschema  # noqa: E402

__all__ = ["__version__", "compare", "parse_surface", "check_subschema"]
