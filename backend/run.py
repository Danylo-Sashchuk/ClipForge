"""
Run the app:  python run.py
Then open the dashboard at http://127.0.0.1:3001
"""
import uvicorn

from config import settings

if __name__ == "__main__":
    print(f"Dashboard:  http://{settings.HOST}:{settings.PORT}")
    print(f"Watching Kick channel: {settings.CHANNEL}")
    uvicorn.run("server:app", host=settings.HOST, port=settings.PORT, reload=False)
