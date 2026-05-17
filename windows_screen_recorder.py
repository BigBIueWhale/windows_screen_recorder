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

When the operator presses Start Recording, a fresh per-session output
folder is created underneath the operator's Videos Known Folder, at the
path::

    <Videos Known Folder>\\WindowsScreenRecorder\\<ISO-8601-session-id>\\

where ``<ISO-8601-session-id>`` is the local wall-clock instant of
session start, formatted as ``YYYY-MM-DDTHH-MM-SS-FFFFFF`` (Microsoft
Windows reserves the colon character, so the canonical ISO 8601 colons
are replaced with hyphens; microseconds eliminate same-second collisions
even on a rapid Stop/Start cycle). This format sorts both alphabetically
and chronologically.

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
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tk_font
import tkinter.messagebox as tk_messagebox
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
# Disable QSV look-ahead: it costs latency we do not need for screen capture.
INTEL_QSV_LOOK_AHEAD_VALUE: Final[int] = 0
# Async depth of 1 minimizes the QSV pipeline latency; we are not bandwidth
# limited so the throughput cost of async_depth=1 is irrelevant.
INTEL_QSV_ASYNC_DEPTH_VALUE: Final[int] = 1
# Keyframe interval expressed in frames. We choose 2 wall-clock seconds, so
# the value is 2 * frame rate. This bounds the lost-on-crash tail to about
# 2 seconds (the most recently-started fragment).
KEYFRAME_INTERVAL_FRAMES: Final[int] = 2 * TARGET_OUTPUT_FRAMES_PER_SECOND


# ============================================================================
# Section 3 - Operator GUI configuration constants
# ============================================================================

GUI_WINDOW_FIXED_WIDTH_PIXELS: Final[int] = 460
GUI_WINDOW_FIXED_HEIGHT_PIXELS: Final[int] = 360
GUI_WINDOW_TITLE_TEXT: Final[str] = APP_DISPLAY_TITLE
GUI_STATUS_POLL_INTERVAL_MILLISECONDS: Final[int] = 200
GUI_BUTTON_FONT_FAMILY: Final[str] = "Segoe UI"
GUI_BUTTON_FONT_SIZE_POINTS: Final[int] = 11
GUI_STATUS_FONT_FAMILY: Final[str] = "Segoe UI"
GUI_STATUS_FONT_SIZE_POINTS: Final[int] = 10
GUI_TITLE_BANNER_FONT_FAMILY: Final[str] = "Segoe UI Semibold"
GUI_TITLE_BANNER_FONT_SIZE_POINTS: Final[int] = 11


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
    """The full set of per-session output file paths."""

    parent_session_folder: Path
    iso8601_session_id: str
    fragmented_mp4_video_file_path: Path
    session_metadata_json_file_path: Path
    display_configuration_json_file_path: Path
    per_frame_log_csv_file_path: Path
    text_log_file_path: Path


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


