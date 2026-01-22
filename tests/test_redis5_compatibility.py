"""
Redis 5+ Compatibility Tests for arq

This test suite verifies that arq works correctly with redis-py >= 5.0
and Redis server >= 5.0. It covers all critical Redis operations used by arq.
"""

import asyncio
import functools
from datetime import datetime, timedelta
from unittest.mock import patch

import msgpack
import pytest
import redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ResponseError, WatchError

from arq import Retry as ArqRetry
from arq import cron
from arq.connections import ArqRedis, RedisSettings, create_pool
from arq.constants import default_queue_name, job_key_prefix, result_key_prefix
from arq.jobs import Job, JobStatus
from arq.worker import Worker, func


# Test 1: Redis Version Check
@pytest.mark.asyncio
async def test_redis_version_check(arq_redis):
    """Verify Redis server and redis-py library versions are >= 5.0"""
    # Check redis-py library version
    redis_py_version = redis.__version__
    major_version = int(redis_py_version.split('.')[0])
    assert major_version >= 5, f'redis-py version {redis_py_version} is < 5.0'

    # Check Redis server version
    info = await arq_redis.info(section='Server')
    redis_server_version = info.get('redis_version', '0.0.0')
    server_major = int(redis_server_version.split('.')[0])
    assert server_major >= 5, f'Redis server version {redis_server_version} is < 5.0'

    print(f'✓ redis-py version: {redis_py_version}')
    print(f'✓ Redis server version: {redis_server_version}')


# Test 2: Basic Connection Operations
@pytest.mark.asyncio
async def test_basic_connection_operations(arq_redis):
    """Test basic Redis connection and operations"""
    # PING
    result = await arq_redis.ping()
    assert result is True

    # SET/GET
    await arq_redis.set('test_key', 'test_value')
    value = await arq_redis.get('test_key')
    assert value == b'test_value'

    # EXISTS
    exists = await arq_redis.exists('test_key')
    assert exists == 1

    # DELETE
    deleted = await arq_redis.delete('test_key')
    assert deleted == 1

    # EXISTS after delete
    exists = await arq_redis.exists('test_key')
    assert exists == 0


@pytest.mark.asyncio
async def test_connection_pool_creation():
    """Test connection pool creation with various settings"""
    # Default settings
    pool1 = await create_pool()
    await pool1.ping()
    await pool1.aclose()

    # With custom settings
    settings = RedisSettings(
        host='localhost',
        port=6379,
        database=0,
        conn_timeout=2,
        conn_retries=3,
    )
    pool2 = await create_pool(settings)
    await pool2.ping()
    await pool2.aclose()

    # From DSN
    settings_dsn = RedisSettings.from_dsn('redis://localhost:6379/0')
    pool3 = await create_pool(settings_dsn)
    await pool3.ping()
    await pool3.aclose()


@pytest.mark.asyncio
async def test_connection_with_retry():
    """Test connection retry configuration"""
    settings = RedisSettings(
        host='localhost',
        port=6379,
        retry=Retry(backoff=NoBackoff(), retries=3),
        retry_on_timeout=True,
        retry_on_error=[redis.exceptions.ConnectionError],
    )
    pool = await create_pool(settings)
    await pool.ping()
    await pool.aclose()


# Test 3: Sorted Set Operations (Job Queues)
@pytest.mark.asyncio
async def test_sorted_set_operations(arq_redis):
    """Test sorted set operations used for job queues"""
    queue_name = 'test_queue'

    # ZADD - add jobs with scores
    score1 = 1000
    score2 = 2000
    score3 = 3000

    await arq_redis.zadd(queue_name, {'job1': score1, 'job2': score2, 'job3': score3})

    # ZCARD - get queue size
    size = await arq_redis.zcard(queue_name)
    assert size == 3

    # ZSCORE - get specific job score
    job_score = await arq_redis.zscore(queue_name, 'job2')
    assert int(job_score) == score2

    # ZRANGE - get job range
    jobs = await arq_redis.zrange(queue_name, 0, -1)
    assert len(jobs) == 3
    assert b'job1' in jobs

    # ZRANGEBYSCORE - get jobs by score
    jobs_by_score = await arq_redis.zrangebyscore(queue_name, min=1500, max=3500)
    assert len(jobs_by_score) == 2
    assert b'job2' in jobs_by_score
    assert b'job3' in jobs_by_score

    # ZINCRBY - increment score
    await arq_redis.zincrby(queue_name, 500, 'job1')
    new_score = await arq_redis.zscore(queue_name, 'job1')
    assert int(new_score) == score1 + 500

    # ZREM - remove job
    removed = await arq_redis.zrem(queue_name, 'job2')
    assert removed == 1

    size = await arq_redis.zcard(queue_name)
    assert size == 2

    # ZREMRANGEBYSCORE - remove by score range
    removed_range = await arq_redis.zremrangebyscore(queue_name, min=0, max=2000)
    assert removed_range >= 1

    # Cleanup
    await arq_redis.delete(queue_name)


