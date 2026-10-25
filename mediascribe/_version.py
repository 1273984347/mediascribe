"""Single source of truth for the MediaScribe version string.

Kept in its own dependency-free module so lightweight consumers — notably
``mediascribe.web.app`` — can read the version without importing the whole
package (whose ``__init__`` pulls in the ASR stack: torch, faster-whisper…).

``mediascribe/__init__.py`` re-exports ``__version__`` from here, so the
public API is unchanged and this file holds the only literal.
"""

__version__ = "3.3.0"
