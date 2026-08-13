"""Tests for the authenticated artwork proxy validation."""

from custom_components.tautulli_active_streams.views import _image_dimension


def test_image_dimensions_are_bounded() -> None:
    """Invalid and excessive proxy dimensions are rejected."""
    assert _image_dimension("16") == 16
    assert _image_dimension("2000") == 2000
    assert _image_dimension("15") is None
    assert _image_dimension("2001") is None
    assert _image_dimension("not-a-number") is None
