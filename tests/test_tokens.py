"""Unit tests for optiproxai token estimation, focused on image-aware counting."""

from __future__ import annotations

import base64

from optiproxai.tokens import _IMAGE_PART_TOKEN_ESTIMATE, _estimate_tokens


def _big_data_uri(bytes_len: int = 1_000_000) -> str:
    return "data:image/png;base64," + base64.b64encode(b"x" * bytes_len).decode()


def _text_prompt() -> str:
    return "Describe this image in detail. " * 200


class TestTokenEstimationImageAware:
    def test_image_data_uri_counts_constant_not_text_tokens(self) -> None:
        text = _text_prompt()
        text_only = _estimate_tokens([{"role": "user", "content": text}])
        with_image = _estimate_tokens(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text},
                        {
                            "type": "image_url",
                            "image_url": {"url": _big_data_uri()},
                        },
                    ],
                }
            ]
        )
        # The image part must add exactly the flat constant, not tokenize the
        # megabyte-scale data URI (which would inflate the estimate by ~250k+).
        assert with_image - text_only == _IMAGE_PART_TOKEN_ESTIMATE
        assert with_image < 50_000

    def test_multiple_images_each_count_constant(self) -> None:
        text = _text_prompt()
        text_only = _estimate_tokens([{"role": "user", "content": text}])
        three_images = _estimate_tokens(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "image_url", "image_url": {"url": _big_data_uri()}},
                        {"type": "image_url", "image_url": {"url": _big_data_uri()}},
                        {"type": "image_url", "image_url": {"url": _big_data_uri()}},
                    ],
                }
            ]
        )
        assert three_images - text_only == 3 * _IMAGE_PART_TOKEN_ESTIMATE
        assert three_images < 50_000

    def test_url_reference_image_counts_constant(self) -> None:
        role_only = _estimate_tokens([{"role": "user", "content": ""}])
        with_url = _estimate_tokens(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://example.com/a.png"},
                        }
                    ],
                }
            ]
        )
        assert with_url - role_only == _IMAGE_PART_TOKEN_ESTIMATE

    def test_image_url_as_string_field_counts_constant(self) -> None:
        role_only = _estimate_tokens([{"role": "user", "content": ""}])
        with_url = _estimate_tokens(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": "https://example.com/b.png"}
                    ],
                }
            ]
        )
        assert with_url - role_only == _IMAGE_PART_TOKEN_ESTIMATE

    def test_text_part_inside_list_matches_plain_string(self) -> None:
        text = _text_prompt()
        plain = _estimate_tokens([{"role": "user", "content": text}])
        listed = _estimate_tokens(
            [{"role": "user", "content": [{"type": "text", "text": text}]}]
        )
        assert listed == plain
