"""Compatibility shim for winuse 0.1 installs.

winuse 0.2 renamed the Windows-side component from "agent" to "host" (C:\\winuse\\host\\winuse_host.py).
A logon task registered by 0.1 still starts C:\\winuse\\agent\\winuse_agent.py; the installer puts this
file there, and it runs the host. Re-running the installer registers the task to the new path and
removes the old folder.
"""
import os
import runpy
import sys

host = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host", "winuse_host.py")
sys.argv[0] = host
runpy.run_path(host, run_name="__main__")
