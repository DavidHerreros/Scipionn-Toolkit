echo "-------------- Installing Flexutils-toolkit --------------"

# Name of the Python package to check
python_package="open3d"

# Function to check if a Python package is installed
is_python_package_installed() {
  conda activate flexutils-tensorflow
    if pip list | grep -F "$1" &> /dev/null; then
        return 0
    else
        return 1
    fi
  conda deactivate
}

# Activate conda in shell
if which conda | sed 's: ::g' &> /dev/null ; then
  CONDABIN=$(which conda | sed 's: ::g')
  eval "$($CONDABIN shell.bash hook)"
else
  echo "Conda not found in path - Exiting"
  exit 1
fi

# Install pynvml package in current env (installation dependency)
conda create -y -n install-temp python=3.9
conda activate install-temp
echo "Adding installation dependencies to current env..."
pip install pynvml packaging
echo "...Done"

# Run Conda installation
if python tensorflow_toolkit/build.py ; then
  echo "Environment: flexutils-tensorflow built succesfully"
else
  echo "Error when building flexutils-tensorflow - Exiting"
  if which conda | sed 's: ::g' ; then
    conda env remove -n flexutils-tensorflow
  fi
  exit 1
fi

# Deactivate and remove temporal installation environment
conda deactivate
conda env remove -n install-temp

# Install flexutils-toolkit in flexutils-tensorflow env
conda activate flexutils-tensorflow
pip install -e . -v
conda deactivate
echo "Package flexutils-toolkit succesfully installed in flexutils-tensorlfow env"
echo "-------------- Flexutils-toolkit installation finished! --------------"

# Install Open3D
if is_python_package_installed $python_package; then
  echo "Open3D package is already installed. Skipping..."
else
  echo "Installing Open3D..."
  bash ./install_open3d.sh
  echo "Done..."
fi
