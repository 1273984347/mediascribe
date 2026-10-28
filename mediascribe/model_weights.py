"""
Model weight manifest — the weights an offline product must pre-provision.

Why this module exists
----------------------
MediaScribe is a **fully offline** product: every inference weight must be
**placed on the machine in advance**. But "model name → where the weight
file lives" used to be implicit, and a missing weight only exploded
mid-run:

* Measured 2026-10-23: ``faster-whisper medium`` on this machine raised
  ``LocalEntryNotFoundError: Cannot find an appropriate cached snapshot
  folder`` — only ``small`` and ``large-v3`` were cached locally and
  HuggingFace was unreachable ⇒ that tier was **entirely unrunnable**
  (not an engine defect; the weight was simply not provisioned).
* Likewise ``openai-whisper`` weights live in ``~/.cache/whisper/*.pt`` —
  a **completely different directory convention** from faster-whisper's
  HuggingFace cache.

This module turns "what weights does an (engine, model) need, where, and
how to provision them" into **queryable data**, consumed by three places:

1. :mod:`mediascribe.doctor` — diagnostics report what's missing and how
   to fix it
2. Transcriber load failures — replaces the raw HuggingFace exception with
   a guided, user-facing error
3. Distribution packaging — the manifest can generate a weight checklist

Directory conventions
---------------------
======================  ==========================================
Engine                  Weight location
======================  ==========================================
faster-whisper          ``$HF_HUB_CACHE/models--Systran--faster-whisper-<model>/``
whisper (openai)        ``~/.cache/whisper/<model>.pt``
whisperx                same as faster-whisper (its backend) + align model
whisper-cpp             ``<models_dir>/ggml-<model>.bin``
======================  ==========================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .i18n import t

#: Canonical engine names (match the CLI ``--engine`` choices)
ENGINE_WHISPER = "whisper"
ENGINE_FASTER = "faster-whisper"
ENGINE_WHISPERX = "whisperx"
ENGINE_WHISPER_CPP = "whisper-cpp"


@dataclass(frozen=True)
class WeightSpec:
    """Weight specification for one (engine, model) pair."""

    engine: str
    model: str
    #: How the weight is stored: ``hf-cache`` / ``openai-ckpt`` / ``ggml-file`` / ``builtin``
    kind: str
    #: Human-readable location description
    location: str
    #: Provisioning hint shown to the user when the weight is missing
    hint: str
    #: Whether this pair supports offline provisioning (False = must be
    # fetched online, e.g. gated models)
    offline_capable: bool = True

    def describe(self) -> str:
        return f"{self.engine}/{self.model} → {self.location}"


# ---------------------------------------------------------------------------
# Directory conventions
# ---------------------------------------------------------------------------


def hf_hub_cache_dir() -> Path:
    """HuggingFace cache root (resolved like ``huggingface_hub`` does)."""
    env = os.environ.get("HF_HUB_CACHE", "").strip()
    if env:
        return Path(env)
    hf_home = os.environ.get("HF_HOME", "").strip()
    if hf_home:
        return Path(hf_home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def openai_whisper_cache_dir() -> Path:
    """openai-whisper weight directory (``~/.cache/whisper``)."""
    env = os.environ.get("WHISPER_CACHE_DIR", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".cache" / "whisper"


def whisper_cpp_models_dir() -> Path:
    """whisper.cpp GGML weight directory (override with ``MEDIASCRIBE_WHISPER_CPP_MODELS``)."""
    env = os.environ.get("MEDIASCRIBE_WHISPER_CPP_MODELS", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".cache" / "mediascribe" / "whisper-cpp"


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _hf_spec(engine: str, model: str) -> WeightSpec:
    return WeightSpec(
        engine=engine,
        model=model,
        kind="hf-cache",
        location=f"{hf_hub_cache_dir() / ('models--Systran--faster-whisper-' + model)}",
        hint=t("WEIGHTS_HINT_HF", model=model, cache_dir=hf_hub_cache_dir()),
    )


def _openai_spec(model: str) -> WeightSpec:
    return WeightSpec(
        engine=ENGINE_WHISPER,
        model=model,
        kind="openai-ckpt",
        location=f"{openai_whisper_cache_dir() / (model + '.pt')}",
        hint=t("WEIGHTS_HINT_OPENAI", model=model, dir=openai_whisper_cache_dir()),
    )


def _cpp_spec(model: str) -> WeightSpec:
    return WeightSpec(
        engine=ENGINE_WHISPER_CPP,
        model=model,
        kind="ggml-file",
        location=f"{whisper_cpp_models_dir() / ('ggml-' + model + '.bin')}",
        hint=t("WEIGHTS_HINT_CPP", model=model, dir=whisper_cpp_models_dir()),
    )


def weight_spec(engine: str, model: str) -> Optional[WeightSpec]:
    """Return the weight spec for (engine, model); ``None`` for unknown pairs."""
    engine = (engine or "").strip()
    model = (model or "").strip()
    if engine in (ENGINE_FASTER, ENGINE_WHISPERX):
        return _hf_spec(engine, model)
    if engine == ENGINE_WHISPER:
        return _openai_spec(model)
    if engine == ENGINE_WHISPER_CPP:
        return _cpp_spec(model)
    return None


def known_weight_pairs() -> List[Tuple[str, str]]:
    """List every (engine, model) pair this module knows about.

    The model roster mirrors ``config._VALID_MODELS``; the inclusion
    invariant is pinned by ``tests/test_model_consistency.py``.
    """
    pairs: List[Tuple[str, str]] = []
    tiers = (
        "tiny",
        "base",
        "small",
        "medium",
        "large",
        "large-v1",
        "large-v2",
        "large-v3",
        "distil-large-v2",
        "distil-large-v3",
    )
    for engine in (ENGINE_FASTER, ENGINE_WHISPERX):
        for model in tiers:
            pairs.append((engine, model))
    for model in tiers:
        pairs.append((ENGINE_WHISPER, model))
        pairs.append((ENGINE_WHISPER_CPP, model))
    return pairs


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------


@dataclass
class WeightStatus:
    """Weight readiness status."""

    engine: str
    model: str
    ok: bool
    location: str = ""
    hint: str = ""
    detail: str = ""

    def message(self) -> str:
        if self.ok:
            return t("WEIGHTS_STATUS_OK", location=self.location)
        return t(
            "WEIGHTS_STATUS_MISSING",
            engine=self.engine,
            model=self.model,
            location=self.location,
            hint=self.hint,
        )


def _hf_present(spec: WeightSpec) -> Tuple[bool, str]:
    root = Path(spec.location)
    if not root.exists():
        return False, t("WEIGHTS_HF_NO_DIR")
    # What actually makes a HuggingFace snapshot dir "offline-capable" is
    # real files under snapshots/<rev>/
    snapshots = root / "snapshots"
    if snapshots.exists():
        for rev in snapshots.iterdir():
            if rev.is_dir() and any(rev.iterdir()):
                return True, t("WEIGHTS_HF_CACHED", rev=rev.name)
    blobs = root / "blobs"
    if blobs.exists() and any(blobs.iterdir()):
        return True, t("WEIGHTS_BLOB_PRESENT")
    return False, t("WEIGHTS_SNAPSHOT_EMPTY")


def _file_present(spec: WeightSpec) -> Tuple[bool, str]:
    p = Path(spec.location)
    if p.exists() and p.stat().st_size > 0:
        return True, t("WEIGHTS_FILE_PRESENT", mib=p.stat().st_size / 1024 / 1024)
    return False, t("WEIGHTS_FILE_MISSING")


def check_weights(engine: str, model: str) -> WeightStatus:
    """Check whether the weights for (engine, model) are provisioned."""
    spec = weight_spec(engine, model)
    if spec is None:
        return WeightStatus(
            engine=engine,
            model=model,
            ok=False,
            location=t("WEIGHTS_UNKNOWN_ENGINE"),
            hint=t("WEIGHTS_UNKNOWN_GUIDANCE"),
            detail="unknown-engine",
        )
    if spec.kind == "hf-cache":
        ok, detail = _hf_present(spec)
    else:
        ok, detail = _file_present(spec)
    return WeightStatus(
        engine=engine,
        model=model,
        ok=ok,
        location=spec.location,
        hint=spec.hint,
        detail=detail,
    )


def preflight_all() -> Dict[Tuple[str, str], WeightStatus]:
    """Scan weight readiness for every known pair (used by doctor / packaging)."""
    return {pair: check_weights(*pair) for pair in known_weight_pairs()}


def missing_hint(engine: str, model: str) -> str:
    """One-line provisioning hint for a missing weight (for exception messages)."""
    spec = weight_spec(engine, model)
    if spec is None:
        return t("WEIGHTS_UNKNOWN_ENGINE_HINT", engine=engine)
    return spec.hint
