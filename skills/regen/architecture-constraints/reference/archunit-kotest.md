# ArchUnit with Kotest or JUnit 5

ArchUnit's core API is runner-agnostic: import classes, build a rule, call `check`. That works
inside a Kotest spec or a JUnit 5 test. Avoid `@AnalyzeClasses`/`@ArchTest` unless the repo
already uses `archunit-junit5`; the plain API is easier to read and to give a header.

```kotlin
// build.gradle.kts (test scope)
testImplementation("com.tngtech.archunit:archunit:1.4.1")
```

Multi-module Gradle: put the test in the module that can see every class it constrains, usually
the deployable (`*-api-service`), or a dedicated `architecture-tests` module that depends on all
the others.

## Kotest spec

```kotlin
class ArchitectureConstraints : FunSpec({
    val classes = ClassFileImporter()
        .withImportOption(ImportOption.DoNotIncludeTests())
        .importPackages("com.example.offers")

    /**
     * @fitness
     * Rule: ...
     * Reason: ...
     * Set by: ...
     * Source: ...
     * Revisit when: ...
     * Regeneration test: yes — ...
     */
    val onlyRestateWritesCooldown: ArchRule =
        noClasses().that().resideOutsideOfPackage("com.example.offers.restate..")
            .and().resideOutsideOfPackage("com.example.offers.database..")
            .should().dependOnClassesThat().haveSimpleName("OfferStateWriter")
            .because("cooldown state has a single logical writer (see header)")

    test("only restate writes cooldown state") { onlyRestateWritesCooldown.check(classes) }
})
```

## JUnit 5

```kotlin
class ArchitectureConstraintsTest {
    private val classes = ClassFileImporter()
        .withImportOption(ImportOption.DoNotIncludeTests())
        .importPackages("com.example.offers")

    /** @fitness … header … */
    private val onlyRestateWritesCooldown: ArchRule = /* as above */

    @Test fun `only restate writes cooldown state`() = onlyRestateWritesCooldown.check(classes)
}
```

## Useful rule shapes

| Constraint | ArchUnit |
|---|---|
| A may not depend on B | `noClasses().that().resideInAPackage("..a..").should().dependOnClassesThat().resideInAPackage("..b..")` |
| only X may use Y | `noClasses().that().resideOutsideOfPackage("..x..").should().dependOnClassesThat().haveSimpleName("Y")` |
| layering | `layeredArchitecture().consideringOnlyDependenciesInLayers().layer("Controllers").definedBy("..controllers..")…whereLayer("Controllers").mayNotBeAccessedByAnyLayer()` |
| no cycles | `slices().matching("com.acme.(*)..").should().beFreeOfCycles()` |
| shared kernel stays framework-free | `noClasses().that().resideInAPackage("..shared..").should().dependOnClassesThat().resideInAnyPackage("io.micronaut..", "org.springframework..")` |

Kotlin note: top-level functions compile to `<File>Kt` classes. Constrain them by package or by
`haveSimpleNameEndingWith("Kt")`.

## Config assertions

Bounds on scar values (timeouts, retry counts, pool TTLs) are fitness functions too. Write them
as plain tests with the same header plus the `@fitness` tag. Read the **declared** default from
the packaged config file (the literal, or `D` in `${VAR:D}`), and never resolve the environment.
Otherwise an env var on the test machine masks or fakes the default. Template:
`../eval-tiering/reference/kotest-templates.md` §5; worked example: `examples/ConfigConstraintsTest.kt`.
