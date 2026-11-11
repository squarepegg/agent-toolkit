// Gate defaults for Kotlin: Kotest (example + property tests), Pitest (mutation), detekt (static).
// Locked after the tests phase; the builder cannot change it.
plugins {
    kotlin("jvm") version "2.0.21"
    id("info.solidsoft.pitest") version "1.15.0"
    id("io.gitlab.arturbosch.detekt") version "1.23.8"
}

repositories { mavenCentral() }

kotlin {
    jvmToolchain(21)
    compilerOptions { allWarningsAsErrors.set(true) }
}

// Pitest's Kotest plugin. sdlc (default): one test unit per root test, from src/sdlcPitest (locked, from the
// skill), so Pitest runs only the covering tests and stops at the first kill. kotest: the spec-level
// kotest-extensions-pitest (one unit per spec class), kept for comparison and as a fallback.
val sdlcPitest: SourceSet by sourceSets.creating
// One jar: Pitest hands its minions each plugin's code location, so classes and META-INF/services must sit together.
val sdlcPitestJar by tasks.registering(Jar::class) {
    archiveFileName.set("sdlc-kotest-pitest.jar")
    destinationDirectory.set(layout.buildDirectory.dir("sdlc"))
    from(sdlcPitest.output)
}
val pitestPlugin = providers.gradleProperty("sdlc.pitestPlugin").getOrElse("sdlc")

dependencies {
    testImplementation("io.kotest:kotest-runner-junit5:5.9.1")
    testImplementation("io.kotest:kotest-assertions-core:5.9.1")
    testImplementation("io.kotest:kotest-property:5.9.1")
    "sdlcPitestCompileOnly"("org.pitest:pitest:1.17.0")
    "sdlcPitestCompileOnly"("org.pitest:pitest-command-line:1.17.0")
    "sdlcPitestCompileOnly"("io.kotest:kotest-framework-engine:5.9.1")
    "sdlcPitestCompileOnly"("io.kotest:kotest-property:5.9.1")
    "sdlcPitestCompileOnly"("io.github.classgraph:classgraph:4.8.172")
    if (pitestPlugin == "sdlc") {
        // Kotest itself comes from the test classpath, which Pitest gives its minions.
        pitest(files(sdlcPitestJar))
    } else {
        pitest("io.kotest.extensions:kotest-extensions-pitest:1.2.0")
    }
    // sdlc:test-dependencies:begin
    // sdlc:test-dependencies:end
}

// Resolves every configuration so the orchestrator can fill the read-only dependency cache.
tasks.register("sdlcResolveDependencies") {
    doLast { configurations.filter { it.isCanBeResolved }.forEach { it.resolve() } }
}

tasks.test {
    useJUnitPlatform()
    // Fuzz mode: ./gradlew test -PpropIterations=20000
    providers.gradleProperty("propIterations").orNull?.let {
        systemProperty("kotest.proptest.default.iteration.count", it)
    }
    testLogging {
        events("failed")
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}

detekt {
    buildUponDefaultConfig = true
    config.setFrom(files("detekt.yml"))
    source.setFrom(files("src/main/kotlin", "src/main/java"))
}

pitest {
    pitestVersion.set("1.17.0")
    junit5PluginVersion.set("1.2.1")
    // Lists split on spaces or commas (the gate passes shard lists comma-separated: speed spec A2).
    val list = { v: String -> v.split(Regex("[ ,]+")).filter { it.isNotBlank() } }
    targetClasses.set(list(providers.gradleProperty("sdlc.pitestTargets").get()))
    // Tests Pitest runs: defaults to the main packages; set sdlc.pitestTests when tests live elsewhere
    // (otherwise Pitest finds no tests and reports "Ran 0 tests").
    targetTests.set(providers.gradleProperty("sdlc.pitestTests").orElse(providers.gradleProperty("sdlc.pitestTargets")).get().split(" ").filter { it.isNotBlank() })
    // Test classes Pitest leaves out (sdlc.pitestExcludedTests), e.g. slow capacity or timing tests. They still run in
    // the gate's test step. Leaving tests out can only lower the score; it never inflates it.
    excludedTestClasses.set(providers.gradleProperty("sdlc.pitestExcludedTests").getOrElse("").split(" ").filter { it.isNotBlank() })
    // Minion heap and timeout. A short timeout counts slow-but-passing runs as kills (TIMED_OUT), which inflates the score.
    jvmArgs.set(providers.gradleProperty("sdlc.pitestJvmArgs").getOrElse("").split(" ").filter { it.isNotBlank() })
    timeoutConstInMillis.set(providers.gradleProperty("sdlc.pitestTimeoutMs").getOrElse("10000").toInt())
    // attack.* always; sdlc.pitestExcludedClasses: the classes other shards cover (the gate's remainder shard).
    excludedClasses.set(listOf("attack.*") + list(providers.gradleProperty("sdlc.pitestExcludedClasses").getOrElse("")))
    // A shard may legitimately have no mutants (the remainder); the gate checks the merged total instead.
    failWhenNoMutations.set(providers.gradleProperty("sdlc.pitestFailWhenNoMutations").getOrElse("true").toBoolean())
    // Compiler-generated noise only: null-check intrinsics and data-class members. The gate checks the
    // compiled classes and fails if any excluded method is hand-written, so nothing real hides here.
    avoidCallsTo.set(listOf("kotlin.jvm.internal.Intrinsics"))
    excludedMethods.set(providers.gradleProperty("sdlc.pitestExcludedMethods").getOrElse("").split(" ").filter { it.isNotBlank() })
    mutators.set(listOf("STRONGER"))
    outputFormats.set(listOf("XML", "HTML"))
    timestampedReports.set(false)
    threads.set(providers.gradleProperty("sdlc.pitestThreads").getOrElse("2").toInt())
    // Analysis units of at most sdlc.pitestUnitSize mutants (Pitest's mutationUnitSize, which the Gradle plugin does
    // not expose; 0 = one unit per class): a unit runs on one thread, so a large class no longer runs on one thread.
    // Units only schedule work; each gets a fresh minion (more isolation, never less). Read by sdlc.pitest.SdlcPitestMain.
    if (pitestPlugin == "sdlc") {
        mainProcessJvmArgs.set(listOf("-Dsdlc.pitest.unitSize=" + providers.gradleProperty("sdlc.pitestUnitSize").getOrElse("10")))
    }
    // Incremental analysis (sdlc.pitestHistory=true). gate.sh --mutation-history supplies in.bin from an earlier gate
    // and keeps out.bin. A pass that reused history is confirmed by a fresh run before it counts, so a stale or
    // tampered history can only cost time, never pass the gate.
    if (providers.gradleProperty("sdlc.pitestHistory").getOrElse("false") == "true") {
        val historyIn = layout.buildDirectory.file("sdlc-pitest-history/in.bin").get().asFile
        if (historyIn.isFile) historyInputLocation.set(historyIn)
        historyOutputLocation.set(layout.buildDirectory.file("sdlc-pitest-history/out.bin"))
    }
}

// The pitest task's main class is set when the task is created (exec() only sets its arguments): point it at the
// wrapper that adds --mutationUnitSize (sdlcPitest/java/sdlc/pitest/SdlcPitestMain.java, in the plugin jar).
if (pitestPlugin == "sdlc") {
    tasks.named<info.solidsoft.gradle.pitest.PitestTask>("pitest") {
        mainClass.set("sdlc.pitest.SdlcPitestMain")
    }
}
