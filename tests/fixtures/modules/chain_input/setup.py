from setuptools import setup

setup(
    name="chaintool",
    version="1.0.0",
    py_modules=["chaintool"],
    entry_points={
        "console_scripts": [
            "chaintool = chaintool:main",
        ],
    },
)
