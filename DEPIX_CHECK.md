# Depix Launch Check

## Result: OK

Depix can be installed and launched successfully on this system.

### Installation Method
- Cloned from https://github.com/beurtschipper/Depix
- Dependency: `Pillow` (installed via pip)

### Test Run
```
python3 depix.py -p images/testimages/testimage3_pixels.png \
  -s images/searchimages/debruin_sublime_Linux_small.png \
  -o /tmp/depix_output.png
```

Depix launched, processed the test image, and produced output without errors.

### Usage
```
depix.py -p <pixelated_image> -s <search_image> [-o <output_image>]
```

### Notes
- The `depix` PyPI package (v1.0.2) fails to build due to setuptools incompatibility
- Cloning from GitHub and running `depix.py` directly works fine
- Requires Python 3 and Pillow
