plugins {
    kotlin("jvm") version "2.0.21"
}

repositories {
    mavenCentral()
}

dependencies {
    // What the shipped examples import (and nothing else).
    testImplementation(platform("org.junit:junit-bom:5.11.3"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
    testImplementation("io.kotest:kotest-assertions-core-jvm:5.9.1")
    testImplementation("com.tngtech.archunit:archunit:1.4.1")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0") // runBlocking in VendorWireContractTest
    // ArchitectureConstraintsTest references io.micronaut.http.client.HttpClient (a rule target);
    // ConfigConstraintsTest uses Micronaut's YamlPropertySourceLoader.
    testImplementation(platform("io.micronaut.platform:micronaut-platform:4.7.6"))
    testImplementation("io.micronaut:micronaut-http-client-core")
    testImplementation("io.micronaut:micronaut-runtime")
    testImplementation("org.yaml:snakeyaml")
    // The stub production sources (src/main) reference the same HttpClient so the ArchUnit rules have a real target.
    compileOnly(platform("io.micronaut.platform:micronaut-platform:4.7.6"))
    compileOnly("io.micronaut:micronaut-http-client-core")
}

kotlin {
    jvmToolchain(21)
}

// The shipped examples are compiled as-is, as test sources. Each skill's examples dir is a source root.
// They refer to production classes of a fictional Offers service; src/main holds tiny stand-ins (test scaffolding only).
sourceSets {
    test {
        kotlin.srcDir("../../architecture-constraints/examples")
        kotlin.srcDir("../../eval-tiering/examples")
    }
}

tasks.test {
    useJUnitPlatform()
    testLogging {
        events("passed", "failed", "skipped")
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}
