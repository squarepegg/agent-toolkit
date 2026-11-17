package com.example.offers.api.clients

/**
 * Durable evaluations (from eval-tiering, 2026-10-14).
 *
 * Requirement: the Vendor client's retry and error-classification policy, stated at the
 * wire, so it survives a rewrite of VendorHttpClient or retryOnConnectionReset.
 * Source: implementation-memory-audit 2026-10-14 rows #1, #2, #6; PR-B; commit-B.
 * Survives a rewrite because: it asserts what Vendor observes (request count) and how the
 * outcome is classified, not which internal function ran.
 *
 * Twin of VendorRetryTest (internal helper, Micronaut exception types). Keep both.
 */

import com.example.offers.api.config.VendorConfig
import com.example.offers.api.exceptions.VendorAuthRejectedException
import com.example.offers.api.exceptions.VendorUnreachableException
import com.example.offers.shared.vendor.CachedRelease
import com.example.offers.shared.vendor.VendorOffer
import io.kotest.assertions.throwables.shouldThrow
import io.kotest.matchers.collections.shouldHaveSize
import io.kotest.matchers.shouldBe
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Test

class VendorWireContractTest {
    private val release = CachedRelease(snapshotId = "snap-1", packageName = "pkg", packageId = "pkg-id")
    private val oneOffer = """{"entries":[{"contentId":"o-1","#schema":{"layout":"L"},"trigger":"t.v1"}]}"""

    /** One call through a fresh client, closed afterwards (it is not Micronaut-managed). */
    private suspend fun call(
        stub: RawHttpStub,
        retries: Int = 1,
        timeoutMs: Long = 200,
    ): List<VendorOffer> {
        val client =
            VendorHttpClient(
                VendorConfig().apply {
                    baseUrl = stub.baseUrl
                    this.timeoutMs = timeoutMs
                    maxConnectionRetries = retries
                },
            )
        try {
            return client.fetchOffersByTriggers(release, listOf("t.v1"), "player-1", "key")
        } finally {
            client.close()
        }
    }

    @Test
    fun `a connection dropped before the response is retried once and the call succeeds`(): Unit =
        runBlocking {
            RawHttpStub(StubAction.Drop, StubAction.Respond(200, oneOffer)).use { stub ->
                call(stub) shouldHaveSize 1
                stub.requests shouldHaveSize 2
            }
        }

    @Test
    fun `with retries disabled a dropped connection is an outage and Vendor sees one request`(): Unit =
        runBlocking {
            RawHttpStub(StubAction.Drop).use { stub ->
                shouldThrow<VendorUnreachableException> { call(stub, retries = 0) }
                stub.requests shouldHaveSize 1
            }
        }

    @Test
    fun `a response slower than the timeout is never retried`(): Unit =
        runBlocking {
            RawHttpStub(StubAction.Stall(millis = 600)).use { stub ->
                shouldThrow<VendorUnreachableException> { call(stub, retries = 3, timeoutMs = 200) }
                stub.requests shouldHaveSize 1
            }
        }

    @Test
    fun `an http error status is never retried`(): Unit =
        runBlocking {
            RawHttpStub(StubAction.Respond(503)).use { stub ->
                shouldThrow<VendorUnreachableException> { call(stub, retries = 3) }
                stub.requests shouldHaveSize 1
            }
        }

    @Test
    fun `vendor 401 and 403 are auth rejections, not outages`(): Unit =
        runBlocking {
            for (status in listOf(401, 403)) {
                RawHttpStub(StubAction.Respond(status)).use { stub ->
                    shouldThrow<VendorAuthRejectedException> { call(stub) }
                    stub.requests.size shouldBe 1
                }
            }
        }
}
