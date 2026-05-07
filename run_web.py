"""FinAgent Web UI 서버 진입점.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python run_web.py

브라우저에서 http://localhost:8000 접속
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=8000,
        workers=1,   # 인-프로세스 job store 사용 — 반드시 1
        reload=False,
    )
