from fastapi import APIRouter
from sqlalchemy import text

from backend.database import engine

router = APIRouter()


# Check whether the backend is running.
@router.get("/")
def home():
    return {
        "message": "AI-Powered Emergency Corridor Backend is running 🚑"
    }


# Check whether FastAPI can connect to PostgreSQL. checks if db is running and connected
@router.get("/db-check")
def database_check():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))

        return {
            "database": "connected",
            "result": result.scalar()
        }