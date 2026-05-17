"""Long-Form Windows Screen Recorder for dual-monitor extend-mode workstations.

Purpose
-------
This is a single-file, GUI-driven, long-form screen recorder for Microsoft Windows 10.
It is intentionally opinionated:

  * It captures the *entire* desktop area of **Display 1** (the Microsoft Windows
    primary display, identified by the ``MONITORINFOF_PRIMARY`` flag of
    ``MONITORINFO.dwFlags``) at the display's native pixel resolution.
  * Its small fixed-size operator GUI window sits on **Display 2** (the
    Microsoft Windows secondary display) so it never appears in the recording.
  * It refuses to run unless the Microsoft Windows display configuration is
    *exactly* two attached extend-mode displays, with exactly one of them
    marked primary. Single-display, mirror-mode, more-than-two-display, and
    enumeration failures all raise loud detailed exceptions on the operator
    pressing "Start Recording".
  * The video output uses Intel Quick Sync Video (the hardware H.264 encoder
    on Intel integrated GPUs - on this target, the Intel UHD Graphics 770
    integrated into the 13th-generation Intel Core i7-13700 processor) via
    the FFmpeg encoder name ``h264_qsv``. Encoding happens through the PyAV
    Python bindings (version 17.0.1).
  * The output container is *fragmented* MP4 (FFmpeg muxer flags
    ``+frag_keyframe+empty_moov+default_base_moof``). This means the file is
    playable up to the last finalized fragment even if the recorder process
    is killed mid-recording (no missing ``moov`` atom corruption).
  * Pause/Resume is implemented by halting the capture thread's enqueuing of
    new frames into the encoder queue. The video's presentation-timestamp
    counter is *not* advanced during pause, so the final video plays back as
    a seamless cut across each pause boundary - no padded duplicate frames
    and no gap in playback time.

Operator-facing behavior
------------------------
On startup the program:

  1. Opts in to Per-Monitor V2 DPI awareness so the operator GUI window's
     ``+x+y`` Tk geometry coordinates are in real physical pixels and
     therefore agree with the bounding rectangles reported by the
     Microsoft Win32 ``EnumDisplayMonitors`` and python-mss APIs.
  2. Resolves the operator's current-user Microsoft Windows Videos
     Known Folder via the Microsoft-blessed ``SHGetKnownFolderPath`` API
     (the modern, OneDrive-Known-Folder-Move-aware, Folder-Redirection-aware
     replacement for ``SHGetFolderPath``).
  3. Validates the dual-extend-mode display configuration (see above).
  4. Verifies that the ``h264_qsv`` Intel Quick Sync Video H.264 encoder is
     advertised as available by the FFmpeg bundled inside the PyAV wheel.
  5. Creates the small fixed-size GUI on Display 2 with four operator
     controls: a status readout, a Start Recording button, a
     Pause / Resume toggle button, and a Stop Recording button.

When the operator presses Start Recording, a fresh per-session
scratch folder is created underneath the current user's per-user
temporary directory (the path resolved by ``tempfile.gettempdir()``,
which on a normally-installed Microsoft Windows workstation is
``%LOCALAPPDATA%\\Temp\\``), at the path::

    <Per-User Temporary Directory>\\WindowsScreenRecorder\\<ISO-8601-session-id>\\

where ``<ISO-8601-session-id>`` is the local wall-clock instant of
session start, formatted as ``YYYY-MM-DDTHH-MM-SS-FFFFFF`` (Microsoft
Windows reserves the colon character, so the canonical ISO 8601 colons
are replaced with hyphens; microseconds eliminate same-second collisions
even on a rapid Stop/Start cycle). This format sorts both alphabetically
and chronologically.

The recorder writes the fragmented-MP4 output container, the per-frame
CSV log, the session metadata JSON, the display configuration JSON,
and the session text log into this *scratch* folder while the session
is running. On graceful Stop, every artifact is copied (via
``shutil.copytree``, with all missing intermediate directories
created) into the structurally identical path underneath the Videos
Known Folder::

    <Videos Known Folder>\\WindowsScreenRecorder\\<ISO-8601-session-id>\\

The scratch folder is removed on successful publication. The
scratch-then-publish split exists because the Microsoft Windows
Videos Known Folder is frequently redirected (via Group Policy folder
redirection or OneDrive Known Folder Move) onto an SMB / OneDrive
backing store that *does* support large sequential bulk-copy but does
*not* reliably support the seek-back-and-overwrite mid-stream I/O
pattern that the fragmented-MP4 muxer issues per fragment. Local
NTFS-backed temporary storage always supports that pattern. If the
publication copy fails, the recording is preserved intact in the
scratch folder; the GUI displays the scratch path and the operator
can copy it manually.

Each per-session output folder contains five forensic artifacts:

  ``recording.mp4``
      The fragmented-MP4 H.264 video file. Native resolution of Display 1,
      30 frames per second, Intel Quick Sync Video H.264, color-space
      conversion BGRA-to-NV12 by FFmpeg's libswscale.

  ``session.json``
      Session metadata: software versions, codec choice, color-space,
      target frame rate, captured monitor's bounding rectangle, output
      paths, start and end timestamps.

  ``display_configuration.json``
      A snapshot of *every* attached display monitor at session-start,
      taken via the Microsoft Win32 ``EnumDisplayMonitors`` API. Includes
      the device name (``\\\\.\\DISPLAY1`` etc.), bounding rectangles in
      virtual-screen coordinates, primary-display flag, and the friendly
      device string reported by ``EnumDisplayDevicesW``.

  ``frames.csv``
      Per-captured-frame log: monotonic wall-clock instant of each
      successful capture, the integer presentation timestamp the frame
      was assigned in the H.264 elementary stream, and the cumulative
      paused duration at the moment of that frame's capture. This file
      is flushed and OS-level-synced after every captured frame so that
      a hard-crash leaves a complete trail up to the crash.

  ``session.log``
      The detailed text log of every notable event during the session:
      capture-loop pacing warnings, encoder back-pressure warnings,
      pause and resume timestamps, exception tracebacks. Flushed after
      every record.

Errors and validation philosophy
--------------------------------
Per the project requirements, *errors come without fallbacks*. Every
failure path raises a dedicated subclass of ``ScreenRecorderError`` with
a verbose, context-rich message: what was expected, what was actually
observed, the exact Microsoft Win32 (or PyAV, or python-mss) call that
disagreed, and the contextual state (display enumeration result, encoder
codec name, output path, frame index, etc.) at the moment of the
disagreement. The operator never sees a generic "Recording failed".

Target environment
------------------
This file is exclusively intended for:

  * Microsoft Windows 10 desktop edition (any build supporting the
    Per-Monitor V2 DPI Awareness Context, which is Microsoft Windows 10
    version 1703 "Creators Update" or later).
  * Python 3.11.5, CPython distribution, x86-64.
  * The Anaconda Python distribution running with elevated
    (administrator-privileged) permissions.
  * PyAV 17.0.1 (the ``av`` package on PyPI) with the bundled FFmpeg
    build that includes the ``h264_qsv`` encoder.
  * python-mss 6.1.0 (the ``mss`` package on PyPI).
  * NumPy 2.x (the ``numpy`` package on PyPI).
  * An Intel processor with an integrated GPU exposing Intel Quick Sync
    Video, with the Intel Graphics driver and the oneVPL runtime
    installed. The reference target machine is a 13th-generation Intel
    Core i7-13700 desktop processor with integrated Intel UHD Graphics
    770, driver branch 31.0.101.4502 or compatible.

This file deliberately makes no provision for any other operating
system, any other display configuration, or any other encoder backend.
"""

from __future__ import annotations

# ----------------------------------------------------------------------------
# Standard library imports
# ----------------------------------------------------------------------------
import collections
import csv
import ctypes
import dataclasses
import datetime as _datetime_module
import enum
import json
import logging
import os
import platform
import queue
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tk_font
import tkinter.messagebox as tk_messagebox
from tkinter import ttk
import traceback
from ctypes import wintypes
from fractions import Fraction
from pathlib import Path
from typing import Any, Final, Optional

# ----------------------------------------------------------------------------
# Third-party imports
# ----------------------------------------------------------------------------
import av  # PyAV: Python bindings to FFmpeg libavcodec / libavformat
import av.codec  # explicit submodule for av.codec.Codec(name, mode)
import av.error  # explicit submodule for av.error.FFmpegError attribute set
import av.logging  # explicit submodule for av.logging.set_level / get_last_error
import mss  # python-mss: GDI-BitBlt-based Windows screenshot library
import mss.base  # explicit submodule for mss.base.MSSBase type annotations
import numpy as np

# ============================================================================
# Section 1 - Software identification constants
# ============================================================================

APP_INTERNAL_NAME: Final[str] = "WindowsScreenRecorder"
APP_DISPLAY_TITLE: Final[str] = "Long-Form Windows Screen Recorder"
APP_SCHEMA_VERSION_FOR_FORENSIC_ARTIFACTS: Final[str] = "1.0.0"

# This integer is the *minimum* python-mss major.minor.patch version we trust
# for the per-thread MSS instance pattern we rely on (see
# _SingleDisplayPrimaryMonitorCaptureWorker). Older python-mss versions
# allocated a shared bytearray buffer rather than a fresh per-grab one.
MINIMUM_REQUIRED_PYTHON_MSS_VERSION: Final[tuple[int, int, int]] = (6, 1, 0)

# PyAV 17.x stable API surface for av.codec.Codec construction and
# av.formats_available / av.codecs_available is required.
MINIMUM_REQUIRED_PYAV_VERSION: Final[tuple[int, int, int]] = (14, 0, 0)


# ============================================================================
# Section 2 - Capture and encoding configuration constants
# ============================================================================

# Constant target frame rate. Screen content is mostly static so 30 frames per
# second is the established sweet spot for screen recording: smooth enough for
# UI animations and cursor motion, low enough to keep multi-hour file sizes
# bounded and to keep both the GDI BitBlt capture and Intel Quick Sync Video
# encoder well below their respective per-frame time budgets.
TARGET_OUTPUT_FRAMES_PER_SECOND: Final[int] = 30

# Bounded inter-thread queue capacity, measured in whole frames.
# 240 frames at 30 frames per second is 8 wall-clock seconds of buffering.
# If the queue ever reaches this depth the encoder thread has fallen 8
# seconds behind the capture thread - on this target machine that should
# never happen under correct operation and is treated as a hard error.
CAPTURE_TO_ENCODER_QUEUE_MAXIMUM_DEPTH_FRAMES: Final[int] = 240

# FFmpeg muxer-level option string for crash-resilient fragmented MP4 output.
# +frag_keyframe       -> start a new fragment at every keyframe
# +empty_moov          -> write an initial empty moov atom so the file is
#                          parseable from byte 0 immediately
# +default_base_moof   -> emit fragments with default-base-is-moof, the
#                          modern fragmented-MP4 layout understood by every
#                          contemporary player and by FFmpeg's own remuxer.
# The combination means: a killed-mid-recording file is playable up through
# the last fragment, with no need to recover a missing moov atom.
FRAGMENTED_MP4_MOVFLAGS_VALUE: Final[str] = (
    "+frag_keyframe+empty_moov+default_base_moof"
)

# FFmpeg encoder name for the Intel Quick Sync Video H.264 hardware encoder.
INTEL_QUICK_SYNC_VIDEO_H264_ENCODER_FFMPEG_NAME: Final[str] = "h264_qsv"

# Intel Quick Sync Video H.264 encoder parameters. ``global_quality`` is the
# QSV-specific replacement for libx264's ``crf`` - a lower number means
# higher quality, higher bitrate. The 18..28 range is the practical sweet
# spot; we pick 22 for visibly-lossless screen recording at moderate bitrate.
INTEL_QSV_GLOBAL_QUALITY_VALUE: Final[int] = 22
INTEL_QSV_PRESET_VALUE: Final[str] = "veryfast"
# Disable QSV look-ahead. ``look_ahead`` is already FFmpeg's documented
# default (``libavcodec/qsvenc_h264.c`` line 133:
# ``AV_OPT_TYPE_BOOL, { .i64 = 0 }, 0, 1, VE``), so passing it explicitly
# is redundant - we set it explicitly anyway because we depend on the
# value, and not depending on a default is the surgically explicit
# choice for a codec option whose default has shifted across past
# FFmpeg releases.
INTEL_QSV_LOOK_AHEAD_VALUE: Final[int] = 0
# Async depth = 4. FFmpeg's documented default
# (``libavcodec/qsv_internal.h`` line 50: ``#define ASYNC_DEPTH_DEFAULT 4``)
# and Intel's reference encoder default in the Intel-maintained
# ``intel/libvpl-tools`` repository at
# ``tools/legacy/sample_encode/src/sample_encode.cpp`` lines 1748-1749:
# ``pParams->nAsyncDepth = 4``. Lower values risk the
# ``MFX_RATECONTROL_ICQ + AsyncDepth=1 + GopRefDist=1`` defect class
# documented for the integrated Intel UHD Graphics 770 GPU and its
# Intel ``vpl-gpu-rt`` H.264 hardware pipeline: under that combination
# the encoder emits its first output packets with
# ``mfxBitstream.TimeStamp`` left uninitialised at libavcodec's
# ``av_mallocz``-zeroed sentinel value, and ``libavcodec/qsvenc.c``
# line 2667 then translates that sentinel via
# ``av_rescale_q(0, ...) = 0`` to ``pkt.pts = 0`` *without* the
# ``MFX_TIMESTAMP_UNKNOWN`` sanitisation present in ``qsvenc.c``'s
# sister file ``libavcodec/qsvdec.c`` lines 64-70. FFmpeg's mp4 muxer
# would then reject the second output packet that surfaces with
# ``pkt.pts = 0`` via the strict-monotonic-DTS guard at
# ``libavformat/mux.c`` lines 410-420. This program does not rely on
# the mp4 muxer catching that defect: every output packet's PTS and
# DTS are synthesized from a strictly-monotonic counter inside
# ``_mux_one_encoded_packet_with_synthesized_pts_and_dts``, so the
# defect surfaces only as a forensic ``WARNING`` line in
# ``session.log`` rather than as a fatal muxer rejection. Intel's own libvpl sample encoder never combines
# AsyncDepth=1 with ICQ - the only modes Intel forces AsyncDepth=1
# in are TCBRC and PartialOutput, neither of which this program uses.
# The defect class is documented in
# https://github.com/intel/vpl-gpu-rt/issues/253 for AV1 and
# https://github.com/Intel-Media-SDK/MediaSDK/issues/978 for the
# H.264 FEI preencode path. The latency cost of AsyncDepth=4 is
# approximately 3 frames at 30 fps = 100 ms end-to-end, which is
# irrelevant for a screen recorder whose output is a video file
# (not a live stream).
INTEL_QSV_ASYNC_DEPTH_VALUE: Final[int] = 4
# Maximum B-frames between non-B-frames: 0 (no B-frames at all).
#
# We must set this EXPLICITLY because ``h264_qsv`` overrides FFmpeg's
# generic AVCodecContext default. The generic libavcodec default for
# ``bf`` (the AVOption that backs ``avctx->max_b_frames``) is 0, as
# defined in
# https://github.com/FFmpeg/FFmpeg/blob/n8.0.1/libavcodec/options_table.h
# (search for the ``"bf"`` entry; the ``DEFAULT`` macro it references
# is ``#define DEFAULT 0`` earlier in that file). But the h264_qsv
# encoder's per-codec defaults table at
# https://github.com/FFmpeg/FFmpeg/blob/n8.0.1/libavcodec/qsvenc_h264.c#L119
# overrides that to ``-1`` (the "automatic / libmfx picks" sentinel):
#
#     static const FFCodecDefault qsv_enc_defaults[] = {
#         { "b",  "1M"    },
#         { "refs",  "0"  },
#         { "g",  "250"   },
#         { "bf", "-1"    },
#         { NULL },
#     };
#
# h264_qsv then computes
#     ``q->param.mfx.GopRefDist = FFMAX(-1, avctx->max_b_frames) + 1;``
# at https://github.com/FFmpeg/FFmpeg/blob/n8.0.1/libavcodec/qsvenc.c#L1078,
# so with the codec-default-overridden ``avctx->max_b_frames = -1``,
# the result is ``GopRefDist = FFMAX(-1, -1) + 1 = 0``. ``GopRefDist = 0``
# is the Intel oneVPL runtime's "the runtime picks" sentinel, and on the
# integrated Intel UHD Graphics 770 GPU on the 13th-generation Intel
# Core i7-13700 desktop processor (the reference target hardware for
# this program) the Intel ``vpl-gpu-rt`` runtime picks
# ``GopRefDist = 3`` (i.e. ``bf = 2`` B-frames between anchors). Without
# the explicit ``bf = 0`` override below, the encoder therefore emits
# packets in H.264 decode order rather than display order, producing a
# PTS sequence like
#
#     I(pts=0, dts=-1)  P(pts=3, dts=0)  B(pts=1, dts=1)
#     B(pts=2, dts=2)   P(pts=6, dts=3)  B(pts=4, dts=4)  B(pts=5, dts=5)
#     ...
#
# - the classic ``bf=2`` GOP pattern in decode order. This constant
# is the in-program guarantee that the pattern never reaches the
# encoder output in the first place: ``GopRefDist == 1`` forbids
# the Intel oneVPL runtime from emitting B-frames at all, which is
# the foundational invariant that makes the PTS/DTS synthesis in
# ``_mux_one_encoded_packet_with_synthesized_pts_and_dts``
# spec-correct (decode order equals display order equals input
# order, so the Nth output packet's spec-correct timestamp is N).
#
# Setting ``bf = 0`` explicitly forces
# ``GopRefDist = FFMAX(-1, 0) + 1 = 1`` (i.e. no B-frames between
# anchors). This is REQUIRED for two correctness invariants this
# program depends on:
#
#   1. The ``pkt.dts := pkt.pts`` override in the mux helper is
#      spec-correct ONLY when there are no B-frames. With B-frames,
#      DTS < PTS for some packets by the H.264 specification, and
#      overwriting DTS with PTS would corrupt the decode timing.
#
#   2. The strict-monotonic-output-PTS watchdog assumes encode order
#      equals display order (no reordering). With B-frames, encode
#      order does not equal display order, so PTS comes out non-
#      monotonic by design.
#
# Both invariants are local Python-side defensive checks. Disabling
# B-frames here makes them tractable; allowing B-frames would require
# threading a full PTS/DTS reordering buffer through the mux helper,
# which has no compelling benefit for this program's content (a
# Microsoft Windows desktop capture is mostly static text plus
# occasional motion; the compression efficiency gain from B-frames
# at quality target 22 is typically under 5%, and is irrelevant for a
# screen recorder writing to local NTFS).
INTEL_QSV_MAX_B_FRAMES_VALUE: Final[int] = 0
# Keyframe interval expressed in frames. We choose 2 wall-clock seconds, so
# the value is 2 * frame rate. This bounds the lost-on-crash tail to about
# 2 seconds (the most recently-started fragment).
KEYFRAME_INTERVAL_FRAMES: Final[int] = 2 * TARGET_OUTPUT_FRAMES_PER_SECOND


# ============================================================================
# Section 3 - Operator GUI configuration constants
# ============================================================================
#
# The operator GUI is built entirely from ttk widgets arranged via the grid
# geometry manager with explicit column and row weights, so the layout
# behaves the same way a wxWidgets BoxSizer + GridSizer + stretch-spacer
# composition behaves. Every dimension below is expressed as a multiple of
# the platform's TkDefaultFont em width or line height - there are no
# hard-coded pixel dimensions or fixed point sizes anywhere in the GUI.
# This makes the GUI scale coherently with the Microsoft Windows display-
# scaling slider and the "Make text bigger" accessibility setting (both of
# which TkDefaultFont reflects automatically at process start).

GUI_WINDOW_TITLE_TEXT: Final[str] = APP_DISPLAY_TITLE
GUI_STATUS_POLL_INTERVAL_MILLISECONDS: Final[int] = 200

# Outer padding between the window edge and the content frame, expressed
# as a multiple of one em (the pixel width of "0" in TkDefaultFont).
GUI_OUTER_PADDING_EM_FRACTION: Final[float] = 1.0
# Inner padding between consecutive widgets inside the content frame.
GUI_INNER_PADDING_EM_FRACTION: Final[float] = 0.5

# The title banner uses the same font family as TkDefaultFont, bolded,
# and this many extra typographic points larger. At a default 10pt UI
# font this produces a 14pt bold banner.
GUI_TITLE_BANNER_EXTRA_POINTS: Final[int] = 4

# Minimum width of each button, in characters. ttk.Button's ``width``
# option is measured in characters so this floor scales correctly with
# the operator's system font size. The longest button text in any state
# is "Force-Cancel Finalize" (21 characters) so 22 gives a one-character
# margin and aligns every button at the same width.
GUI_BUTTON_MINIMUM_WIDTH_CHARACTERS: Final[int] = 22

# Initial label wrap width, expressed as a multiple of one em. Long
# status strings wrap at this width so the GUI opens at a reasonable
# initial size without spanning the full screen horizontally. The
# operator can drag the window wider; each wrappable label is bound to
# its <Configure> event and re-flows its text to fill the new width.
GUI_INITIAL_LABEL_WRAPLENGTH_EM: Final[float] = 60.0


# ============================================================================
# Section 4 - Output folder naming constants
# ============================================================================

OUTPUT_PARENT_SUBFOLDER_NAME_UNDER_VIDEOS_KNOWN_FOLDER: Final[str] = (
    "WindowsScreenRecorder"
)
PER_SESSION_VIDEO_FILE_NAME: Final[str] = "recording.mp4"
PER_SESSION_METADATA_JSON_FILE_NAME: Final[str] = "session.json"
PER_SESSION_DISPLAY_CONFIG_JSON_FILE_NAME: Final[str] = "display_configuration.json"
PER_SESSION_PER_FRAME_LOG_CSV_FILE_NAME: Final[str] = "frames.csv"
PER_SESSION_TEXT_LOG_FILE_NAME: Final[str] = "session.log"


# ============================================================================
# Section 5 - Custom exception hierarchy
# ============================================================================
#
# Every failure path in this program raises one of these. Every constructor
# call provides a verbose multi-line message including the expected value,
# the observed value, the exact API that produced the disagreement, and the
# state context at the moment of the disagreement. We do not catch our own
# exceptions anywhere - they are designed to propagate to the operator with
# their full context intact.

class ScreenRecorderError(Exception):
    """Common base class for every error this module raises."""


class HostOperatingSystemError(ScreenRecorderError):
    """The host operating system is not Microsoft Windows 10 (or compatible)."""


class ThirdPartyDependencyVersionError(ScreenRecorderError):
    """An installed third-party dependency is older than this module requires."""


class WindowsKernelApiInvocationError(ScreenRecorderError):
    """A call into a Microsoft Win32 API (via ctypes) failed unexpectedly."""


class VideosKnownFolderUnresolvableError(ScreenRecorderError):
    """The current user's Microsoft Windows Videos Known Folder cannot be resolved."""


class OutputFolderProvisioningError(ScreenRecorderError):
    """Could not create or write into the per-session output folder."""


class PerUserTemporaryDirectoryUnusableError(ScreenRecorderError):
    """The current user's per-user temporary directory cannot be used as a scratch root.

    The recorder muxes its fragmented-MP4 output to a per-user temporary
    scratch folder during the recording session, and copies the finished
    session artifacts into the Videos Known Folder only on Stop. This
    decoupling exists because the Microsoft Windows Videos Known Folder
    is frequently redirected (via Group Policy folder redirection or
    OneDrive Known Folder Move) onto an SMB / OneDrive backing store
    that does not reliably support the seek-back-and-overwrite I/O
    pattern the fragmented-MP4 muxer issues mid-stream. The per-user
    temporary directory, in contrast, is always backed by the local
    NTFS volume on a normally-installed Microsoft Windows workstation.
    If the per-user temporary directory does not exist or is not a
    directory at session-start, we refuse to record - the alternative
    (silently falling back to an undefined location) would produce
    forensic artifacts the operator cannot locate.
    """


class SessionPublicationToVideosLibraryError(ScreenRecorderError):
    """Could not copy the per-session scratch folder into the Videos Known Folder."""


class DisplayConfigurationValidationError(ScreenRecorderError):
    """The attached display monitors do not satisfy this app's requirements."""


class IntelQuickSyncEncoderUnavailableError(ScreenRecorderError):
    """The Intel Quick Sync Video H.264 encoder ``h264_qsv`` is not in PyAV's bundled FFmpeg."""


class ScreenFrameCaptureFailureError(ScreenRecorderError):
    """The python-mss screen capture API returned an unexpected frame."""


class EncoderBackPressureError(ScreenRecorderError):
    """The encoder queue overflowed - the encoder could not keep up with the capture rate."""


class EncoderPipelineError(ScreenRecorderError):
    """The PyAV / FFmpeg encoder pipeline raised an error while encoding or muxing."""


class IllegalRecordingStateTransitionError(ScreenRecorderError):
    """The operator requested an action that is not legal in the current recording state."""


class InconsistentControllerStateAssertionError(ScreenRecorderError):
    """A controller invariant failed - state and resource ownership disagree.

    This is always a programming defect inside this module: the controller
    arrived at a state where its tracked resources (worker threads, sync
    events, output container handles, log files) do not match what the
    state declares should be live. Crash loudly with full context rather
    than continue in an undefined configuration.
    """


class WorkerThreadJoinTimeoutError(ScreenRecorderError):
    """A worker thread did not exit within the allowed join timeout."""


# ============================================================================
# Section 6 - Microsoft Win32 ctypes bindings
# ============================================================================
#
# We use raw ctypes (no pywin32 dependency) to keep the surface area minimal
# and the EDR signature low. All bindings here are public, documented Win32
# APIs from user32.dll, shell32.dll, ole32.dll, and shcore.dll.

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_ole32 = ctypes.WinDLL("ole32", use_last_error=True)
# shcore.dll is the home of SetProcessDpiAwareness on Windows 8.1+; the
# preferred newer SetProcessDpiAwarenessContext (Windows 10 1703+) is in
# user32.dll. We probe for both at runtime.
try:
    _shcore = ctypes.WinDLL("shcore", use_last_error=True)
except OSError:
    _shcore = None  # type: ignore[assignment]


# ----- GUID struct for SHGetKnownFolderPath ---------------------------------

class _Win32Guid(ctypes.Structure):
    """The Microsoft Win32 ``GUID`` 16-byte structure (binary-compatible)."""

    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# FOLDERID_Videos = {18989B1D-99B5-455B-841C-AB7C74E4DDFC}
# This GUID is documented at:
#   https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid
_FOLDERID_VIDEOS_KNOWN_FOLDER_GUID: Final[_Win32Guid] = _Win32Guid(
    0x18989B1D,
    0x99B5,
    0x455B,
    (ctypes.c_ubyte * 8)(0x84, 0x1C, 0xAB, 0x7C, 0x74, 0xE4, 0xDD, 0xFC),
)


# ----- SHGetKnownFolderPath / CoTaskMemFree signatures ----------------------

_shell32.SHGetKnownFolderPath.argtypes = [
    ctypes.POINTER(_Win32Guid),                # REFKNOWNFOLDERID rfid
    wintypes.DWORD,                            # DWORD dwFlags
    wintypes.HANDLE,                           # HANDLE hToken
    ctypes.POINTER(ctypes.c_wchar_p),          # PWSTR *ppszPath (out)
]
_shell32.SHGetKnownFolderPath.restype = ctypes.HRESULT

_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
_ole32.CoTaskMemFree.restype = None


# ----- EnumDisplayMonitors / GetMonitorInfoW / EnumDisplayDevicesW ----------

_CCHDEVICENAME: Final[int] = 32  # Win32 constant: max length of \\.\DISPLAYn
_MONITORINFOF_PRIMARY: Final[int] = 0x00000001  # MONITORINFO.dwFlags bit


