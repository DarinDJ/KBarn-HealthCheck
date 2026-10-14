"""Command line: python run_check.py <data folder> [--html report.html]"""
import sys

from kbarn_health.cli import main

sys.exit(main())
