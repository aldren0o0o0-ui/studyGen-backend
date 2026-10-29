import os
import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    is_dev = os.getenv("ENVIRONMENT", "development").lower() == "development"
    uvicorn.run(
        "main:app",
        host="0.0.0.0", 
        port=port, 
        reload=is_dev,
    )
