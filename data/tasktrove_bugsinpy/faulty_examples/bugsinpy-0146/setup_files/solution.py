import logging

class RendererBase:
    pass

class LocationEvent:
    pass

class KeyEvent(LocationEvent):
    def __init__(self, key):
        self.key = key

class Done(Exception):
    pass

def _get_renderer(figure, print_method=None, *, draw_disabled=False):
    """
    Get the renderer that would be used to save a `~.Figure`, and cache it on
    the figure.

    If *draw_disabled* is True, additionally replace drawing methods on
    *renderer* by no-ops.  This is used by the tight-bbox-saving renderer,
    which needs to walk through the artist tree to compute the tight-bbox, but
    for which the output file may be closed early.
    """
    # This is implemented by triggering a draw, then immediately jumping out of
    # Figure.draw() by raising an exception.
    raise NotImplementedError("This function is meant to be overridden.")

def get_tight_layout_figure(figure, axes, subplotspec_list, renderer,
                            pad=None, h_pad=None, w_pad=None, rect=None):
    raise NotImplementedError("This function is meant to be overridden.")

class FigureCanvasBase:
    def __init__(self, figure):
        self.figure = figure

    def draw(self, print_method, orientation=None):
        renderer = _get_renderer(
            self.figure,
            print_method
        )
        self.figure.draw(renderer)

class Figure:
    def __init__(self):
        self.axes = []
    
    def draw(self, renderer):
        raise NotImplementedError("This function is meant to be overridden.")

    def get_tightbbox(self, renderer, bbox_extra_artists=None):
        raise NotImplementedError("This function is meant to be overridden.")

    def subplots_adjust(self, **kwargs):
        raise NotImplementedError("This function is meant to be overridden.")

def get_renderer(fig):
    canvas = FigureCanvasBase(fig)
    return canvas.draw

__all__ = ['RendererBase', 'LocationEvent', 'KeyEvent', '_get_renderer', 'get_tight_layout_figure', 'FigureCanvasBase', 'Figure', 'get_renderer']