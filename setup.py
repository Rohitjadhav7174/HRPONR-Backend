from setuptools import setup

setup(
    name="ecommerce-backend",
    version="0.1",
    install_requires=[
        "fastapi==0.104.1",
        "uvicorn==0.24.0",
        "pymongo==4.6.0",
        "pydantic==2.5.0",
        "python-dotenv==1.0.0",
    ],
)