# Test 4: Key Expiration Operations
@pytest.mark.asyncio
async def test_key_expiration_operations(arq_redis):
    """Test key expiration operations"""
    # PSETEX - set with expiration in milliseconds
    await arq_redis.psetex('expire_key', 1000, b'value')

    value = await arq_redis.get('expire_key')
    assert value == b'value'

    # Wait for expiration
    await asyncio.sleep(1.1)
    value = await arq_redis.get('expire_key')
    assert value is None

    # EXPIRE - set expiration on existing key
    await arq_redis.set('expire_key2', 'value2')
    await arq_redis.expire('expire_key2', 1)

    value = await arq_redis.get('expire_key2')
    assert value == b'value2'

    await asyncio.sleep(1.1)
    value = await arq_redis.get('expire_key2')
    assert value is None


# Test 5: Transaction Operations (WATCH/MULTI/EXEC)
@pytest.mark.asyncio
async def test_transaction_operations(arq_redis):
    """Test Redis transaction operations with WATCH/MULTI/EXEC"""
    await arq_redis.set('counter', '0')

    # Successful transaction
    async with arq_redis.pipeline(transaction=True) as pipe:
        await pipe.watch('counter')
        current = await pipe.get('counter')
        assert current == b'0'

        pipe.multi()
        pipe.set('counter', '1')
        pipe.incr('counter')
        result = await pipe.execute()

    assert result[-1] == 2

    # Cleanup
    await arq_redis.delete('counter')


@pytest.mark.asyncio
async def test_watch_error_handling(arq_redis):
    """Test WatchError handling in transactions"""
    key = 'watched_key'
    await arq_redis.set(key, '0')

    # Create two pipelines watching the same key
    async with arq_redis.pipeline(transaction=True) as pipe1:
        await pipe1.watch(key)

        # Modify key from another connection
        await arq_redis.incr(key)

        # This should raise WatchError
        pipe1.multi()
        pipe1.set(key, '100')

        with pytest.raises(WatchError):
            await pipe1.execute()

    # Cleanup
    await arq_redis.delete(key)


# Test 6: Pipeline Operations
@pytest.mark.asyncio
async def test_pipeline_operations(arq_redis):
    """Test pipeline operations"""
    # Transaction pipeline
    async with arq_redis.pipeline(transaction=True) as pipe:
        pipe.set('key1', 'value1')
        pipe.set('key2', 'value2')
        pipe.get('key1')
        pipe.get('key2')
        results = await pipe.execute()

    assert results[2] == b'value1'
    assert results[3] == b'value2'

    # Non-transaction pipeline
    async with arq_redis.pipeline(transaction=False) as pipe:
        pipe.get('key1')
        pipe.get('key2')
        results = await pipe.execute()

    assert results[0] == b'value1'
    assert results[1] == b'value2'

    # Cleanup
    await arq_redis.delete('key1', 'key2')


