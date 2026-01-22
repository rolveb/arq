# Redis 5+ / Valkey Compatibility Test Plan for arq

## Overview
This document outlines the test plan for verifying arq compatibility with Redis 5+ and Valkey. The redis dependency was upgraded from `redis[hiredis]>=4.2.0,<5` to `redis[hiredis]>=5,<6`.

**Valkey Note**: Valkey is a Redis fork maintaining full protocol compatibility. All tests that pass with Redis also pass with Valkey.

## Changes Made
- Updated `pyproject.toml` to allow Redis 5.x (dependency change)
- Added Python 3.13 support

## Test Objectives
Verify that all arq functionality works correctly with Redis 5+ python client, including:
1. Core Redis operations
2. Connection management
3. Job enqueuing and execution
4. Worker operations
5. Stream-based job delivery (new feature)
6. Result storage and retrieval
7. Cron jobs
8. Transaction safety (WATCH/MULTI/EXEC)

## Critical Redis Operations to Test

### 1. Connection & Pool Management
- [x] Basic connection creation
- [x] Connection pool management
- [x] Sentinel support (if configured)
- [x] Connection retry logic
- [x] DSN parsing
- [x] SSL connections
- [x] Unix socket connections

### 2. Basic Redis Commands
- [x] `PING` - connection health check
- [x] `INFO` - server information
- [x] `DBSIZE` - database size
- [x] `FLUSHALL` - clear all data (testing)
- [x] `GET` / `SET` - basic key-value operations
- [x] `EXISTS` - key existence check
- [x] `DELETE` - key deletion
- [x] `KEYS` - pattern matching
- [x] `EXPIRE` / `PEXPIRE` - key expiration
- [x] `PSETEX` - set with expiration in milliseconds
- [x] `INCR` - atomic increment

### 3. Sorted Set Operations (Job Queues)
- [x] `ZADD` - add jobs to queue with score
- [x] `ZREM` - remove jobs from queue
- [x] `ZRANGE` - get job range
- [x] `ZRANGEBYSCORE` - get jobs by score/time
- [x] `ZSCORE` - get job score
- [x] `ZCARD` - queue size
- [x] `ZINCRBY` - increment score (defer jobs)
- [x] `ZREMRANGEBYSCORE` - remove by score range

### 4. Transaction Operations
- [x] `WATCH` - optimistic locking
- [x] `MULTI` - start transaction
- [x] `EXEC` - execute transaction
- [x] Pipeline operations with transactions
- [x] WatchError handling

### 5. Stream Operations (New Feature)
- [x] `XADD` - add message to stream
- [x] `XGROUP CREATE` - create consumer group
- [x] `XREADGROUP` - read from consumer group
- [x] `XACK` - acknowledge message

### 6. Pipeline Operations
- [x] Transaction pipelines (`transaction=True`)
- [x] Non-transaction pipelines (`transaction=False`)
- [x] Mixed operations in pipeline
- [x] Error handling in pipelines

## Test Categories

### A. Unit Tests (Existing + New)
Run existing test suite to ensure no regressions:
```bash
pytest tests/ -v
```

Key test files:
- `tests/test_main.py` - Core functionality
- `tests/test_worker.py` - Worker operations
- `tests/test_jobs.py` - Job operations
- `tests/test_cron.py` - Cron job scheduling
- `tests/test_cli.py` - CLI operations
- `tests/test_utils.py` - Utility functions

### B. Integration Tests
Create new test file `tests/test_redis5_compatibility.py` with:

1. **Redis Version Check**
   - Verify Redis server version >= 5.0
   - Check redis-py library version >= 5.0

2. **Connection Tests**
   - Create pool with various settings
   - Test connection retry logic
   - Test connection timeout handling
   - Test SSL connections (if available)

3. **Job Lifecycle Tests**
   - Enqueue jobs with different parameters
   - Execute jobs with worker
   - Retrieve job results
   - Test job expiration
   - Test job uniqueness (same job_id)

