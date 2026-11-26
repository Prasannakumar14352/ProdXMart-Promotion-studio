"""Compatibility re-export for Telugu Unicode RAQM rendering utilities."""

from telugu_support import (  # noqa: F401
    ROOT,
    TeluguShapingError,
    configure_windows_dll_search_path,
    contains_telugu,
    ensure_telugu_raqm_ready,
    find_fribidi_dll,
    get_telugu_rendering_status,
    is_raqm_ready,
    segment_script_runs,
    split_text_clusters,
)
