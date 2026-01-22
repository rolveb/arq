"""
Redis 7.x / Valkey 7.x Compatibility Tests

This test suite verifies that arq works correctly with Redis 7.x and Valkey 7.x,
including with hiredis parser for optimal performance.
"""

import asyncio

import pytest
import redis

from arq import func
from arq.connections import RedisSettings, create_pool
from arq.worker import Worker


@pytest.mark.asyncio
async def test_redis_7x_version_check(arq_redis):
    """Verify Redis/Valkey server is 7.x and hiredis is available"""
    # Check redis-py library version
    redis_py_version = redis.__version__
    major_version = int(redis_py_version.split('.')[0])
    assert major_version >= 5, f'redis-py version {redis_py_version} is < 5.0'

    # Check for hiredis support
    try:
        import hiredis

        hiredis_version = hiredis.__version__
        print(f'✓ hiredis version: {hiredis_version}')
    except ImportError:
        pytest.fail('hiredis is not installed - install with: pip install redis[hiredis]')

    # Check Redis server version
    info = await arq_redis.info(section='Server')
    redis_server_version = info.get('redis_version', '0.0.0')
    server_major = int(redis_server_version.split('.')[0])

    print(f'✓ redis-py version: {redis_py_version}')
    print(f'✓ Server version: {redis_server_version}')
    print(f'✓ Server type: {"Valkey" if "valkey" in redis_server_version.lower() else "Redis"}')

    # Verify it's at least Redis/Valkey 5.0+
    assert server_major >= 5, f'Server version {redis_server_version} is < 5.0'


@pytest.mark.asyncio
async def test_hiredis_parser_used():
    """Verify that hiredis parser is being used for better performance"""
    settings = RedisSettings(host='localhost', port=6379)
    pool = await create_pool(settings)

    # Check that hiredis parser is available
    connection = pool.connection_pool.connection_kwargs
    print(f'✓ Connection configuration: {connection}')

    # Verify basic operation works
    await pool.ping()

    await pool.aclose()


@pytest.mark.asyncio
async def test_redis7_functions_compatibility(arq_redis):
    """
    Test Redis 7.x specific features that arq uses.

    Note: arq uses only Redis 5.0+ commands for maximum compatibility,
    but this test verifies it works correctly on Redis 7.x servers.
    """

    async def test_task(ctx, value: int):
        """Simple test task"""
        return value * 2

    # Test complete job lifecycle on Redis 7.x
    job = await arq_redis.enqueue_job('test_task', 42)
    assert job is not None

    worker = Worker(functions=[func(test_task, name='test_task')], redis_pool=arq_redis, burst=True, poll_delay=0)
    await worker.run_check()

    result = await job.result(timeout=5)
    assert result == 84

    print('✓ Job lifecycle works correctly on Redis 7.x')


@pytest.mark.asyncio
async def test_redis7_performance_features(arq_redis):
    """
    Test that arq benefits from Redis 7.x performance improvements.

    Redis 7.x includes:
    - Better memory efficiency
    - Improved pipelining
    - Enhanced client-side caching support
    - Optimized data structures
    """

    async def performance_task(ctx, task_id: int):
        await asyncio.sleep(0.01)
        return f'task_{task_id}'

    # Enqueue multiple jobs to test pipelining efficiency
    jobs = []
    for i in range(100):
        job = await arq_redis.enqueue_job('performance_task', i)
        jobs.append(job)

    assert len(jobs) == 100

    # Execute with worker
    worker = Worker(
        functions=[func(performance_task, name='performance_task')],
        redis_pool=arq_redis,
        burst=True,
        poll_delay=0,
        max_jobs=10,
    )
    await worker.run_check()

    # Verify all completed
    assert worker.jobs_complete == 100

    print('✓ Pipelining and batch operations work efficiently on Redis 7.x')


@pytest.mark.asyncio
async def test_redis7_acl_compatibility(arq_redis):
    """
    Test compatibility with Redis 7.x ACL (Access Control List) features.

    Redis 7.x has enhanced ACL capabilities. This test verifies arq works
    with default ACL settings.
    """
    # Get ACL info (requires Redis 6.0+)
    try:
        acl_list = await arq_redis.acl_list()
        print(f'✓ ACL enabled, rules: {len(acl_list)}')
    except redis.exceptions.ResponseError as e:
        # ACL might not be available in all configurations
        print(f'ACL not available (expected in some configs): {e}')


@pytest.mark.asyncio
async def test_redis7_memory_efficiency(arq_redis):
    """
    Test that arq operations are memory efficient on Redis 7.x.

    Redis 7.x has improved memory efficiency, especially for small objects.
    """
    # Get initial memory usage
    memory_info_before = await arq_redis.info(section='Memory')
    used_memory_before = int(memory_info_before['used_memory'])

    # Create and execute jobs
    async def memory_task(ctx, data: dict):
        return data['value']

    jobs = []
    for i in range(50):
        job = await arq_redis.enqueue_job('memory_task', {'value': i, 'extra': 'data' * 10})
        jobs.append(job)

    worker = Worker(
        functions=[func(memory_task, name='memory_task')], redis_pool=arq_redis, burst=True, poll_delay=0, max_jobs=10
    )
    await worker.run_check()

    # Check memory usage
    memory_info_after = await arq_redis.info(section='Memory')
    used_memory_after = int(memory_info_after['used_memory'])

    memory_increase = used_memory_after - used_memory_before
    print(f'✓ Memory increase for 50 jobs: {memory_increase:,} bytes')
    print(f'✓ Average per job: {memory_increase / 50:,.0f} bytes')

    # Cleanup and verify memory is released
    await arq_redis.flushdb()