def provision_fresh_per_session_output_folder(
    videos_known_folder_root: Path,
) -> PerSessionOutputFolderArtifactPaths:
    """Create a fresh per-session output folder and return its artifact paths.

    Creates ``<Videos>\\WindowsScreenRecorder\\<session-id>\\`` and verifies
    it is writable. Raises ``OutputFolderProvisioningError`` if anything
    goes wrong, with the full context of the attempted path and underlying
    OS error.
    """
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

    parent_folder = (
        videos_known_folder_root
        / OUTPUT_PARENT_SUBFOLDER_NAME_UNDER_VIDEOS_KNOWN_FOLDER
    )
    try:
        parent_folder.mkdir(parents=False, exist_ok=True)
    except OSError as mkdir_exc:
        raise OutputFolderProvisioningError(
            f"Could not create the parent output subfolder underneath the "
            f"Microsoft Windows Videos Known Folder.\n"
            f"  Attempted parent folder    : {parent_folder}\n"
            f"  Underlying OSError         : {mkdir_exc!r}\n"
            f"Resolution: ensure the current user (running this program "
            f"with elevated administrator privileges) has write permission "
            f"to '{videos_known_folder_root}'."
        ) from mkdir_exc

    now_local = _datetime_module.datetime.now()
    session_identifier = _build_iso8601_filesystem_safe_session_identifier(
        now_local
    )
    session_folder = parent_folder / session_identifier
    try:
        session_folder.mkdir(parents=False, exist_ok=False)
    except FileExistsError as already_exists_exc:
        raise OutputFolderProvisioningError(
            f"The freshly-constructed ISO 8601 session folder path already "
            f"exists, which is impossible because the session identifier "
            f"includes microseconds.\n"
            f"  Attempted session folder : {session_folder}\n"
            f"This indicates either a clock that runs backwards or a "
            f"corrupted filesystem. Aborting before any data is written."
        ) from already_exists_exc
    except OSError as mkdir_exc:
        raise OutputFolderProvisioningError(
            f"Could not create the per-session output folder.\n"
            f"  Attempted session folder : {session_folder}\n"
            f"  Underlying OSError       : {mkdir_exc!r}"
        ) from mkdir_exc

    # Smoke-test writability with a probe file we delete immediately.
    probe_path = session_folder / ".writability_probe.tmp"
    try:
        probe_path.write_bytes(b"")
        probe_path.unlink()
    except OSError as probe_exc:
        raise OutputFolderProvisioningError(
            f"The per-session output folder was created but is not "
            f"writable.\n"
            f"  Session folder      : {session_folder}\n"
            f"  Underlying OSError  : {probe_exc!r}"
        ) from probe_exc

    return PerSessionOutputFolderArtifactPaths(
        parent_session_folder=session_folder,
        iso8601_session_id=session_identifier,
        fragmented_mp4_video_file_path=(
            session_folder / PER_SESSION_VIDEO_FILE_NAME
        ),
        session_metadata_json_file_path=(
            session_folder / PER_SESSION_METADATA_JSON_FILE_NAME
        ),
        display_configuration_json_file_path=(
            session_folder / PER_SESSION_DISPLAY_CONFIG_JSON_FILE_NAME
        ),
        per_frame_log_csv_file_path=(
            session_folder / PER_SESSION_PER_FRAME_LOG_CSV_FILE_NAME
        ),
        text_log_file_path=(
            session_folder / PER_SESSION_TEXT_LOG_FILE_NAME
        ),
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
            next_presentation_timestamp_value = 0

            while not self._stop_event.is_set():
                # ----- Pause/resume bookkeeping -----
                if self._pause_event.is_set():
                    if pause_started_monotonic is None:
                        pause_started_monotonic = time.monotonic()
                        self._logger.info(
                            "Capture worker observed pause request at PTS "
                            f"{next_presentation_timestamp_value}."
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
                try:
                    raw_screenshot = sct.grab(mss_monitor_capture_region)
                except Exception as mss_grab_exc:
                    raise ScreenFrameCaptureFailureError(
                        f"python-mss .grab() raised "
                        f"{type(mss_grab_exc).__name__}: {mss_grab_exc}\n"
                        f"  Frame index attempted (PTS) : "
                        f"{next_presentation_timestamp_value}\n"
                        f"  Capture region              : "
                        f"{mss_monitor_capture_region}\n"
                        f"  Wall-clock monotonic        : "
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
                        f"  Frame index: "
                        f"{next_presentation_timestamp_value}\n"
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
                                next_presentation_timestamp_value
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
                        f"  Frame index that failed to enqueue : "
                        f"{next_presentation_timestamp_value}\n"
                        f"  Target capture rate                : "
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
                            next_presentation_timestamp_value,
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
                next_presentation_timestamp_value += 1
                # Advance the schedule baseline strictly by the target
                # interval (not by the actual elapsed time): this is what
                # gives us true CFR pacing rather than drift.
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
# Section 14 - Encoder thread: PyAV / Intel Quick Sync H.264 consumer
# ============================================================================

class _IntelQuickSyncVideoFragmentedMp4EncoderWorker(threading.Thread):
    """Worker thread that encodes BGRA frames into a fragmented MP4 via PyAV.

    Owns the PyAV ``OutputContainer`` and the H.264 ``h264_qsv`` stream.
    Intel Quick Sync Video sessions are tied to the thread that first
    invokes ``stream.encode``, so we both construct and consume the stream
    here on this single worker thread.

    The worker honors two distinct stop signals:

      * The shutdown sentinel singleton pushed by the capture worker on
        its way out. This is the *graceful* shutdown: we encode every
        queued frame, flush the encoder via ``stream.encode(None)``, then
        close the container so the final fragment is finalized.

      * The ``force_cancel_event`` set by the controller's
        ``request_force_cancel_finalization``. This is the *force*
        shutdown: as soon as we observe it set we discard whatever
        frames remain in the queue, skip the ``stream.encode(None)``
        flush, and close the container as-is. Because the output is
        fragmented MP4 with ``+frag_keyframe+empty_moov+default_base_moof``,
        the file is still playable up through whichever fragment was
        last finalized; the worst possible loss is the in-flight
        fragment (up to ``KEYFRAME_INTERVAL_FRAMES`` frames worth, i.e.
        approximately two wall-clock seconds at the default frame rate).
    """

    # Polling interval the encoder loop uses when blocked on the queue,
    # so that it can wake up promptly to observe a force-cancel.
    _QUEUE_GET_POLL_TIMEOUT_SECONDS: Final[float] = 0.10

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
        self.frames_discarded_due_to_force_cancel_so_far: int = 0
        self.encoder_was_force_cancelled: bool = False

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
            output_stream.time_base = Fraction(
                1, TARGET_OUTPUT_FRAMES_PER_SECOND
            )
            output_stream.codec_context.options = {
                "preset": INTEL_QSV_PRESET_VALUE,
                "global_quality": str(INTEL_QSV_GLOBAL_QUALITY_VALUE),
                "look_ahead": str(INTEL_QSV_LOOK_AHEAD_VALUE),
                "g": str(KEYFRAME_INTERVAL_FRAMES),
                "async_depth": str(INTEL_QSV_ASYNC_DEPTH_VALUE),
            }

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
                    for output_packet in output_stream.encode(
                        av_nv12_video_frame
                    ):
                        output_container.mux(output_packet)
                except Exception as encode_exc:
                    raise EncoderPipelineError(
                        f"PyAV / Intel Quick Sync Video encoding failed on "
                        f"a frame.\n"
                        f"  Failing frame PTS : "
                        f"{captured_frame.presentation_timestamp_in_one_frame_units}\n"
                        f"  Frame buffer shape : "
                        f"{captured_frame.bgra_frame_pixel_buffer.shape}\n"
                        f"  Frame buffer dtype : "
                        f"{captured_frame.bgra_frame_pixel_buffer.dtype}\n"
                        f"  Underlying          : "
                        f"{type(encode_exc).__name__}: {encode_exc}"
                    ) from encode_exc

                self.frames_successfully_encoded_so_far += 1

            # Graceful path: flush the encoder. Skip on force-cancel.
            if not self.encoder_was_force_cancelled:
                try:
                    for trailing_packet in output_stream.encode(None):
                        output_container.mux(trailing_packet)
                except Exception as flush_exc:
                    raise EncoderPipelineError(
                        f"PyAV / Intel Quick Sync Video encoder flush "
                        f"(stream.encode(None)) failed.\n"
                        f"  Frames successfully encoded before flush: "
                        f"{self.frames_successfully_encoded_so_far}\n"
                        f"  Underlying : "
                        f"{type(flush_exc).__name__}: {flush_exc}"
                    ) from flush_exc
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
            f"Encoder worker exiting normally. Total frames encoded: "
            f"{self.frames_successfully_encoded_so_far}. Frames discarded "
            f"by force-cancel: "
            f"{self.frames_discarded_due_to_force_cancel_so_far}. "
            f"Force-cancelled: {self.encoder_was_force_cancelled}."
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
        session.artifact_paths = provision_fresh_per_session_output_folder(
            videos_folder
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
                "output_video_file_path": str(
                    artifact_paths.fragmented_mp4_video_file_path
                ),
                "output_per_frame_log_csv_file_path": str(
                    artifact_paths.per_frame_log_csv_file_path
                ),
                "output_display_configuration_json_file_path": str(
                    artifact_paths
                    .display_configuration_json_file_path
                ),
                "output_text_log_file_path": str(
                    artifact_paths.text_log_file_path
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
    """The small fixed-size operator GUI window on Display 2.

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
        self._start_button: Optional[tk.Button] = None
        self._pause_resume_button: Optional[tk.Button] = None
        self._stop_button: Optional[tk.Button] = None
        self._force_cancel_button: Optional[tk.Button] = None
        self._force_cancel_button_packed_into_layout: bool = False
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
        self._place_window_centered_on_microsoft_windows_display_2()
        self._tk_root.resizable(False, False)
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

        # Initial refresh + start the periodic poll.
        self._refresh_gui_for_current_state()
        self._tk_root.after(
            GUI_STATUS_POLL_INTERVAL_MILLISECONDS,
            self._periodic_poll_callback,
        )
        self._tk_root.mainloop()

    # ============== Widget construction ==============

    def _construct_all_widgets(self) -> None:
        assert self._tk_root is not None
        title_banner_label = tk.Label(
            self._tk_root,
            text=GUI_WINDOW_TITLE_TEXT,
            font=tk_font.Font(
                family=GUI_TITLE_BANNER_FONT_FAMILY,
                size=GUI_TITLE_BANNER_FONT_SIZE_POINTS,
            ),
            anchor="w",
            padx=12,
            pady=8,
        )
        title_banner_label.pack(fill=tk.X)

        d1 = (
            self._initial_display_config
            .microsoft_windows_display_1_primary_to_be_recorded
        )
        d2 = (
            self._initial_display_config
            .microsoft_windows_display_2_secondary_for_operator_gui
        )
        configuration_description_label = tk.Label(
            self._tk_root,
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
            font=tk_font.Font(
                family=GUI_STATUS_FONT_FAMILY,
                size=GUI_STATUS_FONT_SIZE_POINTS,
            ),
            padx=12,
            pady=4,
        )
        configuration_description_label.pack(fill=tk.X)

        self._status_text_string_var = tk.StringVar(value="")
        status_label = tk.Label(
            self._tk_root,
            textvariable=self._status_text_string_var,
            justify="left",
            anchor="w",
            font=tk_font.Font(
                family=GUI_STATUS_FONT_FAMILY,
                size=GUI_STATUS_FONT_SIZE_POINTS,
            ),
            padx=12,
            pady=4,
            wraplength=GUI_WINDOW_FIXED_WIDTH_PIXELS - 24,
        )
        status_label.pack(fill=tk.X)

        self._details_text_string_var = tk.StringVar(value="")
        details_label = tk.Label(
            self._tk_root,
            textvariable=self._details_text_string_var,
            justify="left",
            anchor="w",
            font=tk_font.Font(
                family=GUI_STATUS_FONT_FAMILY,
                size=GUI_STATUS_FONT_SIZE_POINTS,
            ),
            padx=12,
            pady=4,
            wraplength=GUI_WINDOW_FIXED_WIDTH_PIXELS - 24,
        )
        details_label.pack(fill=tk.X)

        buttons_frame = tk.Frame(self._tk_root, padx=12, pady=12)
        buttons_frame.pack(fill=tk.X)

        button_font = tk_font.Font(
            family=GUI_BUTTON_FONT_FAMILY,
            size=GUI_BUTTON_FONT_SIZE_POINTS,
        )
        self._start_button = tk.Button(
            buttons_frame,
            text="Start Recording",
            command=self._on_operator_pressed_start_recording_button,
            font=button_font,
        )
        self._start_button.pack(fill=tk.X, pady=3)
        self._pause_resume_button = tk.Button(
            buttons_frame,
            text="Pause",
            command=(
                self._on_operator_pressed_pause_or_resume_button
            ),
            font=button_font,
            state=tk.DISABLED,
        )
        self._pause_resume_button.pack(fill=tk.X, pady=3)
        self._stop_button = tk.Button(
            buttons_frame,
            text="Stop Recording",
            command=self._on_operator_pressed_stop_recording_button,
            font=button_font,
            state=tk.DISABLED,
        )
        self._stop_button.pack(fill=tk.X, pady=3)
        # Force-cancel button: built but not packed initially. We pack
        # / pack_forget it based on state in _refresh_gui_for_current_state.
        self._force_cancel_button = tk.Button(
            buttons_frame,
            text="Force-Cancel Finalize",
            command=(
                self._on_operator_pressed_force_cancel_button
            ),
            font=button_font,
            state=tk.DISABLED,
        )

    # ============== Window placement on Display 2 ==============

    def _place_window_centered_on_microsoft_windows_display_2(
        self,
    ) -> None:
        assert self._tk_root is not None
        d2 = (
            self._initial_display_config
            .microsoft_windows_display_2_secondary_for_operator_gui
        )
        upper_left_x = d2.bounding_rectangle_left + max(
            0,
            (d2.bounding_rectangle_width - GUI_WINDOW_FIXED_WIDTH_PIXELS)
            // 2,
        )
        upper_left_y = d2.bounding_rectangle_top + max(
            0,
            (d2.bounding_rectangle_height - GUI_WINDOW_FIXED_HEIGHT_PIXELS)
            // 2,
        )
        self._tk_root.geometry(
            f"{GUI_WINDOW_FIXED_WIDTH_PIXELS}x"
            f"{GUI_WINDOW_FIXED_HEIGHT_PIXELS}+"
            f"{upper_left_x}+{upper_left_y}"
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
        # recently completed session.
        if artifact_paths is not None:
            self._details_text_string_var.set(
                "Session output folder:\n"
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
        if button_config.force_cancel_button_visible:
            if not self._force_cancel_button_packed_into_layout:
                self._force_cancel_button.pack(fill=tk.X, pady=3)
                self._force_cancel_button_packed_into_layout = True
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
            if self._force_cancel_button_packed_into_layout:
                self._force_cancel_button.pack_forget()
                self._force_cancel_button_packed_into_layout = False


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
