//package com.example.benchmarks.hashing
//
//import com.google.highwayhash.HighwayHash
//import org.bouncycastle.crypto.macs.SipHash
//import org.bouncycastle.crypto.params.KeyParameter
//import org.openjdk.jmh.annotations.*
//import java.security.MessageDigest
//import java.util.concurrent.TimeUnit
//
//
//@BenchmarkMode(Mode.AverageTime)
//@OutputTimeUnit(TimeUnit.MILLISECONDS)
//@State(Scope.Thread)
//open class HashBenchmark {
//    private val secretKey = "some_super_secure_secret_key".toByteArray()
//    private val salt = "43c55cff-0bcd-45ac-a368-6e186767b6e2".toByteArray()
//    private val data = "550e8400-e29b-41d4-a716-446655440000".toByteArray()
//
//    private val highwayHashKey = longArrayOf(
//        0x1234567890ABCDEFL, 0x1122334455667788L,
//        0x0FEDCBA987654321L, 0x1234567890ABCDEF
//    )
//
////    @Benchmark
////    fun benchmarkBlake3(): ByteArray {
//////        val combinedKey = secretKey + salt
//////
//////        // Ensure the combined key is exactly 32 bytes
//////        val keyBytes = if (combinedKey.size >= 32) {
//////            combinedKey.copyOfRange(0, 32) // Truncate to 32 bytes
//////        } else {
//////            combinedKey.copyOf(32) // Pad with zeros if less than 32 bytes
//////        }
//////
//////        return Blake3.keyedHash(keyBytes, data)
////        //return Blake3.Blake3_256().digest(secretKey + salt + data)
////
////        com.google.highwayhash.HighwayHash
////    }
//
//    @Benchmark
//    fun benchmarkHighwayHash(): LongArray {
//        val concatenatedData = secretKey + salt + data
//        return HighwayHash.hash256(concatenatedData, 0, concatenatedData.size, highwayHashKey)
//    }
//
//    @Benchmark
//    fun benchmarkSha256(): ByteArray {
//        val digest = MessageDigest.getInstance("SHA-256")
//        return digest.digest(secretKey + salt + data)
//    }
//
//    @Benchmark
//    fun benchmarkSipHash(): ByteArray {
//        val combinedKey = secretKey + salt
//
//        val keyBytes = if (combinedKey.size >= 16) {
//            combinedKey.copyOfRange(0, 16) // Truncate to 16 bytes
//        } else {
//            combinedKey.copyOf(16) // Pad with zeros if less than 16 bytes
//        }
//
//        val sipHash = SipHash()
//        sipHash.init(KeyParameter(keyBytes))
//
//        sipHash.update(data, 0, data.size)
//
//        val result = ByteArray(8) // SipHash output size is 8 bytes (64 bits)
//        sipHash.doFinal(result, 0)
//
//        return result
//    }
//
//    @Benchmark
//    @BenchmarkMode(Mode.Throughput)
//    @OutputTimeUnit(TimeUnit.SECONDS)
//    fun benchmarkHighwayHashHashesPerSecond() {
//        benchmarkHighwayHash()
//    }
//
//    @Benchmark
//    @BenchmarkMode(Mode.Throughput) // Measures total hashes per second
//    @Threads(4) // Simulates 4 concurrent threads (adjust based on CPU)
//    fun benchmarkHighwayHashConcurrent() {
//        benchmarkHighwayHash()
//    }
//
//    @Benchmark
//    @BenchmarkMode(Mode.Throughput)
//    @OutputTimeUnit(TimeUnit.SECONDS)
//    fun benchmarkSiphashHashesPerSecond() {
//        benchmarkSipHash()
//    }
//
//    @Benchmark
//    @BenchmarkMode(Mode.Throughput) // Measures total hashes per second
//    @Threads(4) // Simulates 4 concurrent threads (adjust based on CPU)
//    fun benchmarkSiphashConcurrent() {
//        benchmarkSipHash()
//    }
//
//    @Benchmark
//    @BenchmarkMode(Mode.Throughput)
//    @OutputTimeUnit(TimeUnit.SECONDS)
//    fun benchmarkSha256HashesPerSecond() {
//        benchmarkSha256()
//    }
//
//    @Benchmark
//    @BenchmarkMode(Mode.Throughput) // Measures total hashes per second
//    @Threads(4) // Simulates 4 concurrent threads (adjust based on CPU)
//    fun benchmarkSha256Concurrent() {
//        benchmarkSha256()
//    }
//}