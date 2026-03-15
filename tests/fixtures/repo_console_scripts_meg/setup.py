import setuptools

setuptools.setup(
    name="megtool",
    version="1.0.0",
    packages=setuptools.find_packages(),
    entry_points={
        "console_scripts": ["megtool=megtool.cli:main"],
    },
)
