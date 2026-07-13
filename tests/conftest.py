"""
Shared pytest setup: put the tool directory on sys.path so the tests can
import engine / defringe / app no matter where pytest is launched from,
and point the app at a throwaway settings file so test runs never write
into the live .quicklook_settings.json (they used to clobber the saved
folders and theme).
"""
import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402  (needs the sys.path line above)

app.SETTINGS_PATH = os.path.join(tempfile.mkdtemp(prefix="squishe_test_"),
                                 "settings.json")
