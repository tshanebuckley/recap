"""recap desktop app (napari). A deliberately small starting point for the GUI.

Layout:
    data.py             what to show: an image's channels and its saved masks (no Qt; tested everywhere)
    single_instance.py  one running app per user (Qt lock file + local socket)
    app.py              the napari window: layers, and bringing the window forward on a second launch

Start it with `recap gui` (or the desktop icon from `recap desktop-icon`). Nothing here is imported
by the command line or the pipeline unless the GUI is started, so recap works without napari.
"""
