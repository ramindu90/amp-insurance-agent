import uvicorn

if __name__ == "__main__":
    # Agent Manager's Chat Agent contract expects port 8000.
    uvicorn.run("app:app", host="0.0.0.0", port=8000)
