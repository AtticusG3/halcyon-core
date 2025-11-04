"""Pydantic settings for HALCYON media integrations."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseSettings, Field


class MediaSettings(BaseSettings):
    """Configuration surface for Plex, Overseerr, and TMDB integrations."""

    plex_base_url: Optional[str] = Field(default=None, env="PLEX_BASE_URL")
    plex_token: Optional[str] = Field(default=None, env="PLEX_TOKEN")
    plex_user_name: Optional[str] = Field(default=None, env="PLEX_USER_NAME")

    overseerr_base_url: Optional[str] = Field(default=None, env="OVERSEERR_BASE_URL")
    overseerr_api_key: Optional[str] = Field(default=None, env="OVERSEERR_API_KEY")

    tmdb_api_key: Optional[str] = Field(default=None, env="TMDB_API_KEY")

    library_movies_section: str = Field(default="Movies", env="LIBRARY_MOVIES_SECTION")
    library_tv_section: str = Field(default="TV Shows", env="LIBRARY_TV_SECTION")
    default_media_player_entity: str = Field(
        default="media_player.living_room",
        env="DEFAULT_MEDIA_PLAYER_ENTITY",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


__all__ = ["MediaSettings"]
