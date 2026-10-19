"""
proj7k desktop: the dashboard in its own window, as a desktop app (ADR-0034).

The window is a shell around the dashboard the toolchain already serves (`proj7k.dashboard`, port 7770): the same
pages, the same jobs, nothing duplicated. Starting the app reuses a dashboard that is already running (on Kai's
machine the auto-updated one) and otherwise starts one in the background first, so closing the window never stops
a running job, and an auto-update restart only makes the window reconnect.
"""

from proj7k.desktop.app import main

__all__ = ["main"]
