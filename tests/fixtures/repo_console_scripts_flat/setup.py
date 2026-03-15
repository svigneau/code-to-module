import setuptools

setuptools.setup(
    name="mytool",
    version="1.0.0",
    packages=setuptools.find_packages(),
    entry_points={
        "console_scripts": ["mytool=mytool.cli:main"],
    },
)
