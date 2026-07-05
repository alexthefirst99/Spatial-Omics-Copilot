"""Set up the fair package."""
from setuptools import setup, find_packages
import os

# Function to read requirements from a file and ignore comments
def read_requirements(file_name):
    with open(file_name, 'r') as file:
        requirements = []
        for line in file:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # requirements.txt may contain pip-only local installs such as
            # "-e ./packages/dash_viv_viewer"; those are valid for pip but not for
            # setup.py install_requires.
            if line.startswith(('-e ', '--editable ', '-r ', '--requirement ')):
                continue
            requirements.append(line)
        return requirements

# Determine the appropriate requirements file
requirements_file = 'requirements.txt'

# Read the requirements from the file
additional_requirements = read_requirements(requirements_file)

setup(
    name='niceview',
    version='0.3.0',
    packages=find_packages(where='src') + find_packages(include=['app', 'app.*']),
    package_dir={
        '': 'src',
        'app': 'app',
    },
    entry_points={
        'console_scripts': [
            'mjolnir=app.app:main',
        ],
    },
    install_requires=additional_requirements,
    package_data={
        'app': ['assets/*'],
    },
)
