## Fixed

- Akai MPC: installed with the open-mpc Plugin Manager (version 1.2.0 and older), Frodhes did not show up in the
  plugin browser: the manager pointed the MPC at the plugin's binary by its bare file name, and the binary sat in a
  subfolder. The two binaries (32-bit and 64-bit) are now at the top of the plugin folder, where every installer
  looks. Installing by hand: replace the whole folder "Frodhes - VST - Frodhes" (delete the old one first).
