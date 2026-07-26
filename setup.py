from pathlib import Path

from setuptools import find_packages, setup

this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text()

girder_version = "5.0.13.dev27"

setup(
    name="girder-dashboards",
    version="0.1.1",
    description="Girder plugin adding lightweight, interactive dashboards",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Kacper Kowalik",
    license="BSD-3-Clause",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Environment :: Web Environment",
        "License :: OSI Approved :: BSD License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    packages=find_packages(exclude=["girder_dashboards.tests"]),
    include_package_data=True,
    python_requires=">=3.10",
    install_requires=[
        f"girder>={girder_version}",
        # The Precipitate Analysis dashboard schedules its work as a job on the
        # Celery 'local' queue, with an in-process fallback. Both need these.
        "girder-jobs>=5",
        "girder-worker>=5",
    ],
    extras_require={
        # The scientific stack the precipitate analysis itself needs. Kept out of
        # install_requires so a Girder that only wants the other dashboards is not
        # made to carry scikit-image; install it in *both* the Girder and the
        # Celery worker environment to use that dashboard.
        "precipitate": [
            "numpy>=1.24",
            "scipy>=1.10",
            "scikit-image>=0.21",
            "tifffile>=2023.7.10",
            # TIFFs out of SEM software are usually LZW- or deflate-compressed,
            # which tifffile delegates to imagecodecs.
            "imagecodecs>=2023.3.16",
            "pillow>=9",
            "imageio>=2.28",
        ],
    },
    entry_points={
        "girder.plugin": ["dashboards = girder_dashboards:DashboardsPlugin"],
        # Makes this package's Celery tasks discoverable by girder_worker, which
        # builds CELERY_INCLUDE from this entry point group at worker startup.
        "girder_worker_plugins": [
            "dashboards = girder_dashboards.worker_plugin:DashboardsWorkerPlugin"
        ],
    },
    zip_safe=False,
)
