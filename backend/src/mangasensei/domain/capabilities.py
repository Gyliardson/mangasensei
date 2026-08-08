"""Capability scopes used by public resource access."""

from enum import StrEnum


class CapabilityScope(StrEnum):
    READ_PAGE = "read:page"
    READ_IMAGE = "read:image"
    REPROCESS_PAGE = "reprocess:page"
