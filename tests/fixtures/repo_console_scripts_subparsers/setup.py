import setuptools

setuptools.setup(
    name="multitool",
    version="1.0.0",
    packages=setuptools.find_packages(),
    entry_points={
        "console_scripts": ["multitool=multitool.cli:main"],
    },
)
