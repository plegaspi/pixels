import multiprocessing

from pixels.gui import main_gui

if __name__ == "__main__":
    # Windows starts multiprocessing children by re-running this script; the
    # guard keeps them from opening another GUI, and freeze_support() lets them
    # start inside a PyInstaller-built executable.
    multiprocessing.freeze_support()
    main_gui()
