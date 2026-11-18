"""Vercel entry point: exposes the WSGI `app` at /api (routes via ?op=...)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _tsmadmin.app import app  # noqa: E402,F401
