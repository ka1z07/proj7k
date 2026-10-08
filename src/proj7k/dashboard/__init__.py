"""
proj7k.dashboard: one local web control panel for the live radar, the lazer library sync, the downscaler and the
profiler (ADR-0024). It is the live radar's server with job pages added; `python3 -m proj7k.dashboard` runs it.
"""

from proj7k.dashboard.actions import DashboardConfig, build_actions
from proj7k.dashboard.jobs import Job, JobContext, JobManager
from proj7k.dashboard.server import DashboardServer

__all__ = ["DashboardConfig", "DashboardServer", "Job", "JobContext", "JobManager", "build_actions"]
