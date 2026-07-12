"""
Shared pytest setup: put the tool directory on sys.path so the tests can
import engine / defringe / app no matter where pytest is launched from.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