# Test 7: Job Enqueue and Execution
@pytest.mark.asyncio
async def test_job_enqueue_and_execution(arq_redis):
    """Test complete job lifecycle: enqueue, execute, retrieve result"""

    async def sample_task(ctx, name: str):
        return f'Hello, {name}!'

    # Enqueue job
    job = await arq_redis.enqueue_job('sample_task', 'World')
    assert job is not None
    assert isinstance(job, Job)

    # Check job exists in queue
    score = await arq_redis.zscore(default_queue_name, job.job_id)
    assert score is not None

    # Check job key exists
    job_key = job_key_prefix + job.job_id
    exists = await arq_redis.exists(job_key)
    assert exists == 1

    # Execute job with worker
    worker = Worker(functions=[func(sample_task, name='sample_task')], redis_pool=arq_redis, burst=True, poll_delay=0)
    await worker.run_check()

    # Retrieve result
    result = await job.result(timeout=5)
    assert result == 'Hello, World!'

    # Check job status
    status = await job.status()
    assert status == JobStatus.complete


@pytest.mark.asyncio
async def test_job_with_different_parameters(arq_redis):
    """Test job enqueuing with various parameters"""

    async def param_task(ctx, a: int, b: int, operation: str = 'add'):
        if operation == 'add':
            return a + b
        elif operation == 'multiply':
            return a * b
        return 0

    # Test with positional args
    job1 = await arq_redis.enqueue_job('param_task', 5, 3)
    assert job1 is not None

    # Test with keyword args
    job2 = await arq_redis.enqueue_job('param_task', 4, 7, operation='multiply')
    assert job2 is not None

    # Test with custom job_id
    job3 = await arq_redis.enqueue_job('param_task', 10, 20, _job_id='custom_job_123')
    assert job3.job_id == 'custom_job_123'

    # Test job uniqueness - same job_id should return None
    job4 = await arq_redis.enqueue_job('param_task', 1, 1, _job_id='custom_job_123')
    assert job4 is None

    # Execute all jobs
    worker = Worker(functions=[func(param_task, name='param_task')], redis_pool=arq_redis, burst=True, poll_delay=0)
    await worker.run_check()

    # Check results
    result1 = await job1.result(timeout=5)
    assert result1 == 8

    result2 = await job2.result(timeout=5)
    assert result2 == 28

    result3 = await job3.result(timeout=5)
    assert result3 == 30


@pytest.mark.asyncio
async def test_deferred_job(arq_redis):
    """Test job deferral"""

    async def deferred_task(ctx):
        return 'executed'

    # Defer job by 1 second
    job = await arq_redis.enqueue_job('deferred_task', _defer_by=1)
    assert job is not None

    # Job should not execute immediately
    worker = Worker(
        functions=[func(deferred_task, name='deferred_task')], redis_pool=arq_redis, burst=True, poll_delay=0.1
    )

    # Try to run immediately - should not find any jobs ready
    await worker._poll_iteration()
    assert worker.jobs_complete == 0

    # Wait for job to be ready
    await asyncio.sleep(1.1)

    # Now job should execute
    await worker.run_check()
    result = await job.result(timeout=5)
    assert result == 'executed'


@pytest.mark.asyncio
async def test_job_with_expiration(arq_redis):
    """Test job expiration"""

    async def expire_task(ctx):
        return 'should_not_execute'

    # Enqueue job that expires in 100ms
    job = await arq_redis.enqueue_job('expire_task', _defer_by=2, _expires=0.1)
    assert job is not None

    # Wait for job to expire
    await asyncio.sleep(2.5)

    # Try to execute - job should be expired
    worker = Worker(functions=[func(expire_task, name='expire_task')], redis_pool=arq_redis, burst=True, poll_delay=0)
    await worker.async_run()

    # Job should have failed due to expiration
    assert worker.jobs_failed == 1


# Test 8: Job Retry and Failure
@pytest.mark.asyncio
async def test_job_retry(arq_redis):
    """Test job retry mechanism"""
    call_count = 0

    async def failing_task(ctx):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ArqRetry(defer=0.1)
        return 'success_after_retries'

    job = await arq_redis.enqueue_job('failing_task')

    worker = Worker(
        functions=[func(failing_task, name='failing_task')], redis_pool=arq_redis, burst=True, poll_delay=0.1
    )
    await worker.async_run()

    # Should have retried
    assert worker.jobs_retried >= 2
    assert call_count == 3

    result = await job.result(timeout=10)
    assert result == 'success_after_retries'