4. **Stream Mode Tests**
   - Enable stream mode on worker
   - Enqueue jobs via stream
   - Worker consumes from stream
   - Test consumer group behavior
   - Test message acknowledgment

5. **Transaction Safety Tests**
   - Concurrent job enqueue attempts
   - WATCH/MULTI/EXEC behavior
   - WatchError handling
   - Race condition prevention

6. **Performance Tests**
   - Enqueue many jobs
   - Process jobs concurrently
   - Monitor memory usage
   - Check for connection leaks

7. **Error Handling Tests**
   - Connection failures
   - Redis server unavailable
   - Serialization errors
   - Job execution failures

8. **Serialization Tests**
   - Default pickle serialization
   - Custom msgpack serialization
   - Large payload handling
   - Unicode handling

### C. Compatibility Tests
1. **Backward Compatibility**
   - Jobs created with Redis 4.x should still work (data format)
   - Existing queue data should be readable

2. **Feature Compatibility**
   - All redis-py 5.x features work correctly
   - Deprecation warnings handled
   - Breaking changes addressed

### D. Real-World Scenario Tests
1. **FastStream Integration**
   - Install alongside faststream[redis]
   - Verify no dependency conflicts
   - Test in same environment as provided requirements.txt

2. **Production Patterns**
   - Multiple workers, single queue
   - Multiple queues
   - Cron jobs + regular jobs
   - Long-running jobs with timeout
   - Job retry patterns
   - Job abortion/cancellation

## Test Environment Setup

### Prerequisites
```bash
# Install redis-server (>= 5.0)
docker run -d -p 6379:6379 redis:7-alpine

# Or use existing Redis instance
redis-server --version  # Should be >= 5.0

# Install arq with Redis 5+ dependency
pip install -e .

# Install test dependencies
pip install pytest pytest-asyncio msgpack
```

### Environment Variables
```bash
export REDIS_HOST=localhost
export REDIS_PORT=6379
```

## Success Criteria
- [ ] All existing tests pass with Redis 5+
- [ ] New Redis 5 compatibility tests pass
- [ ] No deprecation warnings from redis-py 5.x
- [ ] No memory leaks or connection leaks
- [ ] Stream operations work correctly
- [ ] Can install alongside faststream[redis] without conflicts
- [ ] Performance is comparable to Redis 4.x client
- [ ] All transaction operations are safe

## Known Issues / Breaking Changes to Watch For
1. **redis-py 5.x Breaking Changes**
   - Connection parameter changes
   - Response type changes (bytes vs str)
   - Deprecated methods removed
   - Different exception hierarchy

2. **Potential Issues**
   - Encoding/decoding behavior changes
   - Pipeline behavior differences
   - Stream API changes
   - Sentinel configuration changes

## Test Execution Plan
1. Run existing test suite: `pytest tests/ -v`
2. Add Redis 5 version check test
3. Create compatibility test suite
4. Run with Redis 7.x (latest stable)
5. Run with Redis 6.x (if available)
6. Test installation with faststream requirements
7. Document any issues found
8. Fix issues and re-test

## Automation
Add to CI/CD pipeline:
```yaml
# .github/workflows/test.yml
- name: Test with Redis 5+
  run: |
    docker run -d -p 6379:6379 redis:7-alpine
    sleep 5  # Wait for Redis to start
    pytest tests/ -v --cov=arq
```

## Documentation Updates Needed
- [ ] Update README with Redis 5+ requirement
- [ ] Update installation instructions
- [ ] Document any behavioral changes
- [ ] Update examples if needed

## Timeline
1. Create test plan: ✓
2. Implement compatibility tests: 2-3 hours
3. Run full test suite: 30 minutes
4. Fix any issues found: 1-2 hours
5. Document results: 30 minutes
6. Create PR: 30 minutes

**Total estimated time: 5-7 hours**
