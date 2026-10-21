import pytest

from config.clusters import CLUSTERS
from hpc.validation_submit import allocated_cpu_count


@pytest.mark.parametrize('cluster_name,partition,requested,expected', [
    ('helma', 'cpu', 12, 48),
    ('helma', 'cpu', 49, 96),
    ('helma', 'preempt_cpu', 12, 12),
    ('helma', 'preempt_cpu', 49, 49),
    ('zih', 'barnard', 12, 12),
    ('rwth', 'c23ms', 12, 12),
])
def test_partition_cpu_granularity(cluster_name, partition, requested, expected):
    cluster = next(c for c in CLUSTERS if c.name == cluster_name)
    assert allocated_cpu_count(cluster, partition, requested) == expected
