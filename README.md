# Browser for gigapixel images and multi-omics data visualization and analysis

## Installation
Currently, you can install the app and run it locally. We plan to launch a web server, so users don't need to install anything and will be able to upload their data for visualization.

### 1. Create a conda environment.
``conda create -n mjolnir python=3.9``
### 2. Activate the environment.
``conda activate mjolnir``
### 3. Download this repository.
  Go to the mjolnir folder
### 4. Install system dependencies (required for ultra-fast large image processing).
For macOS: ``brew install vips``
For Ubuntu/Linux: ``sudo apt-get install libvips-dev``
Or via conda: ``conda install -c conda-forge pyvips``
### 5. Install Mjolnir.
``pip install .``
### 6. Launch the webapp locally. Your web browser should automatically open the app after you run the following command.
``python app/app.py --token {anything you want}`` 

---
## Test dataset
- The Human heart myocardial infarction data [dropbox link](https://www.dropbox.com/scl/fi/6cw3iet2q9c0qb3f51cej/demo_data.tgz?rlkey=4ka207bvbi1u4se3g9u0hwxk6&dl=0)
- The ductal_carcinoma_in_stu [dropbox link](https://www.dropbox.com/scl/fi/kz742n3c205zna9v2k3xe/breast_cancer.tar.gz?rlkey=twnd1dpl44vq9g3e7ik2pm7bd&dl=0)
---
## Video tutorial
- [Mjolnir: Visualization of gene expression at spot and cell level](https://drive.google.com/file/d/1C-bLPtIpAMSOBUauvxYgpxobOXrxf6_L/view?usp=sharing)
- [Mjolnir: Visualization of pathway enrichment heatmaps](https://drive.google.com/file/d/1UKshoNFEvGxeSybW035S4DIaUo4dS8fs/view?usp=sharing)
- [Mjolnir: Semi-supervised annotation of cells](https://drive.google.com/file/d/1Bum4SdfqhBN8IEF6K36QM0INo2WKQces/view?usp=drive_link)
