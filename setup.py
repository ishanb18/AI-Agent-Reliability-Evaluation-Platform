from setuptools import setup, find_packages

setup(
    name="evalplatform",
    version="0.7.0",
    description="Python SDK for AI Agent Reliability Evaluation & Tracing",
    author="Antigravity Team",
    packages=find_packages(),
    install_requires=[
        "requests>=2.28.0",
        "structlog>=23.1.0"
    ],
    python_requires=">=3.9",
)
