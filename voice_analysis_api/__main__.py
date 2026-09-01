"""Run the M2 API with Uvicorn."""
import uvicorn


if __name__ == "__main__":
    uvicorn.run("voice_analysis_api.app:create_app", factory=True, host="127.0.0.1", port=8076)
