"""Small text helpers shared across tasks."""


def truncate(text: str, max_chars: int, *, suffix: str = "…") -> str:
    """Shorten ``text`` to ``max_chars`` characters, appending ``suffix`` when cut."""
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + suffix
    return text
