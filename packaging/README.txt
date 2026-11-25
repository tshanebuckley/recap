recap @VERSION@ (Reconstruction of Cellular Architecture and Phenotypes): offline installer

recap is under development: check its counts against manual counts before relying on them and
keep copies of your images. It is provided "as is", without warranty (MIT license, LICENSE).

Everything recap needs is in this folder (Python, StarDist/TensorFlow, napari, the StarDist
model) except Fiji. No internet is needed to install or to run.

Windows 10/11:      double-click "Install recap.bat"
Mac (Apple silicon): double-click "Install recap.command" (if macOS blocks it: right-click > Open)
Linux:              ./install.sh

The installer unpacks recap into your user folder (no administrator rights needed) and puts a
recap icon on the desktop. Installing takes a few minutes; recap uses about 7 GB once installed
and needs about 12 GB free while installing.

Fiji (for counting nuclei by hand): download it from https://fiji.sc, unzip it, then tell recap
where it is: in the app, More > Fiji location... > Choose Fiji folder, or in a terminal
recap fiji set C:/Users/you/Fiji.app  (the installer prints the exact command).

Documentation: docs/guide/ in the recap repository (start with docs/guide/README.md).
