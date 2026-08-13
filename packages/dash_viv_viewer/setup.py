from pathlib import Path

from setuptools import setup, find_packages


ROOT_README = Path(__file__).resolve().parents[2] / "README.md"

setup(
    name='dash_viv_viewer',
    version='0.1.0',
    author='Your Name',
    description='Dash component for Viv WebGL image viewer with ROI drawing',
    long_description=ROOT_README.read_text(encoding="utf-8") if ROOT_README.exists() else '',
    long_description_content_type='text/markdown',
    packages=find_packages(),
    include_package_data=True,
    package_data={
        'dash_viv_viewer': ['*.js', '*.js.map', 'metadata.json'],
    },
    install_requires=['dash>=2.9.0'],
    python_requires='>=3.8',
)
