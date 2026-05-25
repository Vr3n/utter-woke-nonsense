import time

from django.db import connection


class CaptureQueries:
    """Context manager that captures SQL query count and wall-clock duration.

    Usage::

        with CaptureQueries() as perf:
            results = list(MyModel.objects.filter(...))

        print(f"{perf.count} queries in {perf.duration:.0f}ms")

    The .queries property exposes the full list of captured queries for
    debugging. Only meaningful when DEBUG=True (connection.queries is
    empty otherwise).
    """

    def __enter__(self):
        self.baseline = len(connection.queries)
        self.start = time.monotonic()
        return self

    def __exit__(self, *args):
        self.count = len(connection.queries) - self.baseline
        self.duration = (time.monotonic() - self.start) * 1000

    @property
    def queries(self):
        return connection.queries[self.baseline :]
