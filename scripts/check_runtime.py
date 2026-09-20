import importlib.util
import sys


if sys.version_info < (3, 12):
    raise SystemExit(1)
if '--version-only' in sys.argv:
    raise SystemExit(0)
modules = ['fastapi', 'uvicorn', 'curl_cffi', 'PIL', 'cryptography', 'multipart', 'pybase64']
raise SystemExit(0 if all(importlib.util.find_spec(name) for name in modules) else 1)
