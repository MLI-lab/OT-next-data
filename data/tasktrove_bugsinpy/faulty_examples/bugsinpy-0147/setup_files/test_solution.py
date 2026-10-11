import pytest
from solution import *

class TestScatter:
    def test_scatter_unfilled_edgecolors(self):
        """Test that edgecolors are set correctly for unfilled markers."""
        ax = Axes()
        
        # Using an unfilled marker (filled=False)
        marker_obj = Marker(filled=False)
        
        # Call scatter method
        collection = ax.scatter(c='red', edgecolors='blue', linewidths=2)
        
        # Verify that edgecolors is set to the face color (c='red') for unfilled markers
        assert collection.edgecolors == 'red', (
            f"Expected edgecolors to be 'red' for unfilled markers, "
            f"but got {collection.edgecolors}"
        )

    def test_scatter_filled_edgecolors(self):
        """Test that edgecolors are set independently for filled markers."""
        ax = Axes()
        
        # Using a filled marker (filled=True)
        marker_obj = Marker(filled=True)
        
        # Call scatter method
        collection = ax.scatter(c='green', edgecolors='orange', linewidths=2)
        
        # Verify that edgecolors is set to 'orange' for filled markers
        assert collection.edgecolors == 'orange', (
            f"Expected edgecolors to be 'orange' for filled markers, "
            f"but got {collection.edgecolors}"
        )

    def test_scatter_unfilled_default_edgecolors(self):
        """Test that edgecolors falls back to facecolor when not specified for unfilled markers."""
        ax = Axes()
        
        # Using an unfilled marker (filled=False) and no explicit edgecolors
        marker_obj = Marker(filled=False)
        
        # Call scatter method with no edgecolors
        collection = ax.scatter(c='blue')
        
        # Verify that edgecolors is set to facecolor (c='blue') for unfilled markers
        assert collection.edgecolors == 'blue', (
            f"Expected edgecolors to be 'blue' for unfilled markers, "
            f"but got {collection.edgecolors}"
        )