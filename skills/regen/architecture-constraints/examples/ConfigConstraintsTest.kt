package com.example.offers.api.architecture

// Fitness functions (from architecture-constraints, 2026-10-14). Each carries a provenance header;
// check_provenance.py (regen-skill) validates the headers.
// Checks the *shipped defaults* declared in application.yml: the literal value, or D in
// ${VAR:D}. The file is read directly, never resolved, so env vars set on the test machine
// (e.g. VENDOR_MAX_CONNECTION_RETRIES) cannot mask or fake a default. Deploy-time overrides
// are out of scope here.

import com.example.offers.api.config.VendorConfig
import io.kotest.matchers.ints.shouldBeInRange
import io.kotest.matchers.longs.shouldBeInRange
import io.kotest.matchers.longs.shouldBeLessThanOrEqual
import io.kotest.matchers.shouldBe
import io.micronaut.context.env.yaml.YamlPropertySourceLoader
import org.junit.jupiter.api.Test

class ConfigConstraintsTest {
    /** application.yml flattened to dotted keys, placeholders left unresolved. */
    private val shipped: Map<String, Any?> by lazy {
        val yml = javaClass.getResourceAsStream("/application.yml") ?: throw AssertionError("application.yml not on classpath")
        yml.use { YamlPropertySourceLoader().read("application", it) }
    }
    private val placeholder = Regex("""^\$\{[^:}]+:([^}]*)}$""")

    private fun prop(name: String): Long {
        val raw = shipped[name]?.toString()?.trim() ?: throw AssertionError("$name not set in application.yml")
        val declared = placeholder.matchEntire(raw)?.groupValues?.get(1) ?: raw
        return declared.toLongOrNull() ?: throw AssertionError("$name: '$raw' has no numeric shipped default")
    }

    /**
     * @fitness
     * Rule: The Vendor connection-pool idle timeout is positive and below 60 s.
     * Reason: Micronaut's default 0 never evicts, so stale pooled connections hit LB-reaped sockets.
     *   30 s sits below the common 60 s ALB idle default.
     * Set by: the platform team, 2026-06-11
     * Source: production incident; audit 2026-10-14 #3
     * Revisit when: the real ALB idle timeout is measured (the incident review: "30s is an inference, not a
     *   measured LB value"). Then tighten the bound to that value.
     * Regeneration test: yes — 0 or above the LB idle timeout brings back sporadic fail-opens.
     */
    @Test
    fun `vendor pool idle timeout is positive and below the LB idle timeout`() {
        prop("offers.vendor.pool-idle-timeout-ms") shouldBeInRange 1L..59_999L
    }

    /**
     * @fitness
     * Rule: The Vendor hot-path timeout is at most 200 ms, and at most one connection retry is configured.
     * Reason: decision SLO is p99 < 200 ms server-side. A read timeout is never retried; a reset is
     *   retried at most once, because two retries cannot fit the budget.
     * Set by: the platform team
     * Source: service-slo.yaml:8 (SLO); VendorConfig.kt KDoc; incident review; audit 2026-10-14 #4, #5
     * Revisit when: the SLO changes, or p99 split by retried/not-retried shows headroom (audit tension 1).
     * Regeneration test: yes — raising either value silently trades the SLO for availability.
     */
    @Test
    fun `vendor hot path stays inside the decision latency budget`() {
        prop("offers.vendor.timeout-ms") shouldBeLessThanOrEqual 200L
        prop("offers.vendor.max-connection-retries").toInt() shouldBeInRange 0..1
    }

    /**
     * @fitness
     * Rule: VendorConfig's code defaults equal the application.yml defaults.
     * Reason: both define 200 / 30000 / 1; YAML wins at runtime, so an edit to one is silently ignored.
     *   Guards against drift until one source is removed (compaction-pass 2026-10-14 #1).
     * Set by: architecture-constraints proposal, pending the platform team agreement
     * Source: audit 2026-10-14 tension 4
     * Revisit when: one of the two sources is removed; then delete this test.
     * Regeneration test: yes — a regenerated config class with new defaults would look effective and not be.
     */
    @Test
    fun `vendor config defaults have a single effective value`() {
        val code = VendorConfig()
        prop("offers.vendor.timeout-ms") shouldBe code.timeoutMs
        prop("offers.vendor.pool-idle-timeout-ms") shouldBe code.poolIdleTimeoutMs
        prop("offers.vendor.max-connection-retries") shouldBe code.maxConnectionRetries.toLong()
    }
}
