@echo off
REM Activate your Conda environment if needed:
REM conda activate base

REM Start the FastAPI server
python -m uvicorn testAPI:app --reload --host 127.0.0.1 --port 8000
pause