"""Give Maven's bootstrap JVM the build proxy before settings.xml is loaded."""
from contextlib import contextmanager
import os


@contextmanager
def maven_wrapper_proxy(proxy_properties):
    original = os.environ.get('MAVEN_OPTS')
    # The wrapper downloads Maven before Maven can read its proxy settings.
    # Reuse the JVM proxy and bypass rules used for Gradle on this cluster.
    values = proxy_properties(os.environ)
    options = ' '.join('-D' + key.removeprefix('systemProp.') + '=' + value
                       for key, value in values.items())
    try:
        if options:
            os.environ['MAVEN_OPTS'] = ' '.join(filter(None, (original, options)))
        yield
    finally:
        if original is None:
            os.environ.pop('MAVEN_OPTS', None)
        else:
            os.environ['MAVEN_OPTS'] = original
