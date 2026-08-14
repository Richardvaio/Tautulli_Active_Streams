"""Tests for the authenticated artwork proxy validation."""

from custom_components.tautulli_active_streams.image import ImagePathCache
from custom_components.tautulli_active_streams.views import _image_dimension


def test_image_dimensions_are_bounded() -> None:
    """Invalid and excessive proxy dimensions are rejected."""
    assert _image_dimension("16") == 16
    assert _image_dimension("2000") == 2000
    assert _image_dimension("15") is None
    assert _image_dimension("2001") is None


def test_image_tokens_resolve_only_from_the_bounded_cache() -> None:
    """Opaque tokens resolve to registered upstream image paths."""
    cache = ImagePathCache()
    token = cache.register("/library/metadata/1/thumb/2")
    assert cache.resolve(token) == "/library/metadata/1/thumb/2"
    assert cache.resolve("missing") is None
    assert _image_dimension("not-a-number") is None