@pytest.mark.asyncio
async def test_job_max_retries(arq_redis):
    """Test job max retries exceeded"""

    async def always_failing_task(ctx):
        raise ValueError('always fails')

    job = await arq_redis.enqueue_job('always_failing_task')

    worker = Worker(
        functions=[func(always_failing_task, name='always_failing_task', max_tries=2)],
        redis_pool=arq_redis,
        burst=True,
        poll_delay=0,
        retry_jobs=False,  # Disable automatic retries
    )
    await worker.async_run()

    # Job should have failed
    assert worker.jobs_failed == 1


# Test 9: Stream Operations (New Feature)
@pytest.mark.asyncio
async def test_stream_operations(arq_redis):
    """Test Redis stream operations used by arq"""
    stream_name = 'test_stream'

    # XADD - add message to stream
    msg_id = await arq_redis.xadd(stream_name, {'field1': 'value1', 'field2': 'value2'})
    assert msg_id is not None

    # XGROUP CREATE - create consumer group
    try:
        await arq_redis.xgroup_create(stream_name, 'test_group', '0', mkstream=True)
    except ResponseError as e:
        if 'BUSYGROUP' not in str(e):
            raise

    # XREADGROUP - read from consumer group
    messages = await arq_redis.xreadgroup(
        groupname='test_group', consumername='consumer1', streams={stream_name: '>'}, count=10, block=100
    )

    if messages:
        assert len(messages) > 0
        # XACK - acknowledge message
        stream_data = messages[0]
        stream_msgs = stream_data[1]
        for msg in stream_msgs:
            msg_id = msg[0]
            await arq_redis.xack(stream_name, 'test_group', msg_id)

    # Cleanup
    await arq_redis.delete(stream_name)


@pytest.mark.asyncio
async def test_stream_mode_job_delivery(arq_redis):
    """Test job delivery via Redis streams"""

    async def stream_task(ctx, value: int):
        return value * 2

    # Enqueue job with stream mode
    job = await arq_redis.enqueue_job('stream_task', 42, _use_stream=True)
    assert job is not None

    # Create stream worker
    worker = Worker(
        functions=[func(stream_task, name='stream_task')], redis_pool=arq_redis, burst=True, stream=True, poll_delay=0
    )
    await worker.run_check()

    # Check result
    result = await job.result(timeout=5)
    assert result == 84


# Test 10: Serialization Tests
@pytest.mark.asyncio
async def test_msgpack_serialization():
    """Test custom msgpack serialization with Redis 5+"""
    pool = await create_pool(
        RedisSettings(), job_serializer=msgpack.packb, job_deserializer=functools.partial(msgpack.unpackb, raw=False)
    )

    async def msgpack_task(ctx, data: dict):
        return {'result': data['value'] * 2}

    job = await pool.enqueue_job('msgpack_task', {'value': 21})
    assert job is not None

    worker = Worker(
        functions=[func(msgpack_task, name='msgpack_task')],
        redis_pool=pool,
        burst=True,
        poll_delay=0,
        job_serializer=msgpack.packb,
        job_deserializer=functools.partial(msgpack.unpackb, raw=False),
    )
    await worker.run_check()

    result = await job.result(timeout=5)
    assert result == {'result': 42}

    await pool.aclose()


@pytest.mark.asyncio
async def test_large_payload(arq_redis):
    """Test handling of large payloads"""

    async def large_payload_task(ctx, data: list):
        return len(data)

    # Create large payload (1MB of data)
    large_data = list(range(100000))

    job = await arq_redis.enqueue_job('large_payload_task', large_data)
    assert job is not None

    worker = Worker(
        functions=[func(large_payload_task, name='large_payload_task')], redis_pool=arq_redis, burst=True, poll_delay=0
    )
    await worker.run_check()

    result = await job.result(timeout=10)
    assert result == 100000