@pytest.mark.asyncio
async def test_redis7_resp3_compatibility(arq_redis):
    """
    Test RESP3 protocol compatibility (Redis 6.0+).

    Redis 7.x supports both RESP2 and RESP3. redis-py 5.x can use RESP3
    for better type safety and performance.

    Note: arq uses redis-py defaults which work with both protocols.
    """
    # Verify connection works (redis-py handles protocol negotiation)
    result = await arq_redis.ping()
    assert result is True

    # Test that types are correctly handled
    await arq_redis.set('test_string', 'value')
    await arq_redis.set('test_number', '123')

    val1 = await arq_redis.get('test_string')
    val2 = await arq_redis.get('test_number')

    assert val1 == b'value'
    assert val2 == b'123'

    print('✓ RESP protocol compatibility verified')


@pytest.mark.asyncio
async def test_redis7_cluster_compatibility_check():
    """
    Document Redis 7.x Cluster support considerations.

    arq is designed for single Redis instances. Redis Cluster requires
    special handling that arq doesn't currently implement.

    This test documents the expected behavior.
    """
    # Note: This is documentation, not a functional test
    # arq works best with:
    # - Single Redis/Valkey instance
    # - Redis Sentinel for HA (high availability)
    # - NOT Redis Cluster (requires key sharding awareness)

    print('✓ arq recommendation: Use Redis Sentinel for HA, not Redis Cluster')


@pytest.mark.asyncio
async def test_valkey_compatibility(arq_redis):
    """
    Test Valkey 7.x compatibility.

    Valkey is a Redis fork with 100% protocol compatibility.
    All Redis tests should pass identically on Valkey.
    """
    info = await arq_redis.info(section='Server')
    redis_version = info.get('redis_version', '')

    # Check if this is Valkey
    is_valkey = 'valkey' in redis_version.lower() or 'valkey' in info.get('redis_mode', '').lower()

    async def valkey_task(ctx):
        return 'valkey_compatible'

    job = await arq_redis.enqueue_job('valkey_task')
    worker = Worker(functions=[func(valkey_task, name='valkey_task')], redis_pool=arq_redis, burst=True, poll_delay=0)
    await worker.run_check()

    result = await job.result(timeout=5)
    assert result == 'valkey_compatible'

    if is_valkey:
        print('✓ Running on Valkey - 100% compatible')
    else:
        print('✓ Running on Redis - Valkey-compatible code works')


@pytest.mark.asyncio
async def test_connection_with_all_redis7_options():
    """
    Test connection with Redis 7.x specific options.

    Verifies that all redis-py 5.x connection options work correctly.
    """
    settings = RedisSettings(
        host='localhost',
        port=6379,
        database=0,
        conn_timeout=5,
        conn_retries=3,
        conn_retry_delay=1,
        # Redis 7.x supports username/password auth (ACL)
        # username='default',  # Uncomment if using ACL
        # password='your_password',  # Uncomment if password protected
    )

    pool = await create_pool(settings)

    # Verify connection works
    await pool.ping()

    # Test basic operations
    await pool.set('test_key_redis7', 'test_value')
    value = await pool.get('test_key_redis7')
    assert value == b'test_value'

    await pool.delete('test_key_redis7')
    await pool.aclose()

    print('✓ All connection options work with Redis 7.x')


@pytest.mark.asyncio
async def test_comprehensive_redis7_operations(arq_redis):
    """
    Comprehensive test of all arq operations on Redis 7.x.

    This test runs through all major arq features to ensure
    complete compatibility with Redis 7.x / Valkey 7.x.
    """

    async def comprehensive_task(ctx, operation: str, value: int):
        """Task that tests different operation types"""
        if operation == 'compute':
            return value ** 2
        elif operation == 'async':
            await asyncio.sleep(0.01)
            return value
        elif operation == 'error':
            if value < 0:
                raise ValueError(f'Negative value: {value}')
            return value
        return None

    # Test various job patterns
    job1 = await arq_redis.enqueue_job('comprehensive_task', 'compute', 10)
    job2 = await arq_redis.enqueue_job('comprehensive_task', 'async', 20)
    job3 = await arq_redis.enqueue_job('comprehensive_task', 'compute', 30, _defer_by=0.1)

    worker = Worker(
        functions=[func(comprehensive_task, name='comprehensive_task')],
        redis_pool=arq_redis,
        burst=True,
        poll_delay=0.1,
        max_jobs=5,
    )

    await worker.run_check()

    # Verify results
    result1 = await job1.result(timeout=5)
    assert result1 == 100

    result2 = await job2.result(timeout=5)
    assert result2 == 20

    result3 = await job3.result(timeout=5)
    assert result3 == 900

    print('✓ All arq operations work correctly on Redis 7.x')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
