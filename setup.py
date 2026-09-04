from setuptools import find_packages, setup

setup(
    name="game-recommendation-system",
    version="0.0.0",
    author="Ashish Rathore",
    author_email="rathoreashish146@gmail.com",
    packages=find_packages(),
    install_requires=[
        "Flask>=3.0,<4",
        "python-dotenv>=1.0,<2",
        "langchain>=1.0,<2",
        "langchain-community>=0.4,<1",
        "langchain-huggingface>=1,<2",
        "langchain-ollama>=1,<2",
        "langchain-text-splitters>=1.0,<2",
        "sentence-transformers>=3,<6",
        "pinecone>=6,<7",
        "gunicorn>=23,<27",
    ],
)
