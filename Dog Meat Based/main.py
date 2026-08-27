import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from routers.diet_router import router

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Dog Diet Planner API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/planner")
def planner():
    return FileResponse(os.path.join(BASE_DIR, "dog_diet_planner_v2.html"))

@app.get("/")
def root():
    return {"message": "Dog Diet Planner API is running"}

app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8090, reload=True)
