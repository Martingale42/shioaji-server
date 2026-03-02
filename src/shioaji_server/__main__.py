import uvicorn


def main():
    uvicorn.run(
        "shioaji_server.app:app",
        host="0.0.0.0",
        port=8000,
    )


if __name__ == "__main__":
    main()
