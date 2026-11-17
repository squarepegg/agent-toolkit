package com.example.offers.api.architecture

// Fitness functions (from architecture-constraints, 2026-10-14). FLAGGED: no recorded reason.
// These rules describe structure the code mostly follows, but no doc, commit or PR says why.
// They are disabled. Enable one only after its owner fills Reason, Set by and Source.

import com.tngtech.archunit.core.importer.ClassFileImporter
import com.tngtech.archunit.core.importer.ImportOption
import com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses
import org.junit.jupiter.api.Disabled
import org.junit.jupiter.api.Test

class FlaggedConstraintsTest {
    private val classes =
        ClassFileImporter().withImportOption(ImportOption.DoNotIncludeTests()).importPackages("com.example.offers")

    /**
     * @fitness
     * Rule: Controllers do not depend on clients directly; they go through services or providers.
     * Reason: unknown
     * Set by: unknown
     * Source: observed structure only. FAILS TODAY: EvaluateController imports RestateClient
     *   (EvaluateController.kt:3).
     * Revisit when: the the platform team confirms or rejects the rule.
     * Regeneration test: unknown — ask whether a controller calling Restate directly matters.
     */
    @Disabled("no provenance: reason unknown; and it fails today (EvaluateController → RestateClient)")
    @Test
    fun `controllers do not use clients directly`() {
        noClasses()
            .that()
            .resideInAPackage("..api.controllers..")
            .should()
            .dependOnClassesThat()
            .resideInAPackage("..api.clients..")
            .check(classes)
    }
}