# Test 11: Cron Jobs
@pytest.mark.asyncio
async def test_cron_job_with_redis5(arq_redis):
    """Test cron job scheduling with Redis 5+"""
    executed = []

    async def cron_task(ctx):
        executed.append(datetime.now())
        return 'cron_executed'

    # Create cron job that runs every second
    cron_job = cron(cron_task, second={0, 1, 2, 3, 4, 5}, run_at_startup=True, name='cron_task')

    worker = Worker(
        functions=[func(cron_task, name='cron_task')],
        cron_jobs=[cron_job],
        redis_pool=arq_redis,
        burst=True,
        poll_delay=0.1,
        max_burst_jobs=5,
    )
    await worker.run_check()

    # Cron job should have been executed at least once (run_at_startup=True)
    assert len(executed) >= 1


# Test 12: Concurrent Operations
@pytest.mark.asyncio
async def test_concurrent_job_enqueue(arq_redis):
    """Test concurrent job enqueueing"""

    async def concurrent_task(ctx, task_id: int):
        return task_id

    # Enqueue multiple jobs concurrently
    tasks = [arq_redis.enqueue_job('concurrent_task', i) for i in range(50)]
    jobs = await asyncio.gather(*tasks)

    # All jobs should be enqueued successfully
    assert len([j for j in jobs if j is not None]) == 50

    # Execute all jobs
    worker = Worker(
        functions=[func(concurrent_task, name='concurrent_task')],
        redis_pool=arq_redis,
        burst=True,
        poll_delay=0,
        max_jobs=10,
    )
    await worker.run_check()

    # All jobs should complete
    assert worker.jobs_complete == 50


@pytest.mark.asyncio
async def test_multiple_workers_same_queue(arq_redis):
    """Test multiple workers processing from the same queue"""

    async def worker_task(ctx, value: int):
        await asyncio.sleep(0.1)
        return value * 2

    # Enqueue jobs
    jobs = []
    for i in range(20):
        job = await arq_redis.enqueue_job('worker_task', i)
        jobs.append(job)

    # Create multiple workers
    worker1 = Worker(
        functions=[func(worker_task, name='worker_task')], redis_pool=arq_redis, burst=True, poll_delay=0, max_jobs=5
    )
    worker2 = Worker(
        functions=[func(worker_task, name='worker_task')], redis_pool=arq_redis, burst=True, poll_delay=0, max_jobs=5
    )

    # Run workers concurrently
    await asyncio.gather(worker1.async_run(), worker2.async_run())

    # All jobs should be processed
    total_completed = worker1.jobs_complete + worker2.jobs_complete
    assert total_completed == 20


# Test 13: Health Checks
@pytest.mark.asyncio
async def test_worker_health_check(arq_redis):
    """Test worker health check operations"""

    async def health_task(ctx):
        return 'ok'

    worker = Worker(
        functions=[func(health_task, name='health_task')],
        redis_pool=arq_redis,
        burst=True,
        poll_delay=0,
        health_check_interval=1,
    )

    # Manually trigger health check
    await worker.record_health()

    # Check health key exists
    health_data = await arq_redis.get(worker.health_check_key)
    assert health_data is not None
    assert b'j_complete=' in health_data


# Test 14: Job Abortion
@pytest.mark.asyncio
async def test_job_abortion(arq_redis):
    """Test job abortion functionality"""

    async def long_running_task(ctx):
        await asyncio.sleep(10)
        return 'should_not_complete'

    job = await arq_redis.enqueue_job('long_running_task')

    worker = Worker(
        functions=[func(long_running_task, name='long_running_task')],
        redis_pool=arq_redis,
        burst=False,
        poll_delay=0.1,
        allow_abort_jobs=True,
    )

    # Start worker in background
    worker_task = asyncio.create_task(worker.async_run())

    # Wait a bit for job to start
    await asyncio.sleep(0.5)

    # Abort the job
    abort_result = await job.abort(timeout=2)

    # Cancel worker
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    await worker.close()

    # Job should have been aborted
    assert abort_result is True


# Test 15: Info Operations
@pytest.mark.asyncio
async def test_redis_info_operations(arq_redis):
    """Test Redis INFO command with different sections"""
    # Server info
    server_info = await arq_redis.info(section='Server')
    assert 'redis_version' in server_info

    # Memory info
    memory_info = await arq_redis.info(section='Memory')
    assert 'used_memory_human' in memory_info

    # Clients info
    clients_info = await arq_redis.info(section='Clients')
    assert 'connected_clients' in clients_info

    # DBSIZE
    db_size = await arq_redis.dbsize()
    assert isinstance(db_size, int)


