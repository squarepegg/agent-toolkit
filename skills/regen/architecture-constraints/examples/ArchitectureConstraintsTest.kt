package com.example.offers.api.architecture

// Fitness functions (from architecture-constraints, 2026-10-14). Each carries a provenance header;
// check_provenance.py (regen-skill) validates the headers.
// Scope: classes on the api-service classpath (api-service, database, shared). offers-restate
// is a separate deployable and is not imported here.

import com.tngtech.archunit.core.domain.JavaClasses
import com.tngtech.archunit.core.importer.ClassFileImporter
import com.tngtech.archunit.core.importer.ImportOption
import com.tngtech.archunit.lang.ArchRule
import com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses
import io.micronaut.http.client.HttpClient
import io.micronaut.http.client.HttpClientConfiguration
import org.junit.jupiter.api.Test
import java.net.URL

class ArchitectureConstraintsTest {
    private val classes: JavaClasses =
        ClassFileImporter()
            .withImportOption(ImportOption.DoNotIncludeTests())
            .importPackages("com.example.offers")

    /**
     * @fitness
     * Rule: Only offers-restate (and the database module itself) may use OfferStateWriter.
     * Reason: cooldown state has one logical writer. RecordDecision does read-modify-write, and
     *   Restate serialises it per playerId-offerId key (RestateClient.kt:47). A second writer
     *   would race that read-modify-write and corrupt the window counters.
     * Set by: the platform team, 2026-05-21
     * Source: deletion-test 2026-10-14 (mutation ownership)
     * Revisit when: purchase recording (PRD stretch) needs to write purchasedInWindow, or cooldown
     *   state moves out of the wide-column database.
     * Regeneration test: yes — a regenerated api-service writing cooldown directly would pass
     *   every test and double-count or lose cooldown windows under concurrency.
     */
    private val onlyRestateWritesCooldown: ArchRule =
        noClasses()
            .that()
            .resideOutsideOfPackages("com.example.offers.restate..", "com.example.offers.database..")
            .should()
            .dependOnClassesThat()
            .haveFullyQualifiedName("com.example.offers.database.OfferStateWriter")
            .because("cooldown state has a single logical writer (RecordDecision via Restate)")

    /**
     * @fitness
     * Rule: offers-shared depends on no framework: no Micronaut, Koin, Restate SDK, Cassandra driver or Micrometer.
     * Reason: shared is consumed by two runtimes, api-service (Micronaut) and offers-restate
     *   (Koin + Restate SDK). A framework dependency in shared drags one runtime into the other.
     * Set by: the platform team, recorded in the project README
     * Source: README "Pure-Kotlin types + interfaces (no Micronaut deps)"; offers-shared/build.gradle.kts
     * Revisit when: api-service and restate merge into one runtime.
     * Regeneration test: yes — a regenerated shared using Micronaut annotations would break the restate build.
     */
    private val sharedIsFrameworkFree: ArchRule =
        noClasses()
            .that()
            .resideInAPackage("com.example.offers.shared..")
            .should()
            .dependOnClassesThat()
            .resideInAnyPackage(
                "io.micronaut..",
                "org.koin..",
                "dev.restate..",
                "com.datastax..",
                "io.micrometer..",
            )

    /**
     * @fitness
     * Rule: Vendor HTTP clients are built only by newVendorHttpClient (VendorHttpClient.kt).
     * Reason: Vendor sits behind a cross-cluster LB that reaps idle connections. The factory applies
     *   idle-pool eviction; callers pair it with retryOnConnectionReset. A client built elsewhere
     *   silently loses the hardening.
     * Set by: the platform team, 2026-06-11
     * Source: production incident (prod: ~6 fail-open events in ~7h); audit 2026-10-14 #2, #3
     * Revisit when: Vendor is reached in-cluster without an idle-reaping LB, or the client library changes.
     * Regeneration test: yes — the regression is invisible in tests and shows up as sporadic prod fail-opens.
     */
    private val onlyFactoryBuildsMicronautClients: ArchRule =
        noClasses()
            .that()
            .doNotHaveFullyQualifiedName("com.example.offers.api.clients.VendorHttpClientKt")
            .should()
            .callMethod(HttpClient::class.java, "create", URL::class.java)
            .orShould()
            .callMethod(HttpClient::class.java, "create", URL::class.java, HttpClientConfiguration::class.java)
            .orShould()
            .dependOnClassesThat()
            .haveFullyQualifiedName("io.micronaut.http.client.DefaultHttpClientConfiguration")

    /**
     * @fitness
     * Rule: HealthController (/healthz, /readyz) depends on no downstream (Vendor, the database, Restate, snapshot
     *   cache) and not on SchemaRegistry.
     * Reason: probing a dependency pulls pods out of service when the right behaviour is fail-open
     *   (503 { offer: null, degraded: true }). Gating readiness on the snapshot registry deadlocks
     *   rollouts: no traffic reaches an unready pod, so it never becomes ready.
     * Set by: the platform team, recorded in HealthController KDoc
     * Source: HealthController.kt:8-23 (KDoc; the registry deadlock is :15-22); design doc §12 (local only)
     * Revisit when: readiness needs a dependency that cannot fail open, or a service-level Vendor credential exists.
     * Regeneration test: yes — "improving" readyz to check Vendor is a plausible regeneration and breaks rollouts.
     */
    private val readinessIsDependencyFree: ArchRule =
        noClasses()
            .that()
            .haveSimpleName("HealthController")
            .should()
            .dependOnClassesThat()
            .resideInAnyPackage(
                "com.example.offers.api.clients..",
                "com.example.offers.api.providers..",
                "com.example.offers.database..",
                "com.example.offers.shared.vendor..",
                "com.datastax..",
            ).orShould()
            .dependOnClassesThat()
            // lives in api.services; gating /readyz on it being populated is the deadlock in the KDoc
            .haveFullyQualifiedName("com.example.offers.api.services.SchemaRegistry")

    @Test fun `only restate writes cooldown state`() = onlyRestateWritesCooldown.check(classes)

    @Test fun `offers-shared is framework-free`() = sharedIsFrameworkFree.check(classes)

    @Test fun `only the Vendor factory builds Micronaut http clients`() = onlyFactoryBuildsMicronautClients.check(classes)

    @Test fun `readiness and liveness probe no dependency`() = readinessIsDependencyFree.check(classes)
}
