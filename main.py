# SLH Ecosystem — Railway entrypoint shim.
#
# The real FastAPI app lives in api/main.py (single source of truth).
# This file exists so the legacy `uvicorn main:app` command (Dockerfile,
# railway.json, dev scripts) keeps working without dual-file maintenance.
#
# Do NOT add code here. Edit api/main.py instead.

from api.main import app  # noqa: F401
