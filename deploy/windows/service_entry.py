"""Capture startup failures in the private service log, including import errors."""
import runpy
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent

if __name__ == '__main__':
    original_stdout, original_stderr = sys.stdout, sys.stderr
    with open(ROOT / 'logs/backend.log', 'a', encoding='utf-8', buffering=1) as log:
        sys.stdout = sys.stderr = log
        try:
            runpy.run_path(str(ROOT / 'lan_server.py'), run_name='__main__')
        except BaseException:
            traceback.print_exc()
            raise
        finally:
            sys.stdout, sys.stderr = original_stdout, original_stderr
