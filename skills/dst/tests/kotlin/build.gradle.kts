plugins {
    kotlin("jvm") version "2.0.21"
}

repositories {
    mavenCentral()
}

dependencies {
    testImplementation(platform("org.junit:junit-bom:5.11.3"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
    testImplementation("io.kotest:kotest-assertions-core-jvm:5.9.1")
}

kotlin {
    jvmToolchain(21)
}

// The shipped templates are compiled as-is, as test sources (they are all *Test files plus test-support code).
sourceSets {
    test {
        kotlin.srcDir("../../templates/kotlin")
    }
}

tasks.test {
    useJUnitPlatform {
        // Needs real infrastructure; still compiled, never run here.
        excludeTags("docker")
    }
    testLogging {
        events("passed", "failed", "skipped")
        exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
    }
}