# Test 16: Job Results
@pytest.mark.asyncio
async def test_job_results_retrieval(arq_redis):
    """Test retrieving all job results"""

    async def result_task(ctx, value: int):
        return value * 10

    # Enqueue and execute multiple jobs
    jobs = []
    for i in range(5):
        job = await arq_redis.enqueue_job('result_task', i)
        jobs.append(job)

    worker = Worker(
        functions=[func(result_task, name='result_task')], redis_pool=arq_redis, burst=True, poll_delay=0
    )
    await worker.run_check()

    # Retrieve all job results
    all_results = await arq_redis.all_job_results()
    assert len(all_results) >= 5

    # Check individual results
    for i, job in enumerate(jobs):
        result = await job.result(timeout=5)
        assert result == i * 10


# Test 17: Queued Jobs Info
@pytest.mark.asyncio
async def test_queued_jobs_info(arq_redis):
    """Test retrieving information about queued jobs"""

    async def queued_task(ctx, value: int):
        return value

    # Enqueue jobs but don't execute
    for i in range(3):
        await arq_redis.enqueue_job('queued_task', i)

    # Get queued jobs info
    queued = await arq_redis.queued_jobs()
    assert len(queued) == 3

    for job_def in queued:
        assert job_def.function == 'queued_task'
        assert job_def.score is not None


# Test 18: Error Handling
@pytest.mark.asyncio
async def test_connection_error_handling():
    """Test handling of connection errors"""
    settings = RedisSettings(host='invalid_host', port=9999, conn_timeout=1, conn_retries=0)

    with pytest.raises((ConnectionError, OSError, redis.exceptions.RedisError, asyncio.TimeoutError)):
        await create_pool(settings)


@pytest.mark.asyncio
async def test_serialization_error_handling(arq_redis):
    """Test handling of serialization errors"""
    from arq.jobs import SerializationError

    class UnserializableObject:
        def __reduce__(self):
            raise TypeError('Cannot serialize')

    async def serialization_task(ctx, obj):
        return obj

    # This should raise SerializationError
    with pytest.raises(SerializationError):
        await arq_redis.enqueue_job('serialization_task', UnserializableObject())


@pytest.mark.asyncio
async def test_no_redis62_commands_used(arq_redis):
    """
    Verify that arq doesn't use any Redis 6.2+ commands like GETEX, GETDEL, etc.

    This test ensures compatibility with Redis 5.x by checking that only
    Redis 5.x compatible commands are used during a full job lifecycle.
    """
    from unittest.mock import AsyncMock, MagicMock

    # Commands introduced in Redis 6.2.0 that should NOT be used
    redis_62_commands = ['getex', 'getdel', 'copy', 'zrangestore', 'hrandfield', 'zrandmember', 'lmove']

    # Wrap the redis connection to track command usage
    original_execute_command = arq_redis.execute_command
    commands_used = []

    async def track_execute_command(command, *args, **kwargs):
        commands_used.append(command.lower() if isinstance(command, str) else command)
        return await original_execute_command(command, *args, **kwargs)

    arq_redis.execute_command = track_execute_command

    try:
        # Run a complete job lifecycle
        async def test_task(ctx, value: int):
            return value * 2

        # Enqueue job
        job = await arq_redis.enqueue_job('test_task', 42)
        assert job is not None

        # Execute job with worker
        worker = Worker(
            functions=[func(test_task, name='test_task')], redis_pool=arq_redis, burst=True, poll_delay=0
        )
        await worker.run_check()

        # Retrieve result
        result = await job.result(timeout=5)
        assert result == 84

        # Check no Redis 6.2+ commands were used
        used_62_commands = [cmd for cmd in commands_used if cmd in redis_62_commands]
        assert len(used_62_commands) == 0, f'Redis 6.2+ commands used: {used_62_commands}'

        print(f'✓ No Redis 6.2+ commands used. Commands executed: {len(commands_used)}')
    finally:
        # Restore original method
        arq_redis.execute_command = original_execute_command


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
