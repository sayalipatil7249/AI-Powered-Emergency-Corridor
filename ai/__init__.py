import os

# The models predict one row at a time, every simulated second. By
# default scikit-learn spreads each prediction over every CPU core, which
# makes many small predictions slow and, with several simulations running
# at once, overloads the whole computer. One thread each is fastest here.
# Must be set before scikit-learn is imported.
os.environ.setdefault("OMP_NUM_THREADS", "1")
