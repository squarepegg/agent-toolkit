package com.example.offers.api.clients

// TEST SCAFFOLDING: a minimal client with the retry/classification policy that
// VendorWireContractTest states at the wire, so that test can run against RawHttpStub.
// Policy: a dropped connection is retried up to maxConnectionRetries; a timeout or an HTTP
// error status is never retried; 401/403 are auth rejections, everything else is an outage.

import com.example.offers.api.config.VendorConfig
import com.example.offers.api.exceptions.VendorAuthRejectedException
import com.example.offers.api.exceptions.VendorUnreachableException
import com.example.offers.shared.vendor.CachedRelease
import com.example.offers.shared.vendor.VendorOffer
import java.io.IOException
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.net.http.HttpTimeoutException
import java.time.Duration

/** The single place that builds clients (the rule in ArchitectureConstraintsTest). */
fun newVendorHttpClient(): HttpClient = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).build()

class VendorHttpClient(private val config: VendorConfig) : AutoCloseable {
    private val http = newVendorHttpClient()

    @Suppress("UNUSED_PARAMETER")
    suspend fun fetchOffersByTriggers(
        release: CachedRelease,
        triggers: List<String>,
        playerId: String,
        apiKey: String,
    ): List<VendorOffer> {
        val request =
            HttpRequest.newBuilder(URI.create(config.baseUrl + "/offers"))
                .timeout(Duration.ofMillis(config.timeoutMs))
                .header("Authorization", "Bearer $apiKey")
                .POST(HttpRequest.BodyPublishers.ofString("""{"player":"$playerId","triggers":${triggers.map { "\"$it\"" }}}"""))
                .build()
        var attempt = 0
        while (true) {
            try {
                val response = http.send(request, HttpResponse.BodyHandlers.ofString())
                return when (val status = response.statusCode()) {
                    in 200..299 -> Regex("\"contentId\":\"([^\"]+)\"").findAll(response.body()).map { VendorOffer(it.groupValues[1]) }.toList()
                    401, 403 -> throw VendorAuthRejectedException("vendor rejected credentials: $status")
                    else -> throw VendorUnreachableException("vendor status $status")
                }
            } catch (e: HttpTimeoutException) {
                throw VendorUnreachableException("vendor timed out", e)
            } catch (e: IOException) {
                if (attempt++ >= config.maxConnectionRetries) throw VendorUnreachableException("vendor connection failed", e)
            }
        }
    }

    // JDK HttpClient is closeable only on 21+, which is the pinned toolchain.
    override fun close() = http.close()
}