class _Win32MonitorInfoExW(ctypes.Structure):
    """The Microsoft Win32 ``MONITORINFOEXW`` 104-byte structure."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * _CCHDEVICENAME),
    ]


_MONITOR_ENUMERATION_CALLBACK_PROTOTYPE = ctypes.WINFUNCTYPE(
    wintypes.BOOL,                             # return: TRUE to continue enum
    wintypes.HMONITOR,                         # HMONITOR hMonitor
    wintypes.HDC,                              # HDC hdcMonitor (unused)
    ctypes.POINTER(wintypes.RECT),             # LPRECT lprcMonitor (unused)
    wintypes.LPARAM,                           # LPARAM dwData (unused)
)

_user32.EnumDisplayMonitors.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    _MONITOR_ENUMERATION_CALLBACK_PROTOTYPE,
    wintypes.LPARAM,
]
_user32.EnumDisplayMonitors.restype = wintypes.BOOL

_user32.GetMonitorInfoW.argtypes = [
    wintypes.HMONITOR,
    ctypes.POINTER(_Win32MonitorInfoExW),
]
_user32.GetMonitorInfoW.restype = wintypes.BOOL


class _Win32DisplayDeviceW(ctypes.Structure):
    """The Microsoft Win32 ``DISPLAY_DEVICEW`` structure (424 bytes)."""

    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


_user32.EnumDisplayDevicesW.argtypes = [
    wintypes.LPCWSTR,                          # LPCWSTR lpDevice
    wintypes.DWORD,                            # DWORD iDevNum
    ctypes.POINTER(_Win32DisplayDeviceW),      # PDISPLAY_DEVICEW lpDisplayDevice
    wintypes.DWORD,                            # DWORD dwFlags
]
_user32.EnumDisplayDevicesW.restype = wintypes.BOOL


# ----- Per-Monitor V2 DPI Awareness opt-in ----------------------------------

# The constant -4 is the documented value of the opaque pseudo-handle
#   DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
# at:
#   https://learn.microsoft.com/en-us/windows/win32/api/windef/ne-windef-dpi_awareness_context
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2_HANDLE_VALUE: Final[int] = -4

# These signatures are set at the call site to avoid hard-failing the import
# on older Windows builds; we probe and fall back.


# ----- MonitorFromPoint / GetDpiForMonitor ----------------------------------
#
# These bindings exist so we can ask Microsoft Windows for the effective DPI
# of the *specific* monitor that the operator GUI will be placed onto
# (Display 2), independently of whichever monitor is the primary. Required
# because Tcl/Tk's Windows backend, even on a Per-Monitor-V2-aware process,
# captures the screen DPI exactly once (in TkWinDisplayChanged inside
# win/tkWinX.c) via ``GetDC(NULL); GetDeviceCaps(LOGPIXELSY)`` - and the
# desktop-DC LOGPIXELSY value documented at
#   https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/dpi-related-apis-and-registry-settings
# is "the DPI of the primary display at the time the Windows session was
# started". So on a dual-monitor workstation whose Display 2 is at a
# different scale than Display 1, Tcl/Tk's font metrics and ttk widget
# sizes would be off by the ratio of those two DPI values - exactly as
# documented in the open Tk ticket
#   https://core.tcl-lang.org/tk/tktview?name=a9ee44102b
# We work around this by *manually* fetching Display 2's effective DPI
# via GetDpiForMonitor (which IS PMv2-aware) and re-scaling Tk before any
# widget is constructed.

# Microsoft Win32 ``MONITOR_DPI_TYPE`` enum value:
#   MDT_EFFECTIVE_DPI  = 0  - the DPI scaled by the user's accessibility
#                             settings; this is the value to drive
#                             user-interface layout from.
# See https://learn.microsoft.com/en-us/windows/win32/api/shellscalingapi/ne-shellscalingapi-monitor_dpi_type
_MDT_EFFECTIVE_DPI_VALUE: Final[int] = 0

# Microsoft Win32 ``MonitorFromPoint`` flag:
#   MONITOR_DEFAULTTONEAREST = 0x00000002  - if the point is not inside
#       any monitor, return the nearest monitor's HMONITOR handle. We
#       always feed a point inside Display 2's bounding rectangle, but
#       using the nearest-fallback flag keeps the call total instead
#       of raising on a borderline pixel coordinate.
# See https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-monitorfrompoint
_MONITOR_DEFAULTTONEAREST_FLAG_VALUE: Final[int] = 0x00000002

_user32.MonitorFromPoint.argtypes = [
    wintypes.POINT,                            # POINT pt (by value)
    wintypes.DWORD,                            # DWORD dwFlags
]
_user32.MonitorFromPoint.restype = wintypes.HMONITOR

# GetDpiForMonitor lives in shcore.dll (Windows 8.1+). The reference
# target host for this program is Windows 10 1703+, so shcore.dll is
# always present; we still guard the GetProcAddress with try/except
# in case the user is running an older or sandboxed Windows build.
if _shcore is not None:
    try:
        _shcore.GetDpiForMonitor.argtypes = [
            wintypes.HMONITOR,                 # HMONITOR hmonitor
            ctypes.c_int,                      # MONITOR_DPI_TYPE dpiType
            ctypes.POINTER(wintypes.UINT),     # UINT *dpiX (out)
            ctypes.POINTER(wintypes.UINT),     # UINT *dpiY (out)
        ]
        _shcore.GetDpiForMonitor.restype = ctypes.HRESULT
    except AttributeError:
        # Older Windows build that ships shcore.dll without
        # GetDpiForMonitor. The query helper below will detect this
        # condition and fall back gracefully.
        pass


# ============================================================================
# Section 7 - Microsoft Win32 helper functions
# ============================================================================

def _opt_into_per_monitor_v2_dpi_awareness() -> None:
    """Opt this process into Per-Monitor V2 DPI Awareness.

    This MUST be called before any window is created (whether by Tk, by GDI,
    or by any other path). The Per-Monitor V2 awareness context guarantees
    that physical pixel coordinates we receive from python-mss and from
    ``EnumDisplayMonitors`` will agree with the coordinate space that Tk's
    ``geometry("WxH+X+Y")`` uses to position our operator GUI window.

    Falls back gracefully through:
      Per-Monitor V2 (Windows 10 1703+)  preferred
      Per-Monitor V1 (Windows 8.1+)      acceptable
      System DPI Aware (Windows Vista+)  last resort

    Raises ``WindowsKernelApiInvocationError`` only if *all three* paths
    fail, which would mean we are running on a host operating system this
    module does not target.
    """
    # 1) Per-Monitor V2 via user32.SetProcessDpiAwarenessContext.
    try:
        set_pmv2 = _user32.SetProcessDpiAwarenessContext
        set_pmv2.argtypes = [ctypes.c_void_p]
        set_pmv2.restype = wintypes.BOOL
        if set_pmv2(
            ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2_HANDLE_VALUE)
        ):
            return
    except (AttributeError, OSError):
        pass

    # 2) Per-Monitor V1 via shcore.SetProcessDpiAwareness(2).
    if _shcore is not None:
        try:
            set_pmv1 = _shcore.SetProcessDpiAwareness
            set_pmv1.argtypes = [ctypes.c_int]
            set_pmv1.restype = ctypes.HRESULT
            hr = set_pmv1(2)  # PROCESS_PER_MONITOR_DPI_AWARE
            if hr == 0:
                return
        except (AttributeError, OSError):
            pass

    # 3) System DPI Aware via user32.SetProcessDPIAware().
    try:
        sys_aware = _user32.SetProcessDPIAware
        sys_aware.argtypes = []
        sys_aware.restype = wintypes.BOOL
        if sys_aware():
            return
    except (AttributeError, OSError):
        pass

    raise WindowsKernelApiInvocationError(
        "Failed to opt this process into any flavor of Microsoft Windows DPI "
        "Awareness (Per-Monitor V2, Per-Monitor V1, and System DPI Aware all "
        "failed). The Microsoft Windows host operating system this program is "
        "running on therefore predates Microsoft Windows Vista, which this "
        "program does not target. The expected target host operating system "
        "is Microsoft Windows 10 version 1703 or later. Aborting."
    )


def _opt_into_libav_verbose_diagnostic_log_capture() -> None:
    """Install PyAV's real logging callback so ``FFmpegError.log`` is populated.

    PyAV by default installs ``nolog_callback`` (a no-op cffi callback) as the
    libav-level log sink - see ``av/logging.py`` line ~308 in PyAV 17.x:

        lib.av_log_set_callback(nolog_callback)

    With that callback active, every libav log line (including
    ``AV_LOG_ERROR`` lines emitted immediately before libav returns a fatal
    error code) is discarded. The on-exception ``FFmpegError.log`` attribute
    that PyAV's ``err_check`` reads from ``av.logging.get_last_error()`` is
    therefore always ``None``, so an EINVAL out of (say) the mp4 muxer's
    strict-monotonic-DTS guard surfaces to Python as the maddeningly empty::

        Invalid argument: 'recording.mp4' returned 22

    with no indication of *which* of the dozens of EINVAL paths inside
    ``libavcodec`` / ``libavformat`` actually fired.

    Calling ``av.logging.set_level(<int>)`` at process start replaces
    ``nolog_callback`` with the real ``log_callback`` (``av/logging.py`` line
    ~88), which unconditionally records every ``AV_LOG_ERROR`` line into
    PyAV's ``last_error`` slot regardless of the chosen threshold. The next
    ``err_check`` call then attaches that line to the raised exception as a
    ``(level, source_name, message)`` tuple on ``FFmpegError.log``.

    ``av.logging.VERBOSE`` is chosen over ``DEBUG`` deliberately: the
    diagnostic payload we care about (eg the mp4 muxer's
    ``"Application provided invalid, non monotonically increasing dts to
    muxer"`` line, or h264_qsv's ``"Error during encoding: invalid video
    parameters"`` line) is emitted at ``AV_LOG_ERROR`` and is captured at
    every threshold from ``QUIET`` to ``TRACE``. ``VERBOSE`` keeps
    libavformat's per-frame muxer chatter out of stderr (the default
    callback also writes to stderr at the chosen threshold) while still
    surfacing every codec-level warning that might foreshadow a later
    fatal.

    Must be called exactly once and before any libav call - in particular
    before the ``av.codec.Codec(name, mode)`` probe done at startup to
    verify the ``h264_qsv`` encoder is available. We deliberately do NOT
    wrap this in try/except: if PyAV's logging API surface ever moves, we
    want a loud ``AttributeError`` at startup rather than a silent fallback
    to the no-op callback that would erase our ability to diagnose any
    future encoder failure.
    """
    av.logging.set_level(av.logging.VERBOSE)


# Microsoft Windows' nominal "logical DPI" baseline: at 100% display
# scaling, GetDpiForMonitor reports this number of pixels per logical
# inch. Every higher accessibility scale step is a multiple of this
# (125% -> 120, 150% -> 144, 175% -> 168, 200% -> 192, ...). The Tcl/Tk
# ``tk scaling`` command is in "pixels per point", where a point is
# 1/72 of an inch by typographic convention, so converting an effective
# DPI value into a tk-scaling factor is a pure division by 72.
_MICROSOFT_WINDOWS_LOGICAL_DPI_BASELINE_AT_100_PERCENT_SCALE: Final[int] = 96
_POINTS_PER_INCH_BY_TYPOGRAPHIC_CONVENTION: Final[int] = 72


def _query_effective_dpi_pixels_per_inch_of_monitor_containing_pixel(
    virtual_screen_x_pixel: int,
    virtual_screen_y_pixel: int,
) -> tuple[int, int]:
    """Return ``(dpi_x, dpi_y)`` for the monitor containing the given pixel.

    Uses ``MonitorFromPoint`` followed by ``GetDpiForMonitor`` with the
    ``MDT_EFFECTIVE_DPI`` enum value so the result reflects the user's
    Microsoft Windows "Change the size of text, apps, and other items"
    accessibility scale setting for that specific monitor - not the
    process-wide value, and not the primary monitor's value, both of
    which would defeat the entire point of being Per-Monitor-V2 aware.

    If either Win32 call fails (eg the host operating system predates
    Windows 8.1 and ships a shcore.dll without GetDpiForMonitor, or
    the point falls in a no-monitor coordinate-space gap that even
    ``MONITOR_DEFAULTTONEAREST`` cannot resolve), this function falls
    back to the Microsoft Windows logical-DPI baseline of 96 pixels
    per inch for both axes - which is the right behavior for a host
    on which DPI awareness is not a meaningful concept anyway.
    """
    # Guard: GetDpiForMonitor may be unavailable on pre-8.1 builds.
    if _shcore is None or not hasattr(_shcore, "GetDpiForMonitor"):
        return (
            _MICROSOFT_WINDOWS_LOGICAL_DPI_BASELINE_AT_100_PERCENT_SCALE,
            _MICROSOFT_WINDOWS_LOGICAL_DPI_BASELINE_AT_100_PERCENT_SCALE,
        )

    target_point = wintypes.POINT(
        virtual_screen_x_pixel,
        virtual_screen_y_pixel,
    )
    target_monitor_handle = _user32.MonitorFromPoint(
        target_point,
        wintypes.DWORD(_MONITOR_DEFAULTTONEAREST_FLAG_VALUE),
    )
    if not target_monitor_handle:
        return (
            _MICROSOFT_WINDOWS_LOGICAL_DPI_BASELINE_AT_100_PERCENT_SCALE,
            _MICROSOFT_WINDOWS_LOGICAL_DPI_BASELINE_AT_100_PERCENT_SCALE,
        )

    effective_dpi_x_out = wintypes.UINT(0)
    effective_dpi_y_out = wintypes.UINT(0)
    try:
        # ctypes auto-raises OSError on a failure HRESULT from
        # GetDpiForMonitor (restype is ctypes.HRESULT). The function
        # returns S_OK = 0 on success.
        _shcore.GetDpiForMonitor(
            target_monitor_handle,
            ctypes.c_int(_MDT_EFFECTIVE_DPI_VALUE),
            ctypes.byref(effective_dpi_x_out),
            ctypes.byref(effective_dpi_y_out),
        )
    except OSError:
        return (
            _MICROSOFT_WINDOWS_LOGICAL_DPI_BASELINE_AT_100_PERCENT_SCALE,
            _MICROSOFT_WINDOWS_LOGICAL_DPI_BASELINE_AT_100_PERCENT_SCALE,
        )

    return (
        int(effective_dpi_x_out.value),
        int(effective_dpi_y_out.value),
    )


# Microsoft Windows named system fonts whose pixel metrics were cached
# inside Tcl/Tk's font subsystem at ``Tk_Init`` time using the *primary*
# monitor's DPI (via ``GetDC(NULL); GetDeviceCaps(LOGPIXELSY)`` inside
# ``win/tkWinFont.c``'s TkWinSetupSystemFonts). When we later raise
# ``tk scaling`` to the *target* monitor's DPI to fix multi-monitor
# layouts, those cached pixel metrics do NOT auto-refresh - Tcl/Tk has
# no ``WM_DPICHANGED`` handler whatsoever on Windows (confirmed by
# grepping the Tk ``main`` branch for ``WM_DPICHANGED``, which returns
# zero results). We therefore explicitly re-configure each named font
# to its existing point size, which on the Tcl/Tk side triggers a font
# re-derivation that *does* honor the new scaling.
#
# This is the complete set of named system fonts created by Tk at
# startup. See https://www.tcl-lang.org/man/tcl9.0/TkCmd/font.htm
_MICROSOFT_WINDOWS_TCL_TK_NAMED_SYSTEM_FONT_NAMES: Final[tuple[str, ...]] = (
    "TkDefaultFont",
    "TkTextFont",
    "TkFixedFont",
    "TkMenuFont",
    "TkHeadingFont",
    "TkCaptionFont",
    "TkSmallCaptionFont",
    "TkIconFont",
    "TkTooltipFont",
)


def _apply_target_monitor_effective_dpi_to_tkinter_root(
    tk_root: "tk.Tk",
    target_monitor_effective_dpi_pixels_per_inch: int,
) -> None:
    """Re-scale Tcl/Tk to match the target monitor's effective DPI.

    Must be called immediately after ``tk.Tk()`` and *before any
    widget is constructed*. The Tcler's-Wiki ``tk scaling`` page is
    explicit on the timing constraint: "Measurements made after the
    scaling factor is changed will use the new scaling factor, but it
    is undefined whether existing widgets will resize themselves
    dynamically." So we set scaling early, then build widgets fresh
    against the correct scaling.

    Two steps:

      1. ``tk scaling`` is set to the target monitor's pixels-per-point
         ratio (DPI / 72). Future font measurements and widget natural
         sizes will use this ratio.

      2. Each Microsoft Windows named system font (``TkDefaultFont``,
         ``TkTextFont``, ``TkFixedFont``, etc.) is re-configured to its
         existing point size. Tcl/Tk does not refresh those fonts'
         cached pixel metrics in response to a ``tk scaling`` change;
         re-configuring with the same logical size triggers a font
         re-derivation that does honor the new scaling.

    After this returns, ``font.measure("0")`` against ``TkDefaultFont``
    reflects target-monitor pixels, ttk button heights match what
    will physically render, and ``winfo_reqheight()`` after a layout
    pass returns target-monitor-pixel-correct values.
    """
    new_tk_scaling_pixels_per_point = (
        target_monitor_effective_dpi_pixels_per_inch
        / _POINTS_PER_INCH_BY_TYPOGRAPHIC_CONVENTION
    )
    try:
        tk_root.tk.call(
            "tk",
            "scaling",
            new_tk_scaling_pixels_per_point,
        )
    except tk.TclError:
        # If the Tcl/Tk build does not accept a floating-point scaling
        # value (extremely unlikely on Microsoft Windows; would only
        # happen on a deeply broken Tk install), there is nothing more
        # we can do - the rest of the program will still run.
        return

    for named_font_name in _MICROSOFT_WINDOWS_TCL_TK_NAMED_SYSTEM_FONT_NAMES:
        try:
            named_font = tk_font.nametofont(named_font_name)
        except tk.TclError:
            # Not every named font exists on every Microsoft Windows
            # build (eg TkHeadingFont is platform-conditional). Skip
            # silently and continue.
            continue
        current_size = named_font.cget("size")
        # Tcl/Tk font sizes are positive integers for points and
        # negative integers for direct pixels. Re-deriving makes
        # sense only for point-size fonts; pixel-size fonts already
        # bypass the DPI conversion that ``tk scaling`` controls.
        if isinstance(current_size, int) and current_size > 0:
            try:
                named_font.configure(size=current_size)
            except tk.TclError:
                continue


def _resolve_current_user_videos_known_folder_path() -> Path:
    """Return ``Path`` to the current user's Microsoft Windows Videos Known Folder.

    Uses the Microsoft-blessed ``SHGetKnownFolderPath`` API with
    ``FOLDERID_Videos = {18989B1D-99B5-455B-841C-AB7C74E4DDFC}``, which
    respects Folder Redirection, Group Policy, and OneDrive Known Folder
    Move (it will return the OneDrive-redirected path if the current user
    has Videos enrolled in OneDrive backup).

    Does NOT create the folder if it is missing - the Videos Known Folder
    is always present on a normally-installed Microsoft Windows 10
    workstation, and silently creating it would mask host-machine
    misconfiguration. If the folder is missing, this function still
    returns its resolved path - it is the caller's responsibility to
    verify existence with the dedicated validation routine below.
    """
    out_pointer = ctypes.c_wchar_p()
    try:
        # ctypes auto-raises OSError on failure HRESULTs from
        # SHGetKnownFolderPath (because restype is HRESULT), but we still
        # belt-and-braces verify the out pointer is non-null.
        _shell32.SHGetKnownFolderPath(
            ctypes.byref(_FOLDERID_VIDEOS_KNOWN_FOLDER_GUID),
            wintypes.DWORD(0),  # KF_FLAG_DEFAULT
            wintypes.HANDLE(0),  # NULL = current user
            ctypes.byref(out_pointer),
        )
        if not out_pointer.value:
            raise VideosKnownFolderUnresolvableError(
                "The Microsoft Win32 API SHGetKnownFolderPath returned the "
                "success HRESULT S_OK = 0x00000000 but its out-parameter "
                "ppszPath was the null pointer, which the Microsoft "
                "documentation says shall never happen. This indicates a "
                "host Microsoft Windows installation defect that this "
                "program cannot recover from."
            )
        return Path(out_pointer.value)
    except OSError as os_err:
        # ctypes raised this because SHGetKnownFolderPath returned a failure
        # HRESULT (any non-zero value). Re-wrap with our own type and the
        # most context we can include.
        raise VideosKnownFolderUnresolvableError(
            f"The Microsoft Win32 API SHGetKnownFolderPath failed when "
            f"resolving the Videos Known Folder for the current user.\n"
            f"  Requested KNOWNFOLDERID GUID: "
            f"{{18989B1D-99B5-455B-841C-AB7C74E4DDFC}} (FOLDERID_Videos)\n"
            f"  Requested flags             : KF_FLAG_DEFAULT (0x00000000)\n"
            f"  Requested user token        : NULL (the current user)\n"
            f"  Underlying OSError          : {os_err!r}\n"
            f"This commonly indicates the current user's profile is corrupt, "
            f"or that the Videos Known Folder has been removed from the "
            f"Windows Known Folder registry. This program does not auto-"
            f"create or repair Known Folders - please repair the user's "
            f"Microsoft Windows profile and retry."
        ) from os_err
    finally:
        # Microsoft documentation: caller MUST free the returned PWSTR via
        # CoTaskMemFree(), whether the call succeeded or not.
        if out_pointer:
            _ole32.CoTaskMemFree(out_pointer)


@dataclasses.dataclass(frozen=True)
class _AttachedDisplayMonitor:
    """An immutable snapshot of one Microsoft Windows attached display monitor."""

    win32_device_path: str             # eg "\\.\DISPLAY1"
    win32_friendly_device_string: str  # eg "Generic PnP Monitor"
    bounding_rectangle_left: int
    bounding_rectangle_top: int
    bounding_rectangle_width: int
    bounding_rectangle_height: int
    is_microsoft_windows_primary_display: bool

    @property
    def bounding_rectangle_right(self) -> int:
        return self.bounding_rectangle_left + self.bounding_rectangle_width

    @property
    def bounding_rectangle_bottom(self) -> int:
        return self.bounding_rectangle_top + self.bounding_rectangle_height

    def as_jsonable_dict(self) -> dict[str, Any]:
        return {
            "win32_device_path": self.win32_device_path,
            "win32_friendly_device_string": self.win32_friendly_device_string,
            "bounding_rectangle_left": self.bounding_rectangle_left,
            "bounding_rectangle_top": self.bounding_rectangle_top,
            "bounding_rectangle_width": self.bounding_rectangle_width,
            "bounding_rectangle_height": self.bounding_rectangle_height,
            "is_microsoft_windows_primary_display": (
                self.is_microsoft_windows_primary_display
            ),
        }


def _enumerate_attached_display_monitors() -> list[_AttachedDisplayMonitor]:
    """Enumerate every currently-attached display monitor using Win32 APIs.

    Calls ``EnumDisplayMonitors`` and ``GetMonitorInfoW`` to discover each
    attached HMONITOR's bounding rectangle in virtual-screen coordinates
    and its primary-display flag, then ``EnumDisplayDevicesW`` to look up
    the friendly device string for each.

    Raises ``WindowsKernelApiInvocationError`` if any underlying Microsoft
    Win32 API call returns failure.
    """
    discovered_monitors: list[_AttachedDisplayMonitor] = []
    enumeration_errors: list[str] = []

    def callback(
        hmonitor: int,
        _hdc_unused: int,
        _rect_unused: Any,
        _lparam_unused: int,
    ) -> int:
        monitor_info_struct = _Win32MonitorInfoExW()
        monitor_info_struct.cbSize = ctypes.sizeof(_Win32MonitorInfoExW)
        if not _user32.GetMonitorInfoW(hmonitor, ctypes.byref(monitor_info_struct)):
            last_win32_error_code = ctypes.get_last_error()
            enumeration_errors.append(
                f"GetMonitorInfoW(HMONITOR=0x{hmonitor:016X}) returned FALSE; "
                f"GetLastError()={last_win32_error_code} "
                f"(0x{last_win32_error_code:08X})"
            )
            return 1  # continue enumeration anyway

        device_path = monitor_info_struct.szDevice
        rect = monitor_info_struct.rcMonitor

        # Second pass to obtain the friendly DeviceString for this
        # display device.
        display_device_struct = _Win32DisplayDeviceW()
        display_device_struct.cb = ctypes.sizeof(_Win32DisplayDeviceW)
        friendly_name = device_path  # default to the raw device path
        if _user32.EnumDisplayDevicesW(
            device_path, 0, ctypes.byref(display_device_struct), 0
        ):
            if display_device_struct.DeviceString:
                friendly_name = display_device_struct.DeviceString

        discovered_monitors.append(
            _AttachedDisplayMonitor(
                win32_device_path=device_path,
                win32_friendly_device_string=friendly_name,
                bounding_rectangle_left=rect.left,
                bounding_rectangle_top=rect.top,
                bounding_rectangle_width=rect.right - rect.left,
                bounding_rectangle_height=rect.bottom - rect.top,
                is_microsoft_windows_primary_display=bool(
                    monitor_info_struct.dwFlags & _MONITORINFOF_PRIMARY
                ),
            )
        )
        return 1  # TRUE: keep enumerating

    callback_handle = _MONITOR_ENUMERATION_CALLBACK_PROTOTYPE(callback)
    if not _user32.EnumDisplayMonitors(
        wintypes.HDC(0),
        ctypes.POINTER(wintypes.RECT)(),
        callback_handle,
        wintypes.LPARAM(0),
    ):
        last_win32_error_code = ctypes.get_last_error()
        raise WindowsKernelApiInvocationError(
            f"The Microsoft Win32 API EnumDisplayMonitors returned FALSE.\n"
            f"  Passed HDC                 : NULL (enumerate every monitor "
            f"on the virtual screen)\n"
            f"  Passed clip rectangle      : NULL (no clipping)\n"
            f"  Microsoft Win32 last error : code={last_win32_error_code}, "
            f"value=0x{last_win32_error_code:08X}\n"
            f"This typically indicates the calling process has no access to "
            f"the desktop window station (eg running as a service in "
            f"session 0). This program must be run in an interactive "
            f"desktop session of the operator's user account."
        )

    if enumeration_errors:
        raise WindowsKernelApiInvocationError(
            "The Microsoft Win32 API EnumDisplayMonitors enumerated monitors "
            "but one or more GetMonitorInfoW calls failed during enumeration:"
            "\n  " + "\n  ".join(enumeration_errors)
        )

    return discovered_monitors


# ============================================================================
# Section 8 - Display configuration validation
# ============================================================================

@dataclasses.dataclass(frozen=True)
class ValidatedDualExtendModeDisplayConfiguration:
    """A snapshot of an attached pair of displays validated as dual-extend-mode."""

    microsoft_windows_display_1_primary_to_be_recorded: _AttachedDisplayMonitor
    microsoft_windows_display_2_secondary_for_operator_gui: _AttachedDisplayMonitor
    every_attached_monitor_at_validation_time: tuple[_AttachedDisplayMonitor, ...]

    def as_jsonable_dict(self) -> dict[str, Any]:
        return {
            "microsoft_windows_display_1_primary_to_be_recorded": (
                self.microsoft_windows_display_1_primary_to_be_recorded
                .as_jsonable_dict()
            ),
            "microsoft_windows_display_2_secondary_for_operator_gui": (
                self.microsoft_windows_display_2_secondary_for_operator_gui
                .as_jsonable_dict()
            ),
            "every_attached_monitor_at_validation_time": [
                monitor.as_jsonable_dict()
                for monitor in self.every_attached_monitor_at_validation_time
            ],
        }


def validate_dual_extend_mode_display_configuration() -> (
    ValidatedDualExtendModeDisplayConfiguration
):
    """Validate the host has exactly two attached extend-mode displays.

    Returns the validated configuration with Display 1 (Microsoft Windows
    primary) and Display 2 (Microsoft Windows secondary) explicitly
    identified.

    Raises ``DisplayConfigurationValidationError`` with a verbose,
    context-rich message in every disallowed case:

      * zero attached displays (process has no desktop session)
      * one attached display (this program requires two)
      * three or more attached displays (this program requires exactly two)
      * zero or multiple displays marked primary
      * two displays whose bounding rectangles overlap (Microsoft Windows
        Duplicate-mode configuration, which presents as two HMONITORs only
        in degenerate driver states)
      * python-mss disagrees with EnumDisplayMonitors on the set of
        attached displays
    """
    attached_monitors = _enumerate_attached_display_monitors()
    attached_count = len(attached_monitors)

    if attached_count == 0:
        raise DisplayConfigurationValidationError(
            "The Microsoft Win32 API EnumDisplayMonitors enumerated ZERO "
            "attached display monitors. This is impossible on a Microsoft "
            "Windows desktop session with at least one connected display, "
            "and indicates this process is running with no access to the "
            "Microsoft Windows interactive desktop (eg invoked as a "
            "Microsoft Windows Service in session 0, or as a background "
            "task with no associated window station). This program must "
            "be invoked in an interactive desktop session of the operator."
        )

    if attached_count == 1:
        only_monitor = attached_monitors[0]
        raise DisplayConfigurationValidationError(
            f"Exactly ONE attached display monitor was enumerated; this "
            f"program strictly requires exactly TWO attached displays in "
            f"Microsoft Windows extend mode (Display 1 = primary, will be "
            f"recorded; Display 2 = secondary, hosts the operator GUI).\n"
            f"  Sole enumerated display:\n"
            f"    Microsoft Win32 device path : {only_monitor.win32_device_path}\n"
            f"    Friendly device string      : {only_monitor.win32_friendly_device_string}\n"
            f"    Bounding rectangle (px)     : "
            f"left={only_monitor.bounding_rectangle_left}, "
            f"top={only_monitor.bounding_rectangle_top}, "
            f"width={only_monitor.bounding_rectangle_width}, "
            f"height={only_monitor.bounding_rectangle_height}\n"
            f"    Microsoft Windows primary?  : "
            f"{only_monitor.is_microsoft_windows_primary_display}\n"
            f"Resolution: connect a second monitor in Microsoft Windows "
            f"Display Settings (System -> Display) and set Multiple "
            f"Displays to 'Extend these displays', then retry."
        )

    if attached_count > 2:
        raise DisplayConfigurationValidationError(
            f"{attached_count} attached display monitors were enumerated; "
            f"this program strictly requires exactly TWO. The list of "
            f"enumerated monitors follows; please disconnect or disable "
            f"the unwanted displays in Microsoft Windows Display Settings "
            f"and retry.\n"
            + "\n".join(
                f"  Display [{i}]: device={m.win32_device_path}, "
                f"friendly={m.win32_friendly_device_string}, "
                f"rect=({m.bounding_rectangle_left},"
                f"{m.bounding_rectangle_top},"
                f"{m.bounding_rectangle_width}x"
                f"{m.bounding_rectangle_height}), "
                f"primary={m.is_microsoft_windows_primary_display}"
                for i, m in enumerate(attached_monitors)
            )
        )

    primary_displays = [
        m for m in attached_monitors if m.is_microsoft_windows_primary_display
    ]
    if len(primary_displays) != 1:
        raise DisplayConfigurationValidationError(
            f"Exactly two attached display monitors were enumerated, but "
            f"{len(primary_displays)} (not 1) of them carry the "
            f"MONITORINFOF_PRIMARY (0x00000001) flag in MONITORINFO.dwFlags. "
            f"Microsoft Windows requires exactly one primary display at all "
            f"times; this state indicates a Microsoft Windows display "
            f"configuration defect that this program will not paper over.\n"
            f"  Enumerated monitors:\n"
            + "\n".join(
                f"    [{i}] device={m.win32_device_path}, "
                f"primary={m.is_microsoft_windows_primary_display}"
                for i, m in enumerate(attached_monitors)
            )
        )

    primary_display = primary_displays[0]
    secondary_display = next(
        m for m in attached_monitors if m is not primary_display
    )

    # Sanity: bounding rectangles must not overlap (Duplicate mode would
    # normally collapse to a single HMONITOR, but defensive verification
    # is cheap).
    if _bounding_rectangles_overlap(primary_display, secondary_display):
        raise DisplayConfigurationValidationError(
            f"Both attached display monitors have overlapping bounding "
            f"rectangles in virtual-screen coordinates. This indicates "
            f"Microsoft Windows is in Duplicate / Mirror mode (or a driver "
            f"defect). This program requires Microsoft Windows extend "
            f"mode (Multiple Displays = 'Extend these displays').\n"
            f"  Display 1 (primary)   : rect=("
            f"left={primary_display.bounding_rectangle_left}, "
            f"top={primary_display.bounding_rectangle_top}, "
            f"width={primary_display.bounding_rectangle_width}, "
            f"height={primary_display.bounding_rectangle_height})\n"
            f"  Display 2 (secondary) : rect=("
            f"left={secondary_display.bounding_rectangle_left}, "
            f"top={secondary_display.bounding_rectangle_top}, "
            f"width={secondary_display.bounding_rectangle_width}, "
            f"height={secondary_display.bounding_rectangle_height})"
        )

    # Cross-verify python-mss agrees with EnumDisplayMonitors. python-mss
    # uses the same Microsoft Win32 EnumDisplayMonitors under the hood; if
    # the two disagree we are in undefined territory and must abort.
    with mss.mss() as sct:
        mss_reported_monitors = list(sct.monitors)  # index 0 is virtual screen
    mss_individual_monitors = mss_reported_monitors[1:]

    win32_rect_set = {
        (
            m.bounding_rectangle_left,
            m.bounding_rectangle_top,
            m.bounding_rectangle_width,
            m.bounding_rectangle_height,
        )
        for m in attached_monitors
    }
    mss_rect_set = {
        (m["left"], m["top"], m["width"], m["height"])
        for m in mss_individual_monitors
    }
    if win32_rect_set != mss_rect_set:
        raise DisplayConfigurationValidationError(
            f"The Microsoft Win32 EnumDisplayMonitors API and the python-mss "
            f"library disagree on the set of attached display monitors:\n"
            f"  Win32 EnumDisplayMonitors rects: {sorted(win32_rect_set)}\n"
            f"  python-mss .monitors[1:] rects : {sorted(mss_rect_set)}\n"
            f"This typically indicates python-mss was imported before the "
            f"current process opted in to Per-Monitor V2 DPI Awareness, or "
            f"that the display configuration changed between the two API "
            f"calls. Either way this program will not proceed."
        )

    return ValidatedDualExtendModeDisplayConfiguration(
        microsoft_windows_display_1_primary_to_be_recorded=primary_display,
        microsoft_windows_display_2_secondary_for_operator_gui=secondary_display,
        every_attached_monitor_at_validation_time=tuple(attached_monitors),
    )


def _bounding_rectangles_overlap(
    a: _AttachedDisplayMonitor, b: _AttachedDisplayMonitor
) -> bool:
    return not (
        a.bounding_rectangle_right <= b.bounding_rectangle_left
        or b.bounding_rectangle_right <= a.bounding_rectangle_left
        or a.bounding_rectangle_bottom <= b.bounding_rectangle_top
        or b.bounding_rectangle_bottom <= a.bounding_rectangle_top
    )


# ============================================================================
# Section 9 - Intel Quick Sync Video encoder availability check
# ============================================================================

def verify_intel_quick_sync_video_h264_encoder_available() -> None:
    """Confirm PyAV's bundled FFmpeg exposes the ``h264_qsv`` H.264 encoder.

    Raises ``IntelQuickSyncEncoderUnavailableError`` (with the list of
    encoder names PyAV *does* advertise) if not.
    """
    if INTEL_QUICK_SYNC_VIDEO_H264_ENCODER_FFMPEG_NAME not in av.codecs_available:
        h264_family_present = sorted(
            name for name in av.codecs_available if "h264" in name.lower()
        )
        raise IntelQuickSyncEncoderUnavailableError(
            f"The Intel Quick Sync Video H.264 encoder named "
            f"'{INTEL_QUICK_SYNC_VIDEO_H264_ENCODER_FFMPEG_NAME}' is not "
            f"present in the FFmpeg build bundled inside the installed PyAV "
            f"wheel.\n"
            f"  PyAV version reported               : {av.__version__}\n"
            f"  Encoders containing the substring 'h264' that ARE present: "
            f"{h264_family_present}\n"
            f"This program is hard-coded to require the "
            f"'{INTEL_QUICK_SYNC_VIDEO_H264_ENCODER_FFMPEG_NAME}' encoder "
            f"because it targets the Intel UHD Graphics 770 integrated GPU "
            f"on the 13th-generation Intel Core i7-13700 processor.\n"
            f"Resolution: install a build of FFmpeg / PyAV whose libavcodec "
            f"is linked against Intel oneVPL (formerly Intel Media SDK / "
            f"libmfx). On Anaconda this is typically obtained via:\n"
            f"  conda install -c conda-forge 'ffmpeg=*=*gpl*'\n"
            f"followed by reinstalling 'av' against that FFmpeg."
        )

    # Construct an actual encoder context to surface deferred failures
    # such as a missing oneVPL runtime DLL.
    try:
        av.codec.Codec(
            INTEL_QUICK_SYNC_VIDEO_H264_ENCODER_FFMPEG_NAME, "w"
        )
    except Exception as construction_exc:
        raise IntelQuickSyncEncoderUnavailableError(
            f"The Intel Quick Sync Video H.264 encoder named "
            f"'{INTEL_QUICK_SYNC_VIDEO_H264_ENCODER_FFMPEG_NAME}' is listed "
            f"in av.codecs_available, but constructing a PyAV "
            f"av.codec.Codec(name='{INTEL_QUICK_SYNC_VIDEO_H264_ENCODER_FFMPEG_NAME}', "
            f"mode='w') still raised:\n"
            f"  {type(construction_exc).__name__}: {construction_exc}\n"
            f"This typically indicates the Intel oneVPL runtime DLLs are "
            f"missing from the system PATH, or the Intel Graphics driver "
            f"is too old. Update the Intel Graphics driver to a build of "
            f"at least 31.0.101.4502 (the reference target driver) and "
            f"retry."
        ) from construction_exc


# ============================================================================
# Section 10 - Output folder provisioning
# ============================================================================

@dataclasses.dataclass(frozen=True)
class PerSessionOutputFolderArtifactPaths:
    """The full set of per-session output file paths.

    The session writes into a local per-user temporary scratch folder
    while it is running (``parent_session_folder`` and the
    ``*_file_path`` fields nested under it). On graceful Stop, the
    whole scratch folder is copied into the Videos Known Folder at
    ``final_publication_parent_session_folder``. Two distinct paths
    are tracked because the Videos Known Folder is frequently
    redirected onto an SMB or OneDrive backing store that does not
    reliably support the seek-back-and-overwrite I/O pattern that the
    fragmented-MP4 muxer issues mid-stream; the scratch folder on the
    local NTFS volume always does.

    ``has_been_successfully_published_to_videos_library`` flips to True
    once ``move_session_outputs_from_temporary_scratch_into_videos_library``
    has finished copying every artifact into
    ``final_publication_parent_session_folder``. Until then the live
    on-disk artifacts only exist under ``parent_session_folder``.
    """

    parent_session_folder: Path
    final_publication_parent_session_folder: Path
    iso8601_session_id: str
    fragmented_mp4_video_file_path: Path
    session_metadata_json_file_path: Path
    display_configuration_json_file_path: Path
    per_frame_log_csv_file_path: Path
    text_log_file_path: Path
    has_been_successfully_published_to_videos_library: bool = False


def _build_iso8601_filesystem_safe_session_identifier(
    instant: _datetime_module.datetime,
) -> str:
    """Return ``YYYY-MM-DDTHH-MM-SS-FFFFFF`` for the given local instant.

    Microsoft Windows reserves the colon ':' character in file and folder
    names; we therefore replace each colon of the canonical ISO 8601
    timestamp form with a hyphen, preserving alphabetical-equals-
    chronological sort order. Microseconds are included to make collisions
    on rapid Stop/Start cycles impossible.
    """
    return (
        f"{instant.year:04d}-{instant.month:02d}-{instant.day:02d}T"
        f"{instant.hour:02d}-{instant.minute:02d}-{instant.second:02d}-"
        f"{instant.microsecond:06d}"
    )


def _resolve_per_user_temporary_directory_root_path() -> Path:
    """Return the current user's per-user temporary directory as a ``Path``.

    Delegates to ``tempfile.gettempdir()``, which on Microsoft Windows
    consults the per-user ``TMP``, ``TEMP``, and ``USERPROFILE``
    environment variables (in that order) and finally falls back to the
    Windows directory. On a normally-installed Microsoft Windows
    workstation this resolves to ``%LOCALAPPDATA%\\Temp\\`` for the
    interactively-logged-in user, which is backed by the local NTFS
    volume and is therefore safe for the seek-back-and-overwrite I/O
    pattern issued mid-stream by the fragmented-MP4 muxer (unlike a
    Videos Known Folder redirected onto an SMB share).
    """
    return Path(tempfile.gettempdir())


def provision_fresh_per_session_output_folder(
    *,
    temporary_directory_root: Path,
    videos_known_folder_root: Path,
) -> PerSessionOutputFolderArtifactPaths:
    """Create a fresh per-session scratch folder and return both paths.

    Validates that *both* roots already exist on disk as directories and
    refuses to proceed if either does not - the recorder will not
    silently auto-create either root, because doing so is precisely
    the kind of behavior corporate endpoint detection and response
    tools flag as suspicious.

    Creates the scratch session folder underneath the per-user
    temporary directory at::

        <Temp>\\WindowsScreenRecorder\\<session-id>\\

    and verifies it is writable via a tiny round-trip probe file.

    Computes (but does *not* create) the final publication folder
    underneath the Videos Known Folder at the structurally identical
    path::

        <Videos>\\WindowsScreenRecorder\\<session-id>\\

    The final folder (and any missing intermediates underneath the
    Videos Known Folder) are created later, on Stop, by
    ``move_session_outputs_from_temporary_scratch_into_videos_library``.

    Raises ``OutputFolderProvisioningError`` if scratch provisioning
    fails, or ``PerUserTemporaryDirectoryUnusableError`` /
    ``VideosKnownFolderUnresolvableError`` -style validation failures
    if either root is missing.
    """
    if not temporary_directory_root.is_dir():
        raise PerUserTemporaryDirectoryUnusableError(
            f"The per-user temporary directory resolved by Python's "
            f"tempfile.gettempdir() is not an existing directory on disk.\n"
            f"  Resolved per-user temporary directory path : "
            f"{temporary_directory_root}\n"
            f"  Path.is_dir()                              : False\n"
            f"On a normally-installed Microsoft Windows workstation this "
            f"resolves to '%LOCALAPPDATA%\\Temp\\' for the interactively-"
            f"logged-in user and always exists. Its absence indicates a "
            f"corrupt user profile or a misconfigured TMP / TEMP "
            f"environment variable. This program refuses to silently "
            f"auto-create the per-user temporary directory."
        )

    if not videos_known_folder_root.is_dir():
        raise OutputFolderProvisioningError(
            f"The Microsoft Windows Videos Known Folder resolved by "
            f"SHGetKnownFolderPath (with FOLDERID_Videos = "
            f"{{18989B1D-99B5-455B-841C-AB7C74E4DDFC}}) is not an existing "
            f"directory on disk.\n"
            f"  Resolved Videos Known Folder path : {videos_known_folder_root}\n"
            f"  Path.is_dir()                     : False\n"
            f"This program refuses to silently create the Videos Known "
            f"Folder; doing so is precisely the kind of behavior corporate "
            f"endpoint detection and response tools flag as suspicious. "
            f"Please repair the current user's Microsoft Windows profile "
            f"(eg via the Microsoft Windows Settings app or by re-enabling "
            f"the Videos Known Folder via Group Policy) and retry."
        )

    scratch_parent_folder = (
        temporary_directory_root
        / OUTPUT_PARENT_SUBFOLDER_NAME_UNDER_VIDEOS_KNOWN_FOLDER
    )
    try:
        scratch_parent_folder.mkdir(parents=False, exist_ok=True)
    except OSError as mkdir_exc:
        raise OutputFolderProvisioningError(
            f"Could not create the parent scratch subfolder underneath the "
            f"per-user temporary directory.\n"
            f"  Attempted parent scratch folder : {scratch_parent_folder}\n"
            f"  Underlying OSError              : {mkdir_exc!r}\n"
            f"Resolution: ensure the current user has write permission "
            f"to '{temporary_directory_root}'."
        ) from mkdir_exc

    now_local = _datetime_module.datetime.now()
    session_identifier = _build_iso8601_filesystem_safe_session_identifier(
        now_local
    )
    scratch_session_folder = scratch_parent_folder / session_identifier
    try:
        scratch_session_folder.mkdir(parents=False, exist_ok=False)
    except FileExistsError as already_exists_exc:
        raise OutputFolderProvisioningError(
            f"The freshly-constructed ISO 8601 scratch session folder path "
            f"already exists, which is impossible because the session "
            f"identifier includes microseconds.\n"
            f"  Attempted scratch session folder : "
            f"{scratch_session_folder}\n"
            f"This indicates either a clock that runs backwards or a "
            f"corrupted filesystem. Aborting before any data is written."
        ) from already_exists_exc
    except OSError as mkdir_exc:
        raise OutputFolderProvisioningError(
            f"Could not create the per-session scratch folder.\n"
            f"  Attempted scratch session folder : "
            f"{scratch_session_folder}\n"
            f"  Underlying OSError               : {mkdir_exc!r}"
        ) from mkdir_exc

    # Smoke-test writability with a probe file we delete immediately.
    probe_path = scratch_session_folder / ".writability_probe.tmp"
    try:
        probe_path.write_bytes(b"")
        probe_path.unlink()
    except OSError as probe_exc:
        raise OutputFolderProvisioningError(
            f"The per-session scratch folder was created but is not "
            f"writable.\n"
            f"  Scratch session folder : {scratch_session_folder}\n"
            f"  Underlying OSError     : {probe_exc!r}"
        ) from probe_exc

    final_publication_session_folder = (
        videos_known_folder_root
        / OUTPUT_PARENT_SUBFOLDER_NAME_UNDER_VIDEOS_KNOWN_FOLDER
        / session_identifier
    )

    return PerSessionOutputFolderArtifactPaths(
        parent_session_folder=scratch_session_folder,
        final_publication_parent_session_folder=(
            final_publication_session_folder
        ),
        iso8601_session_id=session_identifier,
        fragmented_mp4_video_file_path=(
            scratch_session_folder / PER_SESSION_VIDEO_FILE_NAME
        ),
        session_metadata_json_file_path=(
            scratch_session_folder / PER_SESSION_METADATA_JSON_FILE_NAME
        ),
        display_configuration_json_file_path=(
            scratch_session_folder
            / PER_SESSION_DISPLAY_CONFIG_JSON_FILE_NAME
        ),
        per_frame_log_csv_file_path=(
            scratch_session_folder
            / PER_SESSION_PER_FRAME_LOG_CSV_FILE_NAME
        ),
        text_log_file_path=(
            scratch_session_folder / PER_SESSION_TEXT_LOG_FILE_NAME
        ),
    )


def move_session_outputs_from_temporary_scratch_into_videos_library(
    artifact_paths: PerSessionOutputFolderArtifactPaths,
) -> PerSessionOutputFolderArtifactPaths:
    """Move the per-session scratch folder into the Videos Known Folder.

    Move semantics, not copy: peak on-disk usage never grows beyond
    the size of the recording. Two paths exist:

      * If the per-user temporary directory and the Videos Known
        Folder reside on the *same* volume, a single ``os.rename``
        atomically relocates the scratch session folder into the
        Videos library. No double-storage transient, no chance of a
        torn copy.

      * If they reside on *different* volumes (the typical operator
        setup: scratch on the local NTFS volume, Videos redirected
        onto an SMB share or OneDrive backing store via Microsoft
        Windows Folder Redirection), the cross-volume rename raises
        ``OSError`` and this function falls back to
        ``shutil.copytree`` + ``shutil.rmtree``. Peak storage is 2x
        during the copy, dropping back to 1x as soon as the
        cross-volume copy completes and the local scratch is removed.

    Creates every missing intermediate directory underneath the Videos
    Known Folder (the per-application ``WindowsScreenRecorder`` parent
    subfolder may not exist yet on a first-ever run) before moving.

    On any copy failure the scratch folder is preserved intact so the
    operator can recover the recording manually. On copy success
    followed by a scratch-cleanup failure the recording is already
    safely at the publication target and the residual scratch is
    abandoned for Microsoft Windows' Storage Sense to reclaim later
    (raising here would falsely suggest the publication did not
    succeed).

    Returns a freshly-constructed ``PerSessionOutputFolderArtifactPaths``
    with ``has_been_successfully_published_to_videos_library = True``
    on success. Raises ``SessionPublicationToVideosLibraryError`` on
    any I/O failure that prevented the recording from reaching the
    publication target.

    The caller must guarantee that every file handle into the scratch
    folder has already been closed; an open handle on Microsoft Windows
    would either deadlock the copy or produce a torn-line copy of the
    log file.
    """
    scratch_source = artifact_paths.parent_session_folder
    publication_destination = (
        artifact_paths.final_publication_parent_session_folder
    )

    if not scratch_source.is_dir():
        raise SessionPublicationToVideosLibraryError(
            f"The per-session scratch source folder does not exist or is "
            f"not a directory; nothing to publish.\n"
            f"  Scratch source folder : {scratch_source}\n"
            f"  Publication target    : {publication_destination}"
        )

    # Create every missing intermediate directory underneath the Videos
    # Known Folder. The Videos Known Folder itself must already exist
    # (validated by provision_fresh_per_session_output_folder), but
    # the per-application subfolder may not.
    publication_parent = publication_destination.parent
    try:
        publication_parent.mkdir(parents=True, exist_ok=True)
    except OSError as parent_mkdir_exc:
        raise SessionPublicationToVideosLibraryError(
            f"Could not create the per-application publication parent "
            f"subfolder underneath the Microsoft Windows Videos Known "
            f"Folder.\n"
            f"  Scratch source folder              : {scratch_source}\n"
            f"  Attempted publication parent       : {publication_parent}\n"
            f"  Underlying OSError                 : "
            f"{parent_mkdir_exc!r}\n"
            f"The session's recording remains intact at the scratch "
            f"source folder above; the operator may copy it manually."
        ) from parent_mkdir_exc

    # Same-volume fast path: a single atomic os.rename relocates the
    # entire scratch session folder into the Videos Known Folder
    # without ever creating a second on-disk copy. On Microsoft
    # Windows, os.rename across volumes (eg the typical operator
    # setup of scratch on local C: vs Videos on a UNC SMB share)
    # raises OSError with WinError 17 / errno EXDEV; we fall through
    # to a copy-then-delete in that case.
    try:
        os.rename(str(scratch_source), str(publication_destination))
    except OSError:
        # Cross-volume move - copy first, then remove the scratch.
        # shutil.copytree refuses to overwrite an existing destination,
        # which is the behavior we want: the publication-target session
        # folder name carries a microsecond-resolution timestamp, so a
        # collision would mean a clock that ran backwards (or an
        # attempt to re-publish a previously-published session, which
        # we reject).
        try:
            shutil.copytree(
                src=str(scratch_source),
                dst=str(publication_destination),
                copy_function=shutil.copy2,
            )
        except (OSError, shutil.Error) as copytree_exc:
            raise SessionPublicationToVideosLibraryError(
                f"Could not copy the per-session scratch folder into "
                f"the Microsoft Windows Videos Known Folder.\n"
                f"  Scratch source folder      : {scratch_source}\n"
                f"  Publication target folder  : {publication_destination}\n"
                f"  Underlying error           : "
                f"{type(copytree_exc).__name__}: {copytree_exc}\n"
                f"The session's recording remains intact at the "
                f"scratch source folder above; the operator may copy "
                f"it manually."
            ) from copytree_exc

        # Best-effort scratch cleanup. A failure here is non-fatal:
        # the data is already safely at the publication target.
        # Intentionally ignore errors - the per-user temporary
        # directory's housekeeping (Microsoft Windows Storage Sense
        # or manual Disk Cleanup) will reclaim any orphaned scratch
        # folder later. Raising here would falsely suggest the
        # publication did not succeed.
        shutil.rmtree(str(scratch_source), ignore_errors=True)

    return dataclasses.replace(
        artifact_paths,
        has_been_successfully_published_to_videos_library=True,
    )


# ============================================================================
# Section 11 - Recording session lifecycle state machine
# ============================================================================

class RecordingLifecycleState(enum.Enum):
    """The states the controller transitions through during its lifetime.

    Transitions are atomic: every state change goes through
    ``RecordingSessionController._transition_state_under_lock``, which both
    validates the legal pre-state set and asserts the post-state invariants
    (resource ownership consistent with state). Illegal transitions raise
    ``IllegalRecordingStateTransitionError``; invariant violations raise
    ``InconsistentControllerStateAssertionError`` (a programming bug).
    """

    IDLE_AWAITING_OPERATOR_START = "IDLE_AWAITING_OPERATOR_START"
    # Asynchronous starter thread is validating + provisioning + spawning
    # worker threads. GUI shows "Preparing..." and disables every button
    # so the operator cannot double-click Start.
    PREPARING_SESSION_RESOURCES = "PREPARING_SESSION_RESOURCES"
    ACTIVELY_RECORDING_DISPLAY_1 = "ACTIVELY_RECORDING_DISPLAY_1"
    PAUSED_BY_OPERATOR = "PAUSED_BY_OPERATOR"
    # Asynchronous finalizer thread is waiting for worker threads to drain
    # and exit, then writing session metadata JSON and releasing all
    # per-session resources. The operator can request a force-cancel in
    # this state; that transition goes to FORCE_CANCELLATION_IN_PROGRESS.
    FINALIZING_OUTPUT_AND_JOINING_WORKERS = (
        "FINALIZING_OUTPUT_AND_JOINING_WORKERS"
    )
    # The operator pressed Force-Cancel because finalization is taking too
    # long. The cancel event has been set; the encoder worker will skip
    # the libavcodec flush and close the fragmented MP4 container as-is
    # (losing only whatever fragment was in flight at the cancel instant).
    FORCE_CANCELLATION_IN_PROGRESS = "FORCE_CANCELLATION_IN_PROGRESS"
    FATAL_ERROR_OBSERVED = "FATAL_ERROR_OBSERVED"


# ============================================================================
# Section 12 - Inter-thread frame container
# ============================================================================

@dataclasses.dataclass(frozen=True)
class _CapturedFrameForEncoder:
    """One BGRA frame captured from Display 1, addressed to the encoder thread."""

    presentation_timestamp_in_one_frame_units: int
    bgra_frame_pixel_buffer: np.ndarray  # shape (H, W, 4), dtype uint8
    monotonic_capture_instant_seconds: float


# Sentinel singleton pushed by the capture thread to tell the encoder thread
# to flush and exit normally. (We use a sentinel object rather than None to
# avoid any ambiguity in the queue's contents.)
class _EncoderShutdownSentinelType:
    pass


_ENCODER_SHUTDOWN_SENTINEL: Final[_EncoderShutdownSentinelType] = (
    _EncoderShutdownSentinelType()
)


# ============================================================================
# Section 13 - Capture thread: python-mss BGRA frame producer
# ============================================================================

class _SingleDisplayPrimaryMonitorCaptureWorker(threading.Thread):
    """Worker thread that captures Display 1 BGRA frames at the target FPS.

    Owns its own ``mss.mss()`` instance (python-mss instances are not safe
    to share across threads on Microsoft Windows because the underlying
    HDCs are per-thread).

    Pacing is wall-clock CFR (Constant Frame Rate): at every frame interval
    the worker captures one BGRA frame and pushes it to the bounded
    inter-thread queue. While paused, it captures nothing - the encoder
    queue stays empty and the encoder thread blocks on the queue.

    Frame indices (used as H.264 presentation timestamps) are NOT advanced
    during pause, so the final video plays back as a seamless cut across
    every pause boundary.
    """

    def __init__(
        self,
        primary_display_to_capture: _AttachedDisplayMonitor,
        outbound_frame_queue: "queue.Queue[_CapturedFrameForEncoder | _EncoderShutdownSentinelType]",
        operator_stop_event: threading.Event,
        operator_pause_event: threading.Event,
        worker_thread_logger: logging.Logger,
        per_frame_csv_writer_lock: threading.Lock,
        per_frame_csv_writer: csv.writer,
        per_frame_csv_file_handle: Any,
    ) -> None:
        super().__init__(
            name="windows_screen_recorder.capture_worker",
            daemon=True,
        )
        self._primary_display = primary_display_to_capture
        self._queue = outbound_frame_queue
        self._stop_event = operator_stop_event
        self._pause_event = operator_pause_event
        self._logger = worker_thread_logger
        self._csv_lock = per_frame_csv_writer_lock
        self._csv_writer = per_frame_csv_writer
        self._csv_file_handle = per_frame_csv_file_handle

        # Sentinel for the deferred fatal exception, if any.
        self.fatal_exception: Optional[BaseException] = None

        # Live statistics for GUI display.
        self.frames_successfully_captured_so_far: int = 0
        self.frames_dropped_due_to_capture_lag_so_far: int = 0
        self.total_paused_wall_clock_seconds: float = 0.0
        self.recording_start_monotonic_seconds: Optional[float] = None
        self.last_captured_frame_monotonic_seconds: Optional[float] = None

    def run(self) -> None:
        try:
            self._run_capture_loop()
        except BaseException as capture_loop_exc:  # noqa: BLE001
            self.fatal_exception = capture_loop_exc
            self._logger.error(
                "Capture worker thread terminated with a fatal exception:\n"
                + "".join(traceback.format_exception(capture_loop_exc))
            )
        finally:
            # Always push the sentinel so the encoder thread does not hang.
            try:
                self._queue.put(_ENCODER_SHUTDOWN_SENTINEL, timeout=5.0)
            except queue.Full:
                self._logger.error(
                    "Capture worker could not enqueue the encoder shutdown "
                    "sentinel within 5 seconds; the encoder thread may "
                    "deadlock."
                )

    def _run_capture_loop(self) -> None:
        target_capture_interval_seconds = 1.0 / TARGET_OUTPUT_FRAMES_PER_SECOND
        expected_frame_width_pixels = (
            self._primary_display.bounding_rectangle_width
        )
        expected_frame_height_pixels = (
            self._primary_display.bounding_rectangle_height
        )
        mss_monitor_capture_region = {
            "left": self._primary_display.bounding_rectangle_left,
            "top": self._primary_display.bounding_rectangle_top,
            "width": expected_frame_width_pixels,
            "height": expected_frame_height_pixels,
        }
        self._logger.info(
            "Capture worker starting. Target display 1 capture region "
            f"(virtual-screen coordinates): {mss_monitor_capture_region}. "
            f"Target frame rate: {TARGET_OUTPUT_FRAMES_PER_SECOND} fps."
        )

        # python-mss requires per-thread instances on Microsoft Windows
        # because the underlying GDI HDCs are thread-local.
        with mss.mss() as sct:
            self.recording_start_monotonic_seconds = time.monotonic()
            scheduled_next_capture_monotonic = (
                self.recording_start_monotonic_seconds
            )
            pause_started_monotonic: Optional[float] = None
            # Variable-frame-rate (VFR) capture timestamping. Every
            # captured BGRA frame's PTS is anchored to the real
            # wall-clock instant of its python-mss BitBlt, expressed
            # in output stream time-base units
            # (``1 / TARGET_OUTPUT_FRAMES_PER_SECOND`` seconds per
            # unit), and discounted by the cumulative wall-clock time
            # the operator has held the recording paused. This is the
            # only sound timestamping discipline for a Microsoft
            # Windows screen recorder: the seven-layer capture-to-
            # encode pipeline (python-mss GDI BitBlt -> NumPy copy
            # -> inter-thread queue.put -> FFmpeg libswscale
            # BGRA-to-NV12 conversion -> libavcodec encode call ->
            # FFmpeg's ``h264_qsv`` wrapper -> Intel oneVPL runtime
            # -> Intel Graphics driver -> Intel Quick Sync Video
            # silicon block on the integrated Intel UHD Graphics 770
            # GPU) is bounded by what the slowest layer can deliver
            # in real time, NOT by a constant 30 Hz clock. A sequence-
            # counter PTS scheme (PTS == frame-index N for the Nth
            # successful capture) would silently speed up the output
            # video whenever any layer of that pipeline lagged: N
            # capture iterations achieved in T real seconds would
            # surface as N output packets with PTS values 0..N-1 in a
            # ``time_base = 1/TARGET_OUTPUT_FRAMES_PER_SECOND``
            # stream, which the output player would render in
            # ``N / TARGET_OUTPUT_FRAMES_PER_SECOND`` wall-clock
            # seconds rather than the operator's actual T seconds of
            # recording. Anchoring every PTS to its real-time capture
            # instant produces an output whose playback duration
            # always equals the operator's real-time recording
            # duration, with any layer-induced lag visible in the
            # output as a brief frozen-content interval rather than
            # as a global playback speedup.
            #
            # The CFR pacing logic below stays in place to ENFORCE A
            # MAXIMUM frame rate (we never produce more than one
            # frame per ``1/TARGET_OUTPUT_FRAMES_PER_SECOND``
            # wall-clock slot); the VFR PTS scheme below accepts
            # whatever EFFECTIVE rate the capture loop achieves under
            # that ceiling.
            last_emitted_presentation_timestamp_in_stream_time_base_units: Optional[
                int
            ] = None

            while not self._stop_event.is_set():
                # ----- Pause/resume bookkeeping -----
                if self._pause_event.is_set():
                    if pause_started_monotonic is None:
                        pause_started_monotonic = time.monotonic()
                        self._logger.info(
                            "Capture worker observed pause request at "
                            "most-recent-emitted PTS "
                            f"{last_emitted_presentation_timestamp_in_stream_time_base_units}."
                        )
                    # Use Event.wait, not time.sleep, so the worker wakes
                    # up the instant the operator presses Stop. The wait
                    # returns True if stop_event is set during the wait.
                    if self._stop_event.wait(timeout=0.020):
                        break
                    continue
                if pause_started_monotonic is not None:
                    pause_duration_seconds = (
                        time.monotonic() - pause_started_monotonic
                    )
                    self.total_paused_wall_clock_seconds += pause_duration_seconds
                    self._logger.info(
                        f"Capture worker observed resume after a pause of "
                        f"{pause_duration_seconds * 1000.0:.1f} milliseconds."
                    )
                    pause_started_monotonic = None
                    scheduled_next_capture_monotonic = time.monotonic()

                # ----- CFR pacing -----
                now_monotonic = time.monotonic()
                if now_monotonic < scheduled_next_capture_monotonic:
                    # Wait via Event so the operator's Stop request
                    # interrupts the pacing sleep immediately.
                    if self._stop_event.wait(
                        timeout=scheduled_next_capture_monotonic
                        - now_monotonic
                    ):
                        break
                    continue
                # If we have drifted more than one full second behind
                # schedule, the host is too loaded for CFR pacing; we reset
                # the schedule baseline rather than capture in a tight loop.
                if (
                    now_monotonic
                    > scheduled_next_capture_monotonic
                    + max(1.0, 30.0 * target_capture_interval_seconds)
                ):
                    drift_seconds = (
                        now_monotonic - scheduled_next_capture_monotonic
                    )
                    self._logger.warning(
                        f"Capture loop fell {drift_seconds:.3f} seconds "
                        f"behind schedule; resetting CFR baseline to "
                        f"the current monotonic instant."
                    )
                    scheduled_next_capture_monotonic = now_monotonic

                # ----- Capture one BGRA frame -----
                capture_start_monotonic = time.monotonic()
                # Compute the spec-correct VFR PTS for this capture
                # instant. ``elapsed_recording_seconds_since_session_start``
                # is real wall-clock time since the capture loop began,
                # minus accumulated pause durations; the recorder
                # docstring at the top of the file declares that
                # pauses are NOT advanced over in the encoded output,
                # so a 5-second pause produces a seamless cut rather
                # than 5 seconds of frozen content. Multiplying by
                # ``TARGET_OUTPUT_FRAMES_PER_SECOND`` converts wall-
                # clock seconds to stream time-base units (where the
                # stream's ``time_base`` is
                # ``Fraction(1, TARGET_OUTPUT_FRAMES_PER_SECOND)``),
                # and ``round`` snaps to the nearest 1/30-second
                # output frame slot.
                elapsed_recording_seconds_since_session_start = (
                    capture_start_monotonic
                    - self.recording_start_monotonic_seconds
                    - self.total_paused_wall_clock_seconds
                )
                candidate_presentation_timestamp_in_stream_time_base_units = (
                    round(
                        elapsed_recording_seconds_since_session_start
                        * TARGET_OUTPUT_FRAMES_PER_SECOND
                    )
                )
                # Strict-monotonic guard: the encoder-side synthesis
                # in
                # ``_mux_one_encoded_packet_with_synthesized_pts_and_dts``
                # requires every input frame's PTS to be strictly
                # greater than the previously enqueued frame's PTS.
                # If the candidate PTS would land in the same (or an
                # earlier) 1/TARGET_OUTPUT_FRAMES_PER_SECOND wall-
                # clock slot the previously emitted frame already
                # occupies, this capture is silently dropped: the
                # output already represents this slot, so emitting
                # another frame for it would either duplicate content
                # the player will not display (the player only shows
                # one frame per PTS slot) or violate the encoder's
                # strict-monotonic-PTS invariant. The CFR pacing
                # logic above is the primary line of defence against
                # this; the guard here is the secondary line that
                # holds across clock anomalies (a monotonic-clock
                # backward jump, a host wake-from-sleep, a Microsoft
                # Windows kernel scheduler quantum that runs us twice
                # in one slot).
                if (
                    last_emitted_presentation_timestamp_in_stream_time_base_units
                    is not None
                    and candidate_presentation_timestamp_in_stream_time_base_units
                    <= last_emitted_presentation_timestamp_in_stream_time_base_units
                ):
                    self.frames_dropped_due_to_capture_lag_so_far += 1
                    scheduled_next_capture_monotonic += (
                        target_capture_interval_seconds
                    )
                    continue
                current_capture_presentation_timestamp_in_stream_time_base_units = (
                    candidate_presentation_timestamp_in_stream_time_base_units
                )

                try:
                    raw_screenshot = sct.grab(mss_monitor_capture_region)
                except Exception as mss_grab_exc:
                    raise ScreenFrameCaptureFailureError(
                        f"python-mss .grab() raised "
                        f"{type(mss_grab_exc).__name__}: {mss_grab_exc}\n"
                        f"  PTS attempted (stream time-base units) : "
                        f"{current_capture_presentation_timestamp_in_stream_time_base_units}\n"
                        f"  Capture region                          : "
                        f"{mss_monitor_capture_region}\n"
                        f"  Wall-clock monotonic                    : "
                        f"{capture_start_monotonic}\n"
                        f"This typically indicates the operator's "
                        f"Microsoft Windows session has lost its desktop "
                        f"(eg the workstation was locked or the user "
                        f"switched). This program does not capture across "
                        f"such session transitions."
                    ) from mss_grab_exc

                # Validate frame shape. mss returns shape (H, W, 4) BGRA.
                if (
                    raw_screenshot.height != expected_frame_height_pixels
                    or raw_screenshot.width != expected_frame_width_pixels
                ):
                    raise ScreenFrameCaptureFailureError(
                        f"python-mss returned a frame whose pixel "
                        f"dimensions disagree with the validated display "
                        f"configuration. The display configuration was "
                        f"presumably changed mid-recording, which this "
                        f"program does not support.\n"
                        f"  Expected   : "
                        f"{expected_frame_width_pixels}x"
                        f"{expected_frame_height_pixels}\n"
                        f"  Got        : "
                        f"{raw_screenshot.width}x{raw_screenshot.height}\n"
                        f"  PTS        : "
                        f"{current_capture_presentation_timestamp_in_stream_time_base_units}\n"
                        f"Resolution: stop the recording, restore the "
                        f"original Microsoft Windows display configuration, "
                        f"and start a fresh recording."
                    )

                # Detach the BGRA pixel buffer from the python-mss
                # ScreenShot object via np.frombuffer + .copy(). The
                # python-mss ScreenShot constructor at v6.1.0 already
                # copies the underlying GDI bytearray once, but we copy
                # again to fully detach from any python-mss-internal
                # lifetime so passing across the thread boundary is safe.
                bgra_pixel_buffer = np.frombuffer(
                    raw_screenshot.raw, dtype=np.uint8
                ).reshape(
                    expected_frame_height_pixels,
                    expected_frame_width_pixels,
                    4,
                ).copy()

                # ----- Enqueue for encoder -----
                try:
                    self._queue.put(
                        _CapturedFrameForEncoder(
                            presentation_timestamp_in_one_frame_units=(
                                current_capture_presentation_timestamp_in_stream_time_base_units
                            ),
                            bgra_frame_pixel_buffer=bgra_pixel_buffer,
                            monotonic_capture_instant_seconds=(
                                capture_start_monotonic
                            ),
                        ),
                        timeout=5.0,
                    )
                except queue.Full as queue_full_exc:
                    raise EncoderBackPressureError(
                        f"The capture-to-encoder queue has been at its "
                        f"capacity of {CAPTURE_TO_ENCODER_QUEUE_MAXIMUM_DEPTH_FRAMES} "
                        f"frames for more than 5 seconds. The Intel Quick "
                        f"Sync Video H.264 encoder cannot consume frames as "
                        f"fast as Display 1 is producing them.\n"
                        f"  PTS that failed to enqueue : "
                        f"{current_capture_presentation_timestamp_in_stream_time_base_units}\n"
                        f"  Target capture rate         : "
                        f"{TARGET_OUTPUT_FRAMES_PER_SECOND} fps\n"
                        f"This program does not silently drop frames in "
                        f"that condition. Resolution: reduce the captured "
                        f"display's resolution, or shut down the offending "
                        f"background workload."
                    ) from queue_full_exc

                # ----- Per-frame CSV log + sync -----
                with self._csv_lock:
                    self._csv_writer.writerow(
                        [
                            current_capture_presentation_timestamp_in_stream_time_base_units,
                            f"{capture_start_monotonic:.6f}",
                            f"{self.total_paused_wall_clock_seconds:.6f}",
                        ]
                    )
                    self._csv_file_handle.flush()
                    try:
                        os.fsync(self._csv_file_handle.fileno())
                    except OSError:
                        # Best-effort fsync; some Microsoft Windows
                        # filesystem drivers do not support fsync on
                        # text-mode handles. Not fatal.
                        pass

                self.frames_successfully_captured_so_far += 1
                self.last_captured_frame_monotonic_seconds = capture_start_monotonic
                last_emitted_presentation_timestamp_in_stream_time_base_units = (
                    current_capture_presentation_timestamp_in_stream_time_base_units
                )
                # Advance the schedule baseline strictly by the target
                # interval (not by the actual elapsed time): this is
                # what holds the schedule's "next slot" line on the
                # original 1/TARGET_OUTPUT_FRAMES_PER_SECOND grid
                # rather than drifting on cumulative jitter.
                scheduled_next_capture_monotonic += (
                    target_capture_interval_seconds
                )

        self._logger.info(
            f"Capture worker exiting normally. Total frames captured: "
            f"{self.frames_successfully_captured_so_far}. Total dropped: "
            f"{self.frames_dropped_due_to_capture_lag_so_far}. Total paused "
            f"wall-clock seconds: "
            f"{self.total_paused_wall_clock_seconds:.3f}."
        )


# ============================================================================
# Section 14 - Encoder thread: PyAV / Intel Quick Sync Video H.264 consumer
# ============================================================================
#
# The full top-to-bottom software call chain that one captured Display 1
# Microsoft-Windows-GDI BGRA pixel buffer travels through, before it
# leaves this process as a finalized H.264 NAL unit muxed into a
# fragmented MP4 box on the local NTFS volume, is the following stack
# of distinct authorship boundaries. Reading this file without an
# explicit map of the boundaries is a recipe for misattributing bugs;
# we therefore enumerate them once, in full, here.
#
#   1. This long-form Microsoft Windows screen recorder Python script
#      (single-file authored, this very source file).
#
#   2. PyAV (the Python binding to FFmpeg's C libraries:
#      ``libavcodec`` for codec dispatch, ``libavformat`` for muxer
#      dispatch, ``libavutil`` for shared primitives, ``libswscale``
#      for the BGRA-to-NV12 colour-space conversion). PyAV is
#      Cython-generated. Repository:
#      https://github.com/PyAV-Org/PyAV. Pinned version: 17.0.1 (see
#      MINIMUM_REQUIRED_PYAV_VERSION).
#
#   3. FFmpeg (the libavcodec / libavformat / libavutil / libswscale
#      suite of C libraries; PyAV statically links a pinned FFmpeg
#      via the ``pyav-ffmpeg`` release artifact, which for PyAV 17.0.1
#      is FFmpeg 8.0.1; see
#      https://github.com/PyAV-Org/pyav-ffmpeg/releases/tag/8.0.1-5).
#      FFmpeg's libavcodec dispatches encode calls to the named
#      encoder; we pin the encoder name to ``h264_qsv``, which is
#      FFmpeg's wrapper around the Intel Quick Sync Video H.264
#      encoder session API.
#
#   4. FFmpeg's ``h264_qsv`` encoder wrapper, implemented across
#      ``libavcodec/qsvenc.c`` (the shared QSV plumbing) and
#      ``libavcodec/qsvenc_h264.c`` (the H.264-specific glue). This
#      layer translates each libavcodec ``AVPacket`` /
#      ``AVCodecContext`` encode call into the corresponding Intel
#      oneVPL C API call (``MFXVideoENCODE_EncodeFrameAsync`` etc.).
#
#   5. The Intel oneVPL runtime (formerly known as the Intel Media
#      SDK; on Microsoft Windows the dynamic-link library
#      ``libmfx.dll`` or ``libvpl.dll``). This is loaded by FFmpeg
#      via the Microsoft Windows operating-system loader
#      (``LoadLibraryExW``) when ``av.codec.Codec("h264_qsv", "w")``
#      first opens an encoder session. On Microsoft Windows hosts the
#      runtime implementation is provided by the Intel-maintained
#      project ``intel/vpl-gpu-rt``
#      (https://github.com/intel/vpl-gpu-rt), distributed as part of
#      the Intel Graphics driver package the operator installs.
#
#   6. The Intel Graphics driver - on 64-bit Microsoft Windows 10/11
#      the kernel-mode display miniport driver ``igdkmd64.sys`` plus
#      the user-mode display driver ``igdumdim64.dll``. The oneVPL
#      runtime in step 5 calls into the user-mode display driver,
#      which packages GPU command-buffer submissions for the kernel
#      to dispatch.
#
#   7. The Intel Quick Sync Video fixed-function H.264 encoder block:
#      a dedicated piece of silicon on the integrated Intel UHD
#      Graphics 770 GPU. On the reference target hardware - the
#      13th-generation Intel Core i7-13700 desktop processor - the
#      Intel UHD Graphics 770 GPU is integrated onto the CPU die.
#      The Microsoft Windows kernel's GPU scheduler is what actually
#      dispatches the command buffer to the hardware encoder block.
#
# Encoded H.264 NAL units flow back up the same seven-layer chain in
# reverse, arriving as ``av.Packet`` objects at this script's encoder
# worker thread (the ``_IntelQuickSyncVideoFragmentedMp4EncoderWorker``
# class defined below). Each emitted packet is then handed back into
# layer 2 / 3 via PyAV's ``OutputContainer.mux`` call, which dispatches
# into FFmpeg's libavformat ``mp4`` muxer; that muxer writes the
# resulting fragmented-MP4 byte stream via the Microsoft Windows kernel
# I/O subsystem (``KernelBase.dll!WriteFile`` ->
# ``ntdll.dll!NtWriteFile`` -> the Microsoft Windows NT kernel I/O
# Manager -> the NTFS file system driver) onto the per-user temporary
# directory on the local NTFS volume.
#
# Why this matters for the configuration below: layers 4, 5, 6, and 7
# each have their own independently-versioned defaults, fallbacks, and
# silent option-coercion behaviours. The constants in Section 2, the
# ``codec_context.options`` dict written below in ``_run_encode_loop``,
# the explicit ``codec_context.open(strict=True)`` call we make before
# the encode loop, and the
# ``_verify_encoder_codec_context_post_open_state_matches_explicit_configuration``
# validator that runs immediately after that open() together constrain
# every configuration knob this program depends on to a single explicit
# value, AND verify that the value actually made it through every
# layer that can observe it. We assume nothing about defaults at any
# layer.
#
# Two things this configuration-locking strategy CANNOT cover, and
# the structural defences this program puts in place against each:
#
#   (a) The Intel oneVPL runtime's H.264 hardware-encode
#       implementation (the ``intel/vpl-gpu-rt`` project, layer 5)
#       silently fails to populate ``mfxBitstream.TimeStamp`` on a
#       non-deterministic subset of its output packets. FFmpeg's
#       ``libavcodec/qsvenc.c`` line 2667 then maps the resulting
#       sentinel / uninitialised value to ``pkt.pts = 0`` without
#       sanitisation, despite the sister file ``qsvdec.c`` lines
#       64-70 sanitising the same sentinel correctly on the decoder
#       side. This is an Intel bug compounded by an FFmpeg bug;
#       neither has been fixed at the time of writing.
#
#       Structural defence: the encoder worker does not trust the
#       libmfx-derived ``pkt.pts`` / ``pkt.dts`` for any output
#       packet. The capture worker writes a wall-clock-anchored,
#       strictly-monotonic PTS into every input frame's
#       ``av.VideoFrame.pts`` (variable-frame-rate timestamping
#       discipline); the encoder worker threads each such PTS
#       through a FIFO at submission and ``popleft``s the matching
#       value at mux time, overwriting whatever Intel's runtime
#       wrote into ``mfxBitstream.TimeStamp``. Under the
#       no-B-frames configuration of invariant (i), the FIFO
#       ``popleft`` is the H.264-spec-correct PTS for the
#       corresponding output packet. The first libmfx-vs-FIFO
#       disagreement emits a verbose ``WARNING`` to
#       ``session.log``; subsequent disagreements are rate-limited
#       to one ``WARNING`` per
#       ``_INTEL_ONEVPL_PTS_DISAGREEMENT_WARNING_LOG_PERIOD_PACKETS``
#       packets so the log stays bounded across multi-hour
#       long-form recordings even when the disagreement fires on
#       every output packet.
#
#   (b) The Intel oneVPL runtime might theoretically also drop or
#       duplicate output packets (i.e. break the 1:1 input-frame-
#       to-output-bitstream correspondence that ``GopRefDist=1`` is
#       supposed to guarantee). The synthesis in (a) RELIES on that
#       1:1 correspondence to map the Nth output packet to the
#       PTS the capture worker assigned to the Nth input frame.
#
#       Structural defence (end-of-session): the encoder worker
#       verifies the pending-input-frame-PTS FIFO is empty
#       immediately after the graceful libavcodec flush
#       (``output_stream.encode(None)``). A non-empty FIFO at
#       that point proves that the encoder produced fewer output
#       packets than this program submitted input frames, and the
#       worker raises ``EncoderPipelineError`` rather than closing
#       a silently truncated output MP4.
#
#       Structural defence (mid-stream): the FIFO ``popleft`` at
#       every mux call also covers the dual case where the runtime
#       produces MORE output packets than input frames. The
#       second ``popleft`` from an empty FIFO raises
#       ``EncoderPipelineError`` at the exact output packet that
#       has no submitted input to pair with, rather than allowing
#       a spurious packet to reach the mp4 muxer.
#
#       What this program CANNOT detect mid-stream: a runtime that
#       drops AND duplicates an equal number of packets across the
#       session, preserving total count but desynchronising
#       content-to-PTS correspondence somewhere inside the stream.
#       Detecting that would require parsing the H.264 bitstream
#       from inside the encoder worker; this program does not.
#
# Long-form recording stability properties of the design described
# above (the recorder is intended to be left running for hours-long
# sessions):
#
#   * Bounded memory. The capture-to-encoder ``queue.Queue`` is
#     bounded at ``CAPTURE_TO_ENCODER_QUEUE_MAXIMUM_DEPTH_FRAMES``
#     frames; the pending-input-frame-PTS FIFO size is bounded by
#     the encoder's internal pipeline depth (~``async_depth``
#     entries at steady state, plus a small variable transient at
#     pipeline fill). Neither grows with session length.
#
#   * Bounded log volume. Per-packet ``DEBUG`` lines accumulate at
#     a fixed rate proportional to the encoder output rate. The
#     ``WARNING`` lines associated with the Intel oneVPL runtime
#     ``mfxBitstream.TimeStamp`` defect are explicitly rate-
#     limited (see (a) above). Total ``session.log`` size grows
#     linearly with session length at a small constant factor.
#
#   * No floating-point drift in PTS arithmetic. The capture
#     worker's VFR PTS computation casts to int via ``round``
#     immediately, so subsequent computation is integer-only and
#     immune to cumulative ``+=`` floating-point error. Python
#     integers are unbounded, and the ``int64_t`` field
#     libavcodec uses internally for PTS holds well over a billion
#     years of 30 fps recording before overflow.
#
#   * No CFR-schedule drift at long horizons. The capture worker
#     resets its CFR baseline if it ever falls more than one
#     full second behind schedule, capping the worst-case drift
#     accumulation at one second regardless of how long the
#     session runs.
#
#   * No PTS-counter coupling between capture and encoder. The
#     FIFO carries the capture worker's PTS values verbatim to the
#     mux helper; there is no integer counter on the encoder side
#     that could drift relative to the capture-side wall clock.


def _format_encoder_pipeline_error_message_from_ffmpeg_error(
    *,
    failing_frame_pts: Optional[int],
    frame_buffer_shape: Optional[tuple[int, ...]],
    frame_buffer_dtype: Optional[str],
    phase_human_readable: str,
    ffmpeg_error: "av.FFmpegError",
) -> str:
    """Build a forensic-grade ``EncoderPipelineError`` message from a PyAV error.

    Surfaces every PyAV-exposed libav-side field on the
    ``av.FFmpegError`` instance:

      * ``errno``        - the integer libav error code (eg 22 for EINVAL).
      * ``strerror``     - libav's textual rendering of that errno.
      * ``filename``     - the ``AVFormatContext`` URL identifier *or* the
                           libavcodec function name. CRITICAL for triage:
                           if it holds the output container path, the
                           failure is inside ``av_interleaved_write_frame``
                           (the muxer); if it holds
                           ``"avcodec_send_frame()"`` /
                           ``"avcodec_receive_packet()"``, the failure is
                           inside libavcodec (the encoder). See
                           ``av/container/output.py`` + ``av/codec/context.py``.
      * ``log``          - the ``(level, source_name, message)`` tuple
                           captured by PyAV's logging callback at the
                           moment the error was raised, populated only
                           because we called
                           ``_opt_into_libav_verbose_diagnostic_log_capture``
                           at startup. Carries the actual libav line, eg::

                               (16, 'mov', 'Application provided invalid,
                                non monotonically increasing dts to muxer
                                in stream 0: 0 >= 0')

                           which is what makes the difference between "we
                           know what failed" and "we are debugging blind".

    We deliberately do NOT swallow or rename the libav errno - the message
    is meant to land verbatim in the operator's error message-box and the
    session.log forensic artifact.
    """
    if ffmpeg_error.log is not None:
        log_level_int, log_source_name, log_message_text = ffmpeg_error.log
        rendered_log_line = (
            f"[level={log_level_int}, source={log_source_name!r}] "
            f"{log_message_text.strip()}"
        )
    else:
        rendered_log_line = (
            "(none captured - PyAV's logging callback did not record an "
            "AV_LOG_ERROR line before err_check raised; this should be "
            "impossible because _opt_into_libav_verbose_diagnostic_log_capture "
            "is called at process start)"
        )

    return (
        f"PyAV / Intel Quick Sync Video encoding pipeline raised an "
        f"av.FFmpegError during the {phase_human_readable} phase.\n"
        f"  Failing frame PTS         : {failing_frame_pts}\n"
        f"  Frame buffer shape        : {frame_buffer_shape}\n"
        f"  Frame buffer dtype        : {frame_buffer_dtype}\n"
        f"  libav errno               : {ffmpeg_error.errno}\n"
        f"  libav strerror            : {ffmpeg_error.strerror!r}\n"
        f"  PyAV filename / context   : {ffmpeg_error.filename!r}\n"
        f"      ^ if this is the output container's path "
        f"(eg 'recording.mp4'), the EINVAL came from "
        f"av_interleaved_write_frame inside the muxer; if it is "
        f"'avcodec_send_frame()' or 'avcodec_receive_packet()', "
        f"the EINVAL came from libavcodec inside the encoder.\n"
        f"  Last captured libav log   : {rendered_log_line}\n"
        f"  PyAV exception class      : "
        f"{type(ffmpeg_error).__name__}"
    )


class _IntelQuickSyncVideoFragmentedMp4EncoderWorker(threading.Thread):
    """Worker thread that encodes BGRA frames into a fragmented MP4 via PyAV.

    Owns the PyAV ``OutputContainer`` and the FFmpeg ``h264_qsv``
    encoded video stream. An Intel Quick Sync Video encoder session
    is tied to the thread that first invokes the libavcodec encode
    entry point (because the Intel oneVPL runtime's session-handle
    affinity is per-OS-thread), so this class both constructs the
    PyAV stream and consumes encoded packets from it inside a single
    Microsoft Windows kernel thread.

    Two correctness invariants the worker imposes on the encoded
    H.264 bitstream, each enforced by an explicit mechanism rather
    than left to FFmpeg's or the Intel oneVPL runtime's default
    behaviour:

    (i)  The output GOP is B-frame-free. The constant
         ``INTEL_QSV_MAX_B_FRAMES_VALUE = 0`` (Section 2) is passed
         as the FFmpeg AVOption ``"bf"`` into the codec context's
         options dict; ``avcodec_open2`` then computes the libmfx
         GOP-reference-distance parameter as
         ``q->param.mfx.GopRefDist = FFMAX(-1, 0) + 1 = 1``, which
         the Intel oneVPL runtime is contractually obliged to
         interpret as "no B-frames between anchors". The
         ``_verify_encoder_codec_context_post_open_state_matches_explicit_configuration``
         validator reads ``codec_context.max_b_frames`` back after
         ``open()`` and refuses to proceed if FFmpeg or the Intel
         oneVPL runtime silently coerced it. With no B-frames in
         the output bitstream, the H.264 specification mandates
         decode order == display order == input order.

    (ii) Each output packet's ``pts`` and ``dts`` are SYNTHESIZED
         from the strictly-monotonic FIFO of input-frame PTS
         values
         (``_pending_input_frame_pts_fifo_in_stream_time_base_units``),
         inside
         ``_mux_one_encoded_packet_with_synthesized_pts_and_dts``,
         and the libmfx-derived ``mfxBitstream.TimeStamp`` /
         ``mfxBitstream.DecodeTimeStamp`` values that FFmpeg's
         ``h264_qsv`` wrapper writes onto ``av.Packet`` are
         discarded. The capture worker writes a real-wall-clock-
         anchored, strictly-monotonic-but-not-necessarily-sequential
         PTS into every input frame's ``av.VideoFrame.pts`` (the
         variable-frame-rate timestamping discipline; see the
         capture worker's docstring), and this encoder worker
         threads each such PTS through the FIFO so the Nth
         ``popleft`` at the mux helper returns the PTS that
         belongs to the Nth output packet under invariant (i).
         This program does not trust the libmfx-derived values
         because Intel's ``vpl-gpu-rt`` H.264 hardware-encode
         implementation silently fails to populate
         ``mfxBitstream.TimeStamp`` on a non-deterministic subset
         of its output packets - and FFmpeg's
         ``libavcodec/qsvenc.c`` line 2667 maps the resulting
         sentinel / zero straight through to ``pkt.pts = 0`` with
         no sanitisation. The first observed disagreement between
         the libmfx-derived ``pkt.pts`` and the FIFO-derived
         synthesized value emits a ``WARNING`` with full context;
         subsequent disagreements are rate-limited to one
         ``WARNING`` per
         ``_INTEL_ONEVPL_PTS_DISAGREEMENT_WARNING_LOG_PERIOD_PACKETS``
         packets (bounded ``session.log`` growth across multi-hour
         long-form recordings), with the per-packet trail
         preserved at ``DEBUG`` and the final total reported once
         at ``INFO`` on encoder-worker exit and persisted into the
         session metadata JSON. A non-empty FIFO at graceful
         flush time is treated as a 1:1-correspondence violation
         on Intel's side and surfaces as an ``EncoderPipelineError``
         rather than silently truncating the output MP4.

    The worker honours two distinct stop signals:

      * The shutdown sentinel singleton pushed by the capture worker
        on its way out. This is the *graceful* shutdown: the worker
        encodes every queued frame, flushes the encoder via
        ``stream.encode(None)``, then closes the output container
        so the final fragmented-MP4 fragment is finalised.

      * The ``force_cancel_event`` set by the controller's
        ``request_force_cancel_finalization`` entry point. This is
        the *force* shutdown: as soon as the worker observes the
        event set, it discards whatever frames remain in the queue,
        skips the ``stream.encode(None)`` flush, and closes the
        container as-is. Because the output is fragmented MP4 with
        the FFmpeg ``movflags`` set
        ``+frag_keyframe+empty_moov+default_base_moof``, the file is
        still playable up through whichever fragment was most
        recently finalised; the worst possible loss is the in-flight
        fragment (up to ``KEYFRAME_INTERVAL_FRAMES`` frames worth,
        i.e. approximately two wall-clock seconds at the default
        frame rate).
    """

    # Polling interval the encoder loop uses when blocked on the queue,
    # so that it can wake up promptly to observe a force-cancel.
    _QUEUE_GET_POLL_TIMEOUT_SECONDS: Final[float] = 0.10

    # Period (in output packets) at which the encoder worker emits a
    # rate-limited ``WARNING`` line summarising ongoing Intel oneVPL
    # runtime ``mfxBitstream.TimeStamp`` disagreements. On a hardware
    # / driver pair where the disagreement fires on every packet from
    # the 6th onward, an unbounded ``WARNING`` per packet would
    # accumulate hundreds of megabytes of ``session.log`` over a
    # multi-hour long-form recording. With ``LOG_PERIOD_PACKETS =
    # 1800``, the ``WARNING`` count is bounded to (one log line per
    # minute of recording at the target 30 fps frame rate) plus the
    # always-emitted first-occurrence ``WARNING`` and the always-
    # emitted final summary at session exit.
    _INTEL_ONEVPL_PTS_DISAGREEMENT_WARNING_LOG_PERIOD_PACKETS: Final[int] = 1800

    def __init__(
        self,
        output_video_file_path: Path,
        target_frame_width_pixels: int,
        target_frame_height_pixels: int,
        inbound_frame_queue: "queue.Queue[_CapturedFrameForEncoder | _EncoderShutdownSentinelType]",
        worker_thread_logger: logging.Logger,
        force_cancel_event: threading.Event,
    ) -> None:
        super().__init__(
            name="windows_screen_recorder.encoder_worker",
            daemon=True,
        )
        self._output_path = output_video_file_path
        self._width = target_frame_width_pixels
        self._height = target_frame_height_pixels
        self._queue = inbound_frame_queue
        self._logger = worker_thread_logger
        self._force_cancel_event = force_cancel_event

        self.fatal_exception: Optional[BaseException] = None
        self.frames_successfully_encoded_so_far: int = 0
        self.output_packets_successfully_muxed_so_far: int = 0
        self.frames_discarded_due_to_force_cancel_so_far: int = 0
        self.encoder_was_force_cancelled: bool = False

        # Disagreement counter for the libmfx-derived ``pkt.pts`` vs.
        # this program's synthesized PTS. Bumped once per output packet
        # on which the two disagree. We log a single ``WARNING`` on
        # the very first disagreement (full context); thereafter only
        # one ``WARNING`` per
        # ``_INTEL_ONEVPL_PTS_DISAGREEMENT_WARNING_LOG_PERIOD_PACKETS``
        # packets (a short tally line). The complete forensic trail
        # is preserved at ``DEBUG`` level on every packet regardless.
        # This rate-limiting keeps the per-session ``session.log``
        # bounded for long-form recordings on a host whose Intel
        # oneVPL runtime persistently emits broken
        # ``mfxBitstream.TimeStamp`` values.
        self.total_intel_onevpl_pts_disagreements_observed: int = 0

        # First-in-first-out queue of input-frame PTS values (in
        # stream time-base units) that have been submitted to FFmpeg's
        # ``h264_qsv`` wrapper via ``output_stream.encode(...)`` but
        # whose corresponding output packet has not yet been muxed.
        # The capture worker writes a strictly-monotonic-but-not-
        # necessarily-sequential PTS into every
        # ``_CapturedFrameForEncoder.presentation_timestamp_in_one_frame_units``
        # value it enqueues (see the long-form VFR rationale in the
        # capture worker's ``_run_capture_loop``); the encoder worker
        # threads each such value into this FIFO at the moment of
        # ``output_stream.encode`` submission, and the mux helper
        # ``_mux_one_encoded_packet_with_synthesized_pts_and_dts``
        # ``popleft``s the matching value at the moment a libavcodec
        # output packet emerges - assigning it as the synthesized
        # ``pkt.pts`` and ``pkt.dts``, in place of the broken values
        # Intel's ``vpl-gpu-rt`` H.264 implementation writes into
        # ``mfxBitstream.TimeStamp`` and ``mfxBitstream.DecodeTimeStamp``.
        # The FIFO is correct because under invariant (i) of this
        # class's docstring (``INTEL_QSV_MAX_B_FRAMES_VALUE = 0``,
        # validated post-open), FFmpeg's ``h264_qsv`` wrapper emits
        # exactly one output packet per input frame in input order;
        # the Nth ``popleft`` therefore yields the PTS of the input
        # frame that the Nth output packet was encoded from.
        self._pending_input_frame_pts_fifo_in_stream_time_base_units: "collections.deque[int]" = (
            collections.deque()
        )
        # Monotonicity ledger for the synthesized output PTS. Tracks
        # the most-recently-muxed packet's synthesized PTS so the mux
        # helper can verify - defensively, on every packet - that the
        # FIFO is yielding strictly-increasing values. By
        # construction the FIFO only ever contains strictly-
        # increasing values (the capture worker enforces that
        # invariant at its enqueue site), so this check is a tautology
        # in the absence of bugs. It is here to make any future bug
        # that breaks that invariant fail loudly at the exact mux
        # call that observes it, rather than silently producing an
        # out-of-order ``av_interleaved_write_frame`` call.
        self._last_muxed_synthesized_output_packet_pts_in_stream_time_base_units: Optional[
            int
        ] = None

    def run(self) -> None:
        try:
            self._run_encode_loop()
        except BaseException as encode_loop_exc:  # noqa: BLE001
            self.fatal_exception = encode_loop_exc
            self._logger.error(
                "Encoder worker thread terminated with a fatal exception:\n"
                + "".join(traceback.format_exception(encode_loop_exc))
            )

    def _run_encode_loop(self) -> None:
        # H.264 requires even pixel dimensions for 4:2:0 chroma; the Intel
        # Quick Sync Video encoder additionally prefers 16-pixel alignment
        # internally. Native display resolutions are always even, but we
        # double-check.
        if self._width % 2 != 0 or self._height % 2 != 0:
            raise EncoderPipelineError(
                f"Display 1's native pixel dimensions are "
                f"{self._width}x{self._height}, which are not both even. "
                f"H.264 4:2:0 chroma subsampling requires even pixel "
                f"dimensions. This program does not auto-pad odd-sized "
                f"displays; please change Display 1's resolution to an "
                f"even-sized mode."
            )

        self._logger.info(
            f"Encoder worker opening fragmented MP4 output container at "
            f"'{self._output_path}'. Encoder: "
            f"{INTEL_QUICK_SYNC_VIDEO_H264_ENCODER_FFMPEG_NAME}. Resolution: "
            f"{self._width}x{self._height}. Target frame rate: "
            f"{TARGET_OUTPUT_FRAMES_PER_SECOND} fps."
        )

        try:
            output_container = av.open(
                str(self._output_path),
                mode="w",
                format="mp4",
                options={"movflags": FRAGMENTED_MP4_MOVFLAGS_VALUE},
            )
        except Exception as open_exc:
            raise EncoderPipelineError(
                f"PyAV av.open() failed when opening the fragmented MP4 "
                f"output container.\n"
                f"  Output path : {self._output_path}\n"
                f"  Underlying  : {type(open_exc).__name__}: {open_exc}"
            ) from open_exc

        try:
            try:
                output_stream = output_container.add_stream(
                    INTEL_QUICK_SYNC_VIDEO_H264_ENCODER_FFMPEG_NAME,
                    rate=Fraction(TARGET_OUTPUT_FRAMES_PER_SECOND, 1),
                )
            except Exception as add_stream_exc:
                raise EncoderPipelineError(
                    f"PyAV OutputContainer.add_stream("
                    f"'{INTEL_QUICK_SYNC_VIDEO_H264_ENCODER_FFMPEG_NAME}') "
                    f"failed.\n"
                    f"  Underlying : "
                    f"{type(add_stream_exc).__name__}: {add_stream_exc}\n"
                    f"This typically means the Intel oneVPL runtime is "
                    f"unable to open an Intel Quick Sync Video session "
                    f"(eg the integrated GPU is disabled in the system BIOS, "
                    f"or the Intel Graphics driver is too old)."
                ) from add_stream_exc

            output_stream.width = self._width
            output_stream.height = self._height
            output_stream.pix_fmt = "nv12"
            # Time-base of the AVStream inside the output container.
            # The mp4 muxer rescales every muxed packet's PTS from
            # ``packet.time_base`` into this stream time-base before
            # writing it into the file's per-track timescale, so this
            # is the unit the output MP4 ultimately stores PTS values
            # in.
            output_stream.time_base = Fraction(
                1, TARGET_OUTPUT_FRAMES_PER_SECOND
            )
            # Time-base of the libavcodec ``AVCodecContext``, which
            # is a DIFFERENT field from ``AVStream.time_base`` and
            # must be set independently. PyAV's
            # ``stream.time_base = ...`` only writes the AVStream
            # field; it does not propagate to the codec context.
            #
            # We force them equal here on purpose. The reason is
            # FFmpeg's H.264 codec descriptor declares
            # ``ticks_per_frame = 2`` (the legacy interlaced-fields
            # convention from
            # ``libavcodec/codec_desc.c::AV_CODEC_PROP_REORDER``-
            # adjacent defaults), so if we leave
            # ``avctx->time_base`` unset and only set
            # ``avctx->framerate = TARGET_OUTPUT_FRAMES_PER_SECOND``
            # via the ``rate`` argument to ``add_stream``, libavcodec
            # picks a default of
            # ``1 / (ticks_per_frame * framerate)`` =
            # ``1 / (2 * TARGET_OUTPUT_FRAMES_PER_SECOND)`` =
            # ``1 / 60`` for our target rate. PyAV then propagates
            # that 1/60 onto every emitted output ``av.Packet`` via
            # ``CodecContext._setup_encoded_packet`` line
            # ``packet.ptr.time_base = self.ptr.time_base``. When
            # ``_mux_one_encoded_packet_with_synthesized_pts_and_dts``
            # below overwrites ``output_packet.pts`` with the
            # FIFO-popped value (computed by the capture worker in
            # stream time-base units of
            # ``1 / TARGET_OUTPUT_FRAMES_PER_SECOND`` seconds per
            # unit, ie 1/30 for the default 30 fps target),
            # ``output_container.mux(...)`` rescales it from the
            # packet's 1/60 time-base into the stream's 1/30 time-
            # base - halving the PTS value en route, halving the
            # output MP4's playback duration, and surfacing as a
            # 2x-faster-than-real-time playback to the operator.
            #
            # Setting ``codec_context.time_base`` equal to
            # ``stream.time_base`` here eliminates that rescale by
            # making it the identity transformation. The post-open
            # validator below cross-checks that libavcodec did not
            # silently override this value during ``avcodec_open2``.
            output_stream.codec_context.time_base = Fraction(
                1, TARGET_OUTPUT_FRAMES_PER_SECOND
            )
            output_stream.codec_context.options = {
                "preset": INTEL_QSV_PRESET_VALUE,
                "global_quality": str(INTEL_QSV_GLOBAL_QUALITY_VALUE),
                "look_ahead": str(INTEL_QSV_LOOK_AHEAD_VALUE),
                "g": str(KEYFRAME_INTERVAL_FRAMES),
                "async_depth": str(INTEL_QSV_ASYNC_DEPTH_VALUE),
                # Force B-frames OFF; see the long-form rationale at
                # the ``INTEL_QSV_MAX_B_FRAMES_VALUE`` constant
                # declaration in Section 2. Encoded packets must come
                # out of FFmpeg's ``h264_qsv`` wrapper in input order
                # so that the PTS/DTS synthesis in
                # ``_mux_one_encoded_packet_with_synthesized_pts_and_dts``
                # is spec-correct (decode order == display order ==
                # input order, so the Nth output packet's
                # spec-correct timestamp is N).
                "bf": str(INTEL_QSV_MAX_B_FRAMES_VALUE),
            }

            # Explicitly open the libavcodec ``AVCodecContext`` NOW,
            # before the first call to ``output_stream.encode(...)``,
            # so that:
            #
            #   (a) FFmpeg's ``avcodec_open2()`` has already run by the
            #       time we reach the encode loop. ``avcodec_open2()``
            #       is the libavcodec entry point that parses the
            #       options dict set above, applies the per-codec
            #       defaults from ``qsv_enc_defaults[]`` at
            #       ``libavcodec/qsvenc_h264.c:119``, and ultimately
            #       initialises the Intel oneVPL encoder session via
            #       ``MFXVideoENCODE_Init``. Any failure at this step -
            #       option rejected by libavcodec, Intel oneVPL runtime
            #       unable to initialise an Intel Quick Sync Video
            #       session, Intel Graphics driver too old, integrated
            #       GPU disabled in the system BIOS - surfaces here
            #       and not on the (unrelated-looking) first frame.
            #
            #   (b) The post-init ``AVCodecContext`` fields become
            #       readable via PyAV's @property accessors on
            #       ``av.video.codeccontext.VideoCodecContext``. The
            #       validator below trusts no layer of the seven-layer
            #       call chain documented at the top of this section
            #       to have silently coerced any value, and reads each
            #       field back to compare against the explicit value
            #       this program requires.
            try:
                output_stream.codec_context.open(strict=True)
            except av.FFmpegError as codec_open_exc:
                raise EncoderPipelineError(
                    f"FFmpeg's ``avcodec_open2()`` failed when opening "
                    f"the ``h264_qsv`` H.264 encoder codec context. "
                    f"This means one of layers 4 through 7 of the "
                    f"encoder call chain documented at the top of "
                    f"Section 14 rejected our configuration: most "
                    f"commonly the Intel oneVPL runtime was unable "
                    f"to initialise an Intel Quick Sync Video session "
                    f"(integrated Intel UHD Graphics 770 GPU disabled "
                    f"in the system BIOS, Intel Graphics driver too "
                    f"old, or the ``libmfx.dll`` / ``libvpl.dll`` "
                    f"runtime is not on the Microsoft Windows DLL "
                    f"search path), but the same exception is raised "
                    f"if libavcodec itself rejected any AVOption in "
                    f"the configuration dict above.\n"
                    f"  Underlying           : "
                    f"{type(codec_open_exc).__name__}: {codec_open_exc}\n"
                    f"  libav errno          : "
                    f"{getattr(codec_open_exc, 'errno', None)}\n"
                    f"  libav strerror       : "
                    f"{getattr(codec_open_exc, 'strerror', None)!r}\n"
                    f"  Last captured log    : "
                    f"{getattr(codec_open_exc, 'log', None)!r}"
                ) from codec_open_exc
            self._verify_encoder_codec_context_post_open_state_matches_explicit_configuration(
                output_stream=output_stream,
            )

            while True:
                # Check the force-cancel signal before every blocking get.
                if self._force_cancel_event.is_set():
                    self.encoder_was_force_cancelled = True
                    self._discard_queue_remainder_due_to_force_cancel()
                    self._logger.warning(
                        "Encoder worker observed force-cancel; abandoning "
                        "the pipeline. Output file will be closed without a "
                        "libavcodec flush; final fragment(s) in flight will "
                        "be lost."
                    )
                    break
                try:
                    queue_item = self._queue.get(
                        timeout=self._QUEUE_GET_POLL_TIMEOUT_SECONDS
                    )
                except queue.Empty:
                    continue  # poll force-cancel again

                if isinstance(queue_item, _EncoderShutdownSentinelType):
                    self._logger.info(
                        "Encoder worker received the graceful shutdown "
                        "sentinel; flushing the Intel Quick Sync Video "
                        "encoder."
                    )
                    break

                captured_frame: _CapturedFrameForEncoder = queue_item
                try:
                    av_bgra_video_frame = av.VideoFrame.from_ndarray(
                        captured_frame.bgra_frame_pixel_buffer,
                        format="bgra",
                    )
                    # Reformat BGRA -> NV12 (the Intel Quick Sync Video
                    # encoder requires NV12 input).
                    av_nv12_video_frame = av_bgra_video_frame.reformat(
                        format="nv12"
                    )
                    av_nv12_video_frame.pts = (
                        captured_frame
                        .presentation_timestamp_in_one_frame_units
                    )
                    av_nv12_video_frame.time_base = output_stream.time_base
                    # Record this input frame's PTS in the FIFO BEFORE
                    # calling ``output_stream.encode`` - so that even if
                    # ``encode`` immediately returns an output packet
                    # (after the async-depth pipeline is full), the mux
                    # helper finds the matching PTS at the head of the
                    # FIFO. The FIFO order mirrors input-frame submission
                    # order, which under the no-B-frame invariant equals
                    # output-packet emission order.
                    self._pending_input_frame_pts_fifo_in_stream_time_base_units.append(
                        captured_frame
                        .presentation_timestamp_in_one_frame_units
                    )
                    for output_packet in output_stream.encode(
                        av_nv12_video_frame
                    ):
                        self._mux_one_encoded_packet_with_synthesized_pts_and_dts(
                            output_packet=output_packet,
                            output_container=output_container,
                        )
                except av.FFmpegError as ffmpeg_err:
                    raise EncoderPipelineError(
                        _format_encoder_pipeline_error_message_from_ffmpeg_error(
                            failing_frame_pts=(
                                captured_frame
                                .presentation_timestamp_in_one_frame_units
                            ),
                            frame_buffer_shape=(
                                captured_frame
                                .bgra_frame_pixel_buffer
                                .shape
                            ),
                            frame_buffer_dtype=str(
                                captured_frame
                                .bgra_frame_pixel_buffer
                                .dtype
                            ),
                            phase_human_readable=(
                                "per-frame encode + mux"
                            ),
                            ffmpeg_error=ffmpeg_err,
                        )
                    ) from ffmpeg_err
                except Exception as non_ffmpeg_exc:
                    # A non-libav Python exception (eg a numpy shape
                    # mismatch from upstream capture). We do not try to
                    # squeeze libav-specific fields out of it; we surface
                    # the Python exception verbatim so the failure stays
                    # diagnosable without us pretending it came from
                    # FFmpeg.
                    raise EncoderPipelineError(
                        f"PyAV / Intel Quick Sync Video encoding pipeline "
                        f"raised a non-libav Python exception on a frame.\n"
                        f"  Failing frame PTS  : "
                        f"{captured_frame.presentation_timestamp_in_one_frame_units}\n"
                        f"  Frame buffer shape : "
                        f"{captured_frame.bgra_frame_pixel_buffer.shape}\n"
                        f"  Frame buffer dtype : "
                        f"{captured_frame.bgra_frame_pixel_buffer.dtype}\n"
                        f"  Underlying Python  : "
                        f"{type(non_ffmpeg_exc).__name__}: {non_ffmpeg_exc}"
                    ) from non_ffmpeg_exc

                self.frames_successfully_encoded_so_far += 1

            # Graceful path: flush the encoder. Skip on force-cancel.
            if not self.encoder_was_force_cancelled:
                try:
                    for trailing_packet in output_stream.encode(None):
                        self._mux_one_encoded_packet_with_synthesized_pts_and_dts(
                            output_packet=trailing_packet,
                            output_container=output_container,
                        )
                except av.FFmpegError as ffmpeg_err:
                    raise EncoderPipelineError(
                        _format_encoder_pipeline_error_message_from_ffmpeg_error(
                            failing_frame_pts=None,
                            frame_buffer_shape=None,
                            frame_buffer_dtype=None,
                            phase_human_readable=(
                                f"libavcodec flush "
                                f"(stream.encode(None)); "
                                f"frames successfully encoded before "
                                f"flush: "
                                f"{self.frames_successfully_encoded_so_far}"
                            ),
                            ffmpeg_error=ffmpeg_err,
                        )
                    ) from ffmpeg_err
                except Exception as non_ffmpeg_flush_exc:
                    raise EncoderPipelineError(
                        f"PyAV / Intel Quick Sync Video encoder flush "
                        f"raised a non-libav Python exception.\n"
                        f"  Frames successfully encoded before flush : "
                        f"{self.frames_successfully_encoded_so_far}\n"
                        f"  Underlying Python                         : "
                        f"{type(non_ffmpeg_flush_exc).__name__}: "
                        f"{non_ffmpeg_flush_exc}"
                    ) from non_ffmpeg_flush_exc

                # Invariant check after a graceful libavcodec flush:
                # every input frame this program submitted to
                # ``output_stream.encode(...)`` must, under the
                # no-B-frames configuration locked in by
                # ``INTEL_QSV_MAX_B_FRAMES_VALUE = 0``, have produced
                # exactly one output packet on the way back up. The
                # ``encode(None)`` call drains the entire Intel oneVPL
                # async pipeline. The FIFO that pairs input PTSes
                # with output packets therefore MUST be empty at this
                # point. A non-empty FIFO here means Intel's
                # ``vpl-gpu-rt`` H.264 implementation silently dropped
                # ``len(FIFO)`` input frames somewhere in its
                # pipeline - a 1:1 correspondence breakage that this
                # program cannot otherwise detect, and that would
                # produce an output MP4 whose every-frame contents
                # are displaced by one frame relative to the
                # operator's real-time screen activity (because every
                # post-drop output packet would be assigned the PTS
                # that originally belonged to the dropped frame).
                if self._pending_input_frame_pts_fifo_in_stream_time_base_units:
                    leftover_pts_values_after_graceful_flush = list(
                        self._pending_input_frame_pts_fifo_in_stream_time_base_units
                    )
                    raise EncoderPipelineError(
                        f"After the graceful libavcodec flush "
                        f"(``output_stream.encode(None)`` returned no "
                        f"further packets), the pending-input-frame-"
                        f"PTS FIFO is not empty - the Intel oneVPL "
                        f"runtime emitted fewer output packets than "
                        f"this program submitted input frames. Under "
                        f"the no-B-frames configuration this program "
                        f"locks in (see ``INTEL_QSV_MAX_B_FRAMES_VALUE`` "
                        f"in Section 2 and the post-open validator "
                        f"that confirms it), input-to-output "
                        f"correspondence is contractually 1:1. A "
                        f"non-empty FIFO at flush time means Intel's "
                        f"``vpl-gpu-rt`` H.264 implementation silently "
                        f"dropped frames in its hardware-encode "
                        f"pipeline.\n"
                        f"  Total input frames submitted to encode() : "
                        f"{self.frames_successfully_encoded_so_far}\n"
                        f"  Total output packets muxed                : "
                        f"{self.output_packets_successfully_muxed_so_far}\n"
                        f"  Unmatched input PTS values remaining      : "
                        f"{len(leftover_pts_values_after_graceful_flush)}\n"
                        f"  First few unmatched PTS values            : "
                        f"{leftover_pts_values_after_graceful_flush[:10]}"
                    )
        finally:
            try:
                output_container.close()
            except Exception as close_exc:  # noqa: BLE001
                # Closing failed: log but do not mask any prior exception.
                self._logger.error(
                    f"PyAV OutputContainer.close() raised "
                    f"{type(close_exc).__name__}: {close_exc}"
                )

        self._logger.info(
            f"Encoder worker exiting normally. "
            f"Total input frames submitted to encode() : "
            f"{self.frames_successfully_encoded_so_far}. "
            f"Total output packets muxed                : "
            f"{self.output_packets_successfully_muxed_so_far}. "
            f"Frames discarded by force-cancel          : "
            f"{self.frames_discarded_due_to_force_cancel_so_far}. "
            f"Intel oneVPL runtime PTS disagreements    : "
            f"{self.total_intel_onevpl_pts_disagreements_observed}. "
            f"Force-cancelled                           : "
            f"{self.encoder_was_force_cancelled}."
        )

    def _mux_one_encoded_packet_with_synthesized_pts_and_dts(
        self,
        *,
        output_packet: "av.Packet",
        output_container: "av.container.OutputContainer",
    ) -> None:
        """Mux one ``h264_qsv``-produced packet after overwriting Intel's PTS and DTS with synthesized values.

        Why we synthesize, instead of using the encoder's own
        timestamps - the short version:

        Intel's ``intel/vpl-gpu-rt`` runtime - the H.264 hardware
        encoder implementation that backs the Intel oneVPL runtime
        on Microsoft Windows hosts with an Intel iGPU, distributed
        by Intel as part of the Intel Graphics driver - silently
        fails to populate ``mfxBitstream.TimeStamp`` on a
        non-deterministic subset of its output packets. On the
        reference target hardware (the integrated Intel UHD
        Graphics 770 GPU on the 13th-generation Intel Core i7-13700
        desktop processor) with this program's
        ``MFX_RATECONTROL_ICQ + AsyncDepth=4 + GopRefDist=1``
        configuration, that defect surfaces empirically from the
        6th output packet onward: the Intel runtime returns a
        bitstream whose ``mfxBitstream.TimeStamp`` field is either
        the ``MFX_TIMESTAMP_UNKNOWN = -1`` sentinel or the
        ``av_mallocz``-zeroed initial value, *despite* having
        received a perfectly valid input ``Data.TimeStamp`` for the
        corresponding input frame.

        FFmpeg's ``libavcodec/qsvenc.c`` line 2667 then translates
        the broken value straight through with::

            qpkt.pkt.pts = av_rescale_q(qpkt.bs->TimeStamp,
                                         (AVRational){1, 90000},
                                         avctx->time_base);

        with no ``MFX_TIMESTAMP_UNKNOWN`` sanitisation - despite
        ``qsvenc.c``'s sister file ``libavcodec/qsvdec.c`` lines
        64-70 explicitly checking for that exact sentinel on the
        decoder side. ``av_rescale_q(-1, ...)`` and
        ``av_rescale_q(0, ...)`` both return 0, so PyAV surfaces
        these broken packets as ``pkt.pts = 0`` - clobbering the
        muxer's monotonic-DTS invariant and (before this method
        existed) crashing the encoder worker with a strict-
        monotonic-PTS watchdog rejection mid-recording.

        Who is at fault, plainly: Intel. The
        ``mfxBitstream.TimeStamp`` field on output is supposed to
        carry through the input ``Data.TimeStamp``; the AV1 path in
        ``intel/vpl-gpu-rt`` does this correctly at
        ``av1ehw_base_general.cpp`` line 1663
        (``task.pBsOut->TimeStamp = task.pSurfIn->Data.TimeStamp;``);
        the H.264 path does not, on this hardware, under our
        configuration. FFmpeg's ``qsvenc.c`` is a secondary
        accomplice for not sanitising the sentinel the way
        ``qsvdec.c`` does, but the upstream cause is Intel's H.264
        encode pipeline failing to honour its own documented
        ``mfxBitstream.TimeStamp`` contract. Neither bug has been
        fixed at the time of writing; every screen recorder that
        hands H.264 work to ``h264_qsv`` on this Intel Graphics
        driver inherits the defect.

        This program does not have the option of waiting for
        Intel.

        What this method does instead:

        It discards ``output_packet.pts`` and ``output_packet.dts``
        entirely - the values Intel's runtime put there via
        ``mfxBitstream.TimeStamp`` and ``mfxBitstream.DecodeTimeStamp``
        - and substitutes the head of the strictly-monotonic FIFO
        of input-frame PTS values
        (``_pending_input_frame_pts_fifo_in_stream_time_base_units``,
        ``popleft``ed once per output packet). The capture worker
        ``append``s the wall-clock-anchored VFR PTS of every input
        frame to this FIFO at submission; this method pops it back
        out at the matching output packet. The popped value is then
        assigned to both ``output_packet.pts`` and
        ``output_packet.dts``.

        The substituted value is the *spec-correct* PTS and DTS
        for this program's encoder configuration. The chain of
        reasoning, every link explicit:

          1. ``INTEL_QSV_MAX_B_FRAMES_VALUE = 0`` is set explicitly
             in the codec context's ``options`` dict in
             ``_run_encode_loop`` and verified post-open by
             ``_verify_encoder_codec_context_post_open_state_matches_explicit_configuration``.
             This means ``avctx->max_b_frames == 0`` after
             ``avcodec_open2`` returns, which in turn means
             ``q->param.mfx.GopRefDist == FFMAX(-1, 0) + 1 == 1``
             when ``libavcodec/qsvenc.c`` line 1078 passes the
             parameter into the Intel oneVPL session
             initialisation via ``MFXVideoENCODE_Init``.
             ``GopRefDist == 1`` is libmfx's explicit instruction
             for "no B-frames between anchors".

          2. With no B-frames, the H.264 specification's encoded
             bitstream has decode order identical to display order:
             each output NAL unit is a single picture, encoded
             with reference only to past pictures, decodable as
             soon as it arrives. The Nth output packet (in the
             order ``output_container.mux`` receives it)
             corresponds to the Nth input frame this program fed
             into ``output_stream.encode``.

          3. The capture worker writes each input frame's
             ``av.VideoFrame.pts`` to the real wall-clock instant
             of its python-mss BitBlt, expressed in stream time-base
             units (variable-frame-rate timestamping; see the
             long-form rationale in the capture worker's
             ``_run_capture_loop``). The PTS sequence is therefore
             strictly monotonic but not necessarily sequential -
             gaps appear in the PTS sequence at wall-clock instants
             where the seven-layer capture-to-encode pipeline could
             not deliver a frame within one
             ``1 / TARGET_OUTPUT_FRAMES_PER_SECOND`` slot.

          4. Combining (2) and (3): the Nth output packet is
             encoded from the Nth input frame, so its spec-correct
             ``pkt.pts`` is the same integer that the capture
             worker wrote into the Nth input frame's
             ``av.VideoFrame.pts``. The encoder worker carries
             that integer forward via the FIFO field
             ``_pending_input_frame_pts_fifo_in_stream_time_base_units``,
             which the ``_run_encode_loop`` ``append``s to at the
             moment of ``output_stream.encode(...)`` submission;
             this method ``popleft``s from it at the moment a
             libavcodec output packet emerges.

          5. With no B-frames, the H.264 specification mandates
             ``DTS == PTS`` for every packet (no out-of-order
             reference frame ever has to be waited on). The
             ``popleft``ed FIFO value is therefore the spec-correct
             ``pkt.dts`` as well.

        That is the entire justification. There is no heuristic,
        no fudge, no "good enough"; under the four numbered
        invariants above the synthesized value is uniquely correct.

        The hopeful step in this chain is link (2)'s ``"the Nth
        output packet corresponds to the Nth input frame"`` premise.
        We rely on it. It is what the H.264 ``GopRefDist=1``
        configuration guarantees - in the spec. We have to hope
        Intel's ``vpl-gpu-rt`` runtime, which has already proven it
        does not honour the ``mfxBitstream.TimeStamp`` contract,
        does at least honour the ``one input frame produces
        exactly one output bitstream`` contract on top of which
        (2) rests. If the runtime were to silently drop an output
        bitstream mid-recording, the FIFO's Nth ``popleft`` would
        return the PTS that originally belonged to the dropped
        frame, and every subsequent output packet would carry a
        PTS belonging to an earlier real-time moment than its
        bitstream actually encodes - the resulting MP4 would play
        back with content displaced by one frame's worth of
        real-time. We cannot detect this from inside the encoder
        worker without parsing the H.264 bitstream and cross-
        referencing frame counts; we do not. We do however detect
        the end-of-session symptom: a non-empty FIFO after the
        graceful ``encode(None)`` flush means the encoder produced
        fewer output packets than this program submitted input
        frames, and ``_run_encode_loop`` raises
        ``EncoderPipelineError`` rather than closing a silently
        truncated output file.

        Diagnostic counterpart: every output packet's libmfx-
        derived ``pkt.pts`` / ``pkt.dts`` and the synthesized
        ``popleft``ed PTS are logged at DEBUG into the per-session
        ``session.log``. The first observed disagreement between
        the libmfx-derived and the synthesized value emits a
        ``WARNING`` line with full context. Subsequent
        disagreements are rate-limited to one ``WARNING`` per
        ``_INTEL_ONEVPL_PTS_DISAGREEMENT_WARNING_LOG_PERIOD_PACKETS``
        output packets so that a host whose Intel oneVPL runtime
        disagrees on every packet does not balloon ``session.log``
        across a multi-hour long-form recording. The full count of
        disagreements observed during the session is logged once
        at INFO at encoder-worker exit, and is also recorded in
        the session metadata JSON.
        """
        # Snapshot the values the Intel oneVPL runtime / FFmpeg's
        # ``h264_qsv`` wrapper actually wrote into the packet,
        # BEFORE we overwrite them, so the forensic log captures
        # the broken upstream state rather than our synthesized
        # repair of it.
        libmfx_derived_packet_pts_in_stream_time_base_units = (
            output_packet.pts
        )
        libmfx_derived_packet_dts_in_stream_time_base_units = (
            output_packet.dts
        )

        # ``popleft`` the PTS of the input frame this output packet
        # was encoded from. An empty FIFO at this point is an
        # unrecoverable contract violation: FFmpeg's ``h264_qsv``
        # wrapper produced an output packet without a corresponding
        # input frame having been submitted, which under the
        # no-B-frames configuration would mean the Intel oneVPL
        # runtime synthesised a frame on its own.
        if not self._pending_input_frame_pts_fifo_in_stream_time_base_units:
            raise EncoderPipelineError(
                f"FFmpeg's ``h264_qsv`` H.264 encoder wrapper "
                f"emitted an output packet, but the pending-input-"
                f"frame-PTS FIFO is empty. Under the no-B-frames "
                f"configuration this program locks in (see the "
                f"``INTEL_QSV_MAX_B_FRAMES_VALUE`` constant and the "
                f"post-open validator that confirms it),"
                f" input-to-output correspondence is contractually "
                f"1:1 and the FIFO must always carry the PTS of "
                f"the input frame the current output packet was "
                f"encoded from. An empty FIFO at this point means "
                f"the Intel oneVPL runtime produced more output "
                f"bitstreams than this program submitted input "
                f"frames - the runtime either fabricated a frame or "
                f"emitted a duplicate of an earlier one.\n"
                f"  Total input frames submitted so far : "
                f"{self.frames_successfully_encoded_so_far}\n"
                f"  Total output packets muxed so far    : "
                f"{self.output_packets_successfully_muxed_so_far}\n"
                f"  libmfx-derived pkt.pts on the "
                f"orphan packet                         : "
                f"{libmfx_derived_packet_pts_in_stream_time_base_units}\n"
                f"  libmfx-derived pkt.dts on the "
                f"orphan packet                         : "
                f"{libmfx_derived_packet_dts_in_stream_time_base_units}\n"
                f"  packet.size_bytes                    : "
                f"{output_packet.size}\n"
                f"  packet.is_keyframe                   : "
                f"{output_packet.is_keyframe}"
            )
        synthesized_pts_and_dts_in_stream_time_base_units = (
            self._pending_input_frame_pts_fifo_in_stream_time_base_units.popleft()
        )

        # Defensive monotonicity ledger. By construction the FIFO
        # only ever contains strictly-increasing values - the
        # capture worker's VFR-PTS strict-monotonic guard enforces
        # that at the enqueue site - so this branch can only be
        # reached if a bug elsewhere violates the invariant. We
        # surface the violation immediately rather than letting a
        # silently-out-of-order ``av_interleaved_write_frame`` call
        # the mp4 muxer reject one stack frame later.
        if (
            self._last_muxed_synthesized_output_packet_pts_in_stream_time_base_units
            is not None
            and synthesized_pts_and_dts_in_stream_time_base_units
            <= self._last_muxed_synthesized_output_packet_pts_in_stream_time_base_units
        ):
            raise EncoderPipelineError(
                f"The pending-input-frame-PTS FIFO ``popleft``ed a "
                f"value that is not strictly greater than the "
                f"previously muxed packet's synthesized PTS. This "
                f"is an internal-invariant violation: the FIFO is "
                f"appended-to only by the capture worker, which "
                f"enforces strict monotonicity on its VFR PTS "
                f"computation, so this branch should be "
                f"unreachable in correct operation.\n"
                f"  Previously muxed synthesized PTS : "
                f"{self._last_muxed_synthesized_output_packet_pts_in_stream_time_base_units}\n"
                f"  Just-popped FIFO PTS              : "
                f"{synthesized_pts_and_dts_in_stream_time_base_units}\n"
                f"  Output packets muxed so far        : "
                f"{self.output_packets_successfully_muxed_so_far}\n"
                f"  Input frames submitted so far      : "
                f"{self.frames_successfully_encoded_so_far}"
            )

        self._logger.debug(
            f"Encoder emitted packet: "
            f"libmfx_pts="
            f"{libmfx_derived_packet_pts_in_stream_time_base_units}, "
            f"libmfx_dts="
            f"{libmfx_derived_packet_dts_in_stream_time_base_units}, "
            f"synthesized_pts_and_dts="
            f"{synthesized_pts_and_dts_in_stream_time_base_units}, "
            f"size_bytes={output_packet.size}, "
            f"duration={output_packet.duration}, "
            f"is_keyframe={output_packet.is_keyframe}, "
            f"is_corrupt={output_packet.is_corrupt}, "
            f"is_disposable={output_packet.is_disposable}, "
            f"input_frames_submitted_so_far="
            f"{self.frames_successfully_encoded_so_far}, "
            f"output_packets_muxed_so_far="
            f"{self.output_packets_successfully_muxed_so_far}, "
            f"fifo_depth="
            f"{len(self._pending_input_frame_pts_fifo_in_stream_time_base_units)}"
        )

        # Rate-limited disagreement WARNING. The first observed
        # disagreement always emits a verbose ``WARNING`` carrying
        # the full diagnostic context the operator needs to file
        # an Intel oneVPL runtime issue (citing the
        # ``libavcodec/qsvenc.c`` line 2667 mapping and the
        # ``intel/vpl-gpu-rt`` issue tracker). Subsequent
        # disagreements bump the running counter; one short
        # ``WARNING`` tally line per
        # ``_INTEL_ONEVPL_PTS_DISAGREEMENT_WARNING_LOG_PERIOD_PACKETS``
        # packets keeps the operator informed without unbounded
        # ``session.log`` growth. Every disagreement is still
        # captured at ``DEBUG`` via the line above.
        if (
            libmfx_derived_packet_pts_in_stream_time_base_units
            != synthesized_pts_and_dts_in_stream_time_base_units
        ):
            self.total_intel_onevpl_pts_disagreements_observed += 1
            if self.total_intel_onevpl_pts_disagreements_observed == 1:
                self._logger.warning(
                    f"FIRST observed disagreement between the Intel "
                    f"oneVPL runtime's libmfx-derived ``pkt.pts`` "
                    f"({libmfx_derived_packet_pts_in_stream_time_base_units}) "
                    f"and the spec-correct synthesized PTS for the "
                    f"current output packet "
                    f"({synthesized_pts_and_dts_in_stream_time_base_units}). "
                    f"libmfx's value is being discarded; the "
                    f"synthesized value is the spec-correct PTS for "
                    f"a B-frame-free H.264 GOP under this program's "
                    f"configuration. Most likely cause: "
                    f"``intel/vpl-gpu-rt`` left "
                    f"``mfxBitstream.TimeStamp`` at the "
                    f"``MFX_TIMESTAMP_UNKNOWN`` sentinel or its "
                    f"``av_mallocz``-zeroed initial value, and "
                    f"``libavcodec/qsvenc.c`` line 2667 mapped that "
                    f"to ``av_rescale_q(...)`` = 0 without the "
                    f"``MFX_TIMESTAMP_UNKNOWN`` sanitisation present "
                    f"in the sister file ``qsvdec.c`` lines 64-70. "
                    f"Output packet index : "
                    f"{synthesized_pts_and_dts_in_stream_time_base_units}, "
                    f"input frames submitted so far : "
                    f"{self.frames_successfully_encoded_so_far}, "
                    f"size_bytes : {output_packet.size}, "
                    f"is_keyframe : {output_packet.is_keyframe}. "
                    f"Subsequent disagreements during this session "
                    f"will be summarised at "
                    f"``DEBUG`` per packet and ``WARNING`` once per "
                    f"{self._INTEL_ONEVPL_PTS_DISAGREEMENT_WARNING_LOG_PERIOD_PACKETS} "
                    f"packets, with a final count logged at "
                    f"encoder-worker exit."
                )
            elif (
                self.total_intel_onevpl_pts_disagreements_observed
                % self._INTEL_ONEVPL_PTS_DISAGREEMENT_WARNING_LOG_PERIOD_PACKETS
                == 0
            ):
                self._logger.warning(
                    f"Intel oneVPL runtime PTS disagreement count "
                    f"so far this session: "
                    f"{self.total_intel_onevpl_pts_disagreements_observed} "
                    f"(rate-limited; full per-packet detail at DEBUG)."
                )

        # Overwrite both PTS and DTS with the spec-correct
        # synthesized value. PTS == DTS for a B-frame-free GOP per
        # the H.264 specification; ``INTEL_QSV_MAX_B_FRAMES_VALUE``
        # is what guarantees B-frame-free.
        output_packet.pts = (
            synthesized_pts_and_dts_in_stream_time_base_units
        )
        output_packet.dts = (
            synthesized_pts_and_dts_in_stream_time_base_units
        )

        self._last_muxed_synthesized_output_packet_pts_in_stream_time_base_units = (
            synthesized_pts_and_dts_in_stream_time_base_units
        )
        output_container.mux(output_packet)
        self.output_packets_successfully_muxed_so_far += 1

    def _verify_encoder_codec_context_post_open_state_matches_explicit_configuration(
        self,
        *,
        output_stream: "av.video.stream.VideoStream",
    ) -> None:
        """Verify the just-opened ``h264_qsv`` codec context matches our explicit configuration.

        Called exactly once, immediately after
        ``output_stream.codec_context.open(strict=True)`` returns, and
        before any encode call. By construction, at this point FFmpeg's
        ``avcodec_open2()`` has finished: it has applied the per-codec
        defaults from ``qsv_enc_defaults[]`` at
        ``libavcodec/qsvenc_h264.c:119``, parsed and applied the
        explicit options dict written in ``_run_encode_loop`` above,
        initialised the Intel oneVPL encoder session, and returned
        success. The resulting steady-state values of every
        ``AVCodecContext`` field that PyAV exposes via the
        ``av.video.codeccontext.VideoCodecContext`` @property accessors
        are now readable.

        This validator does not trust any single layer in the seven-
        layer call chain documented at the top of Section 14 - not
        PyAV's binding, not FFmpeg's libavcodec dispatcher, not
        FFmpeg's ``h264_qsv`` encoder wrapper, not the Intel oneVPL
        runtime that ``h264_qsv`` talks to - to have honoured every
        option we passed. Any of them could in theory silently coerce,
        clamp, or substitute a value. We therefore read every field we
        explicitly configured, exactly once, after open(), and refuse
        to proceed if it disagrees with the value this program
        requires.

        Each mismatch raises ``EncoderPipelineError`` with: the
        expected value (and where it came from inside this source
        file), the actually-observed value, the layer of the call
        chain that owns the discrepancy, and the in-program
        correctness invariant that depends on the field. There is no
        silent fallback: a single mismatch aborts the encoder worker
        before any frame is sent to the encoder.
        """
        codec_context = output_stream.codec_context

        # (1) Sanity: the codec context must actually be open after
        # the explicit open() call above. PyAV's CodecContext.open()
        # is documented to set ``is_open=True`` only after
        # ``avcodec_open2()`` returns success.
        if not codec_context.is_open:
            raise EncoderPipelineError(
                f"PyAV reports ``codec_context.is_open == False`` "
                f"after ``codec_context.open(strict=True)`` returned "
                f"without raising. This violates PyAV's documented "
                f"contract on ``av.codec.context.CodecContext.open``. "
                f"The encoder cannot be safely used in this state."
            )

        # (2) The codec FFmpeg picked must be exactly the one we
        # asked for. libavcodec's dispatch logic, in principle, could
        # silently substitute a different encoder if our name is
        # ambiguous or if a higher-priority encoder claims the same
        # codec ID. We refuse any silent substitution: we require
        # Intel Quick Sync Video specifically, because every
        # downstream invariant in this file (the
        # MFX_TIMESTAMP_UNKNOWN sanitisation gap we worked around with
        # the ``pkt.dts := pkt.pts`` override, the
        # ``GopRefDist=1`` computation derived from
        # ``max_b_frames=0``, the Intel-specific async_depth=4
        # pipeline-fill remedy) is specific to FFmpeg's
        # ``h264_qsv`` wrapper.
        actual_ffmpeg_encoder_name = codec_context.codec.name
        if (
            actual_ffmpeg_encoder_name
            != INTEL_QUICK_SYNC_VIDEO_H264_ENCODER_FFMPEG_NAME
        ):
            raise EncoderPipelineError(
                f"FFmpeg's libavcodec dispatched our encoder request "
                f"to a different encoder than the one this program "
                f"requires. This program is hard-wired to FFmpeg's "
                f"``h264_qsv`` H.264 encoder (the FFmpeg wrapper for "
                f"the Intel Quick Sync Video H.264 encoder session) "
                f"and is not compatible with any other encoder.\n"
                f"  Encoder name requested by this program : "
                f"{INTEL_QUICK_SYNC_VIDEO_H264_ENCODER_FFMPEG_NAME!r}\n"
                f"  Encoder name FFmpeg actually opened    : "
                f"{actual_ffmpeg_encoder_name!r}"
            )

        # (3) ``max_b_frames`` must be exactly 0. The full rationale
        # is documented at the ``INTEL_QSV_MAX_B_FRAMES_VALUE``
        # declaration in Section 2: without this, FFmpeg's
        # ``h264_qsv`` defaults ``avctx->max_b_frames`` to -1 (the
        # "Intel oneVPL runtime picks" sentinel), the runtime picks
        # ``GopRefDist=3`` on the integrated Intel UHD Graphics 770
        # GPU, and the encoder emits packets in H.264 decode order
        # (which differs from display order in a B-frame GOP) -
        # invalidating the foundational invariant that makes
        # the PTS/DTS synthesis in
        # ``_mux_one_encoded_packet_with_synthesized_pts_and_dts``
        # spec-correct (encode order == display order == input order).
        observed_max_b_frames_value = codec_context.max_b_frames
        if observed_max_b_frames_value != INTEL_QSV_MAX_B_FRAMES_VALUE:
            raise EncoderPipelineError(
                f"FFmpeg's libavcodec ``AVCodecContext.max_b_frames`` "
                f"does not match the value this program requires for "
                f"the synthesized-PTS/DTS pipeline in "
                f"``_mux_one_encoded_packet_with_synthesized_pts_and_dts`` "
                f"to be H.264-spec-correct. "
                f"This program is hard-wired to ``max_b_frames = 0`` "
                f"(B-frames disabled); see the long-form rationale at "
                f"the ``INTEL_QSV_MAX_B_FRAMES_VALUE`` constant in "
                f"Section 2.\n"
                f"  Expected ``max_b_frames`` (this program)     : "
                f"{INTEL_QSV_MAX_B_FRAMES_VALUE}\n"
                f"  Observed ``max_b_frames`` after open()       : "
                f"{observed_max_b_frames_value}\n"
                f"  Source of expected value                     : "
                f"``INTEL_QSV_MAX_B_FRAMES_VALUE`` in Section 2"
            )

        # (4) ``gop_size`` (the libavcodec AVCodecContext field that
        # the ``g=`` AVOption sets) must match
        # ``KEYFRAME_INTERVAL_FRAMES``. The keyframe interval bounds
        # the crash-loss tail of the fragmented MP4 output: at most
        # one keyframe-to-keyframe fragment's worth of frames is lost
        # if the recorder process is killed mid-recording.
        observed_gop_size_value = codec_context.gop_size
        if observed_gop_size_value != KEYFRAME_INTERVAL_FRAMES:
            raise EncoderPipelineError(
                f"FFmpeg's libavcodec "
                f"``AVCodecContext.gop_size`` does not match the "
                f"value this program requires.\n"
                f"  Expected ``gop_size`` (this program) : "
                f"{KEYFRAME_INTERVAL_FRAMES}\n"
                f"  Observed ``gop_size`` after open()    : "
                f"{observed_gop_size_value}\n"
                f"  Source of expected value              : "
                f"``KEYFRAME_INTERVAL_FRAMES`` in Section 2"
            )

        # (5)-(6) ``width`` and ``height`` must match the Microsoft
        # Windows Display 1 native pixel dimensions we passed in via
        # the encoder worker constructor. A mismatch here would mean
        # the encoder session is configured for a different frame
        # size than the BGRA buffers we will be feeding it, which
        # would produce either an outright error from
        # libswscale/Intel oneVPL or a silent stride/alignment
        # corruption.
        if codec_context.width != self._width:
            raise EncoderPipelineError(
                f"FFmpeg's libavcodec "
                f"``AVCodecContext.width`` does not match this "
                f"program's configured capture width.\n"
                f"  Expected width (Microsoft Windows Display 1 "
                f"native pixel width) : {self._width}\n"
                f"  Observed width after open()                : "
                f"{codec_context.width}"
            )
        if codec_context.height != self._height:
            raise EncoderPipelineError(
                f"FFmpeg's libavcodec "
                f"``AVCodecContext.height`` does not match this "
                f"program's configured capture height.\n"
                f"  Expected height (Microsoft Windows Display 1 "
                f"native pixel height) : {self._height}\n"
                f"  Observed height after open()                 : "
                f"{codec_context.height}"
            )

        # (7) ``pix_fmt`` must be exactly ``"nv12"``. The Intel Quick
        # Sync Video H.264 encoder hardware block requires NV12 input
        # (8-bit 4:2:0 with interleaved chroma); FFmpeg's
        # ``h264_qsv`` wrapper enforces this at codec init. We
        # convert each captured BGRA frame to NV12 via libswscale
        # before feeding the encoder; this check confirms the
        # encoder will accept what we will produce.
        observed_pix_fmt_value = codec_context.pix_fmt
        if observed_pix_fmt_value != "nv12":
            raise EncoderPipelineError(
                f"FFmpeg's libavcodec "
                f"``AVCodecContext.pix_fmt`` is not ``nv12``. The "
                f"Intel Quick Sync Video H.264 encoder requires NV12 "
                f"input (8-bit 4:2:0 with interleaved chroma). The "
                f"capture worker hands the encoder BGRA pixel "
                f"buffers, which the encoder worker converts to NV12 "
                f"via libswscale before encoding.\n"
                f"  Expected pix_fmt : 'nv12'\n"
                f"  Observed pix_fmt : {observed_pix_fmt_value!r}"
            )

        # (8)-(9) Time-base agreement between the libavcodec
        # ``AVCodecContext`` and the libavformat ``AVStream``. Both
        # must equal ``Fraction(1, TARGET_OUTPUT_FRAMES_PER_SECOND)``
        # so that the FIFO-popped PTS values this program writes onto
        # ``output_packet.pts`` in
        # ``_mux_one_encoded_packet_with_synthesized_pts_and_dts``
        # are NOT silently rescaled by libavformat's
        # ``av_interleaved_write_frame`` on the way into the mp4
        # muxer. If the codec context's time-base disagrees with the
        # stream's time-base, the muxer rescales the packet's PTS by
        # the ratio between them - which is the precise failure mode
        # that surfaced as "output MP4 plays back at exactly 2x real
        # time" on the reference target hardware before this check
        # was added (FFmpeg defaulted ``codec_context.time_base`` to
        # ``1 / (ticks_per_frame * framerate) = 1 / 60`` for an H.264
        # codec descriptor with ``ticks_per_frame = 2`` and the
        # ``framerate`` argument we pass into ``add_stream``, while
        # ``stream.time_base`` was explicitly set to ``1 / 30``;
        # every muxed PTS was halved on the way out).
        expected_time_base_in_stream_time_base_units = Fraction(
            1, TARGET_OUTPUT_FRAMES_PER_SECOND
        )
        observed_codec_context_time_base_value = (
            codec_context.time_base
        )
        if (
            observed_codec_context_time_base_value
            != expected_time_base_in_stream_time_base_units
        ):
            raise EncoderPipelineError(
                f"FFmpeg's libavcodec "
                f"``AVCodecContext.time_base`` does not match the "
                f"value this program requires for the FIFO-popped "
                f"PTS / DTS overrides in "
                f"``_mux_one_encoded_packet_with_synthesized_pts_and_dts`` "
                f"to be muxed without rescaling.\n"
                f"  Expected ``codec_context.time_base`` : "
                f"{expected_time_base_in_stream_time_base_units} "
                f"(= 1 / TARGET_OUTPUT_FRAMES_PER_SECOND)\n"
                f"  Observed ``codec_context.time_base`` : "
                f"{observed_codec_context_time_base_value}\n"
                f"This typically indicates that libavcodec's "
                f"``avcodec_open2`` overrode the explicit "
                f"``codec_context.time_base`` this program set "
                f"before ``open()``, in favour of the H.264 codec "
                f"descriptor's ``ticks_per_frame``-derived default "
                f"of ``1 / (ticks_per_frame * framerate)``."
            )
        observed_stream_time_base_value = output_stream.time_base
        if (
            observed_stream_time_base_value
            != expected_time_base_in_stream_time_base_units
        ):
            raise EncoderPipelineError(
                f"FFmpeg's libavformat ``AVStream.time_base`` does "
                f"not match the value this program requires for "
                f"the FIFO-popped PTS / DTS overrides to be muxed "
                f"without rescaling.\n"
                f"  Expected ``stream.time_base`` : "
                f"{expected_time_base_in_stream_time_base_units} "
                f"(= 1 / TARGET_OUTPUT_FRAMES_PER_SECOND)\n"
                f"  Observed ``stream.time_base`` : "
                f"{observed_stream_time_base_value}\n"
                f"This typically indicates that the mp4 muxer's "
                f"``avformat_write_header`` adjusted the stream's "
                f"time-base to one of its supported per-track "
                f"timescales after this program's explicit "
                f"assignment."
            )

        self._logger.info(
            f"Encoder codec context post-open state passed every "
            f"explicit-configuration validation check: "
            f"codec.name={actual_ffmpeg_encoder_name!r}, "
            f"max_b_frames={observed_max_b_frames_value} "
            f"(== ``GopRefDist=1`` inside the Intel oneVPL runtime, "
            f"meaning no B-frames, meaning encoder output order "
            f"equals input order), "
            f"gop_size={observed_gop_size_value}, "
            f"resolution={codec_context.width}x{codec_context.height}, "
            f"pix_fmt={observed_pix_fmt_value!r}, "
            f"codec_context.time_base="
            f"{observed_codec_context_time_base_value}, "
            f"stream.time_base="
            f"{observed_stream_time_base_value}."
        )

    def _discard_queue_remainder_due_to_force_cancel(self) -> None:
        """Drain whatever is still in the inbound queue, counting the loss."""
        while True:
            try:
                drained_item = self._queue.get_nowait()
            except queue.Empty:
                return
            if not isinstance(drained_item, _EncoderShutdownSentinelType):
                self.frames_discarded_due_to_force_cancel_so_far += 1


# ============================================================================
# Section 15 - Per-session resources bundle
# ============================================================================
#
# A single dataclass that owns ALL resources for one recording session.
# Either ``RecordingSessionController._active_session_resources`` is None
# (meaning no session is in flight, controller in IDLE or ERROR) or it
# points at one of these (meaning a session is in flight, controller in
# PREPARING, RECORDING, PAUSED, FINALIZING or FORCE_CANCELLING). That
# is the *whole* invariant - there is no other per-session field on the
# controller. The release method is idempotent: calling it twice or
# calling it on a partially-constructed bundle is safe.


@dataclasses.dataclass
class _ActiveRecordingSessionResources:
    """All resources owned for the lifetime of one recording session.

    Construction is two-phase: first the ``stop`` / ``pause`` /
    ``force_cancel`` events plus the inter-thread queue are created
    (cheap, infallible), then the per-session output folder + log files
    + worker threads are populated (any of which can fail). The
    ``release_idempotently`` method tolerates either phase being only
    partially complete.
    """

    graceful_stop_event: threading.Event
    operator_pause_event: threading.Event
    force_cancel_event: threading.Event
    inter_thread_frame_queue: (
        "queue.Queue[_CapturedFrameForEncoder | _EncoderShutdownSentinelType]"
    )
    per_frame_csv_writer_lock: threading.Lock

    # Populated by ``_construct_session_resources_under_lifecycle_thread``.
    validated_display_config: Optional[
        ValidatedDualExtendModeDisplayConfiguration
    ] = None
    artifact_paths: Optional[PerSessionOutputFolderArtifactPaths] = None
    session_started_at_local_datetime: Optional[
        _datetime_module.datetime
    ] = None
    per_session_logger: Optional[logging.Logger] = None
    per_session_log_file_handle: Optional[Any] = None
    per_frame_csv_file_handle: Optional[Any] = None
    per_frame_csv_writer: Optional[csv.writer] = None
    capture_worker: Optional[
        _SingleDisplayPrimaryMonitorCaptureWorker
    ] = None
    encoder_worker: Optional[
        _IntelQuickSyncVideoFragmentedMp4EncoderWorker
    ] = None
    finalization_started_at_monotonic_seconds: Optional[float] = None

    @classmethod
    def construct_empty(cls) -> "_ActiveRecordingSessionResources":
        return cls(
            graceful_stop_event=threading.Event(),
            operator_pause_event=threading.Event(),
            force_cancel_event=threading.Event(),
            inter_thread_frame_queue=queue.Queue(
                maxsize=CAPTURE_TO_ENCODER_QUEUE_MAXIMUM_DEPTH_FRAMES,
            ),
            per_frame_csv_writer_lock=threading.Lock(),
        )

    def release_idempotently(
        self, fallback_logger: logging.Logger
    ) -> None:
        """Release every per-session resource. Safe to call any number of times.

        Workers are NOT joined here - the lifecycle thread must already
        have joined them (or accepted that they are abandoned as daemon
        threads under force-cancel timeout).
        """
        if self.per_frame_csv_file_handle is not None:
            handle = self.per_frame_csv_file_handle
            self.per_frame_csv_file_handle = None
            self.per_frame_csv_writer = None
            try:
                handle.flush()
                handle.close()
            except OSError as csv_close_exc:
                fallback_logger.error(
                    "Failed to close per-frame CSV log file handle: "
                    f"{csv_close_exc!r}"
                )

        if self.per_session_logger is not None:
            local_logger = self.per_session_logger
            self.per_session_logger = None
            for handler in list(local_logger.handlers):
                try:
                    handler.flush()
                    handler.close()
                except OSError:
                    pass
                local_logger.removeHandler(handler)

        if self.per_session_log_file_handle is not None:
            handle = self.per_session_log_file_handle
            self.per_session_log_file_handle = None
            try:
                handle.close()
            except OSError:
                pass


# ============================================================================
# Section 16 - Recording session controller (non-blocking, lifecycle-thread driven)
# ============================================================================


class RecordingSessionController:
    """Orchestrates one recording session at a time. Every public method is non-blocking.

    Threading model (elegant invariant)
    -----------------------------------
    For each recording session, exactly ONE background thread - the
    *lifecycle thread* - owns the session's resources from construction
    through finalization. The lifecycle thread:

      1. Constructs the per-session resources (validates the display
         configuration, provisions the output folder, opens the log
         files, spawns the capture and encoder worker threads). On any
         failure during construction, releases what was acquired and
         transitions ``PREPARING_SESSION_RESOURCES -> FATAL_ERROR_OBSERVED``.

      2. Transitions ``PREPARING_SESSION_RESOURCES -> ACTIVELY_RECORDING_DISPLAY_1``
         and waits on the session's ``graceful_stop_event``, periodically
         also polling for unexpected worker thread death.

      3. When ``graceful_stop_event`` is set (by the operator pressing
         Stop, OR because a worker thread died and signaled its own
         shutdown), joins both worker threads with a generous timeout.
         If the controller's state is ``FORCE_CANCELLATION_IN_PROGRESS``
         (because the operator pressed Force-Cancel during finalize),
         the join uses a much shorter timeout and the encoder worker
         skips the libavcodec flush.

      4. Writes the session metadata JSON (always - even on worker
         failure, so the forensic artifacts are complete) and releases
         every per-session resource. Transitions to ``IDLE_AWAITING_OPERATOR_START``
         on success, ``FATAL_ERROR_OBSERVED`` on any failure.

    The lifecycle thread is the ONLY thread that mutates the
    ``_active_session_resources`` field (other than ``request_start_new_recording_session``
    creating the empty resources record and storing it). The GUI main
    thread mutates only the state-machine state via the public ``request_*``
    methods, and reads ``current_state`` / statistics for display.

    Strict state-resource invariant
    -------------------------------
    Exactly one of the following holds at every instant:

      * ``_current_state in {IDLE_AWAITING_OPERATOR_START, FATAL_ERROR_OBSERVED}``
        AND ``_active_session_resources is None``.

      * ``_current_state`` is any other value
        AND ``_active_session_resources is not None``.

    Violation of this invariant raises ``InconsistentControllerStateAssertionError``
    and is treated as a programming defect, never a runtime condition.
    """

    # How long the lifecycle thread waits for each worker to exit under
    # the graceful path (operator pressed Stop only).
    _GRACEFUL_WORKER_JOIN_TIMEOUT_SECONDS: Final[float] = 30.0
    # How long the lifecycle thread waits for each worker under the
    # force-cancel path (operator pressed Force-Cancel after Stop).
    # Daemon threads die with the process anyway; this only governs
    # how long we wait before declaring the worker abandoned.
    _FORCE_CANCEL_WORKER_JOIN_TIMEOUT_SECONDS: Final[float] = 8.0
    # Poll interval for the lifecycle thread's "is a worker dying on
    # its own?" watchdog.
    _LIFECYCLE_THREAD_WATCHDOG_POLL_INTERVAL_SECONDS: Final[float] = 0.50

    def __init__(self) -> None:
        # Reentrant lock so transitions and reads from helper methods
        # can re-enter without deadlock.
        self._state_lock = threading.RLock()
        self._current_state: RecordingLifecycleState = (
            RecordingLifecycleState.IDLE_AWAITING_OPERATOR_START
        )
        # The ONE per-session field. None ⟺ no session in flight.
        self._active_session_resources: Optional[
            _ActiveRecordingSessionResources
        ] = None
        # History (kept across sessions for the GUI to display).
        self._most_recently_completed_session_artifact_paths: Optional[
            PerSessionOutputFolderArtifactPaths
        ] = None
        self._latest_fatal_exception: Optional[BaseException] = None
        # The lifecycle thread handle, recreated on every Start.
        self._lifecycle_thread: Optional[threading.Thread] = None

        # Bootstrap logger used before a per-session logger exists, and
        # also as the fallback during teardown when the per-session
        # logger has already been released.
        self._bootstrap_logger = logging.getLogger(
            f"{APP_INTERNAL_NAME}.controller"
        )

    # ============== Public state queries (thread-safe, non-blocking) ===

    @property
    def current_state(self) -> RecordingLifecycleState:
        with self._state_lock:
            return self._current_state

    @property
    def latest_fatal_exception(self) -> Optional[BaseException]:
        with self._state_lock:
            return self._latest_fatal_exception

    def in_progress_or_most_recently_completed_session_artifact_paths(
        self,
    ) -> Optional[PerSessionOutputFolderArtifactPaths]:
        with self._state_lock:
            session = self._active_session_resources
            if session is not None and session.artifact_paths is not None:
                return session.artifact_paths
            return (
                self._most_recently_completed_session_artifact_paths
            )

    def seconds_elapsed_since_finalization_started(self) -> Optional[float]:
        with self._state_lock:
            session = self._active_session_resources
            if session is None:
                return None
            started = session.finalization_started_at_monotonic_seconds
            if started is None:
                return None
            return time.monotonic() - started

    def live_capture_and_encoder_statistics_snapshot(
        self,
    ) -> dict[str, Any]:
        with self._state_lock:
            session = self._active_session_resources
            if session is None:
                capture = None
                encoder = None
            else:
                capture = session.capture_worker
                encoder = session.encoder_worker
        empty_snapshot: dict[str, Any] = {
            "frames_captured": 0,
            "frames_encoded": 0,
            "frames_discarded_by_force_cancel": 0,
            "total_paused_seconds": 0.0,
            "elapsed_wall_clock_seconds": 0.0,
            "elapsed_recorded_seconds": 0.0,
        }
        if capture is None or encoder is None:
            return empty_snapshot
        start_monotonic = capture.recording_start_monotonic_seconds
        now_monotonic = time.monotonic()
        elapsed_wall = (
            (now_monotonic - start_monotonic) if start_monotonic else 0.0
        )
        return {
            "frames_captured": (
                capture.frames_successfully_captured_so_far
            ),
            "frames_encoded": (
                encoder.frames_successfully_encoded_so_far
            ),
            "frames_discarded_by_force_cancel": (
                encoder.frames_discarded_due_to_force_cancel_so_far
            ),
            "total_paused_seconds": (
                capture.total_paused_wall_clock_seconds
            ),
            "elapsed_wall_clock_seconds": elapsed_wall,
            "elapsed_recorded_seconds": max(
                0.0,
                elapsed_wall - capture.total_paused_wall_clock_seconds,
            ),
        }

    # ============== Public state transitions (non-blocking) ============

    def request_start_new_recording_session(self) -> None:
        """Spawn the lifecycle thread for a new session. Returns within microseconds."""
        with self._state_lock:
            self._transition_state_under_lock(
                from_states={
                    RecordingLifecycleState.IDLE_AWAITING_OPERATOR_START,
                    RecordingLifecycleState.FATAL_ERROR_OBSERVED,
                },
                to_state=(
                    RecordingLifecycleState.PREPARING_SESSION_RESOURCES
                ),
                # On entry to PREPARING_SESSION_RESOURCES we MUST create
                # the empty per-session resources bundle so that the
                # invariant holds before we release the lock.
                resource_mutation=self._allocate_empty_session_under_lock,
            )
            self._latest_fatal_exception = None
            self._lifecycle_thread = threading.Thread(
                name="windows_screen_recorder.lifecycle",
                target=self._lifecycle_thread_body,
                daemon=True,
            )
            self._lifecycle_thread.start()

    def request_pause_capture(self) -> None:
        with self._state_lock:
            session = self._require_active_session_under_lock(
                operation_name="Pause"
            )
            self._transition_state_under_lock(
                from_states={
                    RecordingLifecycleState.ACTIVELY_RECORDING_DISPLAY_1,
                },
                to_state=RecordingLifecycleState.PAUSED_BY_OPERATOR,
            )
            session.operator_pause_event.set()

    def request_resume_capture(self) -> None:
        with self._state_lock:
            session = self._require_active_session_under_lock(
                operation_name="Resume"
            )
            self._transition_state_under_lock(
                from_states={
                    RecordingLifecycleState.PAUSED_BY_OPERATOR,
                },
                to_state=(
                    RecordingLifecycleState.ACTIVELY_RECORDING_DISPLAY_1
                ),
            )
            session.operator_pause_event.clear()

    def request_graceful_stop_and_finalize(self) -> None:
        """Signal the lifecycle thread to finalize. Returns within microseconds."""
        with self._state_lock:
            session = self._require_active_session_under_lock(
                operation_name="Stop"
            )
            self._transition_state_under_lock(
                from_states={
                    RecordingLifecycleState.ACTIVELY_RECORDING_DISPLAY_1,
                    RecordingLifecycleState.PAUSED_BY_OPERATOR,
                },
                to_state=(
                    RecordingLifecycleState
                    .FINALIZING_OUTPUT_AND_JOINING_WORKERS
                ),
            )
            session.finalization_started_at_monotonic_seconds = (
                time.monotonic()
            )
            session.graceful_stop_event.set()
            if session.operator_pause_event.is_set():
                # Clear pause so the capture worker observes the stop.
                session.operator_pause_event.clear()

    def request_force_cancel_finalization(self) -> None:
        """Promote an in-progress graceful finalize into a force cancel.

        Legal only while in FINALIZING_OUTPUT_AND_JOINING_WORKERS. Tells
        the encoder worker to skip the libavcodec flush and tells the
        lifecycle thread to wait for a much shorter join timeout. The
        fragmented MP4 file remains playable up through the last
        finalized fragment; only the in-flight fragment is lost.
        """
        with self._state_lock:
            session = self._require_active_session_under_lock(
                operation_name="Force-Cancel"
            )
            self._transition_state_under_lock(
                from_states={
                    RecordingLifecycleState
                    .FINALIZING_OUTPUT_AND_JOINING_WORKERS,
                },
                to_state=(
                    RecordingLifecycleState.FORCE_CANCELLATION_IN_PROGRESS
                ),
            )
            session.force_cancel_event.set()
            session.graceful_stop_event.set()  # belt-and-braces
            session.operator_pause_event.clear()

    # ============== Internal: state-transition primitives ==============

    def _transition_state_under_lock(
        self,
        *,
        from_states: set[RecordingLifecycleState],
        to_state: RecordingLifecycleState,
        resource_mutation: Optional[Any] = None,
    ) -> None:
        """Atomically validate, transition, optionally mutate resources, assert invariant.

        Caller MUST hold the state lock. ``resource_mutation``, if
        given, is a zero-argument callable that mutates per-session
        resources to satisfy the post-state invariant; it runs after
        the state assignment but before the invariant check.
        """
        if self._current_state not in from_states:
            from_names = sorted(s.name for s in from_states)
            raise IllegalRecordingStateTransitionError(
                f"Illegal recording-lifecycle state transition request.\n"
                f"  Attempted to transition into : {to_state.name}\n"
                f"  Controller's current state   : "
                f"{self._current_state.name}\n"
                f"  Legal source states          : {from_names}\n"
                f"This typically indicates the operator pressed a button "
                f"whose Tk handler should have been disabled in the "
                f"current state, which is a programming error in the "
                f"GUI's state-to-button-enabled-or-disabled mapping."
            )
        self._current_state = to_state
        if resource_mutation is not None:
            resource_mutation()
        self._assert_state_and_resources_invariant_under_lock()

    def _assert_state_and_resources_invariant_under_lock(self) -> None:
        state = self._current_state
        session = self._active_session_resources
        no_session_states = {
            RecordingLifecycleState.IDLE_AWAITING_OPERATOR_START,
            RecordingLifecycleState.FATAL_ERROR_OBSERVED,
        }
        if state in no_session_states:
            if session is not None:
                raise InconsistentControllerStateAssertionError(
                    f"Controller invariant violated: state is "
                    f"{state.name} (which requires no active session "
                    f"resources) but _active_session_resources is "
                    f"populated ({type(session).__name__}). This is a "
                    f"programming defect in the controller."
                )
            return
        # Any other state: session must be present.
        if session is None:
            raise InconsistentControllerStateAssertionError(
                f"Controller invariant violated: state is {state.name} "
                f"(which requires an active session resources bundle) "
                f"but _active_session_resources is None. This is a "
                f"programming defect in the controller."
            )

    def _allocate_empty_session_under_lock(self) -> None:
        if self._active_session_resources is not None:
            raise InconsistentControllerStateAssertionError(
                "Refusing to allocate a new per-session resources bundle "
                "while one already exists. This is a programming defect "
                "in the controller."
            )
        self._active_session_resources = (
            _ActiveRecordingSessionResources.construct_empty()
        )

    def _require_active_session_under_lock(
        self, *, operation_name: str
    ) -> _ActiveRecordingSessionResources:
        if self._active_session_resources is None:
            raise InconsistentControllerStateAssertionError(
                f"Operation '{operation_name}' requires an active session "
                f"resources bundle, but _active_session_resources is None "
                f"(current state: {self._current_state.name}). This is a "
                f"programming defect in the controller."
            )
        return self._active_session_resources

    # ============== Lifecycle thread body ==============================

    def _lifecycle_thread_body(self) -> None:
        """Run the entire session lifecycle. Owns the active session resources.

        This thread NEVER raises out - every failure path is funneled
        into the same finalize-and-transition-to-ERROR safety net. The
        state machine thus has exactly one terminal transition per
        session: ``IDLE_AWAITING_OPERATOR_START`` on success,
        ``FATAL_ERROR_OBSERVED`` on any failure. If even the safety
        net somehow fails, the lifecycle thread's outer try/except
        sets ``FATAL_ERROR_OBSERVED`` directly so the GUI is never
        left observing a transient state with no thread driving it.
        """
        try:
            self._lifecycle_thread_body_with_phases_inner()
        except BaseException as outermost_exc:  # noqa: BLE001
            # Last-resort safety net: an unexpected exception escaped
            # all the inner handlers. Force the controller into a
            # consistent ERROR state so the GUI is never stuck.
            self._bootstrap_logger.error(
                "Lifecycle thread caught an unexpected exception in "
                "its outermost safety net (this indicates a defect in "
                "an inner phase handler):\n"
                + "".join(traceback.format_exception(outermost_exc))
            )
            try:
                self._record_fatal_and_release_partial_session(
                    outermost_exc,
                    origin_description=(
                        "in the lifecycle thread's outermost safety net "
                        "(an inner phase handler failed to handle its "
                        "own exception)"
                    ),
                )
            except BaseException as recovery_exc:  # noqa: BLE001
                # Even the recovery raised. Force a minimal consistent
                # state directly under the lock.
                self._bootstrap_logger.error(
                    "Lifecycle thread's recovery handler itself "
                    "raised:\n"
                    + "".join(
                        traceback.format_exception(recovery_exc)
                    )
                )
                with self._state_lock:
                    self._active_session_resources = None
                    self._latest_fatal_exception = outermost_exc
                    self._current_state = (
                        RecordingLifecycleState.FATAL_ERROR_OBSERVED
                    )
                    self._lifecycle_thread = None

    def _lifecycle_thread_body_with_phases_inner(self) -> None:
        """The phased lifecycle. See ``_lifecycle_thread_body``."""
        # ------ Phase 1 : construct session resources -----
        try:
            self._construct_session_resources_under_lifecycle_thread()
        except BaseException as construction_exc:  # noqa: BLE001
            self._record_fatal_and_release_partial_session(
                construction_exc,
                origin_description=(
                    "while constructing per-session resources "
                    "(validation / folder provisioning / worker thread "
                    "spawn)"
                ),
            )
            return

        with self._state_lock:
            self._transition_state_under_lock(
                from_states={
                    RecordingLifecycleState.PREPARING_SESSION_RESOURCES,
                },
                to_state=(
                    RecordingLifecycleState.ACTIVELY_RECORDING_DISPLAY_1
                ),
            )

        # ------ Phase 2 : run until graceful_stop_event or worker death ----
        worker_thread_self_signaled_failure: Optional[BaseException] = (
            self._wait_for_stop_or_worker_failure_under_lifecycle_thread()
        )

        # ------ Phase 3 : finalize (always reached) ---------------------
        # At this point the operator (or a worker self-failure) has set
        # the graceful_stop_event. Either:
        #   (a) operator pressed Stop -> state was already transitioned
        #       to FINALIZING_OUTPUT_AND_JOINING_WORKERS by
        #       request_graceful_stop_and_finalize(),
        #   (b) operator pressed Force-Cancel after Stop -> state was
        #       further transitioned to FORCE_CANCELLATION_IN_PROGRESS,
        #   (c) worker self-failure -> we transition to FINALIZING now,
        #       because the operator did not press anything.
        with self._state_lock:
            if (
                self._current_state
                == RecordingLifecycleState.ACTIVELY_RECORDING_DISPLAY_1
                or self._current_state
                == RecordingLifecycleState.PAUSED_BY_OPERATOR
            ):
                session = self._require_active_session_under_lock(
                    operation_name="auto-finalize-after-worker-failure",
                )
                self._transition_state_under_lock(
                    from_states={
                        RecordingLifecycleState
                        .ACTIVELY_RECORDING_DISPLAY_1,
                        RecordingLifecycleState.PAUSED_BY_OPERATOR,
                    },
                    to_state=(
                        RecordingLifecycleState
                        .FINALIZING_OUTPUT_AND_JOINING_WORKERS
                    ),
                )
                session.finalization_started_at_monotonic_seconds = (
                    time.monotonic()
                )

        joining_outcome_exception: Optional[BaseException] = (
            self._join_workers_under_lifecycle_thread()
        )

        # Resolve the canonical fatal exception (if any). Order of
        # precedence: worker self-signaled failure (most informative)
        # > join timeout / worker exception observed at join time.
        consolidated_fatal_exception: Optional[BaseException] = (
            worker_thread_self_signaled_failure
            or joining_outcome_exception
        )

        # ------ Phase 4 : write metadata + release resources ----------
        session = self._active_session_resources
        if session is None:
            # Should be impossible (Phase 1 set it; we never cleared
            # it). Defensive belt-and-braces.
            self._record_fatal_and_release_partial_session(
                InconsistentControllerStateAssertionError(
                    "Lifecycle thread reached Phase 4 with no active "
                    "session resources bundle. Programming defect."
                ),
                origin_description=(
                    "at finalize time (active session resources "
                    "unexpectedly cleared)"
                ),
            )
            return
        self._write_session_metadata_under_lifecycle_thread(
            session,
            consolidated_fatal_exception=consolidated_fatal_exception,
        )
        # Release the bundle's auxiliary handles (log file, CSV file).
        # Worker threads are already joined (or abandoned via force-
        # cancel timeout); the bundle does not own a release path for
        # them.
        try:
            session.release_idempotently(
                fallback_logger=self._bootstrap_logger,
            )
        except BaseException as release_exc:  # noqa: BLE001
            # Releasing should never raise (release_idempotently
            # swallows). If it does, log and continue to the state
            # transition - we must clear _active_session_resources
            # before the IDLE/ERROR transition or the invariant check
            # will fail.
            self._bootstrap_logger.error(
                f"_ActiveRecordingSessionResources.release_idempotently "
                f"raised: {release_exc!r}"
            )

        # Publish the scratch session folder into the Videos Known
        # Folder. This is the second half of the scratch-then-publish
        # pipeline: by deferring the network/SMB copy out of the
        # encoder's hot path, we get a single sequential bulk-copy
        # over SMB (which corporate folder-redirected NAS shares do
        # support) instead of the fragmented-MP4 muxer's mid-stream
        # seek-back-and-overwrite pattern (which they routinely do
        # not). Must run *after* release_idempotently so every file
        # handle into the scratch folder is closed.
        if session.artifact_paths is not None:
            try:
                session.artifact_paths = (
                    move_session_outputs_from_temporary_scratch_into_videos_library(
                        session.artifact_paths
                    )
                )
            except SessionPublicationToVideosLibraryError as publish_exc:
                self._bootstrap_logger.error(
                    "Publishing the per-session scratch folder to the "
                    "Microsoft Windows Videos Known Folder failed; the "
                    "recording remains intact at the scratch source "
                    f"folder. Underlying exception: {publish_exc}"
                )
                # If the recording itself succeeded but publication
                # failed, promote the publication failure to the
                # consolidated fatal exception so the GUI surfaces it
                # in the FATAL_ERROR_OBSERVED state (the operator
                # otherwise has no way to learn that their recording
                # is sitting in the scratch folder rather than the
                # expected Videos Library location).
                if consolidated_fatal_exception is None:
                    consolidated_fatal_exception = publish_exc

        with self._state_lock:
            self._most_recently_completed_session_artifact_paths = (
                session.artifact_paths
            )
            self._active_session_resources = None
            if consolidated_fatal_exception is None:
                self._transition_state_under_lock(
                    from_states={
                        RecordingLifecycleState
                        .FINALIZING_OUTPUT_AND_JOINING_WORKERS,
                        RecordingLifecycleState
                        .FORCE_CANCELLATION_IN_PROGRESS,
                    },
                    to_state=(
                        RecordingLifecycleState
                        .IDLE_AWAITING_OPERATOR_START
                    ),
                )
            else:
                self._latest_fatal_exception = (
                    consolidated_fatal_exception
                )
                self._transition_state_under_lock(
                    from_states={
                        RecordingLifecycleState
                        .FINALIZING_OUTPUT_AND_JOINING_WORKERS,
                        RecordingLifecycleState
                        .FORCE_CANCELLATION_IN_PROGRESS,
                    },
                    to_state=(
                        RecordingLifecycleState.FATAL_ERROR_OBSERVED
                    ),
                )
            self._lifecycle_thread = None

    # ---------- Lifecycle thread helpers ----------------

    def _construct_session_resources_under_lifecycle_thread(self) -> None:
        """Populate the empty session bundle. Raises on any failure."""
        with self._state_lock:
            session = self._require_active_session_under_lock(
                operation_name="construct",
            )

        # Re-validate the display configuration. The operator may have
        # plugged or unplugged a display between startup validation and
        # pressing Start.
        validated = validate_dual_extend_mode_display_configuration()
        session.validated_display_config = validated

        videos_folder = (
            _resolve_current_user_videos_known_folder_path()
        )
        temporary_directory_root = (
            _resolve_per_user_temporary_directory_root_path()
        )
        session.artifact_paths = provision_fresh_per_session_output_folder(
            temporary_directory_root=temporary_directory_root,
            videos_known_folder_root=videos_folder,
        )
        session.session_started_at_local_datetime = (
            _datetime_module.datetime.now()
        )

        # Per-session logger writing to the session's text log file.
        per_session_logger = logging.getLogger(
            f"{APP_INTERNAL_NAME}.session."
            f"{session.artifact_paths.iso8601_session_id}"
        )
        # Clear any pre-existing handlers from a previous session that
        # may have shared the logger name (it shouldn't due to the
        # session-id suffix, but defensive).
        for prior_handler in list(per_session_logger.handlers):
            per_session_logger.removeHandler(prior_handler)
        per_session_logger.setLevel(logging.DEBUG)
        per_session_logger.propagate = False
        log_file_handle = open(
            session.artifact_paths.text_log_file_path,
            mode="w",
            encoding="utf-8",
            buffering=1,  # line-buffered
        )
        session.per_session_log_file_handle = log_file_handle
        log_handler = logging.StreamHandler(log_file_handle)
        log_handler.setFormatter(
            logging.Formatter(
                fmt=(
                    "%(asctime)s.%(msecs)03d %(levelname)-7s "
                    "[%(threadName)s] %(message)s"
                ),
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        per_session_logger.addHandler(log_handler)
        session.per_session_logger = per_session_logger

        per_session_logger.info(
            f"Starting recording session "
            f"'{session.artifact_paths.iso8601_session_id}'. Display 1 "
            f"capture region: "
            f"{validated.microsoft_windows_display_1_primary_to_be_recorded.bounding_rectangle_width}"
            f"x"
            f"{validated.microsoft_windows_display_1_primary_to_be_recorded.bounding_rectangle_height}"
            f" px at "
            f"({validated.microsoft_windows_display_1_primary_to_be_recorded.bounding_rectangle_left},"
            f"{validated.microsoft_windows_display_1_primary_to_be_recorded.bounding_rectangle_top}). "
            f"Target frame rate: {TARGET_OUTPUT_FRAMES_PER_SECOND} fps."
        )

        # Persist the display configuration snapshot.
        with open(
            session.artifact_paths.display_configuration_json_file_path,
            mode="w",
            encoding="utf-8",
        ) as display_config_file:
            json.dump(
                validated.as_jsonable_dict(),
                display_config_file,
                indent=2,
            )

        # Per-frame CSV log.
        csv_file_handle = open(
            session.artifact_paths.per_frame_log_csv_file_path,
            mode="w",
            encoding="utf-8",
            newline="",
            buffering=1,
        )
        session.per_frame_csv_file_handle = csv_file_handle
        csv_writer = csv.writer(csv_file_handle)
        csv_writer.writerow(
            [
                "presentation_timestamp_frames",
                "monotonic_capture_instant_seconds",
                "total_paused_wall_clock_seconds_at_capture",
            ]
        )
        session.per_frame_csv_writer = csv_writer

        # Spawn the encoder worker FIRST so it's ready to consume.
        encoder_worker = _IntelQuickSyncVideoFragmentedMp4EncoderWorker(
            output_video_file_path=(
                session.artifact_paths.fragmented_mp4_video_file_path
            ),
            target_frame_width_pixels=(
                validated
                .microsoft_windows_display_1_primary_to_be_recorded
                .bounding_rectangle_width
            ),
            target_frame_height_pixels=(
                validated
                .microsoft_windows_display_1_primary_to_be_recorded
                .bounding_rectangle_height
            ),
            inbound_frame_queue=session.inter_thread_frame_queue,
            worker_thread_logger=per_session_logger,
            force_cancel_event=session.force_cancel_event,
        )
        encoder_worker.start()
        session.encoder_worker = encoder_worker

        capture_worker = _SingleDisplayPrimaryMonitorCaptureWorker(
            primary_display_to_capture=(
                validated
                .microsoft_windows_display_1_primary_to_be_recorded
            ),
            outbound_frame_queue=session.inter_thread_frame_queue,
            operator_stop_event=session.graceful_stop_event,
            operator_pause_event=session.operator_pause_event,
            worker_thread_logger=per_session_logger,
            per_frame_csv_writer_lock=session.per_frame_csv_writer_lock,
            per_frame_csv_writer=csv_writer,
            per_frame_csv_file_handle=csv_file_handle,
        )
        capture_worker.start()
        session.capture_worker = capture_worker

    def _wait_for_stop_or_worker_failure_under_lifecycle_thread(
        self,
    ) -> Optional[BaseException]:
        """Wait until graceful_stop_event OR a worker dies. Return worker exc if any."""
        session = self._active_session_resources
        # Type-assertion: Phase 1 succeeded, so this must be populated.
        assert session is not None
        assert session.capture_worker is not None
        assert session.encoder_worker is not None
        per_session_logger = (
            session.per_session_logger or self._bootstrap_logger
        )
        poll = self._LIFECYCLE_THREAD_WATCHDOG_POLL_INTERVAL_SECONDS
        while not session.graceful_stop_event.wait(timeout=poll):
            # Worker watchdog: if either worker died, surface its
            # fatal exception (if any) and trigger an automatic
            # finalize so the GUI can offer the operator a clean
            # error path. Encoder failures take precedence over
            # capture failures because an encoder failure typically
            # causes the capture queue to fill, leading to a
            # downstream EncoderBackPressureError from the capture
            # worker - the encoder's exception is the actual root
            # cause and the more informative report.
            capture_w = session.capture_worker
            encoder_w = session.encoder_worker
            encoder_dead = not encoder_w.is_alive()
            capture_dead = not capture_w.is_alive()
            if encoder_dead or capture_dead:
                # Pick the canonical exception. Encoder first.
                if encoder_dead and encoder_w.fatal_exception is not None:
                    canonical_exc: BaseException = (
                        encoder_w.fatal_exception
                    )
                    self._log_unexpected_worker_death(
                        per_session_logger,
                        "encoder",
                        canonical_exc,
                    )
                elif capture_dead and capture_w.fatal_exception is not None:
                    canonical_exc = capture_w.fatal_exception
                    self._log_unexpected_worker_death(
                        per_session_logger,
                        "capture",
                        canonical_exc,
                    )
                elif encoder_dead:
                    # Encoder exited without an exception while we
                    # never asked it to stop - this should be
                    # impossible.
                    canonical_exc = EncoderPipelineError(
                        "Encoder worker thread exited with no stored "
                        "fatal exception and no operator stop request. "
                        "This is an internal defect."
                    )
                    self._log_unexpected_worker_death(
                        per_session_logger,
                        "encoder",
                        canonical_exc,
                    )
                else:  # capture_dead with no stored exception
                    canonical_exc = EncoderPipelineError(
                        "Capture worker thread exited with no stored "
                        "fatal exception and no operator stop request. "
                        "This is an internal defect."
                    )
                    self._log_unexpected_worker_death(
                        per_session_logger,
                        "capture",
                        canonical_exc,
                    )
                session.graceful_stop_event.set()
                if session.operator_pause_event.is_set():
                    session.operator_pause_event.clear()
                return canonical_exc
        # graceful_stop_event was set by the operator pressing Stop;
        # no worker self-failure observed.
        return None

    @staticmethod
    def _log_unexpected_worker_death(
        logger_to_use: logging.Logger,
        which_worker_label: str,
        captured_exception: BaseException,
    ) -> None:
        logger_to_use.error(
            f"The {which_worker_label} worker thread died "
            f"spontaneously; triggering automatic finalize. "
            f"Exception: {type(captured_exception).__name__}: "
            f"{captured_exception}"
        )

    def _join_workers_under_lifecycle_thread(
        self,
    ) -> Optional[BaseException]:
        session = self._active_session_resources
        assert session is not None
        assert session.capture_worker is not None
        assert session.encoder_worker is not None
        per_session_logger = (
            session.per_session_logger or self._bootstrap_logger
        )

        def current_join_timeout() -> float:
            with self._state_lock:
                if (
                    self._current_state
                    == RecordingLifecycleState
                    .FORCE_CANCELLATION_IN_PROGRESS
                ):
                    return (
                        self
                        ._FORCE_CANCEL_WORKER_JOIN_TIMEOUT_SECONDS
                    )
            return self._GRACEFUL_WORKER_JOIN_TIMEOUT_SECONDS

        capture_w = session.capture_worker
        encoder_w = session.encoder_worker

        capture_w.join(timeout=current_join_timeout())
        if capture_w.is_alive():
            per_session_logger.error(
                "Capture worker did not exit within the join timeout; "
                "abandoning it (it is a daemon thread and will die "
                "with the process)."
            )
            return WorkerThreadJoinTimeoutError(
                f"The capture worker thread did not exit within the "
                f"join timeout of {current_join_timeout():.1f} seconds. "
                f"Abandoning the thread; the fragmented MP4 output is "
                f"finalized through the last muxed fragment."
            )

        encoder_w.join(timeout=current_join_timeout())
        if encoder_w.is_alive():
            per_session_logger.error(
                "Encoder worker did not exit within the join timeout; "
                "abandoning it (daemon thread)."
            )
            return WorkerThreadJoinTimeoutError(
                f"The encoder worker thread did not exit within the "
                f"join timeout of {current_join_timeout():.1f} seconds. "
                f"Abandoning the thread; the fragmented MP4 output is "
                f"finalized through the last muxed fragment."
            )

        # Both joined. Surface any deferred worker exception, with
        # encoder taking precedence: an encoder failure causes the
        # capture queue to fill, leading to a downstream
        # EncoderBackPressureError from the capture worker; the
        # encoder's exception is the actual root cause.
        if encoder_w.fatal_exception is not None:
            return encoder_w.fatal_exception
        if capture_w.fatal_exception is not None:
            return capture_w.fatal_exception
        return None

    def _write_session_metadata_under_lifecycle_thread(
        self,
        session: _ActiveRecordingSessionResources,
        *,
        consolidated_fatal_exception: Optional[BaseException],
    ) -> None:
        """Write the session metadata JSON. Tolerates missing fields on early failure."""
        try:
            end_local_datetime = _datetime_module.datetime.now()
            display_config = session.validated_display_config
            artifact_paths = session.artifact_paths
            if artifact_paths is None:
                # We never got past initial folder provisioning, so
                # there is no place to write metadata. The bootstrap
                # logger has already recorded the failure.
                return
            display_1 = (
                display_config
                .microsoft_windows_display_1_primary_to_be_recorded
                if display_config is not None
                else None
            )
            capture_w = session.capture_worker
            encoder_w = session.encoder_worker
            payload: dict[str, Any] = {
                "schema_version": (
                    APP_SCHEMA_VERSION_FOR_FORENSIC_ARTIFACTS
                ),
                "app_internal_name": APP_INTERNAL_NAME,
                "app_display_title": APP_DISPLAY_TITLE,
                "iso8601_session_id": artifact_paths.iso8601_session_id,
                "session_started_at_local_datetime_iso8601": (
                    session
                    .session_started_at_local_datetime.isoformat()
                    if session.session_started_at_local_datetime
                    is not None
                    else None
                ),
                "session_ended_at_local_datetime_iso8601": (
                    end_local_datetime.isoformat()
                ),
                "terminated_successfully": (
                    consolidated_fatal_exception is None
                ),
                "force_cancel_observed": (
                    session.force_cancel_event.is_set()
                ),
                "python_version": platform.python_version(),
                "platform_descriptor": platform.platform(),
                "host_node_name": platform.node(),
                "pyav_version": av.__version__,
                "python_mss_version": getattr(
                    mss, "__version__", "unknown"
                ),
                "numpy_version": np.__version__,
                "ffmpeg_encoder_name": (
                    INTEL_QUICK_SYNC_VIDEO_H264_ENCODER_FFMPEG_NAME
                ),
                "ffmpeg_container_format": "mp4",
                "ffmpeg_container_movflags": (
                    FRAGMENTED_MP4_MOVFLAGS_VALUE
                ),
                "qsv_global_quality": INTEL_QSV_GLOBAL_QUALITY_VALUE,
                "qsv_preset": INTEL_QSV_PRESET_VALUE,
                "qsv_look_ahead": INTEL_QSV_LOOK_AHEAD_VALUE,
                "qsv_async_depth": INTEL_QSV_ASYNC_DEPTH_VALUE,
                "qsv_max_b_frames": INTEL_QSV_MAX_B_FRAMES_VALUE,
                "target_output_frames_per_second": (
                    TARGET_OUTPUT_FRAMES_PER_SECOND
                ),
                "keyframe_interval_frames": (
                    KEYFRAME_INTERVAL_FRAMES
                ),
                "captured_display_1_pixel_width": (
                    display_1.bounding_rectangle_width
                    if display_1 is not None
                    else None
                ),
                "captured_display_1_pixel_height": (
                    display_1.bounding_rectangle_height
                    if display_1 is not None
                    else None
                ),
                "captured_display_1_win32_device_path": (
                    display_1.win32_device_path
                    if display_1 is not None
                    else None
                ),
                "captured_display_1_friendly_device_string": (
                    display_1.win32_friendly_device_string
                    if display_1 is not None
                    else None
                ),
                "frames_successfully_captured_total": (
                    capture_w.frames_successfully_captured_so_far
                    if capture_w is not None
                    else 0
                ),
                "frames_successfully_encoded_total": (
                    encoder_w.frames_successfully_encoded_so_far
                    if encoder_w is not None
                    else 0
                ),
                "output_packets_successfully_muxed_total": (
                    encoder_w.output_packets_successfully_muxed_so_far
                    if encoder_w is not None
                    else 0
                ),
                "intel_onevpl_runtime_pts_disagreements_observed_total": (
                    encoder_w.total_intel_onevpl_pts_disagreements_observed
                    if encoder_w is not None
                    else 0
                ),
                "frames_discarded_by_force_cancel_total": (
                    encoder_w.frames_discarded_due_to_force_cancel_so_far
                    if encoder_w is not None
                    else 0
                ),
                "total_paused_wall_clock_seconds": (
                    capture_w.total_paused_wall_clock_seconds
                    if capture_w is not None
                    else 0.0
                ),
                # Output paths are recorded at the *publication* target
                # (underneath the Videos Known Folder) rather than at
                # the per-user temporary scratch location, because
                # session.json is itself copied into the publication
                # target on Stop, and an operator opening that JSON
                # expects every path it references to resolve next to
                # the JSON file. If publication fails the recording
                # remains intact at the scratch source folder; the GUI
                # surfaces that path separately.
                "output_video_file_path": str(
                    artifact_paths.final_publication_parent_session_folder
                    / PER_SESSION_VIDEO_FILE_NAME
                ),
                "output_per_frame_log_csv_file_path": str(
                    artifact_paths.final_publication_parent_session_folder
                    / PER_SESSION_PER_FRAME_LOG_CSV_FILE_NAME
                ),
                "output_display_configuration_json_file_path": str(
                    artifact_paths.final_publication_parent_session_folder
                    / PER_SESSION_DISPLAY_CONFIG_JSON_FILE_NAME
                ),
                "output_text_log_file_path": str(
                    artifact_paths.final_publication_parent_session_folder
                    / PER_SESSION_TEXT_LOG_FILE_NAME
                ),
                "temporary_scratch_session_folder_used_during_muxing": (
                    str(artifact_paths.parent_session_folder)
                ),
            }
            if consolidated_fatal_exception is not None:
                payload["fatal_exception_class"] = type(
                    consolidated_fatal_exception
                ).__name__
                payload["fatal_exception_message"] = str(
                    consolidated_fatal_exception
                )
                payload["fatal_exception_traceback"] = "".join(
                    traceback.format_exception(
                        consolidated_fatal_exception
                    )
                )
            with open(
                artifact_paths.session_metadata_json_file_path,
                mode="w",
                encoding="utf-8",
            ) as metadata_file:
                json.dump(payload, metadata_file, indent=2)
        except BaseException as metadata_write_exc:  # noqa: BLE001
            # Metadata is forensic-grade nice-to-have; never fail the
            # whole session over it. We log via the bootstrap logger
            # so something is recorded on stderr regardless of whether
            # the per-session logger / log file are still alive.
            self._bootstrap_logger.error(
                "Failed to write session metadata JSON: "
                f"{type(metadata_write_exc).__name__}: "
                f"{metadata_write_exc}"
            )

    def _record_fatal_and_release_partial_session(
        self,
        fatal_exception: BaseException,
        *,
        origin_description: str,
    ) -> None:
        """On any phase-1 failure: log, release partial resources, transition to ERROR."""
        self._bootstrap_logger.error(
            f"Fatal exception in recording lifecycle thread "
            f"{origin_description}:\n"
            + "".join(traceback.format_exception(fatal_exception))
        )
        with self._state_lock:
            session = self._active_session_resources
            if session is not None:
                # Best-effort: stop any worker threads that did get
                # spawned, then join them briefly. They are daemon
                # threads so worst case they leak until process exit.
                session.graceful_stop_event.set()
                session.force_cancel_event.set()
                for worker in (
                    session.capture_worker,
                    session.encoder_worker,
                ):
                    if worker is not None and worker.is_alive():
                        worker.join(timeout=2.0)
                try:
                    session.release_idempotently(
                        fallback_logger=self._bootstrap_logger,
                    )
                except Exception as release_exc:  # noqa: BLE001
                    self._bootstrap_logger.error(
                        f"Partial-session release_idempotently raised: "
                        f"{release_exc!r}"
                    )
                # If folder provisioning *did* succeed before the
                # partial failure, the scratch folder may contain a
                # session.log with the failure traceback and a
                # display_configuration.json with the host display
                # state at the moment of failure. Publish those into
                # the Videos Known Folder so the operator can review
                # the forensic artifacts in the same location
                # successful sessions land in.
                if session.artifact_paths is not None:
                    try:
                        session.artifact_paths = (
                            move_session_outputs_from_temporary_scratch_into_videos_library(
                                session.artifact_paths
                            )
                        )
                    except SessionPublicationToVideosLibraryError as publish_exc:
                        self._bootstrap_logger.error(
                            "Publishing the partial-session scratch "
                            "folder to the Microsoft Windows Videos "
                            "Known Folder failed; the partial recording "
                            "and any forensic artifacts remain intact "
                            "at the scratch source folder. Underlying "
                            f"exception: {publish_exc}"
                        )
                    self._most_recently_completed_session_artifact_paths = (
                        session.artifact_paths
                    )
                self._active_session_resources = None
            self._latest_fatal_exception = fatal_exception
            self._current_state = (
                RecordingLifecycleState.FATAL_ERROR_OBSERVED
            )
            self._lifecycle_thread = None
            # Invariant check should now pass (no session, ERROR state).
            self._assert_state_and_resources_invariant_under_lock()


# ============================================================================
# Section 16b - Operator GUI (Tk on Display 2)
# ============================================================================


# State -> button configuration table. Drives every GUI refresh from a
# single source of truth so no state has ad-hoc button logic. Format:
#   start_enabled, pause_label_or_none, pause_enabled, stop_enabled,
#   force_cancel_visible, force_cancel_enabled_after_seconds
@dataclasses.dataclass(frozen=True)
class _GuiButtonConfigForState:
    start_button_enabled: bool
    pause_button_label_text: str
    pause_button_enabled: bool
    stop_button_enabled: bool
    force_cancel_button_visible: bool
    force_cancel_button_minimum_enable_delay_seconds: float


_GUI_BUTTON_CONFIGURATION_PER_STATE: Final[
    dict[RecordingLifecycleState, _GuiButtonConfigForState]
] = {
    RecordingLifecycleState.IDLE_AWAITING_OPERATOR_START: (
        _GuiButtonConfigForState(
            start_button_enabled=True,
            pause_button_label_text="Pause",
            pause_button_enabled=False,
            stop_button_enabled=False,
            force_cancel_button_visible=False,
            force_cancel_button_minimum_enable_delay_seconds=0.0,
        )
    ),
    RecordingLifecycleState.PREPARING_SESSION_RESOURCES: (
        _GuiButtonConfigForState(
            start_button_enabled=False,
            pause_button_label_text="Pause",
            pause_button_enabled=False,
            stop_button_enabled=False,
            force_cancel_button_visible=False,
            force_cancel_button_minimum_enable_delay_seconds=0.0,
        )
    ),
    RecordingLifecycleState.ACTIVELY_RECORDING_DISPLAY_1: (
        _GuiButtonConfigForState(
            start_button_enabled=False,
            pause_button_label_text="Pause",
            pause_button_enabled=True,
            stop_button_enabled=True,
            force_cancel_button_visible=False,
            force_cancel_button_minimum_enable_delay_seconds=0.0,
        )
    ),
    RecordingLifecycleState.PAUSED_BY_OPERATOR: (
        _GuiButtonConfigForState(
            start_button_enabled=False,
            pause_button_label_text="Resume",
            pause_button_enabled=True,
            stop_button_enabled=True,
            force_cancel_button_visible=False,
            force_cancel_button_minimum_enable_delay_seconds=0.0,
        )
    ),
    RecordingLifecycleState.FINALIZING_OUTPUT_AND_JOINING_WORKERS: (
        _GuiButtonConfigForState(
            start_button_enabled=False,
            pause_button_label_text="Pause",
            pause_button_enabled=False,
            stop_button_enabled=False,
            force_cancel_button_visible=True,
            # Five seconds is generous: graceful finalize is typically
            # well under two seconds on this hardware. If we're past
            # five, something is genuinely stuck.
            force_cancel_button_minimum_enable_delay_seconds=5.0,
        )
    ),
    RecordingLifecycleState.FORCE_CANCELLATION_IN_PROGRESS: (
        _GuiButtonConfigForState(
            start_button_enabled=False,
            pause_button_label_text="Pause",
            pause_button_enabled=False,
            stop_button_enabled=False,
            force_cancel_button_visible=True,
            # Already cancelling - button stays visible (so the
            # operator sees it) but disabled.
            force_cancel_button_minimum_enable_delay_seconds=float("inf"),
        )
    ),
    RecordingLifecycleState.FATAL_ERROR_OBSERVED: (
        _GuiButtonConfigForState(
            start_button_enabled=True,
            pause_button_label_text="Pause",
            pause_button_enabled=False,
            stop_button_enabled=False,
            force_cancel_button_visible=False,
            force_cancel_button_minimum_enable_delay_seconds=0.0,
        )
    ),
}


class _OperatorTkinterGui:
    """The operator GUI window placed on Microsoft Windows Display 2.

    The widget tree is assembled entirely from ttk widgets and arranged
    via the grid geometry manager with explicit column and row weights
    and ``sticky`` parameters, so the layout behaves the same way a
    wxWidgets BoxSizer + GridSizer + stretch-spacer composition behaves:
    every widget grows or shrinks predictably as the operator resizes
    the window, the button cluster stays anchored to the bottom of the
    content area, and the status / details labels reflow their text to
    fill the current window width.

    No dimension below this point is a hard-coded pixel value or a
    hard-coded typographic point size. Window initial size, widget
    padding, label wrap width, and button minimum width are all
    expressed as a multiple of the platform's TkDefaultFont em width or
    line height. Microsoft Windows display scaling and the "Make text
    bigger" accessibility setting are both reflected in TkDefaultFont's
    point size at process start, so the GUI scales coherently with
    whatever the operator has configured.

    Responsibilities (deliberately small):

      * Translate operator button clicks into non-blocking controller
        method calls; if the call raises ``ScreenRecorderError``
        synchronously, surface it via a ``tk.messagebox`` and refresh
        the GUI.
      * Periodically (every ``GUI_STATUS_POLL_INTERVAL_MILLISECONDS``)
        refresh widget state from the controller's current state and
        live statistics. Detect newly-observed asynchronous fatal
        exceptions and surface them via a ``tk.messagebox`` exactly
        once.
      * Refuse the OS close-window (X) button while a recording is in
        flight; offer the operator a force-close option during
        finalize.
    """

    def __init__(
        self,
        recording_controller: RecordingSessionController,
        validated_display_config_for_initial_placement: (
            ValidatedDualExtendModeDisplayConfiguration
        ),
        resolved_videos_known_folder_path: Path,
    ) -> None:
        self._controller = recording_controller
        self._initial_display_config = (
            validated_display_config_for_initial_placement
        )
        self._videos_known_folder_path = (
            resolved_videos_known_folder_path
        )
        self._tk_root: Optional[tk.Tk] = None
        self._status_text_string_var: Optional[tk.StringVar] = None
        self._details_text_string_var: Optional[tk.StringVar] = None
        self._start_button: Optional[ttk.Button] = None
        self._pause_resume_button: Optional[ttk.Button] = None
        self._stop_button: Optional[ttk.Button] = None
        self._force_cancel_button: Optional[ttk.Button] = None
        # True while the force-cancel button is grid()'d into the
        # buttons sub-frame, False while it is grid_remove()'d. The
        # grid_remove() API preserves every grid setting (row, column,
        # sticky, padx, pady) so toggling visibility is cheap and the
        # layout snaps back identically when the button reappears.
        self._force_cancel_button_grid_visible: bool = False
        # Tracks the most recently-displayed asynchronous fatal
        # exception so we don't pop up the same messagebox repeatedly
        # from the poll loop.
        self._most_recently_displayed_fatal_exception_id: Optional[int] = (
            None
        )

    # ============== Public API: blocking GUI run ==============

    def construct_and_run_blocking(self) -> None:
        self._tk_root = tk.Tk()
        self._tk_root.title(GUI_WINDOW_TITLE_TEXT)

        # Withdraw the root window immediately on construction so it is
        # invisible during every subsequent sizing / layout step below
        # (DPI re-scaling, ttk theme activation, widget construction,
        # the ``update_idletasks`` + ``update`` event-pump pair that
        # forces the wraplength-bound labels' ``<Configure>`` callbacks
        # to settle, and the final position-only ``geometry`` call).
        # Without this, the operator can briefly see the window flash
        # onto the screen at the wrong size, at the wrong position, or
        # at default (top-left of Display 1) coordinates. We
        # ``deiconify`` exactly once, at the very end of this method,
        # after the layout has stabilized and the window has been
        # placed on Microsoft Windows Display 2.
        self._tk_root.withdraw()

        # CRITICAL ORDERING: before *any* ttk widget is constructed
        # and before *any* font measurement is taken, re-scale Tcl/Tk
        # to the effective DPI of the Microsoft Windows monitor we
        # intend to place this window onto (Display 2). This is the
        # only way to make ``winfo_reqheight()`` and ttk widget
        # metrics agree with the physical pixels Display 2 will draw,
        # because Tcl/Tk's Windows backend captures screen DPI exactly
        # once at ``Tk_Init`` time via ``GetDC(NULL); GetDeviceCaps``
        # - and that desktop-DC value is, per Microsoft's own docs:
        #     https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/dpi-related-apis-and-registry-settings
        # "the DPI of the primary display at the time the Windows
        # session was started", regardless of which monitor a window
        # ultimately lives on. Without this re-scaling step, a window
        # destined for Display 2 is measured against Display 1's DPI
        # and the layout is off by exactly the ratio of those two
        # effective DPI values, which is precisely how the original
        # symptom (the bottom button clipped off by ~half a button
        # height) manifested on hosts whose two attached monitors are
        # at different accessibility scale settings. The open Tcl/Tk
        # ticket documenting this gap:
        #     https://core.tcl-lang.org/tk/tktview?name=a9ee44102b
        microsoft_windows_display_2 = (
            self._initial_display_config
            .microsoft_windows_display_2_secondary_for_operator_gui
        )
        microsoft_windows_display_2_center_x_pixel = (
            microsoft_windows_display_2.bounding_rectangle_left
            + microsoft_windows_display_2.bounding_rectangle_width // 2
        )
        microsoft_windows_display_2_center_y_pixel = (
            microsoft_windows_display_2.bounding_rectangle_top
            + microsoft_windows_display_2.bounding_rectangle_height // 2
        )
        (
            display_2_effective_dpi_x,
            display_2_effective_dpi_y,
        ) = _query_effective_dpi_pixels_per_inch_of_monitor_containing_pixel(
            virtual_screen_x_pixel=(
                microsoft_windows_display_2_center_x_pixel
            ),
            virtual_screen_y_pixel=(
                microsoft_windows_display_2_center_y_pixel
            ),
        )
        # Tcl/Tk's ``tk scaling`` is a single scalar (pixels per
        # point). Microsoft Windows allows the X and Y effective DPI
        # values to differ on a per-monitor basis in theory, but the
        # ``GetDeviceCaps(LOGPIXELSX/LOGPIXELSY)`` API has returned
        # equal X and Y values on every shipping Microsoft Windows
        # 10/11 build since at least 2015, and Microsoft's own UI
        # framework treats them as the same. We drive Tk's scaling
        # off the Y axis (it is what TkWinDisplayChanged samples).
        _apply_target_monitor_effective_dpi_to_tkinter_root(
            tk_root=self._tk_root,
            target_monitor_effective_dpi_pixels_per_inch=(
                display_2_effective_dpi_y
            ),
        )

        # Activate the modern ttk Microsoft Windows theme so labels,
        # buttons and separators get the standard Windows 10 / 11
        # visual style. We try the Windows-native themes in priority
        # order and silently fall back to the cross-platform 'clam'
        # theme if every native theme is unavailable (eg under Wine).
        try:
            ttk_style = ttk.Style(self._tk_root)
            available_ttk_theme_names = set(ttk_style.theme_names())
            for candidate_theme_name in (
                "vista",
                "xpnative",
                "winnative",
                "clam",
            ):
                if candidate_theme_name in available_ttk_theme_names:
                    ttk_style.theme_use(candidate_theme_name)
                    break
        except tk.TclError:
            pass

        # Tool-window decoration: a slim title bar with only the close
        # box, matching the look of a Microsoft Windows tool palette.
        try:
            self._tk_root.attributes("-toolwindow", True)
        except tk.TclError:
            pass

        # Intercept the OS close (X) button so a recording cannot be
        # quietly abandoned mid-stream.
        self._tk_root.protocol(
            "WM_DELETE_WINDOW",
            self._on_operating_system_close_window_requested,
        )

        self._construct_all_widgets()

        # Allow the operator to resize the window. The minimum size is
        # the natural requested size of the widget tree after layout -
        # below that, ttk would clip labels or buttons, which was the
        # exact failure mode the previous fixed-pixel layout exhibited
        # under non-default Microsoft Windows display scaling.
        self._tk_root.resizable(True, True)

        # Force the layout to fully settle BEFORE we read winfo_reqheight
        # for ``minsize``. Two passes are required, in this exact order,
        # for a specific reason rooted in Tcl/Tk's event model:
        #
        # ``update_idletasks`` (see ``generic/tkEvent.c::Tcl_DoOneEvent``
        # with the ``TCL_IDLE_EVENTS`` flag) processes only idle
        # callbacks, which is sufficient for the geometry manager's
        # negotiation to assign widths to every grid cell. But the
        # ``<Configure>`` events that fire as those widths change are
        # *not* idle callbacks - they are window-system events, queued
        # for the next ``Tcl_DoOneEvent(TCL_WINDOW_EVENTS)`` pass. From
        # the Tcl ``update`` man page verbatim:
        #
        #     "there are some kinds of updates that only happen in
        #      response to events, such as those triggered by window
        #      size changes; these updates will not occur in
        #      ``update idletasks``."
        #
        # Our content / status / details labels are bound to a
        # ``<Configure>``-driven wraplength updater
        # (``_bind_label_to_dynamic_wraplength``). The first
        # ``update_idletasks`` settles the grid cell widths; the
        # ``<Configure>`` events are then queued; ``update`` drains
        # them, the wraplength bindings fire, the labels potentially
        # re-flow taller, and the widget tree's ``winfo_reqheight``
        # grows accordingly. A second ``update_idletasks`` finalizes
        # any geometry re-negotiation triggered by the re-flow.
        #
        # Only then is the natural requested size stable. Reading
        # ``winfo_reqheight`` before this dance under-reports by
        # exactly the re-flow growth - which is the precise mechanism
        # behind the original "bottom half of the Stop button is
        # clipped" symptom on monitors whose pixel width drives a
        # narrower-than-initial-wraplength label re-flow.
        self._tk_root.update_idletasks()
        self._tk_root.update()
        self._tk_root.update_idletasks()

        natural_requested_window_width_pixels = (
            self._tk_root.winfo_reqwidth()
        )
        natural_requested_window_height_pixels = (
            self._tk_root.winfo_reqheight()
        )
        self._tk_root.minsize(
            natural_requested_window_width_pixels,
            natural_requested_window_height_pixels,
        )

        # Place the window on Microsoft Windows Display 2 using a
        # POSITION-ONLY ``geometry`` string (``+X+Y`` with no ``WxH``
        # prefix). Two distinct correctness properties depend on this:
        #
        # (1) The initial visible size will be exactly the natural
        #     requested size of the laid-out widget tree, because Tk's
        #     ``UpdateGeometryInfo`` in ``win/tkWinWm.c`` honors
        #     ``winPtr->reqWidth`` / ``reqHeight`` only when
        #     ``wmPtr->width == -1`` (its uninitialized sentinel). A
        #     ``WxH+X+Y`` geometry call writes a non-sentinel
        #     ``wmPtr->width``, locking the toplevel at the size we
        #     passed even if children later need more space.
        #
        # (2) After this method returns and the GUI mainloop begins
        #     polling controller state, every status-text update that
        #     adds a new line to the status or details label causes
        #     the labels' ``winfo_reqheight`` to grow. With the
        #     toplevel still at ``wmPtr->width == -1``,
        #     ``TopLevelReqProc`` (in ``tkWinWm.c``) re-schedules
        #     ``UpdateGeometryInfo``, which now uses the *new*
        #     ``reqHeight`` and grows the toplevel accordingly. A
        #     ``WxH+X+Y`` geometry call would have permanently
        #     disabled this auto-grow, leaving the operator with a
        #     window too small to show the longer status text - the
        #     exact symptom previously observed after a recorder
        #     fatal-error message inflates the status line.
        self._place_window_on_microsoft_windows_display_2(
            centering_estimated_window_width_pixels=(
                natural_requested_window_width_pixels
            ),
            centering_estimated_window_height_pixels=(
                natural_requested_window_height_pixels
            ),
        )

        # Reveal the now-fully-positioned, fully-sized window. After
        # this point any further layout-driven size growth is handled
        # by Tk's auto-grow path (see property (2) above), so the
        # operator never sees a flash or jitter.
        self._tk_root.deiconify()

        # Initial refresh + start the periodic poll.
        self._refresh_gui_for_current_state()
        self._tk_root.after(
            GUI_STATUS_POLL_INTERVAL_MILLISECONDS,
            self._periodic_poll_callback,
        )
        self._tk_root.mainloop()

    # ============== Widget construction ==============

    def _construct_all_widgets(self) -> None:
        """Build the entire widget tree using ttk + grid sizing.

        The layout is a single vertical column of widgets inside an
        outer content frame. Row indices and weights are explicit so
        the layout behaves identically whatever the operator's display
        scaling and font-size accessibility settings are:

          row 0 : title banner label              (weight=0, sticky="ew")
          row 1 : separator                        (weight=0, sticky="ew")
          row 2 : configuration description label  (weight=0, sticky="ew")
          row 3 : separator                        (weight=0, sticky="ew")
          row 4 : status label                     (weight=0, sticky="ew")
          row 5 : details label                    (weight=0, sticky="ew")
          row 6 : stretch spacer                   (weight=1, sticky="nsew")
          row 7 : separator                        (weight=0, sticky="ew")
          row 8 : buttons sub-frame                (weight=0, sticky="ew")

        The stretch spacer at row 6 is the wxWidgets
        ``AddStretchSpacer(1)`` pattern: it absorbs all vertical growth
        when the operator drags the window taller, keeping the button
        cluster anchored against the bottom edge of the content area.
        Every horizontal widget has ``sticky="ew"`` so it stretches
        with the window width.
        """
        assert self._tk_root is not None

        # Resolve TkDefaultFont and derive every dimension below from
        # its em width / line height. We deliberately do NOT hard-code
        # a pixel padding or a point size anywhere past this point.
        default_font = tk_font.nametofont("TkDefaultFont")
        em_width_pixels = max(1, default_font.measure("0"))
        outer_pad_pixels = max(
            1, int(round(em_width_pixels * GUI_OUTER_PADDING_EM_FRACTION))
        )
        inner_pad_pixels = max(
            1, int(round(em_width_pixels * GUI_INNER_PADDING_EM_FRACTION))
        )
        initial_wraplength_pixels = max(
            1,
            int(round(
                em_width_pixels * GUI_INITIAL_LABEL_WRAPLENGTH_EM
            )),
        )

        # Title banner font: same family as TkDefaultFont, bolded and
        # a few points larger. Positive point sizes scale with the
        # system DPI the same way TkDefaultFont does.
        default_font_point_size = default_font.cget("size")
        if default_font_point_size <= 0:
            # Negative sizes are pixel sizes; convert to a sensible
            # positive point-size baseline for the banner derivation.
            default_font_point_size = 10
        title_banner_font = tk_font.Font(
            self._tk_root,
            family=default_font.cget("family"),
            size=(
                default_font_point_size + GUI_TITLE_BANNER_EXTRA_POINTS
            ),
            weight="bold",
        )

        # Root window grid: single stretching column, single
        # stretching row that hosts the content frame.
        self._tk_root.columnconfigure(0, weight=1)
        self._tk_root.rowconfigure(0, weight=1)

        # The content frame's ``padding`` is the outer-margin gap
        # between the window edge and the first widget on every side.
        content_frame = ttk.Frame(
            self._tk_root,
            padding=(
                outer_pad_pixels,
                outer_pad_pixels,
                outer_pad_pixels,
                outer_pad_pixels,
            ),
        )
        content_frame.grid(row=0, column=0, sticky="nsew")
        content_frame.columnconfigure(0, weight=1)
        # Row 6 is the stretch spacer; all other rows are natural-sized.
        content_frame.rowconfigure(6, weight=1)

        # --- row 0: title banner label ----------------------------------
        title_banner_label = ttk.Label(
            content_frame,
            text=GUI_WINDOW_TITLE_TEXT,
            font=title_banner_font,
            anchor="w",
        )
        title_banner_label.grid(
            row=0,
            column=0,
            sticky="ew",
            pady=(0, inner_pad_pixels),
        )

        # --- row 1: separator -------------------------------------------
        ttk.Separator(content_frame, orient="horizontal").grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(0, inner_pad_pixels),
        )

        # --- row 2: configuration description label ---------------------
        d1 = (
            self._initial_display_config
            .microsoft_windows_display_1_primary_to_be_recorded
        )
        d2 = (
            self._initial_display_config
            .microsoft_windows_display_2_secondary_for_operator_gui
        )
        configuration_description_label = ttk.Label(
            content_frame,
            text=(
                "Recording target: Display 1 "
                "(Microsoft Windows primary)\n"
                f"  Device : {d1.win32_device_path}\n"
                f"  Native : {d1.bounding_rectangle_width}x"
                f"{d1.bounding_rectangle_height} px\n"
                "\n"
                "Operator GUI host: Display 2 "
                "(Microsoft Windows secondary)\n"
                f"  Device : {d2.win32_device_path}"
            ),
            justify="left",
            anchor="w",
        )
        configuration_description_label.grid(
            row=2,
            column=0,
            sticky="ew",
            pady=(0, inner_pad_pixels),
        )
        self._bind_label_to_dynamic_wraplength(
            configuration_description_label,
            initial_wraplength_pixels,
        )

        # --- row 3: separator -------------------------------------------
        ttk.Separator(content_frame, orient="horizontal").grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(0, inner_pad_pixels),
        )

        # --- row 4: status label ----------------------------------------
        self._status_text_string_var = tk.StringVar(value="")
        status_label = ttk.Label(
            content_frame,
            textvariable=self._status_text_string_var,
            justify="left",
            anchor="w",
        )
        status_label.grid(
            row=4,
            column=0,
            sticky="ew",
            pady=(0, inner_pad_pixels),
        )
        self._bind_label_to_dynamic_wraplength(
            status_label,
            initial_wraplength_pixels,
        )

        # --- row 5: details label ---------------------------------------
        self._details_text_string_var = tk.StringVar(value="")
        details_label = ttk.Label(
            content_frame,
            textvariable=self._details_text_string_var,
            justify="left",
            anchor="w",
        )
        details_label.grid(
            row=5,
            column=0,
            sticky="ew",
            pady=(0, inner_pad_pixels),
        )
        self._bind_label_to_dynamic_wraplength(
            details_label,
            initial_wraplength_pixels,
        )

        # --- row 6: vertical stretch spacer -----------------------------
        # Empty ttk.Frame whose row has weight=1; this absorbs every
        # vertical pixel the operator gains by dragging the window
        # taller, keeping the button cluster glued to the bottom edge
        # of the content area. (wxWidgets equivalent: AddStretchSpacer.)
        stretch_spacer_frame = ttk.Frame(content_frame)
        stretch_spacer_frame.grid(row=6, column=0, sticky="nsew")

        # --- row 7: separator above the buttons -------------------------
        ttk.Separator(content_frame, orient="horizontal").grid(
            row=7,
            column=0,
            sticky="ew",
            pady=(0, inner_pad_pixels),
        )

        # --- row 8: buttons sub-frame -----------------------------------
        buttons_frame = ttk.Frame(content_frame)
        buttons_frame.grid(row=8, column=0, sticky="ew")
        buttons_frame.columnconfigure(0, weight=1)

        # Buttons are stacked vertically. Each button gets a top-pad
        # except the first; this way the bottom of the cluster has no
        # phantom padding, regardless of whether the (conditionally
        # visible) force-cancel button is grid'd in.
        self._start_button = ttk.Button(
            buttons_frame,
            text="Start Recording",
            command=self._on_operator_pressed_start_recording_button,
            width=GUI_BUTTON_MINIMUM_WIDTH_CHARACTERS,
        )
        self._start_button.grid(
            row=0,
            column=0,
            sticky="ew",
        )
        self._pause_resume_button = ttk.Button(
            buttons_frame,
            text="Pause",
            command=(
                self._on_operator_pressed_pause_or_resume_button
            ),
            width=GUI_BUTTON_MINIMUM_WIDTH_CHARACTERS,
            state=tk.DISABLED,
        )
        self._pause_resume_button.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(inner_pad_pixels, 0),
        )
        self._stop_button = ttk.Button(
            buttons_frame,
            text="Stop Recording",
            command=self._on_operator_pressed_stop_recording_button,
            width=GUI_BUTTON_MINIMUM_WIDTH_CHARACTERS,
            state=tk.DISABLED,
        )
        self._stop_button.grid(
            row=2,
            column=0,
            sticky="ew",
            pady=(inner_pad_pixels, 0),
        )
        # Force-cancel button: built but not grid'd initially. We
        # toggle it in / out of the layout via grid() / grid_remove()
        # inside _refresh_gui_for_current_state. grid_remove preserves
        # every grid setting (row, column, sticky, padx, pady) so a
        # subsequent grid() call snaps the button back to row 3 with
        # the same spacing as the other buttons.
        self._force_cancel_button = ttk.Button(
            buttons_frame,
            text="Force-Cancel Finalize",
            command=(
                self._on_operator_pressed_force_cancel_button
            ),
            width=GUI_BUTTON_MINIMUM_WIDTH_CHARACTERS,
            state=tk.DISABLED,
        )
        # Pre-register the force-cancel button in its target grid
        # cell, then immediately remove it from the layout. The first
        # grid() establishes the (row, column, sticky, pady) tuple
        # that grid_remove()/grid() will preserve.
        self._force_cancel_button.grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(inner_pad_pixels, 0),
        )
        self._force_cancel_button.grid_remove()
        self._force_cancel_button_grid_visible = False

    def _bind_label_to_dynamic_wraplength(
        self,
        label: ttk.Label,
        initial_wraplength_pixels: int,
    ) -> None:
        """Make ``label.wraplength`` track the label's current width.

        Without this binding a ttk.Label with multi-line text refuses
        to wrap dynamically: it keeps the wraplength it was given at
        construction time, and any text wider than that wraplength
        either clips or, if no wraplength was given, pushes the
        containing window arbitrarily wide. (That second failure mode
        is the runaway-horizontal-growth behaviour the previous fixed-
        pixel layout exhibited at high Microsoft Windows display
        scaling.)

        With this binding the label rewraps every time the operator
        drags the window edges - narrower text wraps to more lines,
        wider text consolidates to fewer lines.

        The initial wraplength bounds the label's natural requested
        width at construction time (before the first <Configure> event
        fires) so the window opens at a sane initial size.
        """
        label.configure(wraplength=initial_wraplength_pixels)
        # Guard against redundant configure() round-trips: only update
        # wraplength when the actual width has changed by a meaningful
        # amount. (Tkinter rarely fires <Configure> for unchanged
        # widths but the guard is cheap.)
        last_applied_wraplength_pixels = [initial_wraplength_pixels]

        def on_label_configure_event(event: "tk.Event[Any]") -> None:
            # Subtract 2px to absorb the ttk theme's internal label
            # border so text never reaches the very edge of the label
            # widget; clamp to a sane lower bound so a degenerate
            # event.width=0 cannot make wraplength=0 (which would
            # render the label invisible).
            new_wraplength_pixels = max(50, event.width - 2)
            if (
                new_wraplength_pixels
                == last_applied_wraplength_pixels[0]
            ):
                return
            last_applied_wraplength_pixels[0] = new_wraplength_pixels
            label.configure(wraplength=new_wraplength_pixels)

        label.bind("<Configure>", on_label_configure_event)

    # ============== Window placement on Display 2 ==============

    def _place_window_on_microsoft_windows_display_2(
        self,
        *,
        centering_estimated_window_width_pixels: int,
        centering_estimated_window_height_pixels: int,
    ) -> None:
        """Center the window on Microsoft Windows Display 2.

        Emits a POSITION-ONLY ``wm geometry "+X+Y"`` request - never
        ``WxH+X+Y``. The two width and height arguments are used only
        to estimate the upper-left corner that will visually center the
        natural-size window inside Microsoft Windows Display 2's
        bounding rectangle; they are *not* used to fix the window's
        size. Tk's geometry manager continues to follow the laid-out
        widget tree's ``winfo_reqheight`` / ``winfo_reqwidth`` for the
        actual window dimensions, both initially and when child
        widgets grow at runtime (eg the status label gaining a line
        when a recording transitions to ``FATAL_ERROR_OBSERVED``).

        The position-only form is required because of how
        ``win/tkWinWm.c::ParseGeometry`` and ``UpdateGeometryInfo``
        interact: any string that begins with a digit (ie a ``WxH``
        prefix) causes ``ParseGeometry`` to write a non-sentinel
        ``wmPtr->width``, which then permanently overrides
        ``winPtr->reqWidth`` inside ``UpdateGeometryInfo``. A leading
        ``+`` skips the digit branch entirely and leaves
        ``wmPtr->width == -1`` (the natural-size sentinel) untouched.
        Reset semantics: the only documented way to release a locked
        toplevel back to natural-size auto-grow is ``wm geometry ""``,
        which is brittle to call from inside live state transitions -
        so the simpler, more defensive contract is "never lock it in
        the first place".
        """
        assert self._tk_root is not None
        d2 = (
            self._initial_display_config
            .microsoft_windows_display_2_secondary_for_operator_gui
        )
        upper_left_x_pixels = d2.bounding_rectangle_left + max(
            0,
            (
                d2.bounding_rectangle_width
                - centering_estimated_window_width_pixels
            ) // 2,
        )
        upper_left_y_pixels = d2.bounding_rectangle_top + max(
            0,
            (
                d2.bounding_rectangle_height
                - centering_estimated_window_height_pixels
            ) // 2,
        )
        self._tk_root.geometry(
            f"+{upper_left_x_pixels}+{upper_left_y_pixels}"
        )
        self._tk_root.update_idletasks()

    # ============== Button event handlers (all non-blocking) ===========

    def _on_operator_pressed_start_recording_button(self) -> None:
        self._invoke_controller_request_and_refresh(
            controller_method=(
                self._controller.request_start_new_recording_session
            ),
            operation_description="Start Recording",
        )

    def _on_operator_pressed_pause_or_resume_button(self) -> None:
        # The pause/resume button's command is the same callback in
        # both states; the controller's state decides which transition.
        current_state = self._controller.current_state
        if (
            current_state
            == RecordingLifecycleState.ACTIVELY_RECORDING_DISPLAY_1
        ):
            self._invoke_controller_request_and_refresh(
                controller_method=self._controller.request_pause_capture,
                operation_description="Pause",
            )
        elif current_state == RecordingLifecycleState.PAUSED_BY_OPERATOR:
            self._invoke_controller_request_and_refresh(
                controller_method=self._controller.request_resume_capture,
                operation_description="Resume",
            )
        else:
            # The button should have been disabled in any other state;
            # treat the click as a no-op-with-context.
            tk_messagebox.showwarning(
                title=f"{APP_DISPLAY_TITLE} - Unexpected state",
                message=(
                    f"The Pause / Resume button was clicked while the "
                    f"controller was in state {current_state.name}. "
                    f"The button should have been disabled in this "
                    f"state; please report this as a GUI defect."
                ),
            )

    def _on_operator_pressed_stop_recording_button(self) -> None:
        self._invoke_controller_request_and_refresh(
            controller_method=(
                self._controller.request_graceful_stop_and_finalize
            ),
            operation_description="Stop Recording",
        )

    def _on_operator_pressed_force_cancel_button(self) -> None:
        if not tk_messagebox.askyesno(
            title=f"{APP_DISPLAY_TITLE} - Confirm Force-Cancel",
            message=(
                "Force-cancel will tell the Intel Quick Sync Video "
                "encoder to skip its final libavcodec flush and close "
                "the output container as-is.\n\n"
                "Because the output is a fragmented MP4, the file "
                "remains playable through the last finalized fragment "
                "(at most about two seconds of recording in flight "
                "will be lost).\n\n"
                "Proceed with force-cancel?"
            ),
        ):
            return
        self._invoke_controller_request_and_refresh(
            controller_method=(
                self._controller.request_force_cancel_finalization
            ),
            operation_description="Force-Cancel Finalize",
        )

    def _invoke_controller_request_and_refresh(
        self,
        *,
        controller_method: Any,
        operation_description: str,
    ) -> None:
        try:
            controller_method()
        except ScreenRecorderError as recorder_err:
            self._display_fatal_error_messagebox(
                raised_exception=recorder_err,
                operation_that_failed=operation_description,
            )
        except BaseException as unexpected_err:  # noqa: BLE001
            # Truly unexpected (not one of our typed errors) - show
            # the traceback. We do NOT swallow this; we surface it.
            self._display_fatal_error_messagebox(
                raised_exception=unexpected_err,
                operation_that_failed=(
                    f"{operation_description} (unexpected error type "
                    f"{type(unexpected_err).__name__})"
                ),
            )
        finally:
            self._refresh_gui_for_current_state()

    # ============== Window-close interception ==============

    def _on_operating_system_close_window_requested(self) -> None:
        state = self._controller.current_state
        if state in {
            RecordingLifecycleState.IDLE_AWAITING_OPERATOR_START,
            RecordingLifecycleState.FATAL_ERROR_OBSERVED,
        }:
            assert self._tk_root is not None
            self._tk_root.destroy()
            return
        if state in {
            RecordingLifecycleState.PREPARING_SESSION_RESOURCES,
        }:
            tk_messagebox.showwarning(
                title=(
                    f"{APP_DISPLAY_TITLE} - Cannot close during "
                    "preparation"
                ),
                message=(
                    "The recorder is currently in state "
                    f"{state.name} (preparing the session - validating "
                    "display configuration, provisioning the output "
                    "folder, spawning worker threads). Please wait a "
                    "moment for preparation to complete."
                ),
            )
            return
        if state in {
            RecordingLifecycleState.ACTIVELY_RECORDING_DISPLAY_1,
            RecordingLifecycleState.PAUSED_BY_OPERATOR,
        }:
            tk_messagebox.showwarning(
                title=(
                    f"{APP_DISPLAY_TITLE} - Recording is in progress"
                ),
                message=(
                    f"A recording is currently in state {state.name}. "
                    "Please press the 'Stop Recording' button and wait "
                    "for the recording to finalize before closing this "
                    "window. This protects the integrity of the "
                    "in-progress fragmented MP4 output file and its "
                    "forensic artifacts (per-frame log, metadata, "
                    "display configuration snapshot)."
                ),
            )
            return
        # FINALIZING_OUTPUT_AND_JOINING_WORKERS or
        # FORCE_CANCELLATION_IN_PROGRESS: offer to abandon.
        elapsed_finalize_seconds = (
            self._controller
            .seconds_elapsed_since_finalization_started()
        )
        elapsed_string = (
            f"{elapsed_finalize_seconds:.1f} seconds"
            if elapsed_finalize_seconds is not None
            else "an unknown amount of time"
        )
        if tk_messagebox.askyesno(
            title=(
                f"{APP_DISPLAY_TITLE} - Recording is finalizing"
            ),
            message=(
                f"The recorder has been in state {state.name} for "
                f"{elapsed_string}. Closing this window now will "
                "abandon the worker threads (they are Microsoft "
                "Windows daemon threads and will die with this "
                "process); the fragmented MP4 file will be playable "
                "through the last fragment but the in-flight fragment "
                "will be lost.\n\n"
                "Close the window now and abandon the in-flight "
                "fragment?"
            ),
        ):
            assert self._tk_root is not None
            self._tk_root.destroy()

    # ============== Periodic poll and refresh ==============

    def _periodic_poll_callback(self) -> None:
        try:
            self._observe_and_surface_newly_visible_async_fatal_exception()
            self._refresh_gui_for_current_state()
        finally:
            assert self._tk_root is not None
            self._tk_root.after(
                GUI_STATUS_POLL_INTERVAL_MILLISECONDS,
                self._periodic_poll_callback,
            )

    def _observe_and_surface_newly_visible_async_fatal_exception(
        self,
    ) -> None:
        if (
            self._controller.current_state
            != RecordingLifecycleState.FATAL_ERROR_OBSERVED
        ):
            return
        latest_exc = self._controller.latest_fatal_exception
        if latest_exc is None:
            return
        if (
            id(latest_exc)
            == self._most_recently_displayed_fatal_exception_id
        ):
            return
        # New asynchronous fatal exception - show it once.
        self._most_recently_displayed_fatal_exception_id = id(latest_exc)
        self._display_fatal_error_messagebox(
            raised_exception=latest_exc,
            operation_that_failed=(
                "Background recording lifecycle thread "
                "(asynchronous error)"
            ),
        )

    def _display_fatal_error_messagebox(
        self,
        *,
        raised_exception: BaseException,
        operation_that_failed: str,
    ) -> None:
        # If the exception is one of our typed errors, str(exception)
        # already contains the verbose context-rich message. If it's
        # an unexpected type, include the full traceback.
        if isinstance(raised_exception, ScreenRecorderError):
            body = (
                f"Operation that failed: {operation_that_failed}\n\n"
                f"{type(raised_exception).__name__}:\n\n"
                f"{raised_exception}"
            )
        else:
            body = (
                f"Operation that failed: {operation_that_failed}\n\n"
                f"{type(raised_exception).__name__}: "
                f"{raised_exception}\n\n"
                "Traceback:\n"
                + "".join(
                    traceback.format_exception(raised_exception)
                )
            )
        tk_messagebox.showerror(
            title=(
                f"{type(raised_exception).__name__} - "
                f"{APP_DISPLAY_TITLE}"
            ),
            message=body,
        )

    def _refresh_gui_for_current_state(self) -> None:
        if (
            self._tk_root is None
            or self._status_text_string_var is None
            or self._details_text_string_var is None
            or self._start_button is None
            or self._pause_resume_button is None
            or self._stop_button is None
            or self._force_cancel_button is None
        ):
            return
        state = self._controller.current_state
        button_config = _GUI_BUTTON_CONFIGURATION_PER_STATE[state]
        statistics = (
            self._controller
            .live_capture_and_encoder_statistics_snapshot()
        )
        artifact_paths = (
            self._controller
            .in_progress_or_most_recently_completed_session_artifact_paths()
        )

        # Status line: one line per state, concise and information-dense.
        if state == RecordingLifecycleState.IDLE_AWAITING_OPERATOR_START:
            status_line = (
                "Status: Idle. Press 'Start Recording' to begin."
            )
        elif (
            state
            == RecordingLifecycleState.PREPARING_SESSION_RESOURCES
        ):
            status_line = (
                "Status: Preparing the recording session resources "
                "(validating display configuration, provisioning the "
                "per-session output folder, spawning the capture and "
                "encoder worker threads)..."
            )
        elif (
            state
            == RecordingLifecycleState.ACTIVELY_RECORDING_DISPLAY_1
        ):
            status_line = (
                "Status: Recording Microsoft Windows Display 1.   "
                f"Recorded so far: "
                f"{_format_hms(statistics['elapsed_recorded_seconds'])}.   "
                f"Captured: {statistics['frames_captured']}   "
                f"encoded: {statistics['frames_encoded']}."
            )
        elif state == RecordingLifecycleState.PAUSED_BY_OPERATOR:
            status_line = (
                "Status: Paused by operator.   "
                f"Recorded so far: "
                f"{_format_hms(statistics['elapsed_recorded_seconds'])}.   "
                f"Captured: {statistics['frames_captured']}   "
                f"encoded: {statistics['frames_encoded']}."
            )
        elif (
            state
            == RecordingLifecycleState
            .FINALIZING_OUTPUT_AND_JOINING_WORKERS
        ):
            finalize_seconds = (
                self._controller
                .seconds_elapsed_since_finalization_started()
            )
            finalize_display = (
                f"{finalize_seconds:.1f}s"
                if finalize_seconds is not None
                else "unknown"
            )
            status_line = (
                "Status: Finalizing the fragmented MP4 output "
                "container and joining the capture and encoder worker "
                f"threads (elapsed: {finalize_display}).   "
                f"Captured: {statistics['frames_captured']}   "
                f"encoded: {statistics['frames_encoded']}."
            )
        elif (
            state
            == RecordingLifecycleState.FORCE_CANCELLATION_IN_PROGRESS
        ):
            finalize_seconds = (
                self._controller
                .seconds_elapsed_since_finalization_started()
            )
            finalize_display = (
                f"{finalize_seconds:.1f}s"
                if finalize_seconds is not None
                else "unknown"
            )
            status_line = (
                "Status: Force-cancellation in progress; the encoder "
                "is skipping its final libavcodec flush and the "
                "container will close as-is. Daemon worker threads "
                "are being abandoned if they do not exit within the "
                "force-cancel join timeout. "
                f"Elapsed: {finalize_display}.   "
                f"Discarded frames: "
                f"{statistics['frames_discarded_by_force_cancel']}."
            )
        elif state == RecordingLifecycleState.FATAL_ERROR_OBSERVED:
            err = self._controller.latest_fatal_exception
            status_line = (
                "Status: Fatal error observed. See the error "
                "messagebox for full context. Press 'Start Recording' "
                "to retry. "
                f"Exception: "
                f"{type(err).__name__ if err else 'UnknownError'}"
            )
        else:
            status_line = f"Status: <unknown state {state.name}>"
        self._status_text_string_var.set(status_line)

        # Details line: output folder of the in-progress or most
        # recently completed session. While the session is running (or
        # if Stop-time publication into the Videos Known Folder failed)
        # the artifacts still live in the per-user temporary scratch
        # folder; we surface that path so the operator always knows
        # exactly where their on-disk recording is. Only after a
        # successful publication do we switch to displaying the final
        # Videos-Library location.
        if artifact_paths is not None:
            if (
                artifact_paths
                .has_been_successfully_published_to_videos_library
            ):
                self._details_text_string_var.set(
                    "Session output folder:\n"
                    f"{artifact_paths.final_publication_parent_session_folder}"
                )
            else:
                self._details_text_string_var.set(
                    "Session scratch folder (will copy to Videos "
                    "library on Stop):\n"
                    f"{artifact_paths.parent_session_folder}"
                )
        else:
            self._details_text_string_var.set(
                f"Videos Known Folder: {self._videos_known_folder_path}"
            )

        # Button enabled/disabled and pause/resume label.
        self._start_button.configure(
            state=(
                tk.NORMAL
                if button_config.start_button_enabled
                else tk.DISABLED
            )
        )
        self._pause_resume_button.configure(
            state=(
                tk.NORMAL
                if button_config.pause_button_enabled
                else tk.DISABLED
            ),
            text=button_config.pause_button_label_text,
        )
        self._stop_button.configure(
            state=(
                tk.NORMAL
                if button_config.stop_button_enabled
                else tk.DISABLED
            )
        )

        # Force-cancel button: visibility + enabled-after-delay logic.
        # We use grid_remove() / grid() rather than pack_forget() /
        # pack() so the button preserves every grid setting (row,
        # column, sticky, padx, pady) across visibility toggles and
        # snaps back into row 3 of the buttons sub-frame identically
        # each time.
        if button_config.force_cancel_button_visible:
            if not self._force_cancel_button_grid_visible:
                self._force_cancel_button.grid()
                self._force_cancel_button_grid_visible = True
            elapsed = (
                self._controller
                .seconds_elapsed_since_finalization_started()
            ) or 0.0
            allow = (
                elapsed
                >= button_config
                .force_cancel_button_minimum_enable_delay_seconds
            )
            self._force_cancel_button.configure(
                state=tk.NORMAL if allow else tk.DISABLED
            )
        else:
            if self._force_cancel_button_grid_visible:
                self._force_cancel_button.grid_remove()
                self._force_cancel_button_grid_visible = False


# ============================================================================
# Section 17 - Small helpers
# ============================================================================

def _format_hms(total_seconds: float) -> str:
    total_seconds = max(0.0, float(total_seconds))
    whole_seconds = int(total_seconds)
    hours = whole_seconds // 3600
    minutes = (whole_seconds % 3600) // 60
    seconds = whole_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _verify_host_is_microsoft_windows() -> None:
    if sys.platform != "win32":
        raise HostOperatingSystemError(
            f"This program is exclusively a Microsoft Windows screen "
            f"recorder. The current process is running on "
            f"sys.platform='{sys.platform}', which is not the "
            f"'win32' value Microsoft Windows reports. Aborting."
        )


def _verify_third_party_dependency_versions() -> None:
    pyav_observed = tuple(
        int(component) if component.isdigit() else 0
        for component in av.__version__.split(".")[:3]
    )
    if pyav_observed < MINIMUM_REQUIRED_PYAV_VERSION:
        raise ThirdPartyDependencyVersionError(
            f"PyAV version observed: {av.__version__}; minimum required: "
            f"{'.'.join(str(x) for x in MINIMUM_REQUIRED_PYAV_VERSION)}. "
            f"Upgrade via: pip install --upgrade av"
        )
    mss_version_string = getattr(mss, "__version__", "0.0.0")
    try:
        mss_observed = tuple(
            int(component) if component.isdigit() else 0
            for component in mss_version_string.split(".")[:3]
        )
    except ValueError:
        mss_observed = (0, 0, 0)
    if mss_observed < MINIMUM_REQUIRED_PYTHON_MSS_VERSION:
        raise ThirdPartyDependencyVersionError(
            f"python-mss version observed: {mss_version_string}; minimum "
            f"required: "
            f"{'.'.join(str(x) for x in MINIMUM_REQUIRED_PYTHON_MSS_VERSION)}"
        )


# ============================================================================
# Section 18 - Main entry point
# ============================================================================

def main() -> int:
    """Entry point. Returns process exit code (0 success, non-zero on fatal)."""
    # Configure a minimal root logger so any pre-GUI messages reach stderr.
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s.%(msecs)03d %(levelname)-7s "
            "[%(threadName)s] %(name)s: %(message)s"
        ),
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )
    root_logger = logging.getLogger(APP_INTERNAL_NAME)

    # 1) Sanity: host OS and dependency versions.
    try:
        _verify_host_is_microsoft_windows()
        _verify_third_party_dependency_versions()
    except ScreenRecorderError as preflight_err:
        root_logger.error(str(preflight_err))
        _show_pre_gui_fatal_messagebox(preflight_err)
        return 1

    # 2) Opt this process into Per-Monitor V2 DPI awareness BEFORE any Tk
    # call so geometry math in physical pixels.
    try:
        _opt_into_per_monitor_v2_dpi_awareness()
    except ScreenRecorderError as dpi_err:
        root_logger.error(str(dpi_err))
        _show_pre_gui_fatal_messagebox(dpi_err)
        return 1

    # 3) Install PyAV's real logging callback so any subsequent libav error
    # (encoder, muxer, color-space converter) carries the underlying libav
    # log line on the raised exception's ``.log`` attribute. Must happen
    # before any libav usage - including the ``h264_qsv`` codec availability
    # probe run by ``_verify_intel_quick_sync_video_h264_encoder_available``.
    _opt_into_libav_verbose_diagnostic_log_capture()

    # 3) Resolve the operator's Videos Known Folder up front, so any error
    # surfaces before the GUI appears.
    try:
        videos_known_folder = (
            _resolve_current_user_videos_known_folder_path()
        )
        if not videos_known_folder.is_dir():
            raise OutputFolderProvisioningError(
                f"The Videos Known Folder resolved by SHGetKnownFolderPath "
                f"is not a directory on disk:\n"
                f"  Resolved path : {videos_known_folder}"
            )
    except ScreenRecorderError as videos_err:
        root_logger.error(str(videos_err))
        _show_pre_gui_fatal_messagebox(videos_err)
        return 1

    # 4) Verify the Intel Quick Sync Video H.264 encoder is available
    # before opening any GUI; failing here is unrecoverable for this
    # program.
    try:
        verify_intel_quick_sync_video_h264_encoder_available()
    except ScreenRecorderError as qsv_err:
        root_logger.error(str(qsv_err))
        _show_pre_gui_fatal_messagebox(qsv_err)
        return 1

    # 5) Validate the dual-extend-mode display configuration so we can
    # place the GUI on Display 2.
    try:
        validated_display_config = (
            validate_dual_extend_mode_display_configuration()
        )
    except ScreenRecorderError as display_err:
        root_logger.error(str(display_err))
        _show_pre_gui_fatal_messagebox(display_err)
        return 1

    # 6) Construct the controller and the GUI, and hand off to Tk's
    # mainloop.
    controller = RecordingSessionController()
    gui = _OperatorTkinterGui(
        recording_controller=controller,
        validated_display_config_for_initial_placement=(
            validated_display_config
        ),
        resolved_videos_known_folder_path=videos_known_folder,
    )
    try:
        gui.construct_and_run_blocking()
    except BaseException as gui_err:  # noqa: BLE001
        root_logger.error(
            "Operator GUI mainloop exited with an uncaught exception:\n"
            + "".join(traceback.format_exception(gui_err))
        )
        _show_pre_gui_fatal_messagebox(gui_err)
        return 2

    return 0


def _show_pre_gui_fatal_messagebox(raised_exception: BaseException) -> None:
    """Show a Microsoft Windows MessageBox for a fatal pre-GUI exception.

    Falls back to a stderr print if the Tk fallback fails too.
    """
    title = (
        f"{type(raised_exception).__name__} - {APP_DISPLAY_TITLE}"
    )
    body = str(raised_exception)
    # Prefer the native Microsoft Win32 MessageBoxW so the message
    # appears even if Tkinter is misconfigured.
    try:
        # MB_OK = 0, MB_ICONERROR = 0x10
        _user32.MessageBoxW(
            wintypes.HWND(0),
            ctypes.c_wchar_p(body),
            ctypes.c_wchar_p(title),
            ctypes.c_uint(0x00000010),
        )
        return
    except Exception:  # noqa: BLE001
        pass
    # Last-resort fallback: Tk messagebox.
    try:
        hidden_root = tk.Tk()
        hidden_root.withdraw()
        tk_messagebox.showerror(title=title, message=body)
        hidden_root.destroy()
    except Exception:  # noqa: BLE001
        sys.stderr.write(f"\n{title}\n{body}\n")


if __name__ == "__main__":
    raise SystemExit(main())
