from setuptools import setup, find_packages

setup(
    name='dash_viv_viewer',
    version='0.1.0',
    author='Your Name',
    description='Dash component for Viv WebGL image viewer with ROI drawing',
    long_description=open('README.md').read() if __import__('os').path.exists('README.md') else '',
    packages=find_packages(),
    include_package_data=True,
    package_data={
        'dash_viv_viewer': ['*.js', '*.js.map', 'metadata.json'],
    },
    install_requires=['dash>=2.9.0'],
    python_requires='>=3.8',
)
