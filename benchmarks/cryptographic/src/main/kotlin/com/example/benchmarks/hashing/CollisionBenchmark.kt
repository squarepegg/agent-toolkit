package com.example.benchmarks.hashing

import org.bouncycastle.crypto.macs.SipHash
import org.bouncycastle.crypto.params.KeyParameter
import org.openjdk.jmh.annotations.*
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ThreadLocalRandom
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger


@BenchmarkMode(Mode.Throughput) // Measures hashes per second
@OutputTimeUnit(TimeUnit.MILLISECONDS)
@State(Scope.Thread)
@Threads(4) // Test multi-core behavior
@Fork(1)
@Warmup(iterations = 5, time = 30, timeUnit = TimeUnit.SECONDS) // Longer warmup
@Measurement(iterations = 10, time = 1, timeUnit = TimeUnit.MINUTES)
open class CollisionBenchmark {

    private val secretKey = "some_super_secure_secret_key".toByteArray()
    private val salt = "43c55cff-0bcd-45ac-a368-6e186767b6e2".toByteArray()

    private val seenHashes = ConcurrentHashMap.newKeySet<ByteArray>() // Thread-safe set
    private val collisionCounter = AtomicInteger(0)

    @Benchmark
    fun sipHashCollisionTest(): ByteArray {
        val id = ThreadLocalRandom.current().nextLong()
        val hash = benchmarkSipHash(id.toString().toByteArray())

        // Track uniqueness
        if (!seenHashes.add(hash)) {
            collisionCounter.incrementAndGet()
        }

        return hash
    }

    private fun benchmarkSipHash(data: ByteArray): ByteArray {
        val combinedKey = secretKey + salt

        val keyBytes = if (combinedKey.size >= 16) {
            combinedKey.copyOfRange(0, 16) // Truncate to 16 bytes
        } else {
            combinedKey.copyOf(16) // Pad with zeros if less than 16 bytes
        }

        val sipHash = SipHash()
        sipHash.init(KeyParameter(keyBytes))

        sipHash.update(data, 0, data.size)

        val result = ByteArray(8) // SipHash output size is 8 bytes (64 bits)
        sipHash.doFinal(result, 0)

        return result
    }

    @TearDown(Level.Trial)
    fun reportCollisions() {
        println("Total Collisions Detected: ${collisionCounter.get()}")
        println("Total Unique Hashes: ${seenHashes.size}")
    }
